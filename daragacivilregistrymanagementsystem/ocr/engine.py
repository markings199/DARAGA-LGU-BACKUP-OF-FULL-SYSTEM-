import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_ROOT = BASE_DIR / "forms"


def _norm(s: str) -> str:
    s = (s or "").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{2,}", "\n", s)
    return s.strip()


def _norm_upper(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").upper()).strip()


def _iter_json_templates(folder: Path) -> Iterable[Path]:
    if not folder.exists() or not folder.is_dir():
        return []
    return sorted([p for p in folder.glob("*.json") if p.is_file()])


def _compile_regex(pattern: str) -> re.Pattern:
    # Templates store regex strings; compile with MULTILINE/IGNORECASE by default.
    return re.compile(pattern, re.IGNORECASE | re.MULTILINE)


def _first_capture(regex: re.Pattern, text: str) -> Optional[str]:
    m = regex.search(text)
    if not m:
        return None
    if "value" in m.groupdict():
        return m.group("value")
    if m.groups():
        return m.group(1)
    return m.group(0)


def _clean_value(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    v = re.sub(r"\s+", " ", v)
    v = v.strip(" :\t\r\n")
    return v or None


@dataclass(frozen=True)
class MatchResult:
    matched: bool
    score: float
    reason: str


_FORM_BIRTH = re.compile(
    r"\b(?:MUNICIPAL\s+)?FORM\s*(?:NO\.?|NUMBER|#)?\s*102\b",
    re.IGNORECASE,
)
_FORM_MARRIAGE = re.compile(
    r"\b(?:MUNICIPAL\s+)?FORM\s*(?:NO\.?|NUMBER|#)?\s*97\b",
    re.IGNORECASE,
)
_FORM_DEATH = re.compile(
    r"\b(?:MUNICIPAL\s+)?FORM\s*(?:NO\.?|NUMBER|#)?\s*103\b",
    re.IGNORECASE,
)

_DOC_TYPE_LABELS = {
    "birth": "Birth certificate",
    "marriage": "Marriage certificate",
    "death": "Death certificate",
}


def document_type_label(doc_type: Optional[str]) -> str:
    return _DOC_TYPE_LABELS.get((doc_type or "").strip().lower(), "")


def _squash_alpha(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())


def detect_document_type_detail(ocr_text: str, header_only: bool = False) -> Dict[str, Any]:
    """
    Identify a PSA civil-registry certificate from OCR text.
    The printed type name is at the top: Certificate of Live Birth / Marriage / Death.
    Returns doc_type (birth|marriage|death|None), confidence, reason, label.
    """
    empty = {
        "doc_type": None,
        "confidence": "none",
        "reason": "No readable heading",
        "label": "",
    }
    t = _norm_upper(ocr_text)
    if not t:
        return empty
    squash = _squash_alpha(t)

    def _hit(doc_type: str, confidence: str, reason: str) -> Dict[str, Any]:
        return {
            "doc_type": doc_type,
            "confidence": confidence,
            "reason": reason,
            "label": document_type_label(doc_type),
        }

    if "CERTIFICATEOFLIVEBIRTH" in squash or "LIVEBIRTH" in squash or "LIVEB1RTH" in squash:
        return _hit("birth", "high", "Certificate of Live Birth")
    if "CERTIFICATEOFDEATH" in squash or "OFDEATH" in squash or "OFDEA1H" in squash:
        return _hit("death", "high", "Certificate of Death")
    if "CERTIFICATEOFMARRIAGE" in squash or "OFMARRIAGE" in squash or "OFMARRLAGE" in squash:
        return _hit("marriage", "high", "Certificate of Marriage")
    if "CERTIF" in squash and "MARRIAG" in squash and "LIVEBIRTH" not in squash:
        return _hit("marriage", "high", "Marriage certificate heading")
    if "CERTIF" in squash and "DEATH" in squash and "LIVEBIRTH" not in squash and "MARRIAG" not in squash:
        return _hit("death", "high", "Death certificate heading")
    if "CERTIF" in squash and "LIVE" in squash and "BIRTH" in t:
        return _hit("birth", "high", "Certificate of Live Birth")

    if _FORM_BIRTH.search(t) or "FORMNO102" in squash or "MUNICIPALFORMNO102" in squash:
        return _hit("birth", "high", "Municipal Form No. 102")
    if _FORM_DEATH.search(t) or "FORMNO103" in squash or "MUNICIPALFORMNO103" in squash:
        return _hit("death", "high", "Municipal Form No. 103")
    if _FORM_MARRIAGE.search(t) or "FORMNO97" in squash or "MUNICIPALFORMNO97" in squash:
        return _hit("marriage", "high", "Municipal Form No. 97")

    if "CONTRACTINGPART" in squash or "PLACEOFMARRIAGE" in squash or "DATEOFMARRIAGE" in squash:
        return _hit("marriage", "high", "Marriage certificate heading")
    if "TYPEOFBIRTH" in squash or "WEIGHTATBIRTH" in squash or "NAMEOFCHILD" in squash:
        return _hit("birth", "high", "Birth certificate heading")
    if "DATEOFDEATH" in squash or "CAUSESOFDEATH" in squash or "NAMEOFDECEASED" in squash:
        return _hit("death", "high", "Death certificate heading")

    if header_only:
        return empty

    if "CAUSEOFDEATH" in squash:
        return _hit("death", "high", "Cause of Death")

    birth_keys = (
        "NAME OF CHILD",
        "TYPE OF BIRTH",
        "WEIGHT AT BIRTH",
        "ATTENDANT",
        "PLACE OF BIRTH",
    )
    death_keys = (
        "NAME OF DECEASED",
        "DATE OF DEATH",
        "PLACE OF DEATH",
        "CAUSE OF DEATH",
        "CORPSE DISPOSAL",
    )
    marriage_keys = (
        "HUSBAND",
        "WIFE",
        "DATE OF MARRIAGE",
        "PLACE OF MARRIAGE",
        "SOLEMNIZING",
    )
    birth_hits = sum(1 for k in birth_keys if k in t)
    death_hits = sum(1 for k in death_keys if k in t)
    marriage_hits = sum(1 for k in marriage_keys if k in t)
    ranked = sorted(
        ((birth_hits, "birth"), (death_hits, "death"), (marriage_hits, "marriage")),
        key=lambda item: item[0],
        reverse=True,
    )
    best_score, best_type = ranked[0]
    second = ranked[1][0]
    if best_score >= 3 and best_score > second:
        return _hit(best_type, "medium", "Matching certificate fields")
    if best_score >= 2 and best_score > second:
        return _hit(best_type, "low", "Partial field match")
    return empty


def detect_document_type(ocr_text: str) -> Optional[str]:
    """
    Heuristic document type detection from OCR text.
    Returns: "birth" | "death" | "marriage" | None
    """
    return detect_document_type_detail(ocr_text).get("doc_type")


def load_templates(doc_type: str, template_root: Path = TEMPLATE_ROOT) -> List[Dict[str, Any]]:
    folder = template_root / doc_type
    templates: List[Dict[str, Any]] = []
    for p in _iter_json_templates(folder):
        with p.open("r", encoding="utf-8") as f:
            t = json.load(f)
        t["_path"] = str(p)
        templates.append(t)
    return templates


def match_template(template: Dict[str, Any], ocr_text: str) -> MatchResult:
    """
    Flexible matching:
    - keywords_all: all must appear
    - keywords_any: at least N (min_any) must appear
    - regex_all: all regex patterns must match
    Score is 0..1.
    """
    t = _norm_upper(ocr_text)
    match_cfg = template.get("match", {}) or {}

    keywords_all = [_norm_upper(x) for x in (match_cfg.get("keywords_all") or []) if str(x).strip()]
    keywords_any = [_norm_upper(x) for x in (match_cfg.get("keywords_any") or []) if str(x).strip()]
    min_any = int(match_cfg.get("min_any") or (1 if keywords_any else 0))
    regex_all = [str(x) for x in (match_cfg.get("regex_all") or []) if str(x).strip()]

    # keywords_all
    missing_all = [k for k in keywords_all if k not in t]
    if missing_all:
        return MatchResult(False, 0.0, f"missing keywords_all: {missing_all[:3]}")

    # regex_all
    for rp in regex_all:
        if not _compile_regex(rp).search(ocr_text or ""):
            return MatchResult(False, 0.0, f"missing regex_all: {rp}")

    # keywords_any scoring
    any_hits = sum(1 for k in keywords_any if k and k in t)
    if keywords_any and any_hits < min_any:
        return MatchResult(False, 0.0, f"keywords_any hits {any_hits} < {min_any}")

    # score: weigh any_hits + presence of some fields patterns
    score = 1.0
    if keywords_any:
        score = max(0.1, any_hits / max(1, len(keywords_any)))

    return MatchResult(True, float(min(1.0, score)), f"ok any_hits={any_hits}")


def extract_with_template(template: Dict[str, Any], ocr_text: str) -> Dict[str, Any]:
    """
    Extract fields defined by template['fields'].
    Each field supports:
      - patterns: list of regex strings (first successful capture used)
    Missing fields return None.
    """
    fields_cfg: Dict[str, Any] = template.get("fields", {}) or {}
    out: Dict[str, Any] = {}
    text = _norm(ocr_text or "")

    for field_name, cfg in fields_cfg.items():
        cfg = cfg or {}
        patterns = [str(p) for p in (cfg.get("patterns") or []) if str(p).strip()]
        value: Optional[str] = None
        for pat in patterns:
            regex = _compile_regex(pat)
            value = _first_capture(regex, text)
            value = _clean_value(value)
            if value:
                break
        out[field_name] = value

    return out


def extract_from_ocr_text(
    ocr_text: str,
    doc_type: Optional[str] = None,
    template_root: Path = TEMPLATE_ROOT,
    best_effort: bool = False,
    include_debug: bool = False,
) -> Dict[str, Any]:
    """
    Core API:
    - detect doc type if not provided
    - load templates from templates/<doc_type>/*.json
    - try templates until match; fallback to next
    Returns:
      {
        "document_type": ...,
        "template_path": ...,
        "match_score": ...,
        "fields": { ... extracted ... }
      }
    """
    text = _norm(ocr_text or "")
    detected = doc_type or detect_document_type(text)
    if not detected:
        return {
            "document_type": None,
            "template_path": None,
            "match_score": 0.0,
            "fields": {},
            "error": "Could not detect document type",
        }

    candidates = load_templates(detected, template_root=template_root)
    if not candidates:
        return {
            "document_type": detected,
            "template_path": None,
            "match_score": 0.0,
            "fields": {},
            "error": f"No templates found in {template_root / detected}",
        }

    debug_tried: List[Dict[str, Any]] = []
    best: Tuple[float, Optional[Dict[str, Any]], str] = (0.0, None, "no match")
    for tpl in candidates:
        m = match_template(tpl, text)
        fields = extract_with_template(tpl, text) if (m.matched or best_effort) else {}

        found = sum(1 for v in (fields or {}).values() if v)
        total = max(1, len(tpl.get("fields") or {}))

        if m.matched:
            score = 0.7 * m.score + 0.3 * (found / total)
        else:
            # Best-effort scoring: prioritize templates that actually extract more fields.
            # Keep this below any real match, but still pick a "least bad" template.
            score = 0.05 + 0.25 * (found / total)

        if include_debug:
            debug_tried.append(
                {
                    "template_path": tpl.get("_path"),
                    "matched": bool(m.matched),
                    "match_reason": m.reason,
                    "match_score": float(m.score),
                    "found_fields": int(found),
                    "total_fields": int(total),
                    "final_score": float(score),
                }
            )

        # In non-best-effort mode, only consider matched templates.
        if not best_effort and not m.matched:
            continue

        if score >= best[0]:
            best = (score, {**tpl, "_extracted": fields, "_match": m}, "matched" if m.matched else "best_effort")

    if not best[1]:
        out = {
            "document_type": detected,
            "template_path": None,
            "match_score": 0.0,
            "fields": {},
            "error": "No template matched OCR text",
        }
        if include_debug:
            out["tried"] = debug_tried
        return out

    chosen = best[1]
    fields = chosen.get("_extracted") or {}
    m: MatchResult = chosen.get("_match")  # type: ignore[assignment]
    out = {
        "document_type": detected,
        "template_path": chosen.get("_path"),
        "match_score": float(best[0]),
        "match_reason": getattr(m, "reason", ""),
        "fields": fields,
    }
    if include_debug:
        out["tried"] = debug_tried
    return out


def ocr_pages_to_text(pages: List[Dict[str, Any]]) -> str:
    """
    Utility for PaddleOCR normalized output (ocr_shared.run_ocr_on_image_path).
    """
    lines: List[str] = []
    for page in pages or []:
        for t in page.get("rec_texts", []) or []:
            s = str(t).strip()
            if s:
                lines.append(s)
    return "\n".join(lines)

