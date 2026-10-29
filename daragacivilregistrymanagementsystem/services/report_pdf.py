"""Downloadable PDF of the official Civil Registry register forms."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Any, Dict, Iterable, List, Sequence

from fpdf.fonts import FontFace


def _pdf_text(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    text = re.sub(r" +", " ", text).strip()
    return text.encode("latin-1", "replace").decode("latin-1")


def _cell(row, value: Any = "", **kwargs):
    return row.cell(_pdf_text(value), **kwargs)


def _mm_widths(pdf, fracs: Sequence[float]) -> List[float]:
    total = sum(fracs) or 1.0
    usable = float(pdf.epw)
    return [usable * (float(f) / total) for f in fracs]


class RegisterPDF:
    def __init__(self, doc_type: str):
        try:
            from fpdf import FPDF
        except ImportError as exc:
            raise RuntimeError("PDF support requires fpdf2. Install with: pip install fpdf2") from exc

        self.doc_type = (doc_type or "birth").strip().lower()
        self.pdf = FPDF(orientation="L", unit="mm", format="Legal")
        self.pdf.set_margins(8, 22, 8)
        self.pdf.set_auto_page_break(auto=True, margin=14)
        self.pdf.alias_nb_pages()
        self._bind_header_footer()

    def _bind_header_footer(self) -> None:
        pdf = self.pdf
        doc_type = self.doc_type

        def header() -> None:
            pdf.set_y(8)
            pdf.set_draw_color(0, 0, 0)
            pdf.set_text_color(0, 0, 0)
            left, title, sub, places = _masthead_copy(doc_type)
            col = pdf.epw / 3.0
            pdf.set_font("Times", "I", 7)
            pdf.set_xy(pdf.l_margin, 8)
            pdf.multi_cell(col, 3.2, _pdf_text(left), align="L")
            pdf.set_xy(pdf.l_margin + col, 8)
            pdf.set_font("Times", "B", 12)
            pdf.cell(col, 5, _pdf_text(title), align="C")
            if sub:
                pdf.set_xy(pdf.l_margin + col, 13)
                pdf.set_font("Times", "I", 8)
                pdf.cell(col, 4, _pdf_text(sub), align="C")
            if places:
                _draw_place_lines(pdf, pdf.l_margin + col * 2, 8, col, places)
            pdf.set_y(20)

        def footer() -> None:
            pdf.set_y(-10)
            pdf.set_font("Times", "I", 7)
            pdf.set_text_color(60, 60, 60)
            pdf.cell(0, 6, f"Page {pdf.page_no()} / {{nb}}", align="C")
            pdf.set_text_color(0, 0, 0)

        pdf.header = header  # type: ignore[method-assign]
        pdf.footer = footer  # type: ignore[method-assign]

    def build(self, rows: Iterable[Dict[str, Any]]) -> bytes:
        data = list(rows or [])
        self.pdf.add_page()
        if self.doc_type == "death":
            _draw_death_table(self.pdf, data)
            _draw_closing(
                self.pdf,
                [
                    "Age (Cols. 5-8) - Enter under appropriate column the age of the deceased.",
                    'Fetal Death (Col. 9) - Includes also babies with intra-uterine life of less than 7 months and died within 24 hours after birth. In case of fetal death enter "Yes", otherwise write "N.A."',
                    "Remarks (Col. 23) - In case of Fetal death, enter the maiden name of the mother.",
                ],
                "I HEREBY CERTIFY that the entries herein are true and correct as per entries contained in the original copy of the certificate presented for registration in this office.",
                "Local Civil Registrar",
            )
        elif self.doc_type == "marriage":
            _draw_marriage_table(self.pdf, data)
            _draw_closing(
                self.pdf,
                [
                    "This form should be filled and sent by the Local Civil Registrar to the Civil Registrar-General during the first ten days of each month.",
                    "Write plainly, with unfading ink. This is a permanent record.",
                ],
                "I hereby certify that this is a true copy of the Civil Register of this municipality containing entries of all the marriages that occurred therein during the month of ____________________, 20____.",
                "Local Civil Registrar / Registrador Civil Local",
            )
        else:
            _draw_birth_table(self.pdf, data)
            _draw_closing(
                self.pdf,
                [
                    "1 Type (Col. 10) - Enter whether single birth, twin, three or more.",
                    "2 Order (Col. 11) - If multiple birth in Col. 10, was child first, second, or higher order?",
                ],
                "I HEREBY CERTIFY that the entries herein are true and correct as per entries contained in the original copy of the certificate presented for registration in this office.",
                "Local Civil Registrar",
            )
        out = BytesIO()
        self.pdf.output(out)
        return out.getvalue()


def _masthead_copy(doc_type: str) -> tuple[str, str, str, list[tuple[str, str]]]:
    if doc_type == "death":
        return (
            "(Municipal Form No. 71, Revised 1984)",
            "REGISTER OF DEATH / FETAL DEATH",
            "",
            [("Municipality of", "Daraga")],
        )
    if doc_type == "marriage":
        return (
            "MUNICIPAL Form No. 25 - (Revised, 1958)\nFormulario Municipal No. 25 - (Revisado, 1958)",
            "REGISTER OF MARRIAGES",
            "Registro de Casamientos",
            [("Province of", "Albay"), ("Municipality of", "Daraga")],
        )
    return (
        "(Municipal Form No. 26-1, Revised 1984)",
        "REGISTER OF LIVE BIRTHS",
        "",
        [],
    )


def _draw_place_lines(pdf, x: float, y: float, width: float, places: list[tuple[str, str]]) -> None:
    """Draw 'Municipality of ______' with the place name centered on the underline."""
    line_w = min(36.0, max(28.0, width * 0.46))
    for label, value in places:
        pdf.set_xy(x, y)
        pdf.set_font("Times", "", 8)
        label_text = _pdf_text(label)
        label_w = pdf.get_string_width(label_text + " ")
        start = x + width - line_w - label_w
        pdf.set_xy(start, y)
        pdf.cell(label_w, 3.6, label_text, align="L")
        pdf.cell(line_w, 3.6, _pdf_text(value), align="C")
        underline_y = y + 3.3
        pdf.line(start + label_w, underline_y, start + label_w + line_w, underline_y)
        y += 4.2


def _table_kwargs(pdf, widths: Sequence[float], heading_size: float = 5.5):
    return {
        "col_widths": list(widths),
        "width": pdf.epw,
        "num_heading_rows": 2,
        "first_row_as_headings": True,
        "repeat_headings": 1,
        "text_align": "CENTER",
        "v_align": "MIDDLE",
        "line_height": 3.1,
        "gutter_width": 0,
        "gutter_height": 0,
        "padding": 0.45,
        "wrapmode": "WORD",
        "borders_layout": "ALL",
        "min_row_height": 5.2,
        "headings_style": FontFace(
            family="Times",
            emphasis="BOLD",
            size_pt=heading_size,
            color=(0, 0, 0),
            fill_color=(255, 255, 255),
        ),
    }


def _draw_death_table(pdf, rows: Sequence[Dict[str, Any]]) -> None:
    pdf.set_font("Times", "", 6)
    widths = _mm_widths(
        pdf,
        [5.4, 6.2, 9.5, 3.2, 2.4, 2.4, 2.4, 2.4, 4.0, 5.2, 5.2, 6.8, 6.2, 2.4, 2.4, 3.2, 3.4, 7.4, 6.2, 6.2, 6.0, 5.4, 5.6],
    )
    with pdf.table(**_table_kwargs(pdf, widths, 5.4)) as table:
        r = table.row()
        _cell(r, "(1)\nLCR NUMBER", rowspan=2)
        _cell(r, "(2) DATE OF REGISTRATION\n(Day/Mo/Yr)", rowspan=2)
        _cell(r, "(3) NAME OF THE DECEASED\n(First, Middle, Last)", rowspan=2)
        _cell(r, "(4)\nSEX", rowspan=2)
        _cell(r, "AGE", colspan=4)
        _cell(r, "(9) FETAL DEATH", rowspan=2)
        _cell(r, "(10) CIVIL STATUS", rowspan=2)
        _cell(r, "(11) NATIONALITY", rowspan=2)
        _cell(r, "(12) USUAL RESIDENCE\n(Street/Barangay)", rowspan=2)
        _cell(r, "(13) USUAL OCCUPATION\n(if 15 years old and over)", rowspan=2)
        _cell(r, "DATE AND TIME OF DEATH", colspan=4)
        _cell(r, "(18) PLACE OF DEATH\n(Hospital/Clinic or street/barangay)", rowspan=2)
        _cell(r, "CAUSES OF DEATH", colspan=2)
        _cell(r, "CERTIFYING OFFICER", colspan=2)
        _cell(r, "(23) REMARKS", rowspan=2)
        r = table.row()
        _cell(r, "(5) Years")
        _cell(r, "(6) Mos.")
        _cell(r, "(7) Days")
        _cell(r, "(8) Hrs.")
        _cell(r, "(14) Day")
        _cell(r, "(15) Mo.")
        _cell(r, "(16) Year")
        _cell(r, "(17) Time")
        _cell(r, "(19) Immediate")
        _cell(r, "(20) Underlying")
        _cell(r, "(21) Name")
        _cell(r, "(22) Title/Designation")
        if not rows:
            r = table.row()
            _cell(r, "No records in this register view.", colspan=23)
            return
        for row in rows:
            r = table.row()
            _cell(r, row.get("lcr_number"))
            _cell(r, row.get("date_registration"))
            _cell(r, row.get("name"), align="L")
            _cell(r, row.get("sex"))
            _cell(r, row.get("age_years"))
            _cell(r, row.get("age_months"))
            _cell(r, row.get("age_days"))
            _cell(r, row.get("age_hours"))
            _cell(r, row.get("fetal_death"))
            _cell(r, row.get("civil_status"))
            _cell(r, row.get("nationality"))
            _cell(r, row.get("residence"), align="L")
            _cell(r, row.get("occupation"), align="L")
            _cell(r, row.get("death_day"))
            _cell(r, row.get("death_month"))
            _cell(r, row.get("death_year"))
            _cell(r, row.get("death_time"))
            _cell(r, row.get("place_of_death"), align="L")
            _cell(r, row.get("cause_immediate"), align="L")
            _cell(r, row.get("cause_underlying"), align="L")
            _cell(r, row.get("officer_name"), align="L")
            _cell(r, row.get("officer_title"))
            _cell(r, row.get("remarks"), align="L")


def _draw_marriage_table(pdf, rows: Sequence[Dict[str, Any]]) -> None:
    pdf.set_font("Times", "", 5.5)
    widths = _mm_widths(
        pdf,
        [4.6, 5.2, 9.2, 2.2, 2.2, 4.4, 5.4, 5.4, 5.2, 3.8, 5.2, 3.8, 5.8, 4.8, 5.2, 4.4, 4.4, 4.0, 4.6, 4.4, 3.6, 4.0, 4.6, 4.4],
    )
    with pdf.table(**_table_kwargs(pdf, widths, 5.0)) as table:
        r = table.row()
        _cell(r, "Date of Registration\nFecha del Registro", rowspan=2)
        _cell(r, "Register Number\nNumero de Registro", rowspan=2)
        _cell(r, "Names of the Contracting Parties\nNombres de los Contrayentes", rowspan=2)
        _cell(r, "AGE / EDAD", colspan=2)
        _cell(r, "Nationality\nNacionalidad", rowspan=2)
        _cell(r, "Single, Widowed or Divorced\nSoltero, Viudo o Divorciado", rowspan=2)
        _cell(r, "Residence\nResidencia", rowspan=2)
        _cell(r, "FATHER / PADRE", colspan=2)
        _cell(r, "MOTHER / MADRE", colspan=2)
        _cell(r, "Place of Marriage\nLugar del Casamiento", rowspan=2)
        _cell(r, "Date of Marriage\nFecha del Casamiento", rowspan=2)
        _cell(r, "WITNESSES / TESTIGOS", colspan=2)
        _cell(r, "PERSON GIVING THE CONSENT", colspan=3)
        _cell(r, "SOLEMNIZED BY / CELEBRADO POR", colspan=3)
        _cell(r, "Date of Receipt of Marriage Certificate", rowspan=2)
        _cell(r, "REMARKS / OBSERVACIONES", rowspan=2)
        r = table.row()
        _cell(r, "Yr\nAnos")
        _cell(r, "Mo\nMes")
        _cell(r, "Name / Nombre")
        _cell(r, "Nationality")
        _cell(r, "Name / Nombre")
        _cell(r, "Nationality")
        _cell(r, "Name / Nombre")
        _cell(r, "Residence")
        _cell(r, "Name / Nombre")
        _cell(r, "Residence")
        _cell(r, "Relation to the Minor")
        _cell(r, "Name / Nombre")
        _cell(r, "Title / Titulo")
        _cell(r, "Address / Direccion")
        if not rows:
            r = table.row()
            _cell(r, "No records in this register view.", colspan=24)
            return
        for row in rows:
            r = table.row()
            _cell(r, row.get("date_registration"), rowspan=2)
            _cell(r, row.get("register_number"), rowspan=2)
            _cell(r, f"Husband / Marido {_pdf_text(row.get('husband_name'))}", align="L")
            _cell(r, row.get("husband_age_years"))
            _cell(r, row.get("husband_age_months"))
            _cell(r, row.get("husband_nationality"))
            _cell(r, row.get("husband_status"))
            _cell(r, row.get("husband_residence"), align="L")
            _cell(r, row.get("husband_father"), align="L")
            _cell(r, row.get("husband_father_nat"))
            _cell(r, row.get("husband_mother"), align="L")
            _cell(r, row.get("husband_mother_nat"))
            _cell(r, row.get("place_of_marriage"), rowspan=2, align="L")
            _cell(r, row.get("date_of_marriage"), rowspan=2)
            _cell(r, row.get("witness1_name"), align="L")
            _cell(r, row.get("witness1_res"), align="L")
            _cell(r, row.get("consent_name"), rowspan=2, align="L")
            _cell(r, row.get("consent_res"), rowspan=2, align="L")
            _cell(r, row.get("consent_rel"), rowspan=2)
            _cell(r, row.get("solemnizer_name"), rowspan=2, align="L")
            _cell(r, row.get("solemnizer_title"), rowspan=2)
            _cell(r, row.get("solemnizer_address"), rowspan=2, align="L")
            _cell(r, row.get("date_receipt"), rowspan=2)
            _cell(r, row.get("remarks"), rowspan=2, align="L")
            r = table.row()
            _cell(r, f"Wife / Esposa {_pdf_text(row.get('wife_name'))}", align="L")
            _cell(r, row.get("wife_age_years"))
            _cell(r, row.get("wife_age_months"))
            _cell(r, row.get("wife_nationality"))
            _cell(r, row.get("wife_status"))
            _cell(r, row.get("wife_residence"), align="L")
            _cell(r, row.get("wife_father"), align="L")
            _cell(r, row.get("wife_father_nat"))
            _cell(r, row.get("wife_mother"), align="L")
            _cell(r, row.get("wife_mother_nat"))
            _cell(r, row.get("witness2_name"), align="L")
            _cell(r, row.get("witness2_res"), align="L")


def _draw_birth_table(pdf, rows: Sequence[Dict[str, Any]]) -> None:
    pdf.set_font("Times", "", 6)
    widths = _mm_widths(
        pdf,
        [7.0, 8.0, 6.2, 6.2, 6.2, 4.2, 3.2, 3.2, 3.6, 4.2, 14.0, 4.0, 4.0, 6.2, 6.2, 3.0, 5.5, 5.5, 10.0],
    )
    with pdf.table(**_table_kwargs(pdf, widths, 5.4)) as table:
        r = table.row()
        _cell(r, "(1)\nLCR NUMBER", rowspan=2)
        _cell(r, "(2) DATE OF REGISTRATION\n(Day/Mo./Year)", rowspan=2)
        _cell(r, "(3) NAME OF CHILD", colspan=3)
        _cell(r, "(4)\nSEX", rowspan=2)
        _cell(r, "DATE AND TIME OF BIRTH", colspan=4)
        _cell(r, "(9) PLACE OF BIRTH\nHospital/Institution or street/barangay", rowspan=2)
        _cell(r, "TYPE OF BIRTH", colspan=2)
        _cell(r, "(12) MOTHER (Maiden Name)", colspan=2)
        _cell(r, "(13)\nAGE", rowspan=2)
        _cell(r, "(14)\nNationality", rowspan=2)
        _cell(r, "(15)\nReligion", rowspan=2)
        _cell(r, "(16) Name (First, Last)", rowspan=2)
        r = table.row()
        _cell(r, "First")
        _cell(r, "Middle")
        _cell(r, "Last")
        _cell(r, "(5) Day")
        _cell(r, "(6) Mo.")
        _cell(r, "(7) Year")
        _cell(r, "(8) Time")
        _cell(r, "(10) Type")
        _cell(r, "(11) Order")
        _cell(r, "First")
        _cell(r, "Last")
        if not rows:
            r = table.row()
            _cell(r, "No records in this register view.", colspan=19)
            return
        for row in rows:
            r = table.row()
            _cell(r, row.get("lcr_number"))
            _cell(r, row.get("date_registration"))
            _cell(r, row.get("child_first"))
            _cell(r, row.get("child_middle"))
            _cell(r, row.get("child_last"))
            _cell(r, row.get("sex"))
            _cell(r, row.get("birth_day"))
            _cell(r, row.get("birth_month"))
            _cell(r, row.get("birth_year"))
            _cell(r, row.get("birth_time"))
            _cell(r, row.get("place_of_birth"), align="L")
            _cell(r, row.get("type_of_birth"))
            _cell(r, row.get("birth_order"))
            _cell(r, row.get("mother_first"))
            _cell(r, row.get("mother_last"))
            _cell(r, row.get("mother_age"))
            _cell(r, row.get("mother_nationality"))
            _cell(r, row.get("mother_religion"))
            _cell(r, row.get("father_name"), align="L")


def _draw_closing(pdf, notes: Sequence[str], certify: str, sign_label: str) -> None:
    needed = 8 + 4.2 * len(notes) + 22
    if pdf.get_y() > pdf.h - pdf.b_margin - needed:
        pdf.add_page()
    pdf.ln(3)
    pdf.set_font("Times", "", 7)
    x = pdf.l_margin
    note_w = pdf.epw * 0.70
    for note in notes:
        pdf.set_x(x)
        pdf.multi_cell(note_w, 3.4, _pdf_text(note), align="L")
    pdf.ln(3)
    y = pdf.get_y()
    pdf.set_xy(x, y)
    pdf.multi_cell(note_w, 3.6, _pdf_text(certify), align="L")
    sign_w = pdf.epw * 0.26
    pdf.set_xy(pdf.l_margin + pdf.epw - sign_w, y + 8)
    pdf.cell(sign_w, 5, "______________________________", align="C")
    pdf.set_xy(pdf.l_margin + pdf.epw - sign_w, y + 13)
    pdf.set_font("Times", "B", 7)
    pdf.cell(sign_w, 4, _pdf_text(sign_label), align="C")


def build_register_pdf(doc_type: str, rows: Any = (), extra: Any = None) -> bytes:
    """Build a Legal-landscape PDF of the official Birth, Death, or Marriage register.

    New call: build_register_pdf(doc_type, row_dicts)
    Older call: build_register_pdf(title, headers, row_lists)
    """
    if extra is not None:
        title = str(doc_type or "")
        lowered = title.lower()
        if "death" in lowered:
            kind = "death"
        elif "marriage" in lowered:
            kind = "marriage"
        else:
            kind = "birth"
        mapped = []
        for raw in extra or []:
            cells = [str(c or "") for c in raw]
            if kind == "death":
                mapped.append(
                    {
                        "lcr_number": _at(cells, 0),
                        "date_registration": _at(cells, 1),
                        "name": _at(cells, 2),
                        "sex": _at(cells, 3),
                        "age_years": _at(cells, 4),
                        "age_months": _at(cells, 5),
                        "age_days": _at(cells, 6),
                        "age_hours": _at(cells, 7),
                    }
                )
            elif kind == "marriage":
                mapped.append(
                    {
                        "register_number": _at(cells, 0),
                        "date_registration": _at(cells, 1),
                        "husband_name": _at(cells, 2),
                        "wife_name": _at(cells, 3),
                        "date_of_marriage": _at(cells, 4),
                        "place_of_marriage": _at(cells, 5),
                    }
                )
            else:
                mapped.append(
                    {
                        "lcr_number": _at(cells, 0),
                        "date_registration": _at(cells, 1),
                        "child_first": _at(cells, 2),
                        "child_middle": _at(cells, 3),
                        "child_last": _at(cells, 4),
                        "sex": _at(cells, 5),
                    }
                )
        return RegisterPDF(kind).build(mapped)

    first = next(iter(rows), None) if rows else None
    if isinstance(first, (list, tuple)) and not isinstance(first, (str, bytes)):
        # Row lists without a title: treat as death/marriage/birth by width.
        width = len(first)
        kind = "marriage" if width <= 6 else ("death" if width <= 8 else "birth")
        return build_register_pdf(
            "Register of Marriages" if kind == "marriage" else (
                "Register of Deaths" if kind == "death" else "Register of Live Births"
            ),
            (),
            rows,
        )
    return RegisterPDF(str(doc_type or "birth")).build(rows or [])


def _at(cells: Sequence[str], index: int) -> str:
    return cells[index] if index < len(cells) else ""
