import re
import difflib
from typing import Dict, Any, List

import cv2

from ocr.shared import imread_bgr, run_ocr_on_image
from ocr.engine import extract_from_ocr_text, ocr_pages_to_text
from ocr.field_boxes import extract_field_boxes, is_plausible_cause_of_death
from ocr.person_name import compose_first_middle_last
from ocr.sex import sex_from_ocr_text, sex_from_tokens

DEBUG_TEMPLATE_MATCH = False


MONTHS = {
    "JANUARY",
    "FEBRUARY",
    "MARCH",
    "APRIL",
    "MAY",
    "JUNE",
    "JULY",
    "AUGUST",
    "SEPTEMBER",
    "OCTOBER",
    "NOVEMBER",
    "DECEMBER",
}

STATUS_WORDS = {"SINGLE", "MARRIED", "WIDOWED", "DIVORCED", "SEPARATED", "ANNULLED"}
STATUS_ALIASES = {
    "WIDOW": "WIDOWED",
    "WIDOWER": "WIDOWED",
    "WIDOWED": "WIDOWED",
    "SINGLE": "SINGLE",
    "MARRIED": "MARRIED",
    "DIVORCED": "DIVORCED",
    "SEPARATED": "SEPARATED",
    "ANNULLED": "ANNULLED",
}

NAME_BANNED = {
    "NAME",
    "FATHER",
    "MOTHER",
    "PRINT",
    "INFORMANT",
    "REGISTRY",
    "PROVINCE",
    "CITY",
    "MUNICIPALITY",
    "SEX",
    "MALE",
    "FEMALE",
    "LAST",
    "MIDDLE",
    "FIRST",
    "FIRSAT",
    "MIDDKE",
    "HAME",
    # avoid picking the big title line as the person's name
    "CERTIFICATE",
    "CERTIFICATE OF DEATH",
    "FILL",
    "TYPEWRITE",
    "TYPEWRITTEN",
    "HANDWRITTEN",
    "DECEASED",
}

# Location keywords to help identify place of death
LOCATION_KEYWORDS = {
    "MANILA", "CEBU", "DAVAO", "QUEZON", "RIZAL", "LAGUNA", "BATANGAS",
    "NUEVA", "ILOCOS", "VISAYAS", "METRO", "CALOOCAN", "PASAY", "MAKATI",
    "CITY", "TOWN", "MUNICIPAL", "BARANGAY", "PROVINCE", "HOSPITAL",
    "MEDICAL", "CLINIC", "HEALTH", "CENTER", "CENTRE"
}


def _clean_text(t: str) -> str:
    t = str(t).strip()
    t = t.replace("|", " ")
    t = re.sub(r"[._]{2,}", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _norm_text(t: str) -> str:
    t = _clean_text(t).upper()
    t = re.sub(r"[^A-Z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _alpha_count(t: str) -> int:
    return sum(c.isalpha() for c in t)


def _build_items(result, img_w: int, img_h: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for page in result:
        texts = page.get("rec_texts", [])
        scores = page.get("rec_scores", [])
        polys = page.get("dt_polys", [])

        for text, score, box in zip(texts, scores, polys):
            text = str(text).strip()
            if not text:
                continue
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]

            item = {
                "text": text,
                "score": float(score),
                "x1": int(min(xs)),
                "y1": int(min(ys)),
                "x2": int(max(xs)),
                "y2": int(max(ys)),
            }
            item["cx"] = (item["x1"] + item["x2"]) / 2.0
            item["cy"] = (item["y1"] + item["y2"]) / 2.0
            item["w"] = item["x2"] - item["x1"]
            item["h"] = item["y2"] - item["y1"]
            items.append(item)

    items.sort(key=lambda x: (x["cy"], x["x1"]))
    return items


def _tokens_in_region(items, xmin, xmax, ymin, ymax, min_score=0.30):
    out = []
    for it in items:
        if it["score"] < min_score:
            continue
        if xmin <= it["cx"] <= xmax and ymin <= it["cy"] <= ymax:
            out.append(it)
    out.sort(key=lambda x: (x["cy"], x["x1"]))
    return out


def _group_rows(tokens, y_tol=12):
    if not tokens:
        return []

    tokens = sorted(tokens, key=lambda x: (x["cy"], x["x1"]))
    rows = [[tokens[0]]]

    for it in tokens[1:]:
        row_y = sum(x["cy"] for x in rows[-1]) / len(rows[-1])
        if abs(it["cy"] - row_y) <= y_tol:
            rows[-1].append(it)
        else:
            rows.append([it])

    for row in rows:
        row.sort(key=lambda x: x["x1"])
    return rows


def _join_tokens(tokens):
    return " ".join(_clean_text(t["text"]) for t in sorted(tokens, key=lambda x: x["x1"])).strip()


def _find_label(
    items,
    required_all=None,
    required_any=None,
    forbidden=None,
    x_min=None,
    x_max=None,
    y_min=None,
    y_max=None,
    min_score=0.30,
):
    required_all = required_all or []
    required_any = required_any or []
    forbidden = forbidden or []

    best = None
    best_score = None

    for it in items:
        if it["score"] < min_score:
            continue

        if x_min is not None and it["cx"] < x_min:
            continue
        if x_max is not None and it["cx"] > x_max:
            continue
        if y_min is not None and it["cy"] < y_min:
            continue
        if y_max is not None and it["cy"] > y_max:
            continue

        nt = _norm_text(it["text"])

        if required_all and not all(w.upper() in nt for w in required_all):
            continue
        if required_any and not any(w.upper() in nt for w in required_any):
            continue
        if forbidden and any(w.upper() in nt for w in forbidden):
            continue

        score = (
            len(required_all) * 5
            + sum(1 for w in required_any if w.upper() in nt)
            + it["score"] * 2
            - len(nt) * 0.02
        )

        if best is None or score > best_score:
            best = it
            best_score = score

    return best


def _best_exact_choice(tokens, choices):
    choices = {c.upper() for c in choices}
    vals = []
    for it in tokens:
        nt = _norm_text(it["text"])
        if nt in choices:
            vals.append((it["score"], it["x1"], nt))
    if not vals:
        return ""
    vals.sort(key=lambda x: (-x[0], x[1]))
    return vals[0][2]


def _choose_best_row(tokens, banned_words=None, prefer_y=None):
    banned_words = banned_words or []
    rows = _group_rows(tokens)

    best_text = ""
    best_tuple = None

    for row in rows:
        kept = []
        for it in row:
            text = _clean_text(it["text"])
            nt = _norm_text(text)

            if not text:
                continue
            if len(nt) == 1 and not nt.isdigit():
                continue
            if any(b.upper() in nt for b in banned_words):
                continue

            kept.append(it)

        if not kept:
            continue

        text = _join_tokens(kept)
        letters = _alpha_count(text)
        avg_score = sum(x["score"] for x in kept) / len(kept)
        row_y = sum(x["cy"] for x in kept) / len(kept)

        score = letters + avg_score * 10 + len(kept) * 2
        if prefer_y is not None:
            score -= abs(row_y - prefer_y) * 0.08

        tup = (score, letters, avg_score)

        if best_text == "" or tup > best_tuple:
            best_text = text
            best_tuple = tup

    return _clean_text(best_text)


def _normalize_month_word(word: str) -> str:
    w = _norm_text(word)
    if w in MONTHS:
        return w

    match = difflib.get_close_matches(w, list(MONTHS), n=1, cutoff=0.55)
    if match:
        return match[0]
    return w


_MONTH_ALT = "|".join(sorted(MONTHS, key=len, reverse=True))
_DATE_BLOB_RE = re.compile(
    rf"(?<!\d)(\d{{1,2}})\s+({_MONTH_ALT})\s+(\d{{4}})\b"
)


def _insert_month_spaces(text: str) -> str:
    """Turn 'March2025' / '10January' into spaced date parts."""
    n = _norm_text(text)
    for month in sorted(MONTHS, key=len, reverse=True):
        n = re.sub(rf"(?<=\d)({month})", r" \1", n)
        n = re.sub(rf"({month})(?=\d)", r"\1 ", n)
    return re.sub(r"\s+", " ", n).strip()


def _parse_date_from_blob(text: str) -> str:
    """Read Form 103 dates even when OCR glues month and year together."""
    n = _insert_month_spaces(text)
    n = re.sub(r"\b(DAY|MONTH|YEAR|DATE|OF|DEATH|BIRTH)\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    m = _DATE_BLOB_RE.search(n)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"
    return ""


def _normalize_date_text(text: str) -> str:
    text = _norm_text(text)
    parts = text.split()

    if len(parts) == 3 and re.fullmatch(r"\d{1,2}", parts[0]) and re.fullmatch(r"\d{4}", parts[2]):
        month = _normalize_month_word(parts[1])
        return f"{parts[0]} {month} {parts[2]}"

    return text


def _is_full_date(text: str) -> bool:
    text = _normalize_date_text(text)
    return bool(
        re.fullmatch(
            r"\d{1,2}\s+(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{4}",
            text,
        )
    )


def _extract_date_candidates(tokens):
    rows = _group_rows(tokens)
    found = []

    for row in rows:
        row = sorted(row, key=lambda x: x["x1"])

        for it in row:
            parsed = _parse_date_from_blob(it["text"])
            if parsed:
                found.append(
                    {
                        "text": parsed,
                        "x1": it["x1"],
                        "y1": it["y1"],
                    }
                )
                continue
            nt = _normalize_date_text(it["text"])
            if _is_full_date(nt):
                found.append(
                    {
                        "text": nt,
                        "x1": it["x1"],
                        "y1": it["y1"],
                    }
                )

        for i in range(len(row) - 1):
            parsed = _parse_date_from_blob(
                row[i]["text"] + " " + row[i + 1]["text"]
            )
            if parsed:
                found.append(
                    {
                        "text": parsed,
                        "x1": row[i]["x1"],
                        "y1": min(row[i]["y1"], row[i + 1]["y1"]),
                    }
                )

        for i in range(len(row) - 2):
            a = _norm_text(row[i]["text"])
            b = _normalize_month_word(row[i + 1]["text"])
            c = _norm_text(row[i + 2]["text"])

            if re.fullmatch(r"\d{1,2}", a) and b in MONTHS and re.fullmatch(r"\d{4}", c):
                found.append(
                    {
                        "text": f"{a} {b} {c}",
                        "x1": row[i]["x1"],
                        "y1": min(row[i]["y1"], row[i + 1]["y1"], row[i + 2]["y1"]),
                    }
                )

        for i in range(len(row) - 1):
            a = _norm_text(row[i]["text"])
            b = _normalize_date_text(row[i + 1]["text"])

            if re.fullmatch(r"\d{1,2}", a):
                m = re.fullmatch(
                    r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(\d{4})",
                    b,
                )
                if m:
                    found.append(
                        {
                            "text": f"{a} {m.group(1)} {m.group(2)}",
                            "x1": row[i]["x1"],
                            "y1": min(row[i]["y1"], row[i + 1]["y1"]),
                        }
                    )

    unique = []
    seen = set()
    for d in sorted(found, key=lambda z: (z["x1"], z["y1"], z["text"])):
        key = (d["text"], round(d["x1"] / 8), round(d["y1"] / 8))
        if key not in seen:
            seen.add(key)
            unique.append(d)

    return unique


def _extract_registry_number(items, img_w, img_h):
    upper_right = _tokens_in_region(items, img_w * 0.52, img_w * 0.98, 0, img_h * 0.16, min_score=0.30)

    candidates = []
    for it in upper_right:
        t = _clean_text(it["text"])
        t2 = re.sub(r"\s*-\s*", "-", t)

        if re.fullmatch(r"\d{2,4}-\d{2,6}", t2):
            candidates.append((it["score"], it["x1"], t2))

    if not candidates:
        return ""

    candidates.sort(key=lambda x: (-x[0], x[1]))
    return candidates[0][2]


def _extract_name(items, img_w, img_h):
    name_label = _find_label(
        items,
        required_any=["NAME"],
        forbidden=["FATHER", "MOTHER", "PRINT", "INFORMANT", "PREPARED"],
        x_min=0,
        x_max=img_w * 0.22,
        y_min=img_h * 0.05,
        y_max=img_h * 0.15,
    )

    sex_label = _find_label(
        items,
        required_any=["SEX"],
        x_min=img_w * 0.65,
        x_max=img_w,
        y_min=img_h * 0.06,
        y_max=img_h * 0.16,
    )

    xmin = img_w * 0.12
    xmax = sex_label["x1"] - 10 if sex_label else img_w * 0.75

    if name_label:
        ymin = name_label["y2"] + 2
        ymax = name_label["y2"] + 50
    else:
        ymin = img_h * 0.09
        ymax = img_h * 0.17

    region = _tokens_in_region(items, xmin, xmax, ymin, ymax, min_score=0.30)
    rows = _group_rows(region)

    best_name = ""
    best_tuple = None

    for row in rows:
        kept = []
        row_y = sum(x["cy"] for x in row) / len(row)

        if name_label and row_y <= name_label["cy"] + 3:
            continue

        for it in row:
            text = _clean_text(it["text"])
            nt = _norm_text(text)

            if not text:
                continue
            if "(" in text or ")" in text:
                continue
            if nt in NAME_BANNED:
                continue
            if nt in MONTHS:
                continue
            if nt in {"MALE", "FEMALE"}:
                continue
            if re.fullmatch(r"\d+", nt):
                continue
            if "CITY" in nt or "MUNICIPALITY" in nt or "REGISTRY" in nt:
                continue

            kept.append(it)

        if not kept:
            continue

        text = compose_first_middle_last(kept, xmin, xmax) or _join_tokens(kept)
        letters = _alpha_count(text)
        avg_score = sum(x["score"] for x in kept) / len(kept)

        tup = (letters, len(kept), avg_score, row_y)

        if best_name == "" or tup > best_tuple:
            best_name = text
            best_tuple = tup

    return _sanitize_person_name(best_name)


def _extract_name_of_mother(items, img_w, img_h) -> str:
    """Extract mother's maiden name from the parental information section."""
    mother_label = _find_label(
        items,
        required_all=["MOTHER"],
        forbidden=["FATHER", "DECEASED", "INFORMANT", "PREPARED", "NAME OF DECEASED"],
        x_min=0,
        x_max=img_w * 0.38,
        y_min=img_h * 0.17,
        y_max=img_h * 0.34,
    )
    if not mother_label:
        mother_label = _find_label(
            items,
            required_any=["MAIDEN"],
            forbidden=["FATHER", "DECEASED", "INFORMANT"],
            x_min=0,
            x_max=img_w * 0.42,
            y_min=img_h * 0.19,
            y_max=img_h * 0.36,
        )

    father_label = _find_label(
        items,
        required_all=["FATHER"],
        forbidden=["MOTHER", "DECEASED", "INFORMANT"],
        x_min=0,
        x_max=img_w * 0.38,
        y_min=img_h * 0.17,
        y_max=img_h * 0.32,
    )

    banned = [
        "MOTHER", "MAIDEN", "FATHER", "CITIZENSHIP", "RELIGION", "RESIDENCE",
        "OCCUPATION", "PLACE", "DEATH", "BIRTH", "INFORMANT", "REGISTRY",
    ]

    if mother_label:
        xmin = max(img_w * 0.10, mother_label["x1"] - 5)
        xmax = img_w * 0.75
        ymin = mother_label["y1"] - 6
        ymax = mother_label["y2"] + 42
        if father_label and father_label["cy"] > mother_label["cy"]:
            ymax = min(ymax, father_label["y1"] - 4)

        region = _tokens_in_region(items, xmin, xmax, ymin, ymax, min_score=0.25)
        text = compose_first_middle_last(region, xmin, xmax) or _choose_best_row(
            region,
            banned_words=banned,
            prefer_y=mother_label["cy"] + 10,
        )
        if text and _alpha_count(text) >= 4:
            nt = _norm_text(text)
            if nt not in STATUS_WORDS and nt not in {"FILIPINO", "MALE", "FEMALE"}:
                return _sanitize_person_name(text)

        region_below = _tokens_in_region(
            items,
            img_w * 0.10,
            img_w * 0.75,
            mother_label["y2"] + 2,
            mother_label["y2"] + 38,
            min_score=0.25,
        )
        text = _choose_best_row(
            region_below,
            banned_words=banned,
            prefer_y=mother_label["y2"] + 14,
        )
        if text and _alpha_count(text) >= 4:
            nt = _norm_text(text)
            if nt not in STATUS_WORDS and nt not in {"FILIPINO", "MALE", "FEMALE"}:
                return _sanitize_person_name(text)

    # Fallback: row below father's name when mother label OCR fails.
    if father_label:
        region = _tokens_in_region(
            items,
            img_w * 0.10,
            img_w * 0.75,
            father_label["y2"] + 8,
            father_label["y2"] + 55,
            min_score=0.25,
        )
        text = _choose_best_row(
            region,
            banned_words=banned + ["SINGLE", "MARRIED", "WIDOWED", "DIVORCED"],
            prefer_y=father_label["y2"] + 28,
        )
        if text and _alpha_count(text) >= 4:
            nt = _norm_text(text)
            if nt not in STATUS_WORDS and nt not in {"FILIPINO", "MALE", "FEMALE"}:
                return _sanitize_person_name(text)

    return ""


def _extract_sex(items, img_w, img_h):
    region = _tokens_in_region(
        items,
        img_w * 0.72,
        img_w * 0.97,
        img_h * 0.09,
        img_h * 0.17,
        min_score=0.30,
    )
    value = sex_from_tokens(region)
    if value:
        return value

    sex_label = _find_label(
        items,
        required_any=["SEX"],
        x_min=img_w * 0.58,
        x_max=img_w,
        y_min=img_h * 0.06,
        y_max=img_h * 0.18,
        min_score=0.20,
    )
    if sex_label:
        near = _tokens_in_region(
            items,
            sex_label["x2"] + 2,
            img_w * 0.98,
            sex_label["y1"] - 10,
            sex_label["y2"] + 28,
            min_score=0.20,
        )
        value = sex_from_tokens(near) or sex_from_ocr_text(
            " ".join(it["text"] for it in [sex_label, *near])
        )
        if value:
            return value

    value = sex_from_tokens(items)
    return value


def _extract_date_in_region(items, xmin, xmax, ymin, ymax) -> str:
    """Read a Form 103 date from one write-in cell, including glued OCR tokens."""
    cell = _tokens_in_region(items, xmin, xmax, ymin, ymax, min_score=0.22)
    if not cell:
        return ""
    cell.sort(key=lambda it: (it["x1"], it["cy"]))
    for it in cell:
        parsed = _parse_date_from_blob(it["text"])
        if parsed:
            return parsed
    for i in range(len(cell) - 1):
        parsed = _parse_date_from_blob(cell[i]["text"] + " " + cell[i + 1]["text"])
        if parsed:
            return parsed
    return _parse_date_from_blob(" ".join(it["text"] for it in cell))


def _extract_dates_and_age(items, img_w, img_h):
    # Item 3 is the left cell; item 4 (birth) is center. Keep them separate.
    date_of_death = _extract_date_in_region(
        items,
        img_w * 0.04,
        img_w * 0.33,
        img_h * 0.156,
        img_h * 0.195,
    )
    date_of_birth = _extract_date_in_region(
        items,
        img_w * 0.33,
        img_w * 0.56,
        img_h * 0.156,
        img_h * 0.195,
    )

    age_region = _tokens_in_region(
        items,
        img_w * 0.58,
        img_w * 0.74,
        img_h * 0.14,
        img_h * 0.20,
        min_score=0.30,
    )

    age_candidates = []
    target_x = img_w * 0.66

    for it in age_region:
        parsed = _parse_completed_age(it["text"])
        if parsed:
            age_candidates.append(
                (
                    0 if re.search(r"YEAR|YRS", _norm_text(it["text"])) else 1,
                    abs(it["cx"] - target_x),
                    -it["score"],
                    parsed,
                )
            )
            continue
        nt = _norm_text(it["text"])
        if re.fullmatch(r"\d{1,3}", nt):
            val = int(nt)
            if 0 <= val <= 130:
                age_candidates.append(
                    (
                        1,
                        abs(it["cx"] - target_x),
                        -it["score"],
                        nt,
                    )
                )

    age = ""
    if age_candidates:
        age_candidates.sort()
        age = age_candidates[0][3]

    return date_of_death, date_of_birth, age


_MONTH_WORD_RE = re.compile(
    r"\b(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\b"
)
_AGE_WITH_UNIT_RE = re.compile(r"(?<!\d)(\d{1,3})\s*(YEARS?|YRS)\b")


def _parse_completed_age(text: str) -> str:
    """Read '55', '55 Years', or '55Years' from Form 103 item 5 (1 year or above)."""
    n = _norm_text(text)
    if not n or _MONTH_WORD_RE.search(n) or re.search(r"\b(?:19|20)\d{2}\b", n):
        return ""
    # Printed prompt "If 1 year or above" — never treat bare 1 as age.
    if re.search(r"\b1\s*(YEAR|YRS?)?\s*(OR\s+)?ABOVE\b", n) or re.search(
        r"\bIF\s+1\b", n
    ):
        n = re.sub(r"\bIF\s+1\s*(YEAR|YRS?)?\s*(OR\s+)?ABOVE\b", " ", n)
        n = re.sub(r"\b1\s*(YEAR|YRS?)?\s*(OR\s+)?ABOVE\b", " ", n)
        n = re.sub(r"\s+", " ", n).strip()
    hits = []
    for m in _AGE_WITH_UNIT_RE.finditer(n):
        val = int(m.group(1))
        after = n[m.end() : m.end() + 18].strip()
        before = n[max(0, m.start() - 18) : m.start()]
        if val == 1 and (after.startswith("OR") or "ABOVE" in after[:18] or "UNDER" in before):
            continue
        if 0 <= val <= 130:
            hits.append((2, val))
    if hits:
        hits.sort(reverse=True)
        return str(hits[0][1])
    for m in re.finditer(r"(?:YEARS?|YRS)\s+(\d{1,3})\b", n):
        val = int(m.group(1))
        if 1 <= val <= 130:
            return str(val)
    if re.fullmatch(r"\d{1,3}", n):
        val = int(n)
        # Bare "1" is almost always the printed "If 1 year or above" label.
        if val == 1:
            return ""
        if 2 <= val <= 130:
            return str(val)
    return ""


def _extract_age(items, img_w, img_h) -> str:
    """
    Form 103 item 5: Age at the time of death, 'If 1 year or above'.
    Handwriting is often '55 Years' as one OCR line, not a bare '55'.
    """
    band = [
        it
        for it in items
        if it.get("score", 0) >= 0.22
        and img_h * 0.08 <= it["cy"] <= img_h * 0.32
        and it["cx"] >= img_w * 0.42
    ]
    band.sort(key=lambda it: (it["cy"], it["x1"]))

    unit_hits = []
    for it in band:
        parsed = _parse_completed_age(it["text"])
        if not parsed:
            continue
        n = _norm_text(it["text"])
        if _AGE_WITH_UNIT_RE.search(n):
            unit_hits.append((it["score"], parsed))
    if unit_hits:
        unit_hits.sort(reverse=True)
        return unit_hits[0][1]

    for i, it in enumerate(band):
        if i + 1 >= len(band):
            break
        nxt = band[i + 1]
        if abs(it["cy"] - nxt["cy"]) > img_h * 0.03:
            continue
        parsed = _parse_completed_age(it["text"] + " " + nxt["text"])
        if parsed:
            return parsed

    age_label = _find_label(
        items,
        required_all=["AGE"],
        forbidden=["MARRIAGE", "PARENTS"],
        x_min=img_w * 0.48,
        x_max=img_w,
        y_min=img_h * 0.10,
        y_max=img_h * 0.28,
        min_score=0.22,
    )
    above_label = _find_label(
        items,
        required_any=["ABOVE"],
        x_min=img_w * 0.50,
        x_max=img_w,
        y_min=img_h * 0.12,
        y_max=img_h * 0.28,
        min_score=0.22,
    )
    anchor = age_label or above_label
    if anchor:
        region = _tokens_in_region(
            items,
            max(0.0, anchor["x1"] - img_w * 0.04),
            min(float(img_w), max(anchor["x2"], img_w * 0.82)),
            anchor["y1"] - img_h * 0.01,
            min(float(img_h), anchor["y2"] + img_h * 0.09),
            min_score=0.22,
        )
        for it in region:
            parsed = _parse_completed_age(it["text"])
            if parsed and parsed != "1":
                return parsed
        for i, it in enumerate(region):
            if i + 1 >= len(region):
                break
            parsed = _parse_completed_age(it["text"] + " " + region[i + 1]["text"])
            if parsed:
                return parsed

    value_cell = _tokens_in_region(
        items,
        img_w * 0.52,
        img_w * 0.86,
        img_h * 0.14,
        img_h * 0.26,
        min_score=0.22,
    )
    cell_hits = []
    for it in value_cell:
        parsed = _parse_completed_age(it["text"])
        if parsed and parsed != "1":
            cell_hits.append((it["score"], parsed))
    if cell_hits:
        cell_hits.sort(reverse=True)
        return cell_hits[0][1]
    return ""


def _extract_place_of_birth(items, img_w, img_h):
    pob_label = _find_label(
        items,
        required_all=["PLACE", "BIRTH"],
        forbidden=["DEATH", "MARRIAGE"],
        x_min=0,
        x_max=img_w * 0.38,
        y_min=img_h * 0.12,
        y_max=img_h * 0.26,
    )

    civil_label = _find_label(
        items,
        required_all=["CIVIL", "STATUS"],
        x_min=img_w * 0.60,
        x_max=img_w,
        y_min=img_h * 0.14,
        y_max=img_h * 0.24,
    )

    if pob_label:
        xmin = pob_label["x2"] + 6
        xmax = civil_label["x1"] - 10 if civil_label else img_w * 0.62
        ymin = pob_label["y1"] - 10
        ymax = pob_label["y2"] + 50
        if civil_label and civil_label["y1"] > pob_label["y1"]:
            ymax = min(ymax, civil_label["y1"] + 5)
        region = _tokens_in_region(items, xmin, xmax, ymin, ymax, min_score=0.20)
        text = _choose_best_row(
            region,
            banned_words=["PLACE", "BIRTH", "CIVIL", "STATUS", "CITIZENSHIP", "RESIDENCE", "REGISTRY"],
            prefer_y=pob_label["cy"] + 10,
        )
        if text and _alpha_count(text) >= 3:
            return text
        rows = _group_rows(region)
        for row in rows:
            candidate = _join_tokens(row)
            nt = _norm_text(candidate)
            if _alpha_count(nt) >= 4 and "PLACE" not in nt and "BIRTH" not in nt:
                return _clean_text(candidate)
    region = _tokens_in_region(
        items,
        img_w * 0.20,
        img_w * 0.58,
        img_h * 0.16,
        img_h * 0.22,
        min_score=0.20,
    )
    text = _choose_best_row(
        region,
        banned_words=["CITIZENSHIP", "RESIDENCE", "RELIGION", "MARRIED", "SINGLE", "PLACE", "DEATH"],
        prefer_y=img_h * 0.19,
    )
    return text if text and _alpha_count(text) >= 3 else ""


def _strip_place_of_death_instruction(text: str) -> str:
    """Remove the field label/instruction so only the actual place remains."""
    if not text or not text.strip():
        return ""
    t = _norm_text(text)
    t = re.sub(r"^\d+\.?\s*", "", t)
    t = re.sub(r"\bPLACE\s+OF\s+DEATH\b", " ", t, flags=re.I)
    t = re.sub(
        r"NAME\s+OF\s+HOSPITAL\s*(?:/?\s*CLINIC)?\s*(?:/?\s*INSTITUTION)?\s*(?:/?\s*HOUSE)?\b[A-Z0-9 /]*",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(
        r"\bHOSPITAL\s*/?\s*CLINIC\s*/?\s*INSTITUTION\s*/?\s*HOUSE\b[A-Z0-9 /]*",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(
        r"\b(?:HOUSE\s+NO|STREET|BARANGAY|CITY|MUNICIPALITY|PROVINCE)\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"\s+", " ", t).strip(" .:-,/")
    words = [w for w in t.split() if w]
    form_tokens = {
        "HOSPITAL", "CLINIC", "INSTITUTION", "HOUSE", "STREET", "BARANGAY",
        "CITY", "MUNICIPALITY", "PROVINCE", "NAME", "PLACE", "DEATH", "NO", "ST",
    }
    if not words:
        return ""
    formish = sum(1 for w in words if w in form_tokens)
    if formish >= max(2, len(words) - 1):
        return ""
    return t if _alpha_count(t) >= 4 else ""


def _sanitize_person_name(text: str) -> str:
    """Drop form chrome that sticks to deceased/mother name rows."""
    raw = _clean_text(text or "")
    if not raw:
        return ""
    # Cut off common Form 103 instruction tails.
    raw = re.split(
        r"(?i)\b(?:fill\s+in|typewrite|print\s+in|write\s+in|use\s+black)\b",
        raw,
        maxsplit=1,
    )[0]
    parts = []
    for tok in re.sub(r"[:;|/]+", " ", raw).split():
        nt = _norm_text(tok)
        if not nt or nt in NAME_BANNED or nt in MONTHS:
            continue
        if nt in {"AT", "OF", "THE", "AND", "BY", "IN", "OR"}:
            continue
        if re.fullmatch(r"\d+", nt):
            continue
        if _alpha_count(tok) < 2:
            continue
        parts.append(tok.upper() if tok.isalpha() else tok)
    while parts and _norm_text(parts[-1]) in {"AT", "OF", "THE", "AND", "BY", "IN"}:
        parts.pop()
    name = " ".join(parts).strip()
    return name if _alpha_count(name) >= 4 else ""


def _normalize_civil_status(text: str, sex: str = "") -> str:
    """Map Widow/Widower → Widowed; ignore printed multi-option checklists."""
    if not text:
        return ""
    stripped = re.sub(r"\([^)]*\)", " ", text)
    n = _norm_text(stripped)
    hits = []
    for key, canon in STATUS_ALIASES.items():
        if re.search(rf"\b{key}\b", n):
            if canon not in hits:
                hits.append(canon)
    if len(hits) != 1:
        return ""
    status = hits[0]
    sex_n = _norm_text(sex)
    # Soft gender consistency (do not invent a different status).
    if sex_n in {"FEMALE", "F"} and status == "WIDOWED":
        return "Widowed"
    if sex_n in {"MALE", "M"} and status == "WIDOWED":
        return "Widowed"
    return status.title() if status != "WIDOWED" else "Widowed"


def _is_date_text(text: str) -> bool:
    """Check if text looks like a date (contains month name and/or year)."""
    nt = _norm_text(text)
    
    # Check for month names
    if any(month in nt for month in MONTHS):
        return True
    
    # Check for year patterns
    if re.search(r"\b(19|20)\d{2}\b", nt):
        return True
    
    # Check for day numbers (1-31) followed by month or year
    if re.search(r"\b\d{1,2}\s+(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\b", nt, re.I):
        return True
    
    return False


def _find_place_of_death_fallback(items, img_w, img_h):
    """Fallback: Search for place of death in the expected area of the form."""
    # Look in the typical place of death area (left side, middle portion)
    for it in items:
        if it["score"] < 0.20:
            continue
        # Skip if too high or too low
        if it["cy"] < img_h * 0.15 or it["cy"] > img_h * 0.25:
            continue
        # Skip if too far right
        if it["cx"] > img_w * 0.55:
            continue
            
        txt = _clean_text(it["text"])
        nt = _norm_text(txt)
        
        # Skip if too short
        if len(nt) < 4:
            continue
            
        # CRITICAL: Skip if it looks like a date (this was the bug!)
        if _is_date_text(txt):
            continue
            
        # Skip if contains year (even without month)
        if re.search(r"\b(19|20)\d{2}\b", nt):
            continue
            
        if any(kw in nt for kw in ["NAME", "DATE", "DEATH", "SEX", "AGE", "TYPE", "ORDER", "CITIZEN", "CAUSE", "CIVIL", "STATUS", "BIRTH"]):
            continue
            
        # Look for location-like words
        if any(loc in nt for loc in LOCATION_KEYWORDS):
            return txt
            
        # Also accept text that's likely a place (has multiple words and no numbers)
        if " " in txt and len(txt) > 6 and not re.search(r"\d", nt):
            return txt
    
    return ""


def _extract_place_of_death(items, img_w, img_h):
    # FIXED: Improved extraction with better fallback handling
    
    # First try: Find "Place of Death" label and extract value next to it
    place_label = _find_label(
        items,
        required_all=["PLACE", "DEATH"],
        forbidden=["EXTERNAL", "CERTIFICATION"],
        x_min=0,
        x_max=img_w * 0.35,
        y_min=img_h * 0.16,
        y_max=img_h * 0.23,
    )

    civil_label = _find_label(
        items,
        required_all=["CIVIL", "STATUS"],
        x_min=img_w * 0.65,
        x_max=img_w,
        y_min=img_h * 0.16,
        y_max=img_h * 0.23,
    )

    if place_label:
        xmin = place_label["x2"] + 5
        xmax = civil_label["x1"] - 10 if civil_label else img_w * 0.60
        ymin = place_label["y1"] - 6
        ymax = place_label["y2"] + 40  # Increased from 30 to capture more area
        
        region = _tokens_in_region(items, xmin, xmax, ymin, ymax, min_score=0.25)  # Lowered from 0.30
        
        if region:
            text = _choose_best_row(
                region,
                banned_words=["PLACE", "DEATH", "CIVIL", "STATUS", "CITIZENSHIP", "RESIDENCE", "NAME", "HOSPITAL", "INSTITUTION"],
                prefer_y=place_label["cy"] + 8,
            )
            if text:
                cleaned = _strip_place_of_death_instruction(text)
                # CRITICAL: Don't return if it looks like a date
                if cleaned and _alpha_count(cleaned) >= 2 and not _is_date_text(cleaned):
                    return cleaned

    # Second try: Fallback zone - search in typical place of death area
    region = _tokens_in_region(
        items,
        img_w * 0.15,  # Slightly wider left boundary
        img_w * 0.55,
        img_h * 0.16,
        img_h * 0.24,  # Slightly expanded vertical range
        min_score=0.20,
    )
    
    if region:
        text = _choose_best_row(
            region,
            banned_words=["CITIZENSHIP", "RESIDENCE", "RELIGION", "MARRIED", "SINGLE", "PLACE", "DEATH", "CAUSE", "NAME", "HOSPITAL"],
            prefer_y=img_h * 0.19,
        )
        if text:
            cleaned = _strip_place_of_death_instruction(text)
            # CRITICAL: Don't return if it looks like a date
            if cleaned and _alpha_count(cleaned) >= 2 and not _is_date_text(cleaned):
                return cleaned

    # Third try: Search for location-like text anywhere in the form
    fallback_result = _find_place_of_death_fallback(items, img_w, img_h)
    if fallback_result:
        return fallback_result

    # Last resort: Try to find any text that looks like a place in the death section
    for it in items:
        if it["score"] < 0.25:
            continue
        # Focus on middle-left portion where place of death typically is
        if it["cx"] < img_w * 0.15 or it["cx"] > img_w * 0.50:
            continue
        if it["cy"] < img_h * 0.15 or it["cy"] > img_h * 0.26:
            continue
            
        txt = _clean_text(it["text"])
        nt = _norm_text(txt)
        
        # Skip short text and numbers
        if len(nt) < 5:
            continue
        if re.fullmatch(r"\d+", nt):
            continue
            
        # Skip form labels
        if any(kw in nt for kw in ["PLACE", "DEATH", "CAUSE", "CIVIL", "STATUS", "NAME", "DATE", "SEX", "AGE"]):
            continue
            
        return txt

    return ""


def _extract_civil_status(items, img_w, img_h):
    region = _tokens_in_region(
        items,
        img_w * 0.72,
        img_w * 0.95,
        img_h * 0.17,
        img_h * 0.24,
        min_score=0.25,
    )
    # Prefer a single write-in token, not the printed Single/Married/Widow/... line.
    best = ""
    best_score = -1.0
    for it in region:
        status = _normalize_civil_status(it["text"])
        if status and it["score"] > best_score:
            # Skip rows that still look like the printed option list.
            if "/" in it["text"] or it["text"].count("(") + it["text"].count(")") >= 2:
                continue
            best = status
            best_score = it["score"]
    if best:
        return best
    joined = _join_tokens(region)
    return _normalize_civil_status(joined)


def _best_status_anywhere(items) -> str:
    """Fallback for civil status if the fixed region misses it."""
    best = ""
    best_score = -1.0
    for it in items:
        if "/" in (it.get("text") or ""):
            continue
        status = _normalize_civil_status(it["text"])
        if status and it["score"] > best_score:
            best = status
            best_score = it["score"]
    return best


def _extract_nationality(items, img_w, img_h):
    cit_label = None
    best_score = None

    for it in items:
        nt = _norm_text(it["text"])

        if not (img_w * 0.18 <= it["cx"] <= img_w * 0.46 and img_h * 0.18 <= it["cy"] <= img_h * 0.24):
            continue

        score = 0
        if "CIT" in nt:
            score += 3
        if "SHIP" in nt or "SHP" in nt or "ASIHP" in nt or "IZEN" in nt:
            score += 2
        if nt.startswith("9") or nt.startswith("8") or nt.startswith("10"):
            score += 0.5

        if score <= 0:
            continue

        tup = (score, it["score"], -abs(it["cx"] - img_w * 0.30))
        if cit_label is None or tup > best_score:
            cit_label = it
            best_score = tup

    if cit_label:
        region = _tokens_in_region(
            items,
            cit_label["x1"] - 20,
            cit_label["x2"] + 120,
            cit_label["y2"] + 1,
            cit_label["y2"] + 22,
            min_score=0.30,
        )

        rows = _group_rows(region)
        candidates = []

        for row in rows:
            text = _join_tokens(row)
            nt = _norm_text(text)

            if not nt:
                continue

            if any(
                b in nt
                for b in [
                    "FATHER",
                    "MOTHER",
                    "OCCUPATION",
                    "MAIDEN",
                    "PLACE",
                    "DEATH",
                    "RESIDENCE",
                    "RELIGION",
                    "HOUSE",
                    "BARANGAY",
                    "CITY",
                    "MUNICIPALITY",
                    "CITIZENSHIP",
                ]
            ):
                continue

            if re.search(r"\d", nt):
                continue

            words = nt.split()
            if not (1 <= len(words) <= 3):
                continue

            letters = _alpha_count(text)
            if letters < 4:
                continue

            row_y = sum(x["cy"] for x in row) / len(row)
            avg_score = sum(x["score"] for x in row) / len(row)

            candidates.append(
                (
                    -abs(row_y - (cit_label["y2"] + 8)),
                    avg_score,
                    -len(nt),
                    nt,
                )
            )

        if candidates:
            candidates.sort(reverse=True)
            best = candidates[0][3]

            fixes = {
                "FLPINO": "FILIPINO",
                "FLIPINO": "FILIPINO",
            }
            return fixes.get(best, best)

    fallback = _tokens_in_region(
        items,
        img_w * 0.23,
        img_w * 0.40,
        img_h * 0.205,
        img_h * 0.238,
        min_score=0.30,
    )

    candidates = []
    for it in fallback:
        nt = _norm_text(it["text"])

        if any(b in nt for b in ["FATHER", "MOTHER", "RELIGION", "RESIDENCE", "CITIZENSHIP"]):
            continue
        if re.search(r"\d", nt):
            continue

        words = nt.split()
        if 1 <= len(words) <= 3 and _alpha_count(nt) >= 4:
            candidates.append((it["score"], -len(nt), nt))

    if candidates:
        candidates.sort(reverse=True)
        best = candidates[0][2]
        fixes = {
            "FLPINO": "FILIPINO",
            "FLIPINO": "FILIPINO",
        }
        return fixes.get(best, best)

    return ""


def _extract_cause_of_death(items, img_w, img_h):
    """
    Read medical causes from Form 103 (19a Immediate / 19b Antecedent / 19c Underlying).
    Do not harvest mother-section lines such as 10b/10c (children still living).
    """
    cause_ban = [
        "IMMEDIATE", "CAUSE", "ANTECEDENT", "UNDERLYING", "INTERVAL",
        "ONSET", "DEATH", "EXTERNAL", "MATERNAL", "AUTOPSY", "MEDICAL",
        "CERTIFICATE", "PREGNANT", "LABOUR", "LABOR", "DELIVERY",
        "ACCOMPLISH", "FOR AGES", "CHILDREN", "OCCUPATION", "RESIDENCE",
        "ATTENDANT", "HILOT", "PHYSICIAN", "MIDWIFE",
    ]

    def _value_near_label(label, extra_bottom=42, x2_ratio=0.82):
        if not label:
            return ""
        left = max(label["x2"] + 8, img_w * 0.18)
        if left >= img_w * (x2_ratio - 0.05):
            left = img_w * 0.20
        region = _tokens_in_region(
            items,
            left,
            img_w * x2_ratio,
            label["y1"] - 8,
            label["y2"] + extra_bottom,
            min_score=0.22,
        )
        text = _choose_best_row(
            region,
            banned_words=cause_ban,
            prefer_y=label["cy"],
        )
        text = re.sub(r"^\b[ABC]\b\s*", "", text, flags=re.I).strip(" .:-")
        return text if is_plausible_cause_of_death(text) else ""

    band_top = img_h * 0.22
    band_bot = img_h * 0.72
    left = img_w * 0.02
    mid = img_w * 0.62

    immediate_label = _find_label(
        items,
        required_any=["IMMEDIATE"],
        x_min=left,
        x_max=mid,
        y_min=band_top,
        y_max=band_bot,
        min_score=0.18,
    )
    antecedent_label = _find_label(
        items,
        required_any=["ANTECEDENT"],
        x_min=left,
        x_max=mid,
        y_min=band_top,
        y_max=band_bot,
        min_score=0.18,
    )
    underlying_label = _find_label(
        items,
        required_any=["UNDERLYING"],
        x_min=left,
        x_max=mid,
        y_min=band_top,
        y_max=band_bot,
        min_score=0.18,
    )
    cause_label = _find_label(
        items,
        required_all=["CAUSE", "DEATH"],
        forbidden=["EXTERNAL"],
        x_min=left,
        x_max=mid,
        y_min=band_top,
        y_max=band_bot,
        min_score=0.18,
    )

    parts = []
    for label in (immediate_label, antecedent_label, underlying_label):
        val = _value_near_label(label)
        if val and val not in parts:
            parts.append(val)
    if parts:
        return "; ".join(parts)

    if cause_label:
        region = _tokens_in_region(
            items,
            img_w * 0.16,
            img_w * 0.82,
            cause_label["y1"] - 4,
            cause_label["y2"] + 48,
            min_score=0.22,
        )
        text = _choose_best_row(
            region,
            banned_words=cause_ban + ["PLACE OF OCCURENCE"],
            prefer_y=cause_label["cy"] + 10,
        )
        if is_plausible_cause_of_death(text):
            return text

    return ""


def _extract_date_of_registration(items, img_w, img_h) -> str:
    """
    Form 103 puts the registrar date at items 28/29 near the bottom.
    Some older scans also write it near the registry number at the top.
    """
    bottom = _tokens_in_region(
        items,
        img_w * 0.08,
        img_w * 0.96,
        img_h * 0.70,
        img_h * 0.94,
        min_score=0.25,
    )
    dates = _extract_date_candidates(bottom)
    if dates:
        dates.sort(key=lambda d: (d["x1"], d["y1"]))
        right = [d for d in dates if d.get("x1", 0) >= img_w * 0.42]
        return (right[-1] if right else dates[-1])["text"]

    top = _tokens_in_region(
        items,
        img_w * 0.45,
        img_w * 0.98,
        img_h * 0.02,
        img_h * 0.22,
        min_score=0.25,
    )
    dates = _extract_date_candidates(top)
    if not dates:
        return ""
    dates.sort(key=lambda d: d["x1"])
    return dates[-1]["text"]


def _extract_age_anywhere(items, img_w, img_h) -> str:
    """Fallback: any completed-age phrase in the upper right of Form 103."""
    return _extract_age(items, img_w, img_h)


def _extract_nationality_anywhere(items, img_w, img_h) -> str:
    """
    Fallback nationality detection around the citizenship band.
    """
    fallback = _tokens_in_region(
        items,
        img_w * 0.20,
        img_w * 0.52,
        img_h * 0.17,
        img_h * 0.26,
        min_score=0.25,
    )
    candidates = []
    for it in fallback:
        nt = _norm_text(it["text"])
        if any(
            b in nt
            for b in [
                "FATHER",
                "MOTHER",
                "RELIGION",
                "RESIDENCE",
                "CITIZENSHIP",
                "PLACE",
                "DEATH",
                "REGISTRY",
                "STATUS",
            ]
        ):
            continue
        if re.search(r"\d", nt):
            continue
        words = nt.split()
        if 1 <= len(words) <= 3 and _alpha_count(nt) >= 4:
            candidates.append((it["score"], -len(nt), nt))
    if not candidates:
        return ""
    candidates.sort(reverse=True)
    best = candidates[0][2]
    fixes = {
        "FLPINO": "FILIPINO",
        "FLIPINO": "FILIPINO",
    }
    return fixes.get(best, best)


def _field_confidence(field_name: str, value: str, source: str = "layout") -> str:
    v = (value or "").strip()
    if not v:
        return "LOW"
    n = _norm_text(v)

    if source == "template":
        return "MEDIUM"

    if field_name == "Sex":
        return "HIGH" if n in {"MALE", "FEMALE", "M", "F"} else "MEDIUM"
    if field_name in {"Date of Death", "Date of Registration"}:
        return "HIGH" if _is_full_date(v) else "MEDIUM"
    if field_name == "Age":
        return "HIGH" if re.fullmatch(r"\d{1,3}", n) else "MEDIUM"
    if field_name == "Registry Number":
        compact = n.replace(" ", "")
        if re.fullmatch(r"[A-Z0-9]{2,10}[-/][A-Z0-9]{2,10}", compact):
            return "HIGH"
        return "MEDIUM"
    if field_name == "Cause of Death":
        return "HIGH" if is_plausible_cause_of_death(v) and _alpha_count(v) >= 6 else (
            "MEDIUM" if is_plausible_cause_of_death(v) else "LOW"
        )
    if field_name in {"Name of Deceased", "Name of Mother", "Place of Death", "Civil Status", "Nationality"}:
        return "HIGH" if _alpha_count(v) >= 4 else "MEDIUM"
    return "MEDIUM"


def extract_death_data(img_path: str) -> Dict[str, Any]:
    """
    Run OCR on a death certificate image and return structured data.
    Returns only the fields present on the death certificate form:
    Registry Number, Date of Registration, Name of Deceased, Sex, Age,
    Civil Status, Nationality, Name of Mother, Date of Death, Place of Death, Cause of Death.
    """
    img = imread_bgr(img_path)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {img_path}")

    img_h, img_w = img.shape[:2]
    result = run_ocr_on_image(img)
    box_map: Dict[str, str] = {}
    try:
        box_map = extract_field_boxes(img, "death", pages=result)
    except Exception:
        box_map = {}

    # Template-first extraction (folder-based JSON templates).
    # Merge mode: fill blanks using templates, then keep existing extractor as primary.
    tpl_map: Dict[str, str] = {}
    try:
        ocr_text = ocr_pages_to_text(result)
        tpl = extract_from_ocr_text(ocr_text, doc_type="death", best_effort=True)
        fields = (tpl.get("fields") or {}) if isinstance(tpl, dict) else {}
        if DEBUG_TEMPLATE_MATCH:
            found = sum(1 for v in fields.values() if v)
            print(
                f"[OCR DEATH TEMPLATE] path={tpl.get('template_path')} "
                f"score={tpl.get('match_score')} fields={found}/{len(fields)}"
            )
        found = sum(1 for v in fields.values() if v)
        if tpl.get("template_path") and found >= 1:
            def _v(k: str) -> str:
                val = fields.get(k)
                return str(val).strip() if val else ""

            tpl_map = {
                "Registry Number": _v("registry_number"),
                "Name of Deceased": _v("name_of_deceased"),
                "Sex": _v("sex"),
                "Name of Mother": _v("name_of_mother"),
                "Date of Death": _v("date_of_death"),
                "Place of Death": _v("place_of_death"),
                "Cause of Death": _v("cause_of_death"),
            }
    except Exception:
        # Fall back to the existing layout-based extractor below.
        pass

    items = _build_items(result, img_w, img_h)

    registry_number = _extract_registry_number(items, img_w, img_h)
    date_of_registration = _extract_date_of_registration(items, img_w, img_h)
    name_of_deceased = _sanitize_person_name(_extract_name(items, img_w, img_h))
    sex = _extract_sex(items, img_w, img_h)
    date_of_death, _, age = _extract_dates_and_age(items, img_w, img_h)
    extracted_age = _extract_age(items, img_w, img_h)
    if extracted_age:
        age = extracted_age
    place_of_death = _strip_place_of_death_instruction(
        _extract_place_of_death(items, img_w, img_h)
    )
    civil_status = _normalize_civil_status(
        _extract_civil_status(items, img_w, img_h), sex
    )
    nationality = _extract_nationality(items, img_w, img_h)
    name_of_mother = _sanitize_person_name(_extract_name_of_mother(items, img_w, img_h))
    cause_of_death = _extract_cause_of_death(items, img_w, img_h)

    # Global fallbacks for fields that often become blank on noisy scans.
    if not age:
        age = _extract_age_anywhere(items, img_w, img_h)
    if not civil_status:
        civil_status = _normalize_civil_status(_best_status_anywhere(items), sex)
    if not nationality:
        nationality = _extract_nationality_anywhere(items, img_w, img_h)
    if not date_of_death:
        date_of_death = _extract_date_in_region(
            items,
            img_w * 0.04,
            img_w * 0.33,
            img_h * 0.145,
            img_h * 0.210,
        )
    if not date_of_registration:
        # Final fallback: pick a top-right date if present.
        top_right = _tokens_in_region(items, img_w * 0.50, img_w * 0.98, 0, img_h * 0.25, min_score=0.25)
        top_dates = _extract_date_candidates(top_right)
        if top_dates:
            top_dates.sort(key=lambda d: d["x1"])
            date_of_registration = top_dates[-1]["text"]

    out = {
        "Registry Number": registry_number,
        "Date of Registration": date_of_registration,
        "Name of Deceased": name_of_deceased,
        "Sex": sex,
        "Age": age,
        "Civil Status": civil_status,
        "Nationality": nationality,
        "Name of Mother": name_of_mother,
        "Date of Death": date_of_death,
        "Place of Death": place_of_death,
        "Cause of Death": cause_of_death,
    }
    before_template = {k: (out.get(k) or "") for k in out.keys()}
    # Fill missing values with template results (if any).
    for k, v in (tpl_map or {}).items():
        if v and not (out.get(k) or "").strip():
            if k == "Cause of Death" and not is_plausible_cause_of_death(v):
                continue
            if k == "Date of Death" and not (_is_full_date(v) or re.search(r"\d{4}", v or "")):
                continue
            out[k] = v
    for k, v in (box_map or {}).items():
        if not v:
            continue
        if k == "Cause of Death" and not is_plausible_cause_of_death(v):
            continue
        if k in {"Date of Death", "Date of Registration"}:
            if not (_is_full_date(v) or re.search(r"\d{4}", v or "")):
                continue
            existing = (out.get(k) or "").strip()
            if existing and _is_full_date(existing) and not _is_full_date(v):
                continue
        if k == "Age":
            parsed_age = _parse_completed_age(v)
            if not parsed_age:
                continue
            v = parsed_age
        if k in {"Name of Deceased", "Name of Mother"}:
            v = _sanitize_person_name(v)
            if not v:
                continue
        if k == "Place of Death":
            v = _strip_place_of_death_instruction(v)
            if not v:
                continue
        if k == "Civil Status":
            v = _normalize_civil_status(v, out.get("Sex") or sex)
            if not v:
                continue
        out[k] = v
    # Final hygiene after box/template merges.
    if out.get("Name of Deceased"):
        out["Name of Deceased"] = _sanitize_person_name(out["Name of Deceased"])
    if out.get("Name of Mother"):
        out["Name of Mother"] = _sanitize_person_name(out["Name of Mother"])
    if out.get("Place of Death"):
        out["Place of Death"] = _strip_place_of_death_instruction(out["Place of Death"])
    if out.get("Civil Status"):
        out["Civil Status"] = _normalize_civil_status(
            out["Civil Status"], out.get("Sex") or ""
        )
    if out.get("Age"):
        out["Age"] = _parse_completed_age(out["Age"]) or (
            out["Age"] if re.fullmatch(r"\d{1,3}", _norm_text(out["Age"] or "")) and _norm_text(out["Age"]) != "1" else ""
        )
    if out.get("Cause of Death") and not is_plausible_cause_of_death(out["Cause of Death"]):
        out["Cause of Death"] = ""
    ordered_fields = [
        "Registry Number",
        "Date of Registration",
        "Name of Deceased",
        "Sex",
        "Age",
        "Civil Status",
        "Nationality",
        "Name of Mother",
        "Date of Death",
        "Place of Death",
        "Cause of Death",
    ]
    confidence_map: Dict[str, str] = {}
    source_map: Dict[str, str] = {}
    for key in ordered_fields:
        val = (out.get(key) or "").strip()
        if (box_map or {}).get(key) and val:
            source = "box"
        else:
            is_template_fill = (
                not (before_template.get(key) or "").strip()
                and bool(val)
                and bool((tpl_map or {}).get(key))
            )
            source = "template" if is_template_fill else "layout"
        source_map[key] = source
        confidence_map[key] = _field_confidence(key, val, source=source)
    out["__confidence__"] = confidence_map
    out["__source__"] = source_map
    return out
