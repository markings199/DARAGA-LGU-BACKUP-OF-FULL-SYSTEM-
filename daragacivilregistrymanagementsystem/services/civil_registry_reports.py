"""
Official Civil Registry register reports (Municipal Forms 26-1, 71, 25).

Reads saved archive records only. Pending or rejected staff edits are not stored
on the record until an approved edit is applied, so they never appear here.
Location/barangay filter values come only from existing record fields.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as xml_escape

from services.document_annotation import from_data as annotation_from_data
from services.document_annotation import public_fields as public_record_fields


MONTHS = {
    "JANUARY": "01", "JAN": "01", "FEBRUARY": "02", "FEB": "02",
    "MARCH": "03", "MAR": "03", "APRIL": "04", "APR": "04",
    "MAY": "05", "JUNE": "06", "JUN": "06", "JULY": "07", "JUL": "07",
    "AUGUST": "08", "AUG": "08", "SEPTEMBER": "09", "SEP": "09", "SEPT": "09",
    "OCTOBER": "10", "OCT": "10", "NOVEMBER": "11", "NOV": "11",
    "DECEMBER": "12", "DEC": "12",
}

REGISTER_YEAR_START = 2026
REGISTER_ROWS_PER_PAGE = 25


def register_print_sheets(rows, size: int = REGISTER_ROWS_PER_PAGE) -> List[dict]:
    """Split register rows into official 25-line sheets, padding each sheet."""
    data = list(rows or [])
    if not data:
        return [{"rows": [], "pad": size}]
    sheets = []
    for i in range(0, len(data), size):
        chunk = data[i:i + size]
        sheets.append({"rows": chunk, "pad": size - len(chunk)})
    return sheets

PLACE_KEYS_BY_TYPE = {
    "birth": ("Place of Birth", "Place of Marriage of Parents"),
    "death": ("Place of Death",),
    "marriage": ("Place of Marriage",),
}

BIRTH_EXCEL_HEADERS = [
    "(1) LCR Number",
    "(2) Date of Registration",
    "(3) Child First",
    "(3) Child Middle",
    "(3) Child Last",
    "(4) Sex",
    "(5) Birth Day",
    "(6) Birth Month",
    "(7) Birth Year",
    "(8) Time of Birth",
    "(9) Place of Birth",
    "(10) Type of Birth",
    "(11) Birth Order",
    "(12) Mother First",
    "(12) Mother Last",
    "(13) Mother Age",
    "(14) Mother Nationality",
    "(15) Mother Religion",
    "(16) Father Name",
    "(17) Father Age",
    "(18) Father Nationality",
    "(19) Father Religion",
    "(20) Parents Marriage Day",
    "(21) Parents Marriage Month",
    "(22) Parents Marriage Year",
    "(23) Place of Marriage of Parents",
    "(24) Remarks",
]

DEATH_EXCEL_HEADERS = [
    "(1) LCR Number",
    "(2) Date of Registration",
    "(3) Name of the Deceased",
    "(4) Sex",
    "(5) Age Years",
    "(6) Age Months",
    "(7) Age Days",
    "(8) Age Hours",
    "(9) Fetal Death",
    "(10) Civil Status",
    "(11) Nationality",
    "(12) Usual Residence",
    "(13) Usual Occupation",
    "(14) Death Day",
    "(15) Death Month",
    "(16) Death Year",
    "(17) Time of Death",
    "(18) Place of Death",
    "(19) Immediate Cause",
    "(20) Underlying Cause",
    "(21) Certifying Officer Name",
    "(22) Certifying Officer Title",
    "(23) Remarks",
]

MARRIAGE_EXCEL_HEADERS = [
    "Date of Registration",
    "Register Number",
    "Party",
    "Name",
    "Age Years",
    "Age Months",
    "Nationality",
    "Civil Status",
    "Residence",
    "Father Name",
    "Father Nationality",
    "Mother Name",
    "Mother Nationality",
    "Place of Marriage",
    "Date of Marriage",
    "Witness 1 Name",
    "Witness 1 Residence",
    "Witness 2 Name",
    "Witness 2 Residence",
    "Consent Name",
    "Consent Residence",
    "Consent Relation",
    "Solemnized By Name",
    "Solemnized By Title",
    "Solemnized By Address",
    "Date of Receipt",
    "Remarks",
]


def _txt(data: dict, *keys: str) -> str:
    for key in keys:
        val = data.get(key)
        if val is None:
            continue
        if isinstance(val, dict):
            continue
        s = str(val).strip()
        if s:
            return s
    return ""


_CIVIL_STATUSES = (
    "WIDOWER",
    "WIDOWED",
    "WIDOW",
    "MARRIED",
    "SINGLE",
    "DIVORCED",
    "SEPARATED",
    "ANNULLED",
)


def _civil_status(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    upper = text.upper()
    for status in _CIVIL_STATUSES:
        if re.search(rf"\b{status}\b", upper):
            return status
    return re.split(r"\bRELIG", upper, maxsplit=1)[0].strip(" /-.,")


def _nationality(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    upper = text.upper()
    if "FILIPINO" in upper or "FILIPINA" in upper:
        return "FILIPINO"
    if re.search(r"\bRELIG", upper):
        return ""
    return text


def split_person_name(full: str) -> Tuple[str, str, str]:
    parts = [p for p in re.split(r"\s+", (full or "").strip()) if p]
    if not parts:
        return "", "", ""
    if len(parts) == 1:
        return parts[0], "", ""
    if len(parts) == 2:
        return parts[0], "", parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def split_date(text: str) -> Tuple[str, str, str]:
    raw = (text or "").strip()
    if not raw:
        return "", "", ""
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", raw)
    if m:
        return str(int(m.group(3))), m.group(2).zfill(2), m.group(1)
    m = re.search(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b", raw)
    if m:
        year = m.group(3)
        if len(year) == 2:
            year = ("19" + year) if int(year) >= 50 else ("20" + year)
        return str(int(m.group(1))), m.group(2).zfill(2), year
    month_pat = "|".join(sorted(MONTHS.keys(), key=len, reverse=True))
    m = re.search(rf"\b(\d{{1,2}})\s+({month_pat})\s+(\d{{4}})\b", raw, re.I)
    if m:
        return str(int(m.group(1))), MONTHS[m.group(2).upper()], m.group(3)
    m = re.search(rf"\b({month_pat})\s+(\d{{1,2}}),?\s+(\d{{4}})\b", raw, re.I)
    if m:
        return str(int(m.group(2))), MONTHS[m.group(1).upper()], m.group(3)
    m = re.search(r"\b(19|20)\d{2}\b", raw)
    year = m.group(0) if m else ""
    return "", "", year


def split_time(text: str) -> str:
    raw = (text or "").strip()
    m = re.search(r"\b(\d{1,2}:\d{2}(?:\s*[AP]M)?)\b", raw, re.I)
    return m.group(1).upper() if m else ""


def parse_age_parts(text: str) -> Tuple[str, str, str, str]:
    raw = (text or "").strip()
    if not raw:
        return "", "", "", ""
    years = months = days = hours = ""
    m = re.search(r"(\d+)\s*(?:years?|yrs?)\b", raw, re.I)
    if m:
        years = m.group(1)
    m = re.search(r"(\d+)\s*(?:months?|mos?)\b", raw, re.I)
    if m:
        months = m.group(1)
    m = re.search(r"(\d+)\s*(?:days?)\b", raw, re.I)
    if m:
        days = m.group(1)
    m = re.search(r"(\d+)\s*(?:hours?|hrs?)\b", raw, re.I)
    if m:
        hours = m.group(1)
    if not any((years, months, days, hours)) and re.fullmatch(r"\d{1,3}", raw):
        years = raw
    return years, months, days, hours


def extract_barangay(place: str) -> str:
    raw = (place or "").strip()
    if not raw:
        return ""
    m = re.search(r"\b(?:barangay|brgy\.?|bgy\.?)\s+([^,/]+)", raw, re.I)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip(" .")
    return ""


def extract_municipality(place: str) -> str:
    raw = (place or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in re.split(r"[,/]", raw) if p.strip()]
    if len(parts) >= 2:
        return parts[-2] if len(parts) >= 3 else parts[-1]
    return raw


def _death_remarks(data: dict, fetal: str) -> str:
    remarks = _remarks(data)
    fetal_yes = fetal.strip().upper() in {"YES", "Y", "FETAL"}
    if fetal_yes:
        mother = _txt(data, "Name of Mother")
        if mother and mother.lower() not in remarks.lower():
            remarks = (remarks + " Mother: " + mother).strip()
    return remarks


def _remarks(data: dict) -> str:
    bits = []
    rem = _txt(data, "Remarks")
    if rem:
        bits.append(rem)
    ann = annotation_from_data(data)
    if ann and ann.get("text"):
        bits.append(str(ann["text"]).strip())
    return " ".join(bits).strip()


def record_is_reportable(record) -> bool:
    status = (getattr(record, "status", None) or "archived").strip().lower()
    if status in {"pending", "rejected", "draft"}:
        return False
    return True


def load_record_fields(record) -> dict:
    try:
        raw = json.loads(record.data_json) if record.data_json else {}
    except Exception:
        raw = {}
    data = public_record_fields(raw if isinstance(raw, dict) else {})
    if (record.registry_number or "").strip() and not _txt(data, "Registry Number"):
        data["Registry Number"] = record.registry_number.strip()
    return data


def record_year_values(record, data: dict) -> List[str]:
    years = []
    for text in (
        record.event_date,
        _txt(data, "Date of Registration", "Date of Birth", "Date of Death", "Date of Marriage"),
    ):
        _d, _m, y = split_date(text or "")
        if y:
            years.append(y)
    if getattr(record, "created_at", None):
        years.append(str(record.created_at.year))
    return years


def record_place_texts(doc_type: str, data: dict) -> List[str]:
    keys = PLACE_KEYS_BY_TYPE.get(doc_type, ())
    out = []
    for key in keys:
        val = _txt(data, key)
        if val:
            out.append(val)
    extra = _txt(data, "Usual Residence", "Residence", "Place of Birth")
    if extra and extra not in out:
        out.append(extra)
    return out


def _calendar_year(value) -> Optional[int]:
    try:
        year = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    if 1800 <= year <= 2200:
        return year
    return None


def collect_filter_options(rows: Iterable[dict]) -> Tuple[List[str], List[str], List[str]]:
    years = set()
    barangays = set()
    locations = set()
    for row in rows:
        for y in row.get("_years") or []:
            year = _calendar_year(y)
            if year is not None and year >= REGISTER_YEAR_START:
                years.add(str(year))
        for b in row.get("_barangays") or []:
            if b:
                barangays.add(b)
        for loc in row.get("_locations") or []:
            if loc:
                locations.add(loc)
    this_year = datetime.now().year
    for year in range(REGISTER_YEAR_START, max(this_year, REGISTER_YEAR_START) + 1):
        years.add(str(year))
    return (
        sorted(years),
        sorted(barangays, key=str.lower),
        sorted(locations, key=str.lower),
    )


def _place_tags(places: Sequence[str]) -> Tuple[List[str], List[str]]:
    barangays = []
    locations = []
    for place in places:
        bgy = extract_barangay(place)
        if bgy:
            barangays.append(bgy)
        mun = extract_municipality(place)
        if mun:
            locations.append(mun)
        if place and place not in locations:
            locations.append(place)
    return barangays, locations


def birth_row(record, data: dict) -> dict:
    child = _txt(data, "Name of Child") or (record.full_name or "")
    first, middle, last = split_person_name(child)
    m_first, _m_mid, m_last = split_person_name(_txt(data, "Name of Mother"))
    b_day, b_mo, b_year = split_date(_txt(data, "Date of Birth") or (record.event_date or ""))
    mar_day, mar_mo, mar_year = split_date(_txt(data, "Date of Marriage of Parents"))
    places = record_place_texts("birth", data)
    barangays, locations = _place_tags(places)
    return {
        "lcr_number": _txt(data, "Registry Number") or (record.registry_number or ""),
        "date_registration": _txt(data, "Date of Registration"),
        "child_first": first,
        "child_middle": middle,
        "child_last": last,
        "sex": _txt(data, "Sex"),
        "birth_day": b_day,
        "birth_month": b_mo,
        "birth_year": b_year,
        "birth_time": split_time(_txt(data, "Time of Birth", "Date of Birth")),
        "place_of_birth": _txt(data, "Place of Birth"),
        "type_of_birth": _txt(data, "Type of Birth"),
        "birth_order": _txt(data, "Birth Order"),
        "mother_first": m_first,
        "mother_last": m_last,
        "mother_age": _txt(data, "Age of Mother", "Mother Age"),
        "mother_nationality": _txt(data, "Citizenship of Mother"),
        "mother_religion": _txt(data, "Religion of Mother"),
        "father_name": _txt(data, "Name of Father"),
        "father_age": _txt(data, "Age of Father", "Father Age"),
        "father_nationality": _txt(data, "Citizenship of Father"),
        "father_religion": _txt(data, "Religion of Father"),
        "marriage_day": mar_day,
        "marriage_month": mar_mo,
        "marriage_year": mar_year,
        "place_marriage_parents": _txt(data, "Place of Marriage of Parents"),
        "remarks": _remarks(data),
        "_years": record_year_values(record, data),
        "_barangays": barangays,
        "_locations": locations,
        "_places": places,
    }


def death_row(record, data: dict) -> dict:
    years, months, days, hours = parse_age_parts(_txt(data, "Age"))
    d_day, d_mo, d_year = split_date(_txt(data, "Date of Death") or (record.event_date or ""))
    cause = _txt(data, "Cause of Death")
    places = record_place_texts("death", data)
    barangays, locations = _place_tags(places)
    fetal = _txt(data, "Fetal Death")
    return {
        "lcr_number": _txt(data, "Registry Number") or (record.registry_number or ""),
        "date_registration": _txt(data, "Date of Registration"),
        "name": _txt(data, "Name of Deceased") or (record.full_name or ""),
        "sex": _txt(data, "Sex"),
        "age_years": years,
        "age_months": months,
        "age_days": days,
        "age_hours": hours,
        "fetal_death": fetal,
        "civil_status": _civil_status(_txt(data, "Civil Status")),
        "nationality": _nationality(_txt(data, "Nationality", "Citizenship")),
        "residence": _txt(data, "Usual Residence", "Residence"),
        "occupation": _txt(data, "Usual Occupation", "Occupation"),
        "death_day": d_day,
        "death_month": d_mo,
        "death_year": d_year,
        "death_time": split_time(_txt(data, "Time of Death", "Date of Death")),
        "place_of_death": _txt(data, "Place of Death"),
        "cause_immediate": cause,
        "cause_underlying": _txt(data, "Underlying Cause", "Underlying Cause of Death"),
        "officer_name": _txt(data, "Certifying Officer", "Name of Attendant", "Attendant"),
        "officer_title": _txt(data, "Title of Position", "Certifying Officer Title"),
        "remarks": _death_remarks(data, fetal),
        "_years": record_year_values(record, data),
        "_barangays": barangays,
        "_locations": locations,
        "_places": places,
    }


def marriage_row(record, data: dict) -> dict:
    h_years, h_months, _, _ = parse_age_parts(_txt(data, "Husband Age"))
    w_years, w_months, _, _ = parse_age_parts(_txt(data, "Wife Age"))
    places = record_place_texts("marriage", data)
    barangays, locations = _place_tags(places)
    return {
        "date_registration": _txt(data, "Date of Registration"),
        "register_number": _txt(data, "Registry Number") or (record.registry_number or ""),
        "husband_name": _txt(data, "Husband Name"),
        "wife_name": _txt(data, "Wife Name"),
        "husband_age_years": h_years,
        "husband_age_months": h_months,
        "wife_age_years": w_years,
        "wife_age_months": w_months,
        "husband_nationality": _nationality(_txt(data, "Husband Citizenship", "Husband Nationality")),
        "wife_nationality": _nationality(_txt(data, "Wife Citizenship", "Wife Nationality")),
        "husband_status": _civil_status(_txt(data, "Husband Civil Status")),
        "wife_status": _civil_status(_txt(data, "Wife Civil Status")),
        "husband_residence": _txt(data, "Husband Residence", "Residence"),
        "wife_residence": _txt(data, "Wife Residence"),
        "husband_father": _txt(data, "Husband Father"),
        "wife_father": _txt(data, "Wife Father"),
        "husband_father_nat": _nationality(_txt(data, "Husband Father Citizenship", "Husband Father Nationality")),
        "wife_father_nat": _nationality(_txt(data, "Wife Father Citizenship", "Wife Father Nationality")),
        "husband_mother": _txt(data, "Husband Mother"),
        "wife_mother": _txt(data, "Wife Mother"),
        "husband_mother_nat": _nationality(_txt(data, "Husband Mother Citizenship", "Husband Mother Nationality")),
        "wife_mother_nat": _nationality(_txt(data, "Wife Mother Citizenship", "Wife Mother Nationality")),
        "place_of_marriage": _txt(data, "Place of Marriage"),
        "date_of_marriage": _txt(data, "Date of Marriage") or (record.event_date or ""),
        "witness1_name": _txt(data, "Witness 1", "Witness 1 Name"),
        "witness1_res": _txt(data, "Witness 1 Residence"),
        "witness2_name": _txt(data, "Witness 2", "Witness 2 Name"),
        "witness2_res": _txt(data, "Witness 2 Residence"),
        "consent_name": _txt(data, "Person Giving Consent", "Consent Name"),
        "consent_res": _txt(data, "Consent Residence"),
        "consent_rel": _txt(data, "Consent Relation"),
        "solemnizer_name": _txt(data, "Solemnized By", "Priest/Minister"),
        "solemnizer_title": _txt(data, "Solemnizer Title", "Title"),
        "solemnizer_address": _txt(data, "Solemnizer Address"),
        "date_receipt": _txt(data, "Date of Receipt", "Date of Registration"),
        "remarks": _remarks(data),
        "_years": record_year_values(record, data),
        "_barangays": barangays,
        "_locations": locations,
        "_places": places,
    }


def row_matches_filters(row: dict, year: str, barangay: str, location: str) -> bool:
    row_years = {_calendar_year(y) for y in (row.get("_years") or [])}
    row_years.discard(None)
    if not any(value >= REGISTER_YEAR_START for value in row_years):
        return False
    want = _calendar_year(year)
    if want is not None:
        if want < REGISTER_YEAR_START or want not in row_years:
            return False
    if barangay:
        needle = barangay.strip().lower()
        hay = " ".join(row.get("_barangays") or []).lower() + " " + " ".join(row.get("_places") or []).lower()
        if needle not in hay:
            return False
    if location:
        needle = location.strip().lower()
        hay = " ".join(row.get("_locations") or []).lower() + " " + " ".join(row.get("_places") or []).lower()
        if needle not in hay:
            return False
    return True


def _norm_find_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").casefold()).strip()


def _compact_find_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def row_matches_find(row: dict, query: str) -> bool:
    """Match a person name or LCR / registry number inside the current register."""
    q = _norm_find_text(query)
    if not q:
        return True
    lcr = " ".join(
        str(row.get(key) or "")
        for key in ("lcr_number", "register_number")
    )
    names = " ".join(
        str(row.get(key) or "")
        for key in (
            "child_first",
            "child_middle",
            "child_last",
            "name",
            "husband_name",
            "wife_name",
        )
    )
    hay = _norm_find_text(f"{names} {lcr}")
    tokens = q.split()
    if tokens and all(token in hay for token in tokens):
        return True
    compact_q = _compact_find_id(q)
    compact_lcr = _compact_find_id(lcr)
    return bool(compact_q) and compact_q in compact_lcr


def birth_excel_row(row: dict) -> List[str]:
    return [
        row.get("lcr_number", ""),
        row.get("date_registration", ""),
        row.get("child_first", ""),
        row.get("child_middle", ""),
        row.get("child_last", ""),
        row.get("sex", ""),
        row.get("birth_day", ""),
        row.get("birth_month", ""),
        row.get("birth_year", ""),
        row.get("birth_time", ""),
        row.get("place_of_birth", ""),
        row.get("type_of_birth", ""),
        row.get("birth_order", ""),
        row.get("mother_first", ""),
        row.get("mother_last", ""),
        row.get("mother_age", ""),
        row.get("mother_nationality", ""),
        row.get("mother_religion", ""),
        row.get("father_name", ""),
        row.get("father_age", ""),
        row.get("father_nationality", ""),
        row.get("father_religion", ""),
        row.get("marriage_day", ""),
        row.get("marriage_month", ""),
        row.get("marriage_year", ""),
        row.get("place_marriage_parents", ""),
        row.get("remarks", ""),
    ]


def death_excel_row(row: dict) -> List[str]:
    return [
        row.get("lcr_number", ""),
        row.get("date_registration", ""),
        row.get("name", ""),
        row.get("sex", ""),
        row.get("age_years", ""),
        row.get("age_months", ""),
        row.get("age_days", ""),
        row.get("age_hours", ""),
        row.get("fetal_death", ""),
        row.get("civil_status", ""),
        row.get("nationality", ""),
        row.get("residence", ""),
        row.get("occupation", ""),
        row.get("death_day", ""),
        row.get("death_month", ""),
        row.get("death_year", ""),
        row.get("death_time", ""),
        row.get("place_of_death", ""),
        row.get("cause_immediate", ""),
        row.get("cause_underlying", ""),
        row.get("officer_name", ""),
        row.get("officer_title", ""),
        row.get("remarks", ""),
    ]


def marriage_excel_rows(row: dict) -> List[List[str]]:
    shared_tail = [
        row.get("place_of_marriage", ""),
        row.get("date_of_marriage", ""),
        row.get("witness1_name", ""),
        row.get("witness1_res", ""),
        row.get("witness2_name", ""),
        row.get("witness2_res", ""),
        row.get("consent_name", ""),
        row.get("consent_res", ""),
        row.get("consent_rel", ""),
        row.get("solemnizer_name", ""),
        row.get("solemnizer_title", ""),
        row.get("solemnizer_address", ""),
        row.get("date_receipt", ""),
        row.get("remarks", ""),
    ]
    husband = [
        row.get("date_registration", ""),
        row.get("register_number", ""),
        "Husband",
        row.get("husband_name", ""),
        row.get("husband_age_years", ""),
        row.get("husband_age_months", ""),
        row.get("husband_nationality", ""),
        row.get("husband_status", ""),
        row.get("husband_residence", ""),
        row.get("husband_father", ""),
        row.get("husband_father_nat", ""),
        row.get("husband_mother", ""),
        row.get("husband_mother_nat", ""),
    ] + shared_tail
    wife = [
        row.get("date_registration", ""),
        row.get("register_number", ""),
        "Wife",
        row.get("wife_name", ""),
        row.get("wife_age_years", ""),
        row.get("wife_age_months", ""),
        row.get("wife_nationality", ""),
        row.get("wife_status", ""),
        row.get("wife_residence", ""),
        row.get("wife_father", ""),
        row.get("wife_father_nat", ""),
        row.get("wife_mother", ""),
        row.get("wife_mother_nat", ""),
    ] + shared_tail
    return [husband, wife]


def build_xlsx(sheets: Dict[str, Tuple[List[str], List[List[Any]]]]) -> bytes:
    """Office Open XML workbook (Excel) without extra packages."""
    buf = io.BytesIO()
    sheet_names = list(sheets.keys()) or ["Sheet1"]

    def sheet_xml(headers: List[str], rows: List[List[Any]]) -> bytes:
        cells = []
        all_rows = [headers] + rows

        def cell(r_idx: int, c_idx: int, value: Any) -> str:
            ref = _xlsx_cell_ref(c_idx, r_idx)
            text = "" if value is None else str(value)
            return (
                f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                f"{xml_escape(text)}</t></is></c>"
            )

        row_xml = []
        for r_i, row in enumerate(all_rows, start=1):
            c_xml = "".join(cell(r_i, c_i, val) for c_i, val in enumerate(row, start=1))
            row_xml.append(f'<row r="{r_i}">{c_xml}</row>')
        cols = max((len(r) for r in all_rows), default=1)
        return (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<dimension ref="A1:{_xlsx_cell_ref(cols, max(len(all_rows), 1))}"/>'
            "<sheetData>"
            + "".join(row_xml)
            + "</sheetData></worksheet>"
        ).encode("utf-8")

    wb_sheets = []
    rels = []
    for i, name in enumerate(sheet_names, start=1):
        safe = re.sub(r"[\\/*?:\[\]]", " ", name)[:31] or f"Sheet{i}"
        wb_sheets.append(f'<sheet name="{xml_escape(safe)}" sheetId="{i}" r:id="rId{i}"/>')
        rels.append(
            f'<Relationship Id="rId{i}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
        )

    content_types_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, len(sheet_names) + 1)
    )

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "[Content_Types].xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                '<Default Extension="xml" ContentType="application/xml"/>'
                '<Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                f"{content_types_overrides}"
                "</Types>"
            ),
        )
        zf.writestr(
            "_rels/.rels",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" '
                'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
                'Target="xl/workbook.xml"/>'
                "</Relationships>"
            ),
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                + "".join(rels)
                + "</Relationships>"
            ),
        )
        zf.writestr(
            "xl/workbook.xml",
            (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                "<sheets>"
                + "".join(wb_sheets)
                + "</sheets></workbook>"
            ),
        )
        for i, name in enumerate(sheet_names, start=1):
            headers, rows = sheets.get(name, ([], []))
            zf.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml(headers, rows))
    return buf.getvalue()


def _xlsx_cell_ref(col: int, row: int) -> str:
    name = ""
    n = col
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return f"{name}{row}"
