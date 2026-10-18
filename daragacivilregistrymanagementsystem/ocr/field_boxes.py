"""
Map write-in boxes on Municipal Forms 102 / 103 / 97 after one page OCR.

Prefer passing items/pages from the main extractor so PaddleOCR runs only once.
If items are omitted, this module aligns the scan and runs a single page OCR.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from ocr.person_name import WIDE_FML_NAME_KEYS
from ocr.shared import run_ocr_on_image
from ocr.sex import sex_from_ocr_text

_CAUSE_JUNK = (
    "CHILDREN",
    "CHILD STILL",
    "BORN ALIVE",
    "STILL LIVING",
    "INCLUDING THIS BIRTH",
    "NOW DEAD",
    "MAIDEN NAME",
    "ATTENDANT AT BIRTH",
    "NO OF CHILD",
    "NO OF CHILDREN",
    "10A",
    "10B",
    "10C",
    "OCCUPATION",
    "HOUSEWIFE",
    "CERTIFICATION OF ATTENDANT",
    "ACCOMPLISH",
    "FOR AGES",
    "AGED 8 DAYS",
    "0 TO 7",
    "MEDICAL CERTIFICATE",
    "INTERVAL BETWEEN",
    "ONSET AND DEATH",
    "MATERNAL CONDITION",
    "EXTERNAL CAUSES",
    "IF THE DECEASED",
    "ITEMS 14",
    "AT THE BACK",
)


def is_plausible_cause_of_death(text: str) -> bool:
    """Drop mother/children form lines that are not a medical cause of death."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return False
    letters = sum(c.isalpha() for c in raw)
    if letters < 4:
        return False
    upper = re.sub(r"[^A-Z0-9 ]+", " ", raw.upper())
    upper = re.sub(r"\s+", " ", upper).strip()
    if re.search(r"\b10\s*[ABC]\b", upper):
        return False
    if any(token in upper for token in _CAUSE_JUNK):
        return False
    # Form option lines / numbered prompts, not diagnoses
    if re.search(r"\b(HOSPITAL|CLINIC|INSTITUTION|HOUSE)\b.*\b(STREET|BARANGAY)\b", upper):
        return False
    return True


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_SPEC_LOCK = threading.Lock()
_SPECS: Dict[str, Dict[str, Any]] = {}

_DOC_FILES = {
    "birth": "birth_boxes.json",
    "death": "death_boxes.json",
    "marriage": "marriage_boxes.json",
}

_NOISE = {
    "CERTIFICATE", "LIVE", "BIRTH", "DEATH", "MARRIAGE", "REPUBLIC",
    "PHILIPPINES", "MUNICIPAL", "FORM", "OFFICE", "CIVIL", "REGISTRAR",
    "GENERAL", "PROVINCE", "CITY", "MUNICIPALITY", "REGISTRY", "NUMBER",
    "NO", "NAME", "FIRST", "FIRSTNAME", "MIDDLE", "LAST", "LASTNAME",
    "SEX", "DATE", "DAY", "MONTH", "YEAR", "PLACE", "OF", "THE", "AND",
    "HOSPITAL", "CLINIC", "INSTITUTION", "HOUSE", "STREET", "BARANGAY",
    "TYPE", "IF", "MULTIPLE", "CHILD", "WAS", "ORDER", "WEIGHT",
    "GRAMS", "MAIDEN", "CITIZENSHIP", "RELIGION", "RELIGIOUS", "SECT",
    "OCCUPATION", "AGE", "TIME", "THIS", "RESIDENCE", "COUNTRY",
    "FATHER", "MOTHER", "PARENTS", "ATTENDANT", "INFORMANT",
    "CERTIFICATION", "REMARKS", "ANNOTATIONS", "LCRO", "CRG", "USE",
    "ONLY", "MALE", "FEMALE", "SINGLE", "TWIN", "TRIPLET",
    "HUSBAND", "WIFE", "CONTRACTING", "PARTIES", "SOLEMNIZE",
    "MIDDELE", "MIDDEL", "MIDLE", "FIRT", "FRST",
    "RECEIVED", "REGISTERED", "PREPARED", "SIGNATURE", "PRINT",
    "TITLE", "POSITION", "ADDRESS", "RELATIONSHIP", "DECEASED",
    "CAUSE", "IMMEDIATE", "ANTECEDENT", "UNDERLYING", "MEDICAL",
    "STATUS", "CIVIL", "NATIONALITY", "COMPLETED", "YEARS",
}

_SEX_OK = {"MALE", "FEMALE", "M", "F"}
_STATUS_OK = {
    "SINGLE", "MARRIED", "WIDOWED", "WIDOW", "WIDOWER",
    "DIVORCED", "SEPARATED", "ANNULLED",
}

_NATIONALITY_WORDS = {
    "FILIPINO": "Filipino",
    "FILIPINA": "Filipino",
    "FILIPING": "Filipino",
    "FILPINO": "Filipino",
    "FLIPINO": "Filipino",
    "AMERICAN": "American",
    "CHINESE": "Chinese",
    "JAPANESE": "Japanese",
    "KOREAN": "Korean",
    "BRITISH": "British",
    "INDIAN": "Indian",
    "AUSTRALIAN": "Australian",
    "CANADIAN": "Canadian",
}

_ADDRESS_TOKENS = {
    "BLOCK", "LOT", "PUROK", "BARANGAY", "BRGY", "STREET", "HOMES",
    "SUBDIVISION", "VILLAGE", "SITIO", "PHASE", "ROAD", "AVENUE",
    "CAMELLA", "HOUSE", "BLDG", "BUILDING",
}


def _norm_token(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _load_spec(doc_type: str) -> Optional[Dict[str, Any]]:
    key = (doc_type or "").strip().lower()
    if key not in _DOC_FILES:
        return None
    with _SPEC_LOCK:
        path = TEMPLATE_DIR / _DOC_FILES[key]
        if not path.exists():
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        cached = _SPECS.get(key)
        if cached is not None and cached.get("_json_mtime") == mtime:
            return cached
        spec = json.loads(path.read_text(encoding="utf-8"))
        image_path = TEMPLATE_DIR / spec["image"]
        img = None
        if cached is not None and cached.get("_path") == str(image_path):
            img = cached.get("_image")
            spec["_orb"] = cached.get("_orb")
        if img is None:
            img = cv2.imread(str(image_path))
        if img is None:
            return None
        spec["_image"] = img
        spec["_path"] = str(image_path)
        spec["_json_mtime"] = mtime
        _SPECS[key] = spec
        return spec


def _gray_max_side(gray: np.ndarray, max_side: int = 800) -> Tuple[np.ndarray, float]:
    h, w = gray.shape[:2]
    longest = max(h, w)
    if longest <= max_side:
        return gray, 1.0
    scale = max_side / float(longest)
    small = cv2.resize(
        gray,
        (max(32, int(round(w * scale))), max(32, int(round(h * scale)))),
        interpolation=cv2.INTER_AREA,
    )
    return small, scale


def _scale_homography(h_small, scale_scan: float, scale_tpl: float):
    if h_small is None:
        return None
    if scale_scan == 1.0 and scale_tpl == 1.0:
        return h_small
    ss = np.array(
        [[scale_scan, 0.0, 0.0], [0.0, scale_scan, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    st_inv = np.array(
        [[1.0 / scale_tpl, 0.0, 0.0], [0.0, 1.0 / scale_tpl, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    return st_inv @ h_small @ ss


def _template_orb(spec: Dict[str, Any]):
    packed = spec.get("_orb")
    if packed is not None:
        return packed
    t_gray = cv2.cvtColor(spec["_image"], cv2.COLOR_BGR2GRAY)
    t_small, scale_t = _gray_max_side(t_gray)
    detector = cv2.ORB_create(nfeatures=900, fastThreshold=12)
    kp_t, des_t = detector.detectAndCompute(t_small, None)
    packed = (kp_t, des_t, scale_t)
    spec["_orb"] = packed
    return packed


def _find_homography(scan, spec: Dict[str, Any]) -> Optional[Any]:
    kp_t, des_t, scale_t = _template_orb(spec)
    detector = cv2.ORB_create(nfeatures=900, fastThreshold=12)
    s_gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY)
    s_small, scale_s = _gray_max_side(s_gray)
    kp_s, des_s = detector.detectAndCompute(s_small, None)
    if des_t is None or des_s is None or len(kp_t) < 12 or len(kp_s) < 12:
        return None
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    try:
        pairs = matcher.knnMatch(des_s, des_t, k=2)
    except cv2.error:
        return None
    good = []
    for pair in pairs:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < 0.75 * n.distance:
            good.append(m)
    if len(good) < 14:
        return None
    src = np.float32([kp_s[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_t[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, 5.0)
    if homography is None:
        return None
    inliers = int(mask.sum()) if mask is not None else 0
    if inliers < 10:
        return None
    return _scale_homography(homography, scale_s, scale_t)


def _items_from_pages(pages, img_w: int, img_h: int) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for page in pages or []:
        texts = page.get("rec_texts") or []
        scores = page.get("rec_scores") or []
        polys = page.get("dt_polys") or []
        for i, text in enumerate(texts):
            s = str(text or "").strip()
            if not s:
                continue
            poly = polys[i] if i < len(polys) else None
            if poly is None:
                continue
            arr = np.asarray(poly, dtype=np.float32)
            if arr.ndim != 2 or arr.shape[0] < 2:
                continue
            xs = arr[:, 0]
            ys = arr[:, 1]
            x1 = float(np.clip(xs.min(), 0, img_w))
            x2 = float(np.clip(xs.max(), 0, img_w))
            y1 = float(np.clip(ys.min(), 0, img_h))
            y2 = float(np.clip(ys.max(), 0, img_h))
            score = float(scores[i]) if i < len(scores) else 0.0
            items.append(
                {
                    "text": s,
                    "score": score,
                    "x1": x1,
                    "y1": y1,
                    "x2": x2,
                    "y2": y2,
                    "cx": (x1 + x2) / 2.0,
                    "cy": (y1 + y2) / 2.0,
                }
            )
    items.sort(key=lambda it: (it["cy"], it["x1"]))
    return items


def _project_items_to_template(
    items: List[Dict[str, Any]],
    homography,
    scan_w: int,
    scan_h: int,
    template_w: int,
    template_h: int,
) -> List[Dict[str, Any]]:
    if not items:
        return []
    if homography is None:
        sx = template_w / float(max(1, scan_w))
        sy = template_h / float(max(1, scan_h))
        out = []
        for it in items:
            cx = it["cx"] * sx
            cy = it["cy"] * sy
            out.append({**it, "cx": cx, "cy": cy})
        return out
    pts = np.float32([[[it["cx"], it["cy"]]] for it in items])
    try:
        mapped = cv2.perspectiveTransform(pts, homography)
    except cv2.error:
        return _project_items_to_template(items, None, scan_w, scan_h, template_w, template_h)
    out = []
    for it, pt in zip(items, mapped):
        cx, cy = float(pt[0][0]), float(pt[0][1])
        out.append({**it, "cx": cx, "cy": cy})
    return out


def _text_in_box(
    items: List[Dict[str, Any]],
    box: List[float],
    img_w: int,
    img_h: int,
    *,
    left_to_right: bool = False,
    min_score: float = 0.2,
    pad_frac_x: float = 0.03,
    pad_frac_y: float = 0.08,
) -> str:
    x1, y1, x2, y2 = box
    left = x1 * img_w
    right = x2 * img_w
    top = y1 * img_h
    bottom = y2 * img_h
    pad_x = max(4.0, (right - left) * pad_frac_x)
    pad_y = max(3.0, (bottom - top) * pad_frac_y)
    left -= pad_x
    right += pad_x
    top -= pad_y
    bottom += pad_y
    hits = [
        it
        for it in items
        if left <= it["cx"] <= right and top <= it["cy"] <= bottom and it.get("score", 1.0) >= min_score
    ]
    if left_to_right:
        hits.sort(key=lambda it: (it["x1"], it["cy"]))
    else:
        hits.sort(key=lambda it: (it["cy"], it["x1"]))
    return " ".join(it["text"] for it in hits).strip()


def _name_from_fml_slots(
    items: List[Dict[str, Any]],
    box: List[float],
    img_w: int,
    img_h: int,
    printed: str = "",
) -> str:
    """Form 102/103 names are First | Middle | Last left-to-right."""
    x1, y1, x2, y2 = box
    span = max((x2 - x1), 0.05)
    printed_all = f"{printed or ''} Name First Middle Last"
    parts: List[str] = []
    for i in range(3):
        sub = [x1 + span * i / 3.0, y1, x1 + span * (i + 1) / 3.0, y2]
        raw = _text_in_box(items, sub, img_w, img_h, left_to_right=True)
        raw = _subtract_printed(raw, printed_all)
        raw = _clean_value("name", raw)
        if raw:
            parts.append(raw)
    joined = _join_name_parts(parts)
    if joined:
        return joined
    raw = _text_in_box(items, box, img_w, img_h, left_to_right=True)
    raw = _subtract_printed(raw, printed_all)
    return _clean_value("name", raw)


def _subtract_printed(scan_text: str, printed_text: str) -> str:
    printed = {_norm_token(t) for t in re.findall(r"[A-Za-z0-9]+", printed_text or "")}
    printed.discard("")
    kept: List[str] = []
    for token in re.findall(r"[A-Za-z0-9/'\\-]+", scan_text or ""):
        n = _norm_token(token)
        if not n:
            continue
        if n in printed:
            continue
        if n in _NOISE and n not in _SEX_OK and n not in _STATUS_OK:
            continue
        kept.append(token)
    return " ".join(kept).strip()


_MONTH_NAMES = (
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
)
_DATE_WORD_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTH_NAMES) + r")\s+(\d{4})\b"
)


def _clean_date_value(text: str) -> str:
    """Keep handwritten dates; drop Form 103 printed 'Day, Month, Year' labels."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return ""
    n = re.sub(r"[^A-Z0-9 ]+", " ", raw.upper())
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"\b(DAY|MONTH|YEAR|DATE|OF|DEATH|BIRTH)\b", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    for month in sorted(_MONTH_NAMES, key=len, reverse=True):
        n = re.sub(rf"(?<=\d)({month})", r" \1", n)
        n = re.sub(rf"({month})(?=\d)", r"\1 ", n)
    n = re.sub(r"\s+", " ", n).strip()
    m = _DATE_WORD_RE.search(n)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"
    m = re.search(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})\b", raw)
    if m:
        year = m.group(3)
        if len(year) == 2:
            year = ("20" if int(year) <= 30 else "19") + year
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{year}"
    return ""


def _citizenship_value(text: str) -> str:
    """Keep a real nationality. Residence lines (Block, Lot, Purok) are not citizenship."""
    upper = re.sub(r"[^A-Z ]", " ", (text or "").upper())
    upper = re.sub(r"\s+", " ", upper).strip()
    if not upper:
        return ""
    words = [w for w in upper.split() if w not in {"SEX", "MALE", "FEMALE", "CITIZENSHIP"}]
    for word in words:
        if word in _NATIONALITY_WORDS:
            return _NATIONALITY_WORDS[word]
    if any(word in _ADDRESS_TOKENS for word in words):
        return ""
    if len(words) == 1 and len(words[0]) >= 4:
        return words[0].title()
    return ""


def _clean_value(kind: str, raw: str) -> str:
    text = re.sub(r"\s+", " ", (raw or "").strip())
    if not text:
        return ""
    upper = text.upper()
    if kind == "sex":
        return sex_from_ocr_text(text)
    if kind == "status":
        # Drop printed option lists: (Single/Married/Widow/Widower/...)
        stripped = re.sub(r"\([^)]*\)", " ", text)
        upper = stripped.upper()
        order = (
            "WIDOWED", "WIDOWER", "WIDOW", "MARRIED", "SINGLE",
            "DIVORCED", "SEPARATED", "ANNULLED",
        )
        hits = [w for w in order if re.search(rf"\b{w}\b", upper)]
        # More than one status word ⇒ OCR grabbed the printed checklist, not the write-in.
        if len(hits) != 1:
            return ""
        word = hits[0]
        if word in {"WIDOWER", "WIDOW"}:
            return "Widowed"
        return word.title()
    if kind == "citizenship":
        return _citizenship_value(text)
    if kind == "age":
        hits = []
        upper = text.upper()
        upper = re.sub(r"\b(?:19|20)\d{2}\b", " ", upper)
        upper = re.sub(r"\bAGE\b", " ", upper)
        # OCR often reads 31 as 3I / 3l and 28 as 2B.
        compact = re.sub(r"[^A-Z0-9]", "", upper)
        mapped = compact.translate(str.maketrans({
            "O": "0", "Q": "0", "D": "0",
            "I": "1", "L": "1",
            "Z": "2",
            "S": "5",
            "B": "8",
            "G": "6",
        }))
        if re.fullmatch(r"\d{1,3}", mapped):
            val = int(mapped)
            if 12 <= val <= 99:
                return str(val)
        for m in re.finditer(r"(?<!\d)(\d{1,3})\s*(YEARS?|YRS)?\b", upper):
            val = int(m.group(1))
            unit = m.group(2) or ""
            after = upper[m.end() : m.end() + 16].strip()
            before = upper[max(0, m.start() - 16) : m.start()]
            if val == 1 and (after.startswith("OR") or "ABOVE" in after[:16] or "UNDER" in before):
                continue
            if 0 <= val <= 130:
                hits.append((1 if unit else 0, val))
        if not hits:
            return ""
        with_unit = [h for h in hits if h[0]]
        if with_unit:
            return str(with_unit[-1][1])
        adult = [h for h in hits if 18 <= h[1] <= 90]
        pool = adult or [h for h in hits if h[1] != 1]
        if not pool:
            return ""
        return str(pool[-1][1])
    if kind == "registry":
        compact = re.sub(r"[^A-Za-z0-9/\-]", "", text)
        return compact if len(compact) >= 3 else ""
    if kind == "date":
        return _clean_date_value(text)
    if kind == "cause":
        letters = re.sub(r"[^A-Za-z]", "", text)
        if len(letters) < 4 or not is_plausible_cause_of_death(text):
            return ""
        return text
    if kind == "name":
        upper = text.upper()
        if any(tip in upper for tip in (
            "TITLE OR POSITION", "TITLE OF POSITION", "NAME IN PRINT",
            "SIGNATURE", "HOUSE NO", "(HOUSE", "BARANGAY)", "MUNICIPALITY)",
        )):
            return ""
        if "(" in text and ")" in text:
            return ""
        letters = re.sub(r"[^A-Za-z ]", " ", text)
        words = [
            w for w in letters.split()
            if w.upper() not in _NATIONALITY_WORDS
            and w.upper() not in {"MALE", "FEMALE", "SEX", "FILL", "IN", "BY", "AT", "OF", "THE"}
        ]
        # Cut instruction tails that stick to handwritten names.
        cut = []
        for w in words:
            if w.upper() in {"FILL", "TYPEWRITE", "PRINT", "WRITE"}:
                break
            cut.append(w)
        letters = " ".join(cut).strip()
        return letters if len(letters) >= 5 else ""
    if kind == "place":
        return _place_of_death_value(text)
    return text


_PLACE_FORM_TOKENS = {
    "HOSPITAL", "CLINIC", "INSTITUTION", "HOUSE", "STREET", "BARANGAY",
    "CITY", "MUNICIPALITY", "PROVINCE", "NAME", "PLACE", "DEATH", "NO", "ST",
}


def _place_of_death_value(text: str) -> str:
    """Keep a real place; reject form instruction lines and date/time bleed."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if not raw:
        return ""
    # Drop parenthetical instruction and leading item number (e.g. "6.")
    cleaned = re.sub(r"\([^)]*\)", " ", raw)
    cleaned = re.sub(r"^\d+\.?\s*", "", cleaned)
    cleaned = re.sub(
        r"(?i)\b(name of\s+)?hospital/?clinic/?institution/?house\b[^.]*",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"(?i)\b(hospital|clinic|institution|house)\s*/\s*(clinic|institution|house|street)\b",
        " ",
        cleaned,
    )
    # Cut date/time that often leaks from the next marriage form row.
    months = (
        "JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|"
        "SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER"
    )
    cut = re.search(rf"(?i)\b\d{{1,2}}\s+(?:{months})\s+\d{{2,4}}\b", cleaned)
    if cut:
        cleaned = cleaned[: cut.start()]
    cut = re.search(
        r"(?i)\b\d{1,2}\s*[:.]\s*\d{2}\s*(?:AM|PM)?\b|\b\d{1,2}\s+\d{2}\s*(?:AM|PM)\b|\b(?:AM|PM)\b",
        cleaned,
    )
    if cut:
        cleaned = cleaned[: cut.start()]
    cleaned = re.sub(r"(?i)\b(?:CITY\s+)?MUNICIPALITY(?:\s+PROVINCE)?\s*$", " ", cleaned)
    # Birth Form 102: type/order/weight options must not become Place of Birth.
    cleaned = re.sub(
        r"(?i)\b(?:5\s*[ABC]|TYPE OF BIRTH|IF MULTIPLE|BIRTH ORDER|WEIGHT(?:\s+AT\s+BIRTH)?|"
        r"PREVIOUS(?:\s+LIVE)?\s+BIRTHS|INCLUDING\s+FETAL|"
        r"SINGLE|TWIN|TRIPLET|SECOND|THIRD|ETC)\b.*$",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"(?i)\b\d{1,2}\s+\d{2,4}\s*g(?:rams?)?\b", " ", cleaned)
    cleaned = re.sub(r"(?i)\b\d{3,5}\s*g(?:rams?)?\b", " ", cleaned)
    cleaned = re.sub(r"[/]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .:-,")
    upper = re.sub(r"[^A-Z0-9 ]+", " ", cleaned.upper())
    upper = re.sub(r"\s+", " ", upper).strip()
    words = [w for w in upper.split() if w]
    if not words:
        return ""
    formish = sum(1 for w in words if w in _PLACE_FORM_TOKENS)
    if formish >= max(2, len(words) - 1):
        return ""
    if len(words) <= 2 and all(w in _PLACE_FORM_TOKENS or len(w) <= 2 for w in words):
        return ""
    letters = sum(c.isalpha() for c in cleaned)
    return cleaned if letters >= 4 else ""


def _fill_boxes(spec: Dict[str, Any], items_template: List[Dict[str, Any]]) -> Dict[str, str]:
    template = spec["_image"]
    th, tw = template.shape[:2]
    out: Dict[str, str] = {}
    for field in spec.get("fields") or []:
        key = field["key"]
        kind = field.get("kind") or "text"
        if kind == "name" and key in WIDE_FML_NAME_KEYS:
            value = _name_from_fml_slots(
                items_template, field["box"], tw, th, field.get("printed") or ""
            )
        elif kind == "name":
            raw = _text_in_box(items_template, field["box"], tw, th, left_to_right=True)
            value = _subtract_printed(raw, field.get("printed") or "")
            value = _clean_value(kind, value)
        elif kind == "sex":
            raw = _text_in_box(items_template, field["box"], tw, th)
            value = sex_from_ocr_text(raw)
        elif kind == "age":
            raw = _text_in_box(
                items_template, field["box"], tw, th,
                min_score=0.08, pad_frac_x=0.45, pad_frac_y=0.85,
            )
            value = _subtract_printed(raw, field.get("printed") or "")
            value = _clean_value(kind, value)
        else:
            raw = _text_in_box(items_template, field["box"], tw, th)
            value = _subtract_printed(raw, field.get("printed") or "")
            value = _clean_value(kind, value)
        if value and key not in out:
            out[key] = value
    return _compose_party_full_names(out)


def _compose_party_full_names(out: Dict[str, str]) -> Dict[str, str]:
    """Form 97 writes First / Middle / Last — join them for Husband Name and Wife Name."""
    for who in ("Husband", "Wife"):
        parts = []
        for part in ("First", "Middle", "Last"):
            val = re.sub(r"\s+", " ", (out.get(f"{who} {part}") or "").strip())
            if val:
                parts.append(val)
        composed = _collapse_doubled_person_name(_join_name_parts(parts))
        current = _collapse_doubled_person_name(
            re.sub(r"\s+", " ", (out.get(f"{who} Name") or "").strip())
        )
        if composed and (not current or len(composed.split()) >= len(current.split())):
            out[f"{who} Name"] = composed
        elif current:
            out[f"{who} Name"] = current
        elif composed:
            out[f"{who} Name"] = composed
        out.pop(f"{who} First", None)
        out.pop(f"{who} Middle", None)
        out.pop(f"{who} Last", None)
    return out


def _name_keys(words: List[str]) -> List[str]:
    return [re.sub(r"[^A-Za-z]", "", w).upper() for w in words]


def _join_name_parts(parts: List[str]) -> str:
    """Join First/Middle/Last without concatenating overlapping OCR chunks."""
    cleaned: List[str] = []
    for raw in parts:
        piece = re.sub(r"\s+", " ", (raw or "").strip())
        if not piece:
            continue
        piece_words = piece.split()
        existing = cleaned[:]
        existing_keys = _name_keys(existing)
        piece_keys = _name_keys(piece_words)
        if not piece_keys:
            continue
        # Skip if this chunk already appears as a contiguous run.
        if any(
            existing_keys[i : i + len(piece_keys)] == piece_keys
            for i in range(0, max(1, len(existing_keys) - len(piece_keys) + 1))
        ):
            continue
        # If the new chunk already contains the whole name so far, keep the fuller one.
        if existing_keys and len(piece_keys) > len(existing_keys):
            if any(
                piece_keys[i : i + len(existing_keys)] == existing_keys
                for i in range(0, max(1, len(piece_keys) - len(existing_keys) + 1))
            ):
                cleaned = piece_words[:]
                continue
        # Strip edge overlap: ... SANTOS + SANTOS DELA → ... SANTOS DELA
        if existing_keys and piece_keys:
            max_overlap = min(len(existing_keys), len(piece_keys))
            for n in range(max_overlap, 0, -1):
                if existing_keys[-n:] == piece_keys[:n]:
                    piece_words = piece_words[n:]
                    piece_keys = piece_keys[n:]
                    break
        if not piece_words:
            continue
        cleaned.extend(piece_words)
    # Drop consecutive duplicate tokens.
    out: List[str] = []
    for w in cleaned:
        if not out or _name_keys([out[-1]]) != _name_keys([w]):
            out.append(w)
    return " ".join(out).strip()


def _collapse_doubled_person_name(name: str) -> str:
    """Remove exact/partial doubled person names from box OCR."""
    words = [w for w in re.sub(r"\s+", " ", (name or "").strip()).split() if w]
    n = len(words)
    if n < 4:
        return " ".join(words)

    keys = _name_keys(words)
    if n % 2 == 0:
        half = n // 2
        if half >= 2 and keys[:half] == keys[half:]:
            return " ".join(words[:half])
    for k in range(n // 2, 1, -1):
        if keys[:k] == keys[n - k :]:
            return " ".join(words[: n - k])
    return " ".join(words)


def extract_field_boxes(
    img,
    doc_type: str,
    items: Optional[List[Dict[str, Any]]] = None,
    pages=None,
) -> Dict[str, str]:
    """
    Fill write-in boxes from OCR tokens.

    Pass `items` or `pages` from the main extractor to avoid a second PaddleOCR call.
    Coordinates on items/pages must match `img`.
    """
    spec = _load_spec(doc_type)
    if spec is None or img is None:
        return {}
    template = spec["_image"]
    th, tw = template.shape[:2]
    sh, sw = img.shape[:2]
    homography = _find_homography(img, spec)

    if items is None:
        if pages is not None:
            items = _items_from_pages(pages, sw, sh)
        else:
            # Fallback only: one page OCR on the aligned image.
            if homography is not None:
                aligned = cv2.warpPerspective(img, homography, (tw, th))
            else:
                aligned = cv2.resize(img, (tw, th), interpolation=cv2.INTER_AREA)
            pages = run_ocr_on_image(aligned)
            items_template = _items_from_pages(pages, tw, th)
            return _fill_boxes(spec, items_template)

    items_template = _project_items_to_template(items, homography, sw, sh, tw, th)
    return _fill_boxes(spec, items_template)


# Keys shown on OCR review metadata forms (not every printed box on the certificate).
_FORM_METADATA_KEYS = {
    "birth": [
        "Registry Number",
        "Date of Registration",
        "Name of Child",
        "Sex",
        "Date of Birth",
        "Place of Birth",
        "Name of Mother",
        "Citizenship of Mother",
        "Name of Father",
        "Citizenship of Father",
        "Date of Marriage of Parents",
        "Place of Marriage of Parents",
        "Remarks",
    ],
    "marriage": [
        "Registry Number",
        "Date of Registration",
        "Date of Marriage",
        "Place of Marriage",
        "Husband Name",
        "Husband Age",
        "Husband Citizenship",
        "Husband Civil Status",
        "Husband Father",
        "Husband Mother",
        "Wife Name",
        "Wife Age",
        "Wife Citizenship",
        "Wife Civil Status",
        "Wife Father",
        "Wife Mother",
    ],
    "death": [
        "Registry Number",
        "Date of Registration",
        "Name of Deceased",
        "Sex",
        "Age",
        "Civil Status",
        "Nationality",
        "Date of Death",
        "Place of Death",
        "Cause of Death",
    ],
}

_BOX_KEY_TO_FORM = {
    "Husband First": "Husband Name",
    "Husband Middle": "Husband Name",
    "Husband Last": "Husband Name",
    "Wife First": "Wife Name",
    "Wife Middle": "Wife Name",
    "Wife Last": "Wife Name",
}


def _union_box(a: List[float], b: List[float]) -> List[float]:
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _boxes_should_merge(a: List[float], b: List[float]) -> bool:
    """Join stacked write-ins (cause a/b/c). Keep far-apart cells (two Date lines) separate."""
    overlap_x = min(a[2], b[2]) - max(a[0], b[0])
    overlap_y = min(a[3], b[3]) - max(a[1], b[1])
    if overlap_x > 0 and overlap_y > 0:
        return True
    min_w = min(a[2] - a[0], b[2] - b[0])
    if overlap_x < 0.45 * max(0.01, min_w):
        return False
    if a[3] <= b[1]:
        gap = b[1] - a[3]
    elif b[3] <= a[1]:
        gap = a[1] - b[3]
    else:
        gap = 0.0
    return gap <= 0.02


def metadata_field_guides(doc_type: str) -> List[Dict[str, Any]]:
    """Normalized write-in boxes for the metadata form of one certificate type."""
    key = (doc_type or "").strip().lower()
    wanted = _FORM_METADATA_KEYS.get(key)
    filename = _DOC_FILES.get(key)
    if not wanted or not filename:
        return []
    path = TEMPLATE_DIR / filename
    if not path.exists():
        return []
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    grouped: Dict[str, List[List[float]]] = {}
    for field in spec.get("fields") or []:
        raw_key = str(field.get("key") or "").strip()
        form_key = _BOX_KEY_TO_FORM.get(raw_key, raw_key)
        if form_key not in wanted:
            continue
        box = field.get("box") or []
        if not isinstance(box, list) or len(box) != 4:
            continue
        try:
            coords = [max(0.0, min(1.0, float(n))) for n in box]
        except (TypeError, ValueError):
            continue
        if coords[2] <= coords[0] or coords[3] <= coords[1]:
            continue
        buckets = grouped.setdefault(form_key, [])
        merged = False
        for i, existing in enumerate(buckets):
            if _boxes_should_merge(existing, coords):
                buckets[i] = _union_box(existing, coords)
                merged = True
                break
        if not merged:
            buckets.append(coords)
    guides = []
    for form_key in wanted:
        for box in grouped.get(form_key) or []:
            x1, y1, x2, y2 = box
            guides.append(
                {
                    "key": form_key,
                    "left": round(x1, 4),
                    "top": round(y1, 4),
                    "width": round(x2 - x1, 4),
                    "height": round(y2 - y1, 4),
                }
            )
    return guides


def all_metadata_field_guides() -> Dict[str, List[Dict[str, Any]]]:
    return {name: metadata_field_guides(name) for name in _FORM_METADATA_KEYS}


def _form_frame(bgr) -> Tuple[int, int, int, int]:
    """Pixel rectangle of the printed certificate inside a scan (margins excluded)."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((11, 11), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    img_area = float(max(1, w * h))
    best = None
    best_area = 0.0
    for contour in contours:
        x, y, cw, ch = cv2.boundingRect(contour)
        area = float(cw * ch)
        if area < 0.40 * img_area or area > 0.995 * img_area:
            continue
        if area > best_area:
            best_area = area
            best = (int(x), int(y), int(x + cw), int(y + ch))
    if best is None:
        return (0, 0, w, h)
    return best


def align_field_guides_to_image(doc_type: str, image_path: str) -> List[Dict[str, Any]]:
    """Map template write-in boxes onto this scan, including photo margins."""
    guides = metadata_field_guides(doc_type)
    if not guides:
        return []
    img = cv2.imread(str(image_path))
    if img is None:
        return guides
    height, width = img.shape[:2]
    if width < 32 or height < 32:
        return guides
    x1, y1, x2, y2 = _form_frame(img)
    frame_w = max(1, x2 - x1)
    frame_h = max(1, y2 - y1)
    if frame_w / float(width) >= 0.92 and frame_h / float(height) >= 0.92:
        return guides
    aligned: List[Dict[str, Any]] = []
    for field in guides:
        gx1 = x1 + float(field["left"]) * frame_w
        gy1 = y1 + float(field["top"]) * frame_h
        gx2 = x1 + (float(field["left"]) + float(field["width"])) * frame_w
        gy2 = y1 + (float(field["top"]) + float(field["height"])) * frame_h
        left = max(0.0, min(0.99, gx1 / float(width)))
        top = max(0.0, min(0.99, gy1 / float(height)))
        right = max(left + 0.01, min(1.0, gx2 / float(width)))
        bottom = max(top + 0.008, min(1.0, gy2 / float(height)))
        aligned.append(
            {
                "key": field["key"],
                "left": round(left, 4),
                "top": round(top, 4),
                "width": round(right - left, 4),
                "height": round(bottom - top, 4),
            }
        )
    return aligned
