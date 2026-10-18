"""Identify birth, marriage, or death from the official heading at the top.

Municipal Form No. 102 / 97 / 103 and CERTIFICATE OF LIVE BIRTH / MARRIAGE / DEATH
always print at the top of the PSA page. Cropped scans fall back to a unique
field label from the same official forms.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ocr.engine import detect_document_type_detail, document_type_label


TITLE_TEMPLATE_DIR = Path(__file__).resolve().parent / "title_templates"
_DOC_KEYS = ("birth", "marriage", "death")
_TITLE_REASONS = {
    "birth": "Certificate of Live Birth",
    "marriage": "Certificate of Marriage",
    "death": "Certificate of Death",
}

_TEMPLATE_CACHE: Optional[Dict[str, List[np.ndarray]]] = None


def _pages_to_text(pages: Optional[List[dict]]) -> str:
    lines: List[str] = []
    for page in pages or []:
        for text in page.get("rec_texts") or []:
            value = str(text or "").strip()
            if value:
                lines.append(value)
    return "\n".join(lines)


def _as_gray(img) -> np.ndarray:
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _resize_width(gray: np.ndarray, width: int) -> np.ndarray:
    height, current = gray.shape[:2]
    if current == width:
        return gray
    scale = width / float(max(1, current))
    return cv2.resize(
        gray,
        (width, max(16, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )


def _crop_frac(gray: np.ndarray, y0: float, y1: float, x0: float = 0.0, x1: float = 1.0) -> np.ndarray:
    h, w = gray.shape[:2]
    ya = max(0, int(h * y0))
    yb = min(h, max(ya + 8, int(h * y1)))
    xa = max(0, int(w * x0))
    xb = min(w, max(xa + 8, int(w * x1)))
    return gray[ya:yb, xa:xb]


def _iter_official_pages(key: str) -> List[np.ndarray]:
    pages: List[np.ndarray] = []
    src = TITLE_TEMPLATE_DIR / "source"
    if src.is_dir():
        for path in sorted(src.glob(f"{key}*")):
            if path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                pages.append(img)
    png = TITLE_TEMPLATE_DIR / f"{key}.png"
    if png.exists():
        img = cv2.imread(str(png), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            pages.append(img)
    return pages


def _title_crops_from_page(page: np.ndarray) -> List[np.ndarray]:
    if page.shape[0] >= 700:
        return [
            _crop_frac(page, 0.048, 0.095, 0.18, 0.82),
            _crop_frac(page, 0.040, 0.110, 0.12, 0.88),
            _crop_frac(page, 0.048, 0.095, 0.48, 0.86),
        ]
    return [
        _crop_frac(page, 0.22, 0.58, 0.10, 0.90),
        _crop_frac(page, 0.22, 0.58, 0.45, 0.92),
    ]


def _load_templates() -> Dict[str, List[np.ndarray]]:
    """Crops of the large printed title: CERTIFICATE OF LIVE BIRTH / MARRIAGE / DEATH."""
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is not None:
        return _TEMPLATE_CACHE
    packed: Dict[str, List[np.ndarray]] = {}
    for key in _DOC_KEYS:
        crops: List[np.ndarray] = []
        for page in _iter_official_pages(key):
            crops.extend(_title_crops_from_page(page))
        if crops:
            packed[key] = crops
    _TEMPLATE_CACHE = packed
    return _TEMPLATE_CACHE


def _score(search: np.ndarray, template: np.ndarray, fracs: Tuple[float, ...]) -> float:
    th, tw = template.shape[:2]
    sh, sw = search.shape[:2]
    if th < 8 or tw < 8 or sh < 12 or sw < 12:
        return -1.0
    best = -1.0
    for frac in fracs:
        new_w = max(16, int(sw * frac))
        new_h = max(8, int(round(th * (new_w / float(tw)))))
        if new_h >= sh - 1 or new_w >= sw - 1:
            continue
        resized = cv2.resize(template, (new_w, new_h), interpolation=cv2.INTER_AREA)
        result = cv2.matchTemplate(search, resized, cv2.TM_CCOEFF_NORMED)
        score = float(result.max())
        if score > best:
            best = score
        if best >= 0.84:
            return best
    return best


def _pick(scores: Dict[str, float], min_score: float, min_margin: float) -> Optional[Tuple[str, float, float]]:
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_type, best_score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    margin = best_score - second
    if best_score >= min_score and margin >= min_margin:
        return best_type, best_score, margin
    return None


def match_title_templates(img) -> Dict[str, Any]:
    """
    Fast visual match against the official top heading, then unique field labels.
    """
    empty = {
        "doc_type": None,
        "label": "",
        "confidence": "none",
        "reason": "Title did not match a known certificate",
        "scores": {},
    }
    templates = _load_templates()
    if not templates:
        empty["reason"] = "Title templates are missing"
        return empty

    gray = _as_gray(img)
    h, w = gray.shape[:2]
    if max(h, w) > 1400:
        scale = 1400.0 / float(max(h, w))
        gray = cv2.resize(
            gray,
            (max(80, int(round(w * scale))), max(80, int(round(h * scale)))),
            interpolation=cv2.INTER_AREA,
        )
        h, w = gray.shape[:2]

    bands = [
        _resize_width(_crop_frac(gray, 0.0, 0.20, 0.0, 1.0), 480),
        _resize_width(_crop_frac(gray, 0.03, 0.12, 0.10, 0.90), 480),
    ]
    if h < 900:
        bands.append(_resize_width(_crop_frac(gray, 0.0, 0.65, 0.0, 1.0), 480))

    scales = (0.55, 0.65, 0.75, 0.85)
    scores = {key: -1.0 for key in templates}
    for band in bands:
        for key, crops in templates.items():
            for crop in crops:
                scores[key] = max(scores[key], _score(band, crop, scales))

    hit = _pick(scores, 0.55, 0.08)
    if hit:
        doc_type, best_score, _margin = hit
        return {
            "doc_type": doc_type,
            "label": document_type_label(doc_type),
            "confidence": "high" if best_score >= 0.68 else "medium",
            "reason": _TITLE_REASONS[doc_type],
            "scores": {k: round(v, 3) for k, v in scores.items()},
        }

    empty["scores"] = {k: round(v, 3) for k, v in scores.items()}
    return empty


def _ocr_title_band(img) -> Dict[str, Any]:
    from ocr.shared import run_ocr_on_image

    height, width = img.shape[:2]
    y2 = max(48, int(height * 0.16))
    crop = img[0:y2, 0:width]
    if crop.shape[1] > 400:
        scale = 400.0 / float(crop.shape[1])
        crop = cv2.resize(
            crop,
            (400, max(40, int(round(crop.shape[0] * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    pages = run_ocr_on_image(crop, max_side=400)
    header_text = _pages_to_text(pages)
    detail = detect_document_type_detail(header_text, header_only=True)
    if not detail.get("doc_type"):
        return {
            "doc_type": None,
            "label": "",
            "confidence": "none",
            "reason": detail.get("reason") or "Could not read the certificate heading",
            "header_text": header_text,
        }
    return {
        "doc_type": detail["doc_type"],
        "label": detail.get("label") or document_type_label(detail["doc_type"]),
        "confidence": detail.get("confidence") or "medium",
        "reason": detail.get("reason") or "",
        "header_text": header_text,
    }


def detect_from_image(path: str, ocr_fallback: bool = True) -> Dict[str, Any]:
    """
    Identify a civil-registry certificate from the printed title at the top.
    Visual matching of the official formats runs first (no OCR).
    """
    empty = {
        "doc_type": None,
        "label": "",
        "confidence": "none",
        "reason": "Could not read the certificate heading",
        "header_text": "",
    }
    img = cv2.imread(path)
    if img is None:
        empty["reason"] = "Could not open the image"
        return empty

    visual = match_title_templates(img)
    if visual.get("doc_type"):
        visual["header_text"] = visual.get("reason") or ""
        return visual

    if not ocr_fallback:
        visual["header_text"] = ""
        return visual

    return _ocr_title_band(img)
