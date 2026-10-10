"""Civil Registry certification form (CR Form 1A / 2A / 3A) print metadata and helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Any


FORM_META: dict[str, dict[str, str]] = {
    "birth": {
        "form_no": "1 A",
        "availability": "(Birth-Available)",
        "register": "Births",
        "office": "OFFICE OF THE LOCAL CIVIL REGISTRAR",
        "facts_of": "birth",
        "intro_style": "page_of_book",
    },
    "death": {
        "form_no": "2A",
        "availability": "(Death Available)",
        "register": "Deaths",
        "office": "OFFICE OF THE MUNICIPAL CIVIL REGISTRAR",
        "facts_of": "death",
        "intro_style": "page_of_book",
    },
    "marriage": {
        "form_no": "3A",
        "availability": "(Marriage Available)",
        "register": "Marriages",
        "office": "OFFICE OF THE MUNICIPAL CIVIL REGISTRAR",
        "facts_of": "marriage",
        "intro_style": "page_book",
    },
}


def cert_issue_date(when: datetime | None = None) -> str:
    """e.g. June 2, 2026"""
    dt = when or datetime.now()
    return f"{dt.strftime('%B')} {dt.day}, {dt.year}"


def cert_field(data: dict[str, Any] | None, key: str, default: str = "") -> str:
    if not isinstance(data, dict):
        return default
    val = data.get(key)
    if val is None:
        return default
    s = str(val).strip()
    if not s:
        return default
    if "date" in key.lower() and len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
            return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
        except ValueError:
            return s
    return s


def form_meta(document_type: str) -> dict[str, str]:
    return FORM_META.get(document_type, FORM_META["birth"])


PRINT_FORMAT_ORIGINAL = "original"
PRINT_FORMAT_CERTIFICATION = "certification"
PRINT_FORMAT_BOTH = "both"
PRINT_FORMATS = (PRINT_FORMAT_ORIGINAL, PRINT_FORMAT_CERTIFICATION, PRINT_FORMAT_BOTH)


def normalize_print_format(value: str | None) -> str:
    v = (value or "").strip().lower()
    return v if v in PRINT_FORMATS else PRINT_FORMAT_ORIGINAL


def print_includes_original(print_format: str | None) -> bool:
    v = normalize_print_format(print_format)
    return v in (PRINT_FORMAT_ORIGINAL, PRINT_FORMAT_BOTH)


def print_includes_certification(print_format: str | None) -> bool:
    v = normalize_print_format(print_format)
    return v in (PRINT_FORMAT_CERTIFICATION, PRINT_FORMAT_BOTH)


def certification_form_label(document_type: str) -> str:
    meta = form_meta(document_type)
    return f"Certification form (CR Form No. {meta['form_no']} — {meta['availability'].strip('()')})"


def print_format_label(print_format: str, document_type: str = "birth") -> str:
    kind = normalize_print_format(print_format)
    if kind == PRINT_FORMAT_CERTIFICATION:
        return certification_form_label(document_type)
    if kind == PRINT_FORMAT_BOTH:
        return "Original document and certification form (both copies)"
    return "Original document (scanned certificate)"


def print_format_short_label(print_format: str, document_type: str = "birth") -> str:
    """Compact label for admin/staff tables."""
    kind = normalize_print_format(print_format)
    if kind == PRINT_FORMAT_CERTIFICATION:
        meta = form_meta(document_type)
        return f"CR Form {meta['form_no']}"
    if kind == PRINT_FORMAT_BOTH:
        return "Both copies"
    return "Original scan"
