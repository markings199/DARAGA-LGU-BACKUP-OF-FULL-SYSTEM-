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

_TEMPLATE_CACHE: Optional[Dict[str, np.ndarray]] = None


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


def _load_official_page(key: str) -> Optional[np.ndarray]:
    for name in (f"{key}.jpg", f"{key}.png", f"{key}.jpeg"):
        path = TITLE_TEMPLATE_DIR / "source" / name
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is not None:
            return img
    return cv2.imread(str(TITLE_TEMPLATE_DIR / f"{key}.png"), cv2.IMREAD_GRAYSCALE)


def _load_templates() -> Dict[str, np.ndarray]:
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is not None:
        return _TEMPLATE_CACHE
    titles: Dict[str, np.ndarray] = {}
    for key in _DOC_KEYS:
        page = _load_official_page(key)
        if page is None:
            continue
        if page.shape[0] >= 700:
            titles[key] = _crop_frac(page, 0.045, 0.135, 0.12, 0.88)
        else:
            titles[key] = _crop_frac(page, 0.22, 0.72, 0.12, 0.90)
    _TEMPLATE_CACHE = titles
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
    titles = _load_templates()
    if not titles:
        empty["reason"] = "Title templates are missing"
        return empty

    gray = _as_gray(img)
    top = _resize_width(_crop_frac(gray, 0.0, 0.20, 0.0, 1.0), 520)
    title_scores = {
        key: _score(top, tpl, (0.68, 0.74, 0.80)) for key, tpl in titles.items()
    }
    hit = _pick(title_scores, 0.48, 0.04)
    if hit:
        doc_type, best_score, _margin = hit
        confidence = "high" if best_score >= 0.58 else "medium"
        return {
            "doc_type": doc_type,
            "label": document_type_label(doc_type),
            "confidence": confidence,
            "reason": _TITLE_REASONS[doc_type],
            "scores": {k: round(v, 3) for k, v in title_scores.items()},
        }

    empty["scores"] = {k: round(v, 3) for k, v in title_scores.items()}
    return empty


def _ocr_title_band(img) -> Dict[str, Any]:
    from ocr.shared import run_ocr_on_image

    height, width = img.shape[:2]
    y2 = max(48, int(height * 0.20))
    crop = img[0:y2, 0:width]
    if crop.shape[1] > 560:
        scale = 560.0 / float(crop.shape[1])
        crop = cv2.resize(
            crop,
            (560, max(40, int(round(crop.shape[0] * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    pages = run_ocr_on_image(crop, max_side=560)
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
