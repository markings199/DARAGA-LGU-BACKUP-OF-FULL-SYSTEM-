"""
Marriage certificate OCR extraction.
Fixed implementation v2 - addresses name extraction issues.
"""
import re
import difflib
from datetime import datetime
from typing import Dict, Any, List

import cv2
import numpy as np
from ocr.shared import run_ocr_on_image, run_ocr_text_only
from ocr.engine import extract_from_ocr_text, ocr_pages_to_text
from ocr.field_boxes import extract_field_boxes
from ocr.sex import sex_from_ocr_text


# =========================================================
# CONFIG
# =========================================================
DEBUG_OCR_RAW = False
DEBUG_LABELS = False
DEBUG_PREPROCESS = False
DEBUG_NAME_BAND = False
DEBUG_DRAW_BOXES = False
DEBUG_TEMPLATE_MATCH = False


# =========================================================
# PREPROCESS
# =========================================================
def preprocess_image(img):
    """Light contrast only. Do not pad or enlarge the page — that makes PaddleOCR slower."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.10, beta=6)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


# =========================================================
# OCR DEBUG VISUALIZER
# =========================================================
def draw_debug_boxes(img, items):
    """Draw OCR bounding boxes and text."""
    debug_img = img.copy()
    for it in items:
        x1, y1, x2, y2 = it["x1"], it["y1"], it["x2"], it["y2"]
        cv2.rectangle(debug_img, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(debug_img, it["text"], (x1, max(15, y1 - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 0, 255), 1, cv2.LINE_AA)
    cv2.imwrite("debug_ocr_boxes.jpg", debug_img)
    print("\n✔ Debug OCR image saved: debug_ocr_boxes.jpg\n")


# =========================================================
# CONSTANTS
# =========================================================
MONTHS = {
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"
}

HELPER_WORDS = {
    "FIRST", "LAST", "MIDDLE", "INITIAL", "DAY", "MONTH", "YEAR",
    "AGE", "HUSBAND", "WIFE", "PARTIES", "PARTIEN", "PARTY",
    "MIDDLE INITIAL", "MIDDIE INITIAL", "MIDDLE INITLAL", "MIDDLE INLTIAL"
}

NAME_SLOT_LABELS = {
    "FIRST", "FIRT", "FIST", "FRST",
    "MIDDLE", "MIDLE", "MIDDDE", "MIDDELE", "MIDDEL", "MIDDLES",
    "LAST", "LST", "LASTT",
}

NAME_BANNED_WORDS = {
    "CITIZENSHIP", "PROVINCE", "PROVINCEO", "RELIGION", "RESIDENCE",
    "MALE", "FEMALE", "FILIPINO", "FILIPING", "OFFICE", "CIVIL",
    "REGISTRY", "REGISTRAR", "REGIS", "MUNICIPALITY", "MUNIEIPALITY",
    "CITY", "FORMNO", "FORM", "QUADRUPLICAIE", "ACCOMPLISHED",
    "AUGUST", "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER",
    "PLACE", "BIRTH", "DEATH", "MARRIAGE", "NO", "NUMBER",
    "REPUBLIC", "PHILIPPINES", "PHILIPPINE", "STATISTICS",
    "AUTHORITY", "GENERAL", "MANILA", "CERTIFICATE", "REGISTRATION",
    "COPY", "OFFICIAL", "LOCAL", "REGISTRATION", "CONTRACTING",
    "PARTIES", "HUSBAND", "WIFE", "AGE", "SEX", "STATUS",
}

NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V"}
NAME_PARTICLES = {"DE", "DEL", "DELA", "DA", "VAN", "VON", "SAN", "SANTA", "LA", "LAS", "LOS", "Y", "DI"}

CITIZENSHIP_CANON = {
    "FILIPINO": "FILIPINO",
    "FILPINO": "FILIPINO",
    "FLIPINO": "FILIPINO",
    "FILIPINA": "FILIPINO",
    "AMERICAN": "AMERICAN",
    "CHINESE": "CHINESE",
    "JAPANESE": "JAPANESE",
    "KOREAN": "KOREAN",
    "BRITISH": "BRITISH",
    "INDIAN": "INDIAN",
    "AUSTRALIAN": "AUSTRALIAN",
    "CANADIAN": "CANADIAN",
}

PLACE_LANDMARKS = (
    "QUEZON CITY", "CITY HALL", "MUNICIPAL HALL", "BARANGAY", "MANILA",
    "QUEZON", "MAKATI", "PASIG", "TAGUIG", "CALOOCAN", "PASAY", "MUNTINLUPA",
    "PARANAQUE", "PARAÑAQUE", "LAS PINAS", "LAS PIÑAS", "ERMITA", "CUBAO",
    "ALABANG", "CEBU", "DAVAO", "ALBAY", "CAMALIG", "DARAGA", "LEGAZPI",
    "STREET", "AVENUE", "ROAD", "CHURCH", "CATHEDRAL", "HALL",
)

COMMON_WORD_FIXES = {
    "FILPINO": "FILIPINO", "FLPINO": "FILIPINO", "FLIPINO": "FILIPINO",
    "FILIPING": "FILIPINO", "F ILIPINO": "FILIPINO",
    "MALEL": "MALE", "FEMALEL": "FEMALE",
    "ROMAN CATHOLIG": "ROMAN CATHOLIC", "CATHOLIG": "CATHOLIC",
}


# =========================================================
# HELPERS
# =========================================================
def clean_text(t: str) -> str:
    t = str(t).strip()
    t = t.replace("|", " ")
    t = re.sub(r"[._]{2,}", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def norm_text(t: str) -> str:
    t = clean_text(t).upper()
    t = re.sub(r"[^A-Z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def compact_text(t: str) -> str:
    return re.sub(r"\s+", "", norm_text(t))


def alpha_count(t: str) -> int:
    return sum(ch.isalpha() for ch in t)


def tokens_in_region(items, xmin, xmax, ymin, ymax, min_score=0.20):
    out = []
    for it in items:
        if it["score"] < min_score:
            continue
        if xmin <= it["cx"] <= xmax and ymin <= it["cy"] <= ymax:
            out.append(it)
    out.sort(key=lambda x: (x["cy"], x["x1"]))
    return out


def group_rows(tokens, y_tol=16):
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


def join_tokens(tokens):
    return " ".join(clean_text(t["text"]) for t in sorted(tokens, key=lambda x: x["x1"])).strip()


def apply_common_word_fix(text: str) -> str:
    nt = norm_text(text)
    return COMMON_WORD_FIXES.get(nt, nt)


def is_date_noise_token(nt: str) -> bool:
    if not nt:
        return False
    if re.search(r"\d", nt) and any(month[:3] in nt for month in MONTHS):
        return True
    if re.fullmatch(r"\d{1,2}[A-Z]{3,9}\d{0,4}", nt):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}(AM|PM)?", nt):
        return True
    if re.fullmatch(r"\d{4}", nt) and 1900 <= int(nt) <= 2100:
        return True
    return False


def _is_name_slot_label(nt: str) -> bool:
    token = re.sub(r"[^A-Z]", "", nt or "")
    if not token:
        return False
    if token in NAME_SLOT_LABELS:
        return True
    return bool(difflib.get_close_matches(token, ["FIRST", "MIDDLE", "LAST"], n=1, cutoff=0.72))


def sanitize_person_name(name: str) -> str:
    """Drop OCR junk, dates, and form words so only a person name remains."""
    parts = []
    for raw in re.sub(r"[:;|]+", " ", clean_text(name)).split():
        nt = norm_text(raw)
        if not nt or is_helper_token(raw) or is_form_metadata(raw):
            continue
        if _is_name_slot_label(nt):
            continue
        if is_date_noise_token(nt) or nt in NAME_BANNED_WORDS:
            continue
        if re.fullmatch(r"\d+", nt):
            continue
        mixed = raw != raw.upper() and raw != raw.lower() and raw != raw.title()
        if mixed and alpha_count(raw) <= 3:
            continue
        if re.fullmatch(r"[A-Z]\.?", nt) or nt in NAME_SUFFIXES or nt in NAME_PARTICLES:
            parts.append(nt if nt in NAME_SUFFIXES or nt in NAME_PARTICLES else raw.upper().rstrip("."))
            continue
        if alpha_count(raw) < 3:
            continue
        parts.append(raw.upper())
    while parts and norm_text(parts[0]) not in NAME_PARTICLES and alpha_count(parts[0]) < 3:
        parts.pop(0)
    while parts and norm_text(parts[-1]) not in NAME_SUFFIXES | NAME_PARTICLES and alpha_count(parts[-1]) < 3:
        parts.pop()
    return collapse_repeated_name(" ".join(parts).strip())


def collapse_repeated_name(name: str) -> str:
    """
    Undo doubled OCR names, e.g.
      MIGUEL SANTOS REYES MIGUEL SANTOS REYES → MIGUEL SANTOS REYES
      ANGELICA FERNANDEZ DELA PENA ANGELICA FERNANDEZ → ANGELICA FERNANDEZ DELA PENA
      KEVIN SANTOS SANTOS DELA TORRE → KEVIN SANTOS DELA TORRE
    """
    words = [w for w in re.sub(r"\s+", " ", str(name or "").strip()).split() if w]
    if not words:
        return ""

    def _key(seq):
        return [norm_text(w) for w in seq]

    # Always drop consecutive duplicate tokens (middle name echoed twice).
    deduped = [words[0]]
    for w in words[1:]:
        if norm_text(w) != norm_text(deduped[-1]):
            deduped.append(w)
    words = deduped
    n = len(words)
    if n < 4:
        return " ".join(words)

    # Exact half duplicate: ABC ABC
    if n % 2 == 0:
        half = n // 2
        if half >= 2 and _key(words[:half]) == _key(words[half:]):
            return " ".join(words[:half])

    # Longest prefix that is repeated as the suffix (length >= 2).
    for k in range(n // 2, 1, -1):
        if _key(words[:k]) == _key(words[n - k :]):
            return " ".join(words[: n - k])

    # Contiguous repeated block of length >= 2 (AB AB C → AB C).
    out = []
    i = 0
    while i < n:
        matched = False
        max_block = min((n - i) // 2, 4)
        for k in range(max_block, 1, -1):
            if _key(words[i : i + k]) == _key(words[i + k : i + 2 * k]):
                out.extend(words[i : i + k])
                i += 2 * k
                matched = True
                break
        if not matched:
            out.append(words[i])
            i += 1
    return " ".join(out)


def compose_full_name(*parts: str) -> str:
    """Join First, Middle, Last in form order. Skip empty, duplicate, or already-contained chunks."""
    cleaned: List[str] = []
    for raw in parts:
        piece = sanitize_person_name(raw or "")
        if not piece:
            continue
        piece_words = piece.split()
        existing = " ".join(cleaned).split()
        existing_keys = [norm_text(w) for w in existing]
        piece_keys = [norm_text(w) for w in piece_words]

        # Skip if this piece already appears as a contiguous run in the name so far.
        if piece_keys and any(
            existing_keys[i : i + len(piece_keys)] == piece_keys
            for i in range(0, max(1, len(existing_keys) - len(piece_keys) + 1))
        ):
            continue

        # If the new piece already contains the whole name so far, replace with the fuller piece.
        if existing_keys and len(piece_keys) > len(existing_keys):
            if any(
                piece_keys[i : i + len(existing_keys)] == existing_keys
                for i in range(0, max(1, len(piece_keys) - len(existing_keys) + 1))
            ):
                cleaned = piece_words[:]
                continue

        # Drop overlapping prefix of the new piece that already ends the name
        # (First=KEVIN SANTOS + Last=SANTOS DELA TORRE → KEVIN SANTOS DELA TORRE).
        if existing_keys and piece_keys:
            max_overlap = min(len(existing_keys), len(piece_keys))
            for n in range(max_overlap, 0, -1):
                if existing_keys[-n:] == piece_keys[:n]:
                    piece_words = piece_words[n:]
                    piece_keys = piece_keys[n:]
                    break
        if not piece_words:
            continue

        key = norm_text(" ".join(piece_words))
        if key in {norm_text(c) for c in cleaned}:
            continue
        cleaned.append(" ".join(piece_words))
    return collapse_repeated_name(" ".join(cleaned).strip())


def prefer_fuller_name(current: str, incoming: str) -> str:
    """Keep the value that looks like First + Middle + Last, not last name only."""
    a = sanitize_person_name(current or "")
    b = sanitize_person_name(incoming or "")
    if not a:
        return b
    if not b:
        return a
    # Prefer non-doubled / shorter when both encode the same person.
    if collapse_repeated_name(a) == collapse_repeated_name(b):
        return a if len(a.split()) <= len(b.split()) else b
    if len(b.split()) != len(a.split()):
        return b if len(b.split()) > len(a.split()) else a
    return b if len(b) > len(a) else a


def extract_stacked_fml_name(items, x_min, x_max, y_min, y_max) -> str:
    """
    Form fills that stack:
      First:  Kevin
      Middle: Santos
      Last:   Dela Torre
    Read each label and take the value to its right (First → Middle → Last).
    """
    region = [
        it for it in items
        if it.get("score", 0) >= 0.12
        and x_min <= it["cx"] <= x_max
        and y_min <= it["cy"] <= y_max
    ]
    if not region:
        return ""

    def _find_slot(label_words):
        best = None
        best_score = -1.0
        for it in region:
            nt = norm_text(it["text"]).rstrip(":")
            if nt in label_words or any(nt == w or nt.startswith(w) for w in label_words):
                if it["score"] > best_score:
                    best = it
                    best_score = it["score"]
        return best

    parts = []
    for labels in (("FIRST", "FIRT", "FRST"), ("MIDDLE", "MIDLE", "MIDDDE"), ("LAST", "LST")):
        lab = _find_slot(labels)
        if not lab:
            parts.append("")
            continue
        # Value sits on the same row, to the right of the label.
        vals = [
            it for it in region
            if abs(it["cy"] - lab["cy"]) <= max(10.0, (lab["y2"] - lab["y1"]) * 0.9)
            and it["cx"] > lab["cx"] + 4
            and norm_text(it["text"]).rstrip(":") not in {"FIRST", "MIDDLE", "LAST"}
        ]
        vals.sort(key=lambda it: it["x1"])
        chunk = []
        for it in vals:
            t = clean_text(it["text"])
            nt = norm_text(t)
            if not t or is_helper_token(t) or nt in NAME_BANNED_WORDS:
                continue
            if alpha_count(t) < 2:
                continue
            chunk.append(t)
        parts.append(" ".join(chunk))
    if sum(1 for p in parts if p) >= 2:
        return compose_full_name(*parts)
    return ""


def _name_tokens_to_full(tokens, x_min, x_max) -> str:
    """
    Split one spouse column into First | Middle | Last (Municipal Form 97).

    Some scans write First/Middle/Last in three horizontal columns; this template
    (and many local fills) stack them vertically — First on top, then Middle, then Last.
    """
    usable = []
    for it in tokens:
        t = clean_text(it["text"])
        nt = norm_text(t)
        if not t or is_helper_token(t) or is_form_metadata(t):
            continue
        if re.fullmatch(r"\d+", nt) or is_date_noise_token(nt):
            continue
        if nt in {"FIRST", "MIDDLE", "LAST", "NAME", "HUSBAND", "WIFE", "INITIAL"} or _is_name_slot_label(nt):
            continue
        if nt in NAME_BANNED_WORDS:
            continue
        if alpha_count(t) < 2:
            continue
        usable.append((it, t))

    if not usable:
        return ""

    xs = [it["cx"] for it, _ in usable]
    ys = [it["cy"] for it, _ in usable]
    x_span = max(xs) - min(xs) if xs else 0.0
    y_span = max(ys) - min(ys) if ys else 0.0
    buckets: List[List[str]] = [[], [], []]

    sorted_y = sorted(ys)
    row_gaps = [sorted_y[i + 1] - sorted_y[i] for i in range(len(sorted_y) - 1)]
    has_stacked_rows = y_span >= 18 and sum(1 for g in row_gaps if g >= 10) >= 1

    # Vertical First / Middle / Last (stacked rows) — order by top→bottom.
    if has_stacked_rows and y_span >= x_span * 0.55:
        y0, y1 = min(ys), max(ys)
        height = max(1.0, y1 - y0)
        for it, t in sorted(usable, key=lambda p: p[0]["cy"]):
            rel = (it["cy"] - y0) / height
            rel = min(0.999, max(0.0, rel))
            buckets[0 if rel < 0.34 else 1 if rel < 0.67 else 2].append(t)
    else:
        # Horizontal First | Middle | Last — order by left→right.
        width = max(1.0, float(x_max) - float(x_min))
        for it, t in sorted(usable, key=lambda p: p[0]["x1"]):
            rel = (it["cx"] - x_min) / width
            rel = min(0.999, max(0.0, rel))
            buckets[0 if rel < 0.34 else 1 if rel < 0.67 else 2].append(t)

    return compose_full_name(
        " ".join(buckets[0]),
        " ".join(buckets[1]),
        " ".join(buckets[2]),
    )


_ADDRESS_TOKENS = {
    "BLOCK", "LOT", "PUROK", "BARANGAY", "BRGY", "STREET", "HOMES",
    "SUBDIVISION", "VILLAGE", "SITIO", "PHASE", "ROAD", "AVENUE",
    "CAMELLA", "HOUSE", "BLDG", "BUILDING",
}


def normalize_citizenship_value(text: str) -> str:
    nt = compact_text(text)
    if not nt:
        return ""
    upper = norm_text(text)
    words = [w for w in upper.split() if w not in {"SEX", "MALE", "FEMALE", "CITIZENSHIP"}]
    for key, canon in CITIZENSHIP_CANON.items():
        key_n = norm_text(key)
        if key_n and re.search(rf"\b{re.escape(key_n)}\b", upper):
            return canon
    if any(word in _ADDRESS_TOKENS for word in words):
        return ""
    if re.search(r"\b(MALE|FEMALE|SEX)\b", upper) or sex_from_ocr_text(text):
        return ""
    if len(words) != 1:
        return ""
    match = difflib.get_close_matches(
        compact_text(words[0]), [compact_text(k) for k in CITIZENSHIP_CANON], n=1, cutoff=0.78
    )
    if match:
        for key, canon in CITIZENSHIP_CANON.items():
            if compact_text(key) == match[0]:
                return canon
    cleaned = apply_common_word_fix(words[0])
    return cleaned if alpha_count(cleaned) >= 4 else ""


def normalize_month_word(word: str) -> str:
    w = norm_text(word)
    if w in MONTHS:
        return w
    if len(w) < 3:
        return w
    if len(w) >= 2 and w[0] == w[1]:
        if w[1:] in MONTHS:
            return w[1:]
    squashed = re.sub(r"(.)\1+", r"\1", w)
    if squashed in MONTHS:
        return squashed
    match = difflib.get_close_matches(w, list(MONTHS), n=1, cutoff=0.70)
    if match:
        return match[0]
    match = difflib.get_close_matches(squashed, list(MONTHS), n=1, cutoff=0.70)
    if match:
        return match[0]
    return w


def normalize_sex_value(text: str) -> str:
    return sex_from_ocr_text(text)


def pattern_score(text: str, patterns):
    nt = norm_text(text)
    ct = compact_text(text)
    best = 0.0
    for pat in patterns:
        p = norm_text(pat)
        pc = compact_text(pat)
        if all(tok in nt for tok in p.split()):
            score = 10.0 + len(p.split())
            best = max(best, score)
            continue
        ratio = difflib.SequenceMatcher(None, ct, pc).ratio()
        if ratio >= 0.62:
            best = max(best, ratio * 10.0)
    return best


def find_best_label(items, patterns, x_min=None, x_max=None, y_min=None, y_max=None,
                    forbidden=None, min_score=0.20):
    forbidden = forbidden or []
    best = None
    best_tuple = None
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
        nt = norm_text(it["text"])
        if any(f.upper() in nt for f in forbidden):
            continue
        ps = pattern_score(it["text"], patterns)
        if ps <= 0:
            continue
        tup = (ps, it["score"], -len(nt))
        if best is None or tup > best_tuple:
            best = it
            best_tuple = tup
    return best


def find_label_below(items, anchor_label, patterns, y_min_offset=2, y_max_offset=70, x_max=None):
    if not anchor_label:
        return None
    return find_best_label(
        items, patterns=patterns,
        x_min=0, x_max=x_max,
        y_min=anchor_label["y2"] + y_min_offset,
        y_max=anchor_label["y2"] + y_max_offset,
        min_score=0.20
    )


def infer_working_xmax(items, img_w, img_h):
    blockers = [
        find_best_label(items, ["FOR OCRG USE ONLY"], x_min=img_w * 0.55, y_min=0, y_max=img_h * 0.30),
        find_best_label(items, ["REMARKS ANNOTATION"], x_min=img_w * 0.55, y_min=0, y_max=img_h * 0.15),
        find_best_label(items, ["POPULATION REFERENCE NO"], x_min=img_w * 0.55, y_min=0, y_max=img_h * 0.20),
    ]
    xs = [b["x1"] for b in blockers if b is not None]
    if xs:
        return min(xs) - 20
    return int(img_w * 0.70)


def is_helper_token(text: str) -> bool:
    raw = clean_text(text)
    nt = norm_text(raw)
    if "(" in raw or ")" in raw:
        return True
    # "AGE 31" / "31AGE" is the write-in, not the printed header.
    if re.search(r"\d", nt) and "AGE" in nt:
        return False
    for hw in HELPER_WORDS:
        if hw in nt:
            return True
    junk = {"RNSTIAL", "INLTIAL", "INITLAL", "INTAL", "ISITAL",
            "RUTAL", "AWUT", "AMTL", "TRAT", "FORE", "LAMT", "TRL"}
    if any(j in nt for j in junk):
        return True
    return False


def is_form_metadata(text: str) -> bool:
    """Check if text appears to be form metadata/boilerplate"""
    raw = clean_text(text)
    nt = norm_text(raw)
    form_patterns = [
        r"^FORM\s*NO\.?\d+", r"^FORMNO\.?\d+", r"FORMNO\d+",
        r"CERTIFICATE\s*NO", r"REGISTRATION\s*NO", r"MUNICIPAL\s*FORM",
        r"MANILA", r"OFFICE\s*OF\s*THE", r"REPUBLIC\s*OF", r"PHILIPPINE",
        r"STATISTICS", r"CIVIL\s*REGISTRAR",
    ]
    for pattern in form_patterns:
        if re.search(pattern, nt, re.IGNORECASE):
            return True
    if len(nt) <= 2 and not re.match(r"^[A-Z]\.?$", nt):
        return True
    return False


def infer_column_split_from_headers(items, name_label, working_xmax, img_w, img_h):
    husband_marker = find_best_label(
        items, ["HUSBAND"],
        x_min=name_label["x2"] if name_label else img_w * 0.15,
        x_max=working_xmax, y_min=img_h * 0.06, y_max=img_h * 0.18
    )
    wife_marker = find_best_label(
        items, ["WIFE"],
        x_min=name_label["x2"] if name_label else img_w * 0.30,
        x_max=working_xmax, y_min=img_h * 0.06, y_max=img_h * 0.18
    )
    if husband_marker and wife_marker and husband_marker["cx"] < wife_marker["cx"]:
        return (husband_marker["cx"] + wife_marker["cx"]) / 2.0
    return None


def infer_column_split_from_name(items, name_label, working_xmax, img_w):
    if not name_label:
        return img_w * 0.48
    row_tokens = tokens_in_region(
        items, name_label["x2"] + 5, working_xmax,
        name_label["y1"] - 18, name_label["y2"] + 50, min_score=0.15
    )
    xs = []
    for it in row_tokens:
        if is_helper_token(it["text"]):
            continue
        if alpha_count(it["text"]) < 2:
            continue
        xs.append(it["cx"])
    if len(xs) < 2:
        return (name_label["x2"] + working_xmax) / 2.0
    c1, c2 = min(xs), max(xs)
    for _ in range(12):
        g1, g2 = [], []
        for x in xs:
            if abs(x - c1) <= abs(x - c2):
                g1.append(x)
            else:
                g2.append(x)
        if not g1 or not g2:
            break
        new_c1, new_c2 = sum(g1) / len(g1), sum(g2) / len(g2)
        if abs(new_c1 - c1) < 0.5 and abs(new_c2 - c2) < 0.5:
            c1, c2 = new_c1, new_c2
            break
        c1, c2 = new_c1, new_c2
    if c1 > c2:
        c1, c2 = c2, c1
    return (c1 + c2) / 2.0


def split_left_right(tokens, split_x, label_x2=0):
    left, right = [], []
    for it in tokens:
        if it["x2"] <= label_x2:
            continue
        if is_helper_token(it["text"]):
            continue
        if it["cx"] < split_x:
            left.append(it)
        else:
            right.append(it)
    left.sort(key=lambda x: x["x1"])
    right.sort(key=lambda x: x["x1"])
    return left, right


def normalize_name(tokens):
    parts = []
    for it in sorted(tokens, key=lambda x: x["x1"]):
        t = clean_text(it["text"])
        nt = norm_text(t)
        if not t or is_helper_token(t) or is_form_metadata(t):
            continue
        if re.fullmatch(r"\d+", nt):
            continue
        if alpha_count(t) < 2 and not re.fullmatch(r"[A-Z]\.?", t, re.I):
            continue
        if nt in NAME_BANNED_WORDS:
            continue
        if any(re.search(r"\b" + re.escape(b) + r"\b", nt) for b in NAME_BANNED_WORDS):
            continue
        parts.append(t)

    name = " ".join(parts).strip()
    header_phrases = [
        "OFFICE OF THE CIVIL REGISTRAR GENERAL", "OFFICE OF THE CIVIL REGISTRAR",
        "REPUBLIC OF THE PHILIPPINES", "PHILIPPINE STATISTICS AUTHORITY",
        "PHILIPPINE STATISTICAL AUTHORITY", "REPUBLIC OF PHILIPPINES",
        "CITIZENSHIP", "PROVINCE", "RELIGION", "RESIDENCE", "MALE", "FEMALE",
        "FILIPINO", "MUNICIPALITY", "PLACE OF BIRTH",
    ]
    for banned in header_phrases:
        name = re.sub(r"\b" + re.escape(banned) + r"\b", " ", name, flags=re.I)
    name = re.sub(r"\bFORM\s*NO\.?\s*\d+\b", " ", name, flags=re.I)
    name = re.sub(r"\bMUNICIPAL\s+FORM\s*NO\.?\s*\d+\b", " ", name, flags=re.I)
    name = re.sub(r"\bREGISTRY\s*NO\.?\s*\d+\b", " ", name, flags=re.I)
    name = re.sub(r"\bFORMNO\.?\s*\d+\b", " ", name, flags=re.I)
    name = re.sub(r"\bSAN\s+N\s+ANDRES\b", "SAN ANDRES", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip()
    return sanitize_person_name(name)


def normalize_simple_row(tokens, banned=None):
    banned = banned or set()
    parts = []
    for it in sorted(tokens, key=lambda x: x["x1"]):
        t = clean_text(it["text"])
        nt = norm_text(t)
        if not t or is_helper_token(t) or nt in banned:
            continue
        parts.append(t)
    out = " ".join(parts).strip()
    return apply_common_word_fix(out)


def extract_band_tokens(items, label_item, working_xmax, next_label=None, extra_bottom=26, extra_top=8, min_score=0.20):
    if not label_item:
        return []
    ymin = label_item["y1"] - extra_top
    if next_label:
        ymax = min(label_item["y2"] + extra_bottom, next_label["y1"] - 6)
    else:
        ymax = label_item["y2"] + extra_bottom
    return tokens_in_region(
        items, label_item["x2"] + 6, working_xmax,
        ymin, ymax, min_score=min_score
    )


def extract_two_side_band(items, label_item, working_xmax, split_x, next_label=None, normalizer="simple", banned=None, extra_bottom=26):
    if not label_item:
        return "", ""
    band_tokens = extract_band_tokens(items, label_item, working_xmax, next_label=next_label, extra_bottom=extra_bottom)
    left, right = split_left_right(band_tokens, split_x, label_item["x2"])
    banned = banned or set()
    if normalizer == "name":
        return normalize_name(left), normalize_name(right)
    return normalize_simple_row(left, banned=banned), normalize_simple_row(right, banned=banned)


def parse_day_number(token: str):
    digits = re.sub(r"\D", "", token)
    if not digits:
        return None
    candidates = []
    if 1 <= int(digits) <= 31:
        candidates.append(int(digits))
    if len(digits) >= 2:
        first2 = int(digits[:2])
        if 1 <= first2 <= 31:
            candidates.append(first2)
    if len(digits) >= 2:
        last2 = int(digits[-2:])
        if 1 <= last2 <= 31:
            candidates.append(last2)
    first1 = int(digits[0])
    if 1 <= first1 <= 9:
        candidates.append(first1)
    if not candidates:
        return None
    for val in candidates:
        if val >= 10:
            return val
    return candidates[0]


def parse_date_from_string(text: str) -> str:
    nt = norm_text(text)
    if not nt:
        return ""
    tokens = re.findall(r"\d+|[A-Z]+", nt)
    if not tokens:
        return ""
    month_idx, month_val = None, None
    for i, tok in enumerate(tokens):
        m = normalize_month_word(tok)
        if m in MONTHS:
            month_idx, month_val = i, m
            break
    if month_idx is None:
        return ""
    year = None
    for tok in tokens[month_idx + 1:]:
        if re.fullmatch(r"\d{4}", tok):
            year = tok
            break
    if year is None:
        for tok in tokens:
            if re.fullmatch(r"\d{4}", tok):
                year = tok
                break
    if year is None:
        return ""
    day = None
    for tok in reversed(tokens[:month_idx]):
        parsed = parse_day_number(tok)
        if parsed is not None:
            day = parsed
            break
    if day is None:
        for tok in tokens[month_idx + 1:]:
            if tok == year:
                continue
            parsed = parse_day_number(tok)
            if parsed is not None:
                day = parsed
                break
    if day is None:
        return ""
    return f"{day:02d} {month_val} {year}"


def extract_date_candidates(tokens):
    rows = group_rows(tokens, y_tol=18)
    found = []
    for row in rows:
        row = sorted(row, key=lambda x: x["x1"])
        for it in row:
            parsed = parse_date_from_string(it["text"])
            if parsed:
                found.append({"text": parsed, "x1": it["x1"], "y1": it["y1"]})
        for window in range(2, 6):
            for i in range(len(row) - window + 1):
                combined = " ".join(clean_text(row[j]["text"]) for j in range(i, i + window))
                parsed = parse_date_from_string(combined)
                if parsed:
                    found.append({"text": parsed, "x1": row[i]["x1"],
                                  "y1": min(row[j]["y1"] for j in range(i, i + window))})
    unique, seen = [], set()
    for d in sorted(found, key=lambda z: (z["x1"], z["y1"], z["text"])):
        key = (d["text"], round(d["x1"] / 8), round(d["y1"] / 8))
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def parse_named_date(date_str):
    if not date_str:
        return None
    parsed = parse_date_from_string(date_str)
    text = norm_text(parsed or date_str)
    parts = text.split()
    if len(parts) >= 3:
        day, month, year = parts[0], normalize_month_word(parts[1]), parts[2]
        if re.fullmatch(r"\d{1,2}", day) and month in MONTHS and re.fullmatch(r"\d{4}", year):
            try:
                return datetime.strptime(f"{int(day):02d} {month} {year}", "%d %B %Y")
            except Exception:
                pass
    m = re.search(r"\b(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})\b", str(date_str))
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            if a > 12:
                return datetime(year, b, a)
            return datetime(year, a, b)
        except Exception:
            try:
                return datetime(year, b, a)
            except Exception:
                return None
    return None


def compute_age_from_dates(dob_str, marriage_date_str):
    dob_dt = parse_named_date(dob_str)
    mar_dt = parse_named_date(marriage_date_str)
    if not dob_dt or not mar_dt:
        return ""
    age = mar_dt.year - dob_dt.year
    if (mar_dt.month, mar_dt.day) < (dob_dt.month, dob_dt.day):
        age -= 1
    if 12 <= age <= 99:
        return str(age)
    return ""


_BIRTH_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_AGE_DIGIT_MAP = str.maketrans({
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1", "|": "1", "!": "1",
    "Z": "2",
    "S": "5",
    "B": "8",
    "G": "6",
})


def _is_plausible_marriage_age(val) -> bool:
    try:
        n = int(val)
    except (TypeError, ValueError):
        return False
    return 12 <= n <= 99


def _parse_age_value(text: str) -> str:
    """Read Form 97 item 2b Age, including OCR digit confusions (3I→31, 2B→28)."""
    if not text:
        return ""
    nt = norm_text(text)
    nt = re.sub(r"\b(?:AGE|YEARS?|YRS|YO|Y/O)\b", " ", nt)
    if _BIRTH_YEAR_RE.search(nt) and not re.fullmatch(r"\d{1,3}", nt):
        nt = _BIRTH_YEAR_RE.sub(" ", nt)
    nt = re.sub(r"\s+", " ", nt).strip()
    if not nt:
        return ""
    if re.fullmatch(r"\d{1,3}", nt):
        return nt if _is_plausible_marriage_age(nt) else ""
    compact = re.sub(r"[^A-Z0-9]", "", nt)
    if 1 <= len(compact) <= 3:
        mapped = compact.translate(_AGE_DIGIT_MAP)
        if re.fullmatch(r"\d{1,3}", mapped) and _is_plausible_marriage_age(mapped):
            return str(int(mapped))
    hits = []
    for m in re.finditer(r"(?<!\d)(\d{1,3})(?!\d)", nt):
        if _is_plausible_marriage_age(m.group(1)):
            hits.append(int(m.group(1)))
    if hits:
        typical = [v for v in hits if 14 <= v <= 80]
        return str((typical or hits)[-1])
    return ""


def _is_birth_year_token(text: str) -> bool:
    nt = norm_text(text or "")
    if _BIRTH_YEAR_RE.search(nt):
        return True
    mapped = re.sub(r"[^A-Z0-9]", "", nt).translate(_AGE_DIGIT_MAP)
    return bool(re.fullmatch(r"(?:19|20)\d{2}", mapped))


def _small_age(text: str):
    parsed = _parse_age_value(text)
    return [int(parsed)] if parsed else []


def _is_age_header(text: str) -> bool:
    return bool(re.fullmatch(r"(?:2B)?AGE", norm_text(text or "")))


def _is_day_header(text: str) -> bool:
    nt = norm_text(text or "")
    return nt in {"DAY", "2A", "2A DAY"} or bool(re.fullmatch(r"2A?DAY", nt))


def _birth_year_value(text: str) -> int:
    m = _BIRTH_YEAR_RE.search(norm_text(text or ""))
    if m:
        return int(m.group(1))
    mapped = re.sub(r"[^A-Z0-9]", "", norm_text(text or "")).translate(_AGE_DIGIT_MAP)
    m = re.fullmatch(r"((?:19|20)\d{2})", mapped)
    return int(m.group(1)) if m else 0


def _age_from_tokens(tokens, dob_text="") -> str:
    """Form 97: Age is only the cell after Year. Day (05 / 18 / 14 / 03) is never Age."""
    if not tokens:
        return ""
    years = [it for it in tokens if _is_birth_year_token(it["text"])]
    year_it = max(years, key=lambda it: it["cx"]) if years else None
    day = _dob_day_number(dob_text)
    hits = []
    for it in tokens:
        if _is_age_header(it["text"]) or _is_day_header(it["text"]):
            continue
        if year_it is not None and it["cx"] < year_it["x2"] - 1:
            continue
        if year_it is not None and it["x1"] > year_it["x2"] + max(it.get("w", 20) * 6, 80):
            continue
        parsed = _parse_age_value(it["text"])
        if not parsed:
            continue
        if day and str(int(parsed)) == day:
            continue
        hits.append((it["cx"], int(parsed)))
    if not hits:
        return ""
    hits.sort()
    return str(hits[0][1])


def _form97_dob_band(items, img_w, img_h, dob_label=None):
    """Item 2a/2b only: Day Month Year Age | Day Month Year Age."""
    y1 = img_h * 0.145
    y2 = img_h * 0.235
    if dob_label:
        y1 = max(img_h * 0.10, dob_label["y1"] - img_h * 0.01)
        y2 = min(img_h * 0.28, dob_label["y2"] + img_h * 0.09)
    band = tokens_in_region(items, img_w * 0.14, img_w * 0.999, y1, y2, min_score=0.05)
    years = []
    for it in sorted(band, key=lambda t: t["cx"]):
        year = _birth_year_value(it["text"])
        if 1935 <= year <= 2014:
            years.append(it)
    return y1, y2, years, band


def extract_form97_ages(items, img_w, img_h, split_x=None, dob_label=None, img=None):
    """
    Municipal Form 97 item 2 is always:

        Husband: Day | Month | Year | Age
        Wife:    Day | Month | Year | Age

    Husband Age is the last cell of the left half (after his birth year).
    Wife Age is the last cell of the right half (after her birth year).
    """
    mid = split_x if split_x else img_w * 0.515
    _y1, _y2, years, band = _form97_dob_band(items, img_w, img_h, dob_label)
    ordered = sorted(band, key=lambda it: (it["cx"], it["x1"]))
    hus_years = [it for it in years if it["cx"] < mid]
    wife_years = [it for it in years if it["cx"] >= mid]
    headers = [it for it in ordered if _is_age_header(it["text"])]
    hus_hdr = [h for h in headers if h["cx"] < mid]
    wife_hdr = [h for h in headers if h["cx"] >= mid]
    day_headers = [it for it in ordered if _is_day_header(it["text"])]

    def age_in_column(x1, x2, year_it=None):
        if x2 <= x1 + 6:
            return ""
        if year_it:
            year_nt = norm_text(year_it["text"])
            year_m = _BIRTH_YEAR_RE.search(year_nt)
            after_year = year_nt[year_m.end():] if year_m else ""
            same = _parse_age_value(after_year)
            if same:
                return same
        hits = []
        for it in ordered:
            if year_it is not None and it is year_it:
                continue
            if _is_age_header(it["text"]) or _is_day_header(it["text"]):
                continue
            if it["cx"] < x1 or it["cx"] > x2:
                continue
            if _is_birth_year_token(it["text"]):
                continue
            parsed = _parse_age_value(it["text"])
            if parsed:
                hits.append((it["cx"], int(parsed)))
        if not hits:
            return ""
        hits.sort()
        return str(hits[0][1])

    def column_right_of_year(year_it, x_limit):
        x1 = year_it["x2"] + img_w * 0.002
        x2 = min(x_limit, year_it["x2"] + img_w * 0.07)
        for hdr in day_headers:
            if hdr["cx"] > year_it["cx"] and hdr["x1"] < x2:
                x2 = min(x2, hdr["x1"] - 2)
        return age_in_column(x1, x2, year_it)

    def column_under_header(hdr, year_it, x_limit):
        x1 = hdr["x1"] - img_w * 0.01
        x2 = min(x_limit, max(hdr["x2"] + img_w * 0.02, hdr["x1"] + img_w * 0.05))
        return age_in_column(x1, x2, year_it)

    wife_limit = wife_years[0]["x1"] - 6 if wife_years else min(mid - 4, img_w * 0.52)
    hus = ""
    wife = ""
    if hus_hdr:
        hus = column_under_header(hus_hdr[-1], hus_years[-1] if hus_years else None, wife_limit)
    if not hus and hus_years:
        hus = column_right_of_year(hus_years[-1], wife_limit)
    if wife_hdr:
        wife = column_under_header(wife_hdr[-1], wife_years[-1] if wife_years else None, img_w * 0.999)
    if not wife and wife_years:
        wife = column_right_of_year(wife_years[-1], img_w * 0.999)

    if img is not None:
        crop_h, crop_w = read_form97_age_crops(
            img, items, img_w, img_h, split_x, dob_label, need_hus=True, need_wife=True
        )
        hus = crop_h or hus
        wife = crop_w or wife
    return hus, wife


def _prepare_digit_crop(bgr):
    """Upscale the Form 97 Age cell so two handwritten digits (29 / 27) stay readable."""
    if bgr is None or bgr.size == 0:
        return None
    pad = 18
    canvas = cv2.copyMakeBorder(
        bgr, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255)
    )
    h, w = canvas.shape[:2]
    scale = min(7.0, max(3.6, 280.0 / max(min(h, w), 1)))
    out = cv2.resize(
        canvas, (max(48, int(w * scale)), max(48, int(h * scale))),
        interpolation=cv2.INTER_CUBIC,
    )
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    gray = cv2.convertScaleAbs(gray, alpha=1.55, beta=10)
    try:
        clahe = cv2.createCLAHE(clipLimit=2.4, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
    except Exception:
        pass
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def ocr_crop_text(img, box) -> str:
    h, w = img.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in box]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 10 or y2 - y1 < 10:
        return ""
    crop = _prepare_digit_crop(img[y1:y2, x1:x2])
    if crop is None:
        return ""
    try:
        return run_ocr_text_only(crop, max_side=400)
    except Exception:
        return ""


def read_form97_age_crops(
    img, items, img_w, img_h, split_x=None, dob_label=None, need_hus=True, need_wife=True
):
    """Re-read only the two Age cells: last column after each birth year."""
    if img is None or (not need_hus and not need_wife):
        return "", ""
    mid = split_x if split_x else img_w * 0.515
    y1, y2, years, band = _form97_dob_band(items, img_w, img_h, dob_label)
    hus_years = [it for it in years if it["cx"] < mid]
    wife_years = [it for it in years if it["cx"] >= mid]
    headers = [it for it in band if _is_age_header(it["text"])]
    hus_hdr = [h for h in headers if h["cx"] < mid]
    wife_hdr = [h for h in headers if h["cx"] >= mid]
    row_y1 = int(min((it["y1"] for it in years), default=y1) - img_h * 0.01)
    row_y2 = int(max((it["y2"] for it in years), default=y2) + img_h * 0.016)

    def crop_box(x1, x2, year_it=None, hdr=None):
        top = row_y1
        bot = row_y2
        if year_it:
            top = int(year_it["y1"] - img_h * 0.012)
            bot = int(year_it["y2"] + img_h * 0.02)
        if hdr:
            x1 = min(x1, hdr["x1"] - img_w * 0.006)
            x2 = max(x2, hdr["x2"] + img_w * 0.01)
            top = min(top, int(hdr["y2"] - img_h * 0.004))
        if x2 <= x1 + 10:
            return ""
        return _parse_age_value(ocr_crop_text(img, (x1, top, x2, bot))) or ""

    wife_x1 = wife_years[0]["x1"] - 6 if wife_years else (mid - 4)
    hus = ""
    wife = ""
    if need_hus and hus_years:
        hus = crop_box(
            hus_years[-1]["x2"] + img_w * 0.002,
            min(wife_x1, hus_years[-1]["x2"] + img_w * 0.065, mid - 4),
            hus_years[-1],
            hus_hdr[-1] if hus_hdr else None,
        )
    if need_wife and wife_years:
        wife = crop_box(
            wife_years[-1]["x2"] + img_w * 0.002,
            min(img_w - 2, wife_years[-1]["x2"] + img_w * 0.07),
            wife_years[-1],
            wife_hdr[-1] if wife_hdr else None,
        )
    if need_hus and not hus:
        hus = crop_box(img_w * 0.438, min(mid - 4, img_w * 0.505), hus_years[-1] if hus_years else None)
    if need_wife and not wife:
        wife = crop_box(img_w * 0.90, img_w * 0.992, wife_years[-1] if wife_years else None)
    return hus, wife


def _dob_day_number(dob_text: str) -> str:
    match = re.search(r"\b(\d{1,2})\b", dob_text or "")
    if not match:
        return ""
    try:
        return str(int(match.group(1)))
    except ValueError:
        return ""


def _prefer_form97_age(written: str, computed: str, dob_text: str = "") -> str:
    """Keep the handwritten Age cell. Do not use the birth day (05 / 18) as Age."""
    written_age = _parse_age_value(written or "")
    computed_age = _parse_age_value(computed or "") if computed else ""
    day = _dob_day_number(dob_text)
    if written_age and day and str(int(written_age)) == day:
        if not computed_age or written_age != computed_age:
            written_age = ""
    if written_age and computed_age and written_age == computed_age:
        return written_age
    if written_age and 14 <= int(written_age) <= 90:
        return written_age
    return computed_age or written_age or ""


def extract_dob_and_age(side_tokens, marriage_date_text=""):
    side_tokens = sorted(side_tokens, key=lambda x: (x["cy"], x["x1"]))
    date_candidates = extract_date_candidates(side_tokens)
    dob = date_candidates[0]["text"] if date_candidates else ""
    handwritten = _age_from_tokens(side_tokens, dob)
    computed_age = compute_age_from_dates(dob, marriage_date_text)
    return dob, _prefer_form97_age(handwritten, computed_age, dob)


_PLACE_FORM_TAIL = {
    "CITY", "MUNICIPALITY", "PROVINCE", "OFFICE", "HOUSE", "STREET", "ST",
    "BARANGAY", "BRGY", "NO", "NUMBER", "MOSQUE", "TEMPLE", "CHAPEL",
    "ADDRESS", "PLACE", "MARRIAGE", "OF", "THE", "AND",
}

_MONTH_ALT = (
    "JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|"
    "SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER"
)


def normalize_place(text: str) -> str:
    """Keep only the marriage venue. Drop City/Municipality labels, date, and time bleed."""
    text = clean_text(text or "")
    if not text:
        return ""
    text = re.sub(r"\bPLACE OF MARRIAGE\b", " ", text, flags=re.I)
    text = re.sub(r"\bADDRESS\b", " ", text, flags=re.I)
    text = re.sub(r"\bMANILA CITY HALL\b", " ", text, flags=re.I)
    text = re.sub(r"\(.*?OFFICE OF THE.*?\)", " ", text, flags=re.I)
    text = re.sub(r"\([^)]*(?:CITY|MUNICIPALITY|PROVINCE|OFFICE|HOUSE|BARANGAY)[^)]*\)", " ", text, flags=re.I)
    text = re.sub(r"\.{2,}", " ", text)
    text = re.sub(r"(?<=\w)\.(?=\w)", "", text)
    text = re.sub(r"\bEBMITA\b", "ERMITA", text, flags=re.I)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    spaced = text
    for landmark in sorted(PLACE_LANDMARKS, key=len, reverse=True):
        spaced = re.sub(
            rf"(?i)(?<=[A-Za-z0-9])({re.escape(landmark)})",
            r" \1",
            spaced,
        )
    text = re.sub(r"\s+", " ", spaced).strip(" .:-,")

    # Hard cut before date of marriage / time of marriage that leaked into this field.
    cut = re.search(
        rf"\b\d{{1,2}}\s+(?:{_MONTH_ALT})\s+\d{{2,4}}\b",
        text,
        flags=re.I,
    )
    if cut:
        text = text[: cut.start()].strip(" .:-,")
    cut = re.search(
        r"\b\d{1,2}\s*[:.]\s*\d{2}\s*(?:AM|PM)?\b|\b\d{1,2}\s+\d{2}\s*(?:AM|PM)\b|\b(?:AM|PM)\b",
        text,
        flags=re.I,
    )
    if cut:
        text = text[: cut.start()].strip(" .:-,")
    # Item numbers + time junk: "17 am pm", "3 00pm"
    text = re.sub(r"\b\d{1,2}\s*(?:AM|PM)(?:\s*(?:AM|PM))?\s*$", "", text, flags=re.I)
    text = re.sub(r"\b\d{1,2}\s+\d{2}\s*(?:AM|PM)?\s*$", "", text, flags=re.I)

    # Printed prompt cluster often stuck at the end.
    text = re.sub(
        r"(?i)\b(?:OFFICE|HOUSE|BARANGAY|CHURCH|MOSQUE|CITY|MUNICIPALITY|PROVINCE)"
        r"(?:\s*/?\s*(?:OFFICE|HOUSE|BARANGAY|CHURCH|MOSQUE|CITY|MUNICIPALITY|PROVINCE))+\s*$",
        " ",
        text,
    )
    text = re.sub(r"(?i)\b(?:CITY\s+)?MUNICIPALITY(?:\s+PROVINCE)?\s*$", " ", text)
    text = re.sub(r"(?i)\bPROVINCE\s*$", " ", text)

    words = [w for w in re.sub(r"\s+", " ", text).strip().split() if w]
    # Drop trailing form-only labels (keep real "Parish Church").
    while words and norm_text(words[-1]) in _PLACE_FORM_TAIL:
        if norm_text(words[-1]) in {"CHURCH", "MOSQUE", "TEMPLE", "CHAPEL"} and len(words) >= 2:
            break
        if norm_text(words[-1]) == "CITY" and len(words) >= 2 and norm_text(words[-2]) not in _PLACE_FORM_TAIL:
            # Keep "Quezon City" / "Legazpi City"; drop only after province-like tails already cleaned.
            break
        words.pop()

    text = " ".join(words).strip(" .:-,/")
    return text if alpha_count(text) >= 4 else ""


def _is_place_noise_token(text: str) -> bool:
    """True for date/time/form tokens that must not enter Place of Marriage."""
    nt = norm_text(text)
    if not nt:
        return True
    if nt in _PLACE_FORM_TAIL and nt not in {"CHURCH", "MOSQUE", "TEMPLE", "CHAPEL"}:
        return True
    if nt in MONTHS:
        return True
    if re.fullmatch(r"\d{1,4}", nt):
        return True
    if re.search(r"\b(AM|PM)\b", nt):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", nt):
        return True
    return False


def normalize_marriage_date_text(tokens):
    rows = group_rows(tokens, y_tol=18)
    best_text, best_score = "", -1
    for row in rows:
        row = sorted(row, key=lambda x: x["x1"])
        row_text = join_tokens(row)
        nt = norm_text(row_text)
        date_val = parse_date_from_string(row_text)
        time_val = ""
        for t in row:
            s = clean_text(t["text"])
            m = re.search(r"(\d{1,2}:\d{2}\s?(AM|PM)?)", s, re.I)
            if m:
                time_val = m.group(1).upper().replace(" ", "")
                break
        score = 0
        if date_val:
            score += 4
        if time_val:
            score += 2
        if any(mon in nt for mon in MONTHS):
            score += 2
        if re.search(r"\d{4}", nt):
            score += 1
        if score > best_score:
            best_score = score
            if date_val and time_val:
                best_text = f"{date_val} {time_val}"
            elif date_val:
                best_text = date_val
            elif time_val:
                best_text = time_val
    return best_text


def find_marriage_date_fallback(items, place_mar_label, working_xmax):
    if not place_mar_label:
        return ""
    region = tokens_in_region(
        items, 0, working_xmax,
        place_mar_label["y2"] + 10, place_mar_label["y2"] + 110, min_score=0.20
    )
    if not region:
        return ""
    return normalize_marriage_date_text(region)


def extract_registry_number(items, img_w, img_h, working_xmax):
    """Read municipal/PSA registry numbers from the header, including split OCR tokens."""
    region = tokens_in_region(items, img_w * 0.28, img_w * 0.99, 0, img_h * 0.24, min_score=0.12)
    label = find_best_label(
        items,
        patterns=["REGISTRY NO", "REGISTRY NUMBER", "REGISTR Y NO", "REG. NO", "REG NO"],
        x_min=img_w * 0.20, x_max=img_w * 0.99,
        y_min=0, y_max=img_h * 0.28,
        min_score=0.12,
    )
    if label:
        near = tokens_in_region(
            items, label["x1"] - 20, img_w * 0.99,
            label["y1"] - 18, label["y2"] + 55, min_score=0.12
        )
        region = near + region

    vals = []
    ordered = sorted(region, key=lambda x: (x["cy"], x["x1"]))

    def consider(raw: str, score: float):
        t = re.sub(r"\s*-\s*", "-", clean_text(raw))
        compact = re.sub(r"\s+", "", t)
        if re.fullmatch(r"\d{4}-\d{3,8}", compact):
            vals.append((score + 0.35, compact))
        elif re.fullmatch(r"\d{2,8}-\d{2,8}", compact):
            vals.append((score + 0.2, compact))
        elif re.fullmatch(r"[A-Z0-9]{2,8}-\d{2,4}-[A-Z0-9]{2,10}", compact, flags=re.I):
            vals.append((score + 0.25, compact.upper()))
        elif re.fullmatch(r"\d{6,12}", compact):
            vals.append((score * 0.4, compact))

    for it in ordered:
        consider(it["text"], it["score"])

    for i, it in enumerate(ordered[:-1]):
        nxt = ordered[i + 1]
        if abs(it["cy"] - nxt["cy"]) > 22:
            continue
        if nxt["x1"] - it["x2"] > img_w * 0.12:
            continue
        a = re.sub(r"\D", "", it["text"])
        b = re.sub(r"\D", "", nxt["text"])
        if 2 <= len(a) <= 6 and 3 <= len(b) <= 8:
            consider(f"{a}-{b}", (it["score"] + nxt["score"]) / 2)

    if not vals:
        return ""
    vals.sort(key=lambda x: -x[0])
    return vals[0][1]


def extract_date_of_registration(items, img_w, img_h, marriage_date_text=""):
    """
    Form 97 puts Date of Registration in item 22 (bottom right), not in the header.
    Item 21 Received-by date is the fallback.
    """
    marriage_norm = parse_date_from_string(marriage_date_text)

    def first_date(region, skip_marriage=True):
        found = []
        seen = set()
        for cand in extract_date_candidates(region):
            parsed = parse_date_from_string(cand["text"])
            if not parsed or parsed in seen:
                continue
            if skip_marriage and marriage_norm and parsed == marriage_norm:
                continue
            seen.add(parsed)
            found.append(parsed)
        return found[0] if found else ""

    registered = find_best_label(
        items,
        patterns=["REGISTERED AT THE OFFICE", "REGISTERED AT THE", "22. REGISTERED", "REGISTERED"],
        x_min=img_w * 0.35, x_max=img_w * 0.99,
        y_min=img_h * 0.62, y_max=img_h * 0.92,
        min_score=0.12,
        forbidden=["DATE OF MARRIAGE", "DATE OF BIRTH"],
    )
    if registered:
        got = first_date(tokens_in_region(
            items, max(img_w * 0.40, registered["x1"] - 20), img_w * 0.99,
            registered["y1"], min(img_h * 0.92, registered["y2"] + img_h * 0.08),
            min_score=0.12,
        ))
        if got:
            return got

    received = find_best_label(
        items,
        patterns=["RECEIVED BY", "21. RECEIVED", "RECEIVED"],
        x_min=0, x_max=img_w * 0.55,
        y_min=img_h * 0.62, y_max=img_h * 0.92,
        min_score=0.12,
        forbidden=["DATE OF MARRIAGE", "DATE OF BIRTH"],
    )
    if received:
        got = first_date(tokens_in_region(
            items, received["x1"] - 10, min(img_w * 0.55, received["x2"] + img_w * 0.35),
            received["y1"], min(img_h * 0.92, received["y2"] + img_h * 0.08),
            min_score=0.12,
        ))
        if got:
            return got

    label = find_best_label(
        items,
        patterns=["DATE OF REGISTRATION", "DATE RECEIVED", "RECEIVED IN THIS OFFICE"],
        x_min=img_w * 0.20, x_max=img_w * 0.99,
        y_min=0, y_max=img_h * 0.30,
        min_score=0.12,
        forbidden=["DATE OF MARRIAGE", "DATE OF BIRTH"],
    )
    regions = []
    if label:
        regions.append(tokens_in_region(
            items, label["x1"] - 10, img_w * 0.99,
            label["y1"] - 16, label["y2"] + 70, min_score=0.12
        ))
    regions.append(tokens_in_region(items, img_w * 0.42, img_w * 0.99, 0, img_h * 0.22, min_score=0.18))
    for region in regions:
        got = first_date(region)
        if got:
            return got
    return ""


def extract_parent_pair(items, label, working_xmax, split_x, stop_label=None):
    if not label:
        return "", ""
    ymax = label["y2"] + 32
    if stop_label and stop_label["y1"] > label["y1"] + 4:
        ymax = min(ymax, stop_label["y1"] - 4)
    tokens = tokens_in_region(
        items, label["x2"] + 4, working_xmax,
        label["y1"] - 8, ymax, min_score=0.16
    )
    left, right = split_left_right(tokens, split_x, label["x2"])
    husband_parent = _name_tokens_to_full(left, label["x2"], split_x) or normalize_name(left)
    wife_parent = _name_tokens_to_full(right, split_x, working_xmax) or normalize_name(right)
    return husband_parent, wife_parent


def resolve_duplicate_parents(items, father_label, mother_label, working_xmax, split_x, h_f, h_m, w_f, w_m):
    same_h = bool(h_f and h_m and norm_text(h_f) == norm_text(h_m))
    same_w = bool(w_f and w_m and norm_text(w_f) == norm_text(w_m))
    if not (same_h or same_w) or not father_label:
        return h_f, h_m, w_f, w_m
    y1 = mother_label["y2"] + 40 if mother_label else father_label["y2"] + 90
    tokens = tokens_in_region(
        items, father_label["x2"] + 4, working_xmax,
        father_label["y1"] - 6, y1, min_score=0.14
    )
    name_rows = []
    for row in group_rows(tokens, y_tol=13):
        left, right = split_left_right(row, split_x, father_label["x2"])
        ln, rn = normalize_name(left), normalize_name(right)
        if ln or rn:
            name_rows.append((ln, rn))
    if len(name_rows) >= 2:
        return (
            name_rows[0][0] or h_f,
            name_rows[1][0],
            name_rows[0][1] or w_f,
            name_rows[1][1],
        )
    if same_h:
        h_m = ""
    if same_w:
        w_m = ""
    return h_f, h_m, w_f, w_m


def extract_citizenship_pair(items, label, working_xmax, split_x, next_label, img_w, img_h):
    h, w = "", ""
    if label:
        h, w = extract_two_side_band(
            items, label, working_xmax, split_x,
            next_label=next_label, banned={"CITIZENSHIP", "CITIZENAHIP"}, extra_bottom=16
        )
    h = normalize_citizenship_value(h)
    w = normalize_citizenship_value(w)
    if h and w:
        return h, w
    y_min = label["y1"] - 10 if label else img_h * 0.16
    y_max = (next_label["y1"] - 4) if next_label else (label["y2"] + 50 if label else img_h * 0.34)
    band = tokens_in_region(items, img_w * 0.12, working_xmax, y_min, y_max, min_score=0.12)
    left, right = split_left_right(band, split_x, label["x2"] if label else img_w * 0.12)
    if not h:
        h = normalize_citizenship_value(join_tokens(left))
    if not w:
        w = normalize_citizenship_value(join_tokens(right))
    return h, w


def _field_confidence(field_name: str, value: str, source: str = "layout") -> str:
    v = (value or "").strip()
    if not v:
        return "LOW"
    n = norm_text(v)

    if source == "template":
        return "MEDIUM"

    if field_name in {"Date of Marriage", "Date of Registration"}:
        return "HIGH" if parse_date_from_string(v) else "MEDIUM"
    if field_name in {"Husband Age", "Wife Age"}:
        return "HIGH" if re.fullmatch(r"\d{1,3}", n) else "MEDIUM"
    if field_name == "Registry Number":
        compact = n.replace(" ", "")
        if re.fullmatch(r"[A-Z0-9]{2,10}[-/][A-Z0-9]{2,10}", compact):
            return "HIGH"
        return "MEDIUM"
    if field_name in {
        "Husband Name", "Wife Name", "Place of Marriage", "Husband Citizenship",
        "Wife Citizenship", "Husband Civil Status", "Wife Civil Status",
        "Husband Father", "Husband Mother", "Wife Father", "Wife Mother"
    }:
        return "HIGH" if alpha_count(v) >= 4 else "MEDIUM"
    return "MEDIUM"


# =========================================================
# MAIN NAME EXTRACTION FUNCTION (FIXED)
# =========================================================
def extract_names_from_band(items, name_label, dob_label, working_xmax, split_x, img_w=None, img_h=None):
    """Read First + Middle + Last on the same row as 'Name of Contracting Parties'."""
    if img_w is None and items:
        img_w = max(it["x2"] for it in items)
    if img_h is None and items:
        img_h = max(it["y2"] for it in items)
    img_w = img_w or 600
    img_h = img_h or 800

    y_start = int(img_h * 0.105)
    y_end = int(img_h * 0.195)
    if name_label:
        y_start = min(y_start, int(name_label["y1"] - img_h * 0.012))
        y_end = max(y_end, int(name_label["y2"] + img_h * 0.01))
    if dob_label:
        y_end = min(y_end, dob_label["y1"] - 4)
    if y_end <= y_start:
        y_end = y_start + int(img_h * 0.06)

    x_start = int(img_w * 0.12)
    x_end = working_xmax
    mid = split_x if split_x and split_x > x_start else (x_start + x_end) / 2.0

    tokens = tokens_in_region(items, x_start, x_end, y_start, y_end, min_score=0.08)
    left, right = split_left_right(tokens, mid, x_start)

    # Prefer explicit First:/Middle:/Last: stacked rows (correct form order).
    husband = extract_stacked_fml_name(items, x_start, mid, y_start, y_end)
    wife = extract_stacked_fml_name(items, mid, x_end, y_start, y_end)
    if not husband:
        husband = _name_tokens_to_full(left, x_start, mid)
    if not wife:
        wife = _name_tokens_to_full(right, mid, x_end)

    if DEBUG_NAME_BAND:
        print(f"Name row: husband='{husband}', wife='{wife}' split={mid:.0f}")

    if husband or wife:
        return husband, wife

    left, right = split_left_right(tokens, mid, x_start)
    return sanitize_person_name(normalize_name(left)), sanitize_person_name(normalize_name(right))


# =========================================================
# BUILD ITEMS FROM OCR RESULT
# =========================================================
def build_items(result):
    items = []
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
                "x1": int(min(xs)), "y1": int(min(ys)),
                "x2": int(max(xs)), "y2": int(max(ys)),
            }
            item["cx"] = (item["x1"] + item["x2"]) / 2.0
            item["cy"] = (item["y1"] + item["y2"]) / 2.0
            item["w"] = item["x2"] - item["x1"]
            item["h"] = item["y2"] - item["y1"]
            items.append(item)
    items.sort(key=lambda x: (x["cy"], x["x1"]))
    return items


# =========================================================
# MAIN: extract_marriage_data
# =========================================================
def extract_marriage_data(img_path: str) -> Dict[str, Any]:
    img = cv2.imread(img_path)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {img_path}")

    img = preprocess_image(img)

    if DEBUG_PREPROCESS:
        cv2.imwrite("debug_preprocessed_marriage.jpg", img)

    img_h, img_w = img.shape[:2]
    result = run_ocr_on_image(img)
    box_map: Dict[str, str] = {}
    try:
        box_map = extract_field_boxes(img, "marriage", pages=result)
    except Exception:
        box_map = {}

    # Template-first extraction (folder-based JSON templates).
    tpl_map: Dict[str, str] = {}
    try:
        ocr_text = ocr_pages_to_text(result)
        tpl = extract_from_ocr_text(ocr_text, doc_type="marriage", best_effort=True)
        fields = (tpl.get("fields") or {}) if isinstance(tpl, dict) else {}
        if DEBUG_TEMPLATE_MATCH:
            found = sum(1 for v in fields.values() if v)
            print(
                f"[OCR MARRIAGE TEMPLATE] path={tpl.get('template_path')} "
                f"score={tpl.get('match_score')} fields={found}/{len(fields)}"
            )
        found = sum(1 for v in fields.values() if v)
        if tpl.get("template_path") and found >= 1:
            def _v(k: str) -> str:
                val = fields.get(k)
                return str(val).strip() if val else ""

            tpl_map = {
                "Registry Number": _v("registry_number"),
                "Date of Registration": _v("date_of_registration"),
                "Husband Name": sanitize_person_name(_v("husband_name")),
                "Wife Name": sanitize_person_name(_v("wife_name")),
                "Date of Marriage": _v("date_of_marriage"),
                "Place of Marriage": normalize_place(_v("place_of_marriage")),
            }
    except Exception:
        pass

    items = build_items(result)

    if DEBUG_DRAW_BOXES:
        draw_debug_boxes(img, items)

    if DEBUG_OCR_RAW:
        print("\n========== RAW OCR ITEMS ==========")
        for it in items:
            print(f'{it["text"]} | ({it["x1"]},{it["y1"]})-({it["x2"]},{it["y2"]}) score={it["score"]:.2f}')
        print("===================================\n")

    # LABELS / LAYOUT
    working_xmax = infer_working_xmax(items, img_w, img_h)

    name_label = find_best_label(
        items,
        patterns=["NAME OF CONTRACTING PARTIES", "NAME OF CONTRACTING",
                  "CONTRACTING PARTIES", "CONTRACTING PARTIEN", "NAME OF PARTIES", "CONTRACTING"],
        x_min=0, x_max=img_w * 0.40,
        y_min=img_h * 0.04, y_max=img_h * 0.28,
        min_score=0.15,
    )

    dob_label = find_best_label(
        items,
        patterns=["DATE OF BIRTH AGE", "DATE OF BIRTH/AGE", "DATE OF BIRTH", "BIRTH AGE"],
        x_min=0, x_max=img_w * 0.32,
        y_min=img_h * 0.10, y_max=img_h * 0.32
    )

    pob_label = find_best_label(
        items,
        patterns=["PLACE OF BIRTH"],
        x_min=0, x_max=img_w * 0.30,
        y_min=img_h * 0.14, y_max=img_h * 0.32
    )

    sex_label = find_best_label(
        items,
        patterns=["SEX MALE OR FEMALE", "SEX"],
        x_min=0, x_max=img_w * 0.30,
        y_min=img_h * 0.16, y_max=img_h * 0.36
    )

    religion_label = find_best_label(
        items,
        patterns=["RELIGION"],
        x_min=0, x_max=img_w * 0.30,
        y_min=img_h * 0.20, y_max=img_h * 0.45
    )

    cit_top_label = None
    if sex_label and religion_label:
        cit_top_label = find_best_label(
            items,
            patterns=["CITIZENSHIP", "CITIZENAHIP"],
            x_min=0, x_max=img_w * 0.30,
            y_min=sex_label["y2"] + 2,
            y_max=religion_label["y1"] - 2,
            min_score=0.20
        )

    if cit_top_label is None:
        cit_top_label = find_best_label(
            items,
            patterns=["CITIZENSHIP", "CITIZENAHIP"],
            x_min=0, x_max=img_w * 0.30,
            y_min=img_h * 0.18, y_max=img_h * 0.30
        )

    civil_label = find_best_label(
        items,
        patterns=["CIVIL STATUS"],
        x_min=0, x_max=img_w * 0.30,
        y_min=img_h * 0.22, y_max=img_h * 0.48
    )

    father_label = find_best_label(
        items,
        patterns=["NAME OF FATHER", "FATHER"],
        x_min=0, x_max=img_w * 0.32,
        y_min=img_h * 0.24, y_max=img_h * 0.56
    )

    mother_min_y = (father_label["y2"] + 8) if father_label else img_h * 0.30
    mother_label = find_best_label(
        items,
        patterns=["NAME OF MOTHER", "MOTHER"],
        x_min=0, x_max=img_w * 0.32,
        y_min=mother_min_y, y_max=img_h * 0.66
    )
    if father_label and mother_label and mother_label["cy"] <= father_label["cy"] + 10:
        mother_label = find_best_label(
            items,
            patterns=["NAME OF MOTHER", "MOTHER"],
            x_min=0, x_max=img_w * 0.32,
            y_min=father_label["y2"] + 12, y_max=img_h * 0.66
        )

    father_cit_label = find_label_below(
        items, father_label,
        patterns=["CITIZENSHIP", "CITIZENAHIP"],
        y_min_offset=2, y_max_offset=80,
        x_max=img_w * 0.30
    )

    mother_cit_label = find_label_below(
        items, mother_label,
        patterns=["CITIZENSHIP", "CITIZENAHIP"],
        y_min_offset=2, y_max_offset=80,
        x_max=img_w * 0.30
    )

    place_mar_label = find_best_label(
        items,
        patterns=["PLACE OF MARRIAGE"],
        x_min=0, x_max=img_w * 0.30,
        y_min=img_h * 0.42, y_max=img_h * 0.72
    )

    date_mar_label = find_best_label(
        items,
        patterns=["DATE OF MARRIAGE", "TIME OF MARRIAGE", "DATE AND TIME OF MARRIAGE", "DATE", "DATE:"],
        x_min=0, x_max=img_w * 0.45,
        y_min=img_h * 0.40, y_max=img_h * 0.78,
        forbidden=["DATE OF BIRTH", "DATE OF REGISTRATION"]
    )

    if DEBUG_LABELS:
        print("\n========= DETECTED LABELS =========")
        def show(label, name):
            if label:
                print(f"{name}: {label['text']}  (y={label['cy']:.1f})")
            else:
                print(f"{name}: NOT FOUND")
        show(name_label, "NAME LABEL")
        show(dob_label, "DOB LABEL")
        show(pob_label, "PLACE OF BIRTH")
        show(sex_label, "SEX")
        show(cit_top_label, "CITIZENSHIP")
        show(civil_label, "CIVIL STATUS")
        show(father_label, "FATHER")
        show(mother_label, "MOTHER")
        show(place_mar_label, "PLACE OF MARRIAGE")
        show(date_mar_label, "DATE OF MARRIAGE")
        print("===================================\n")

    split_x = infer_column_split_from_headers(items, name_label, working_xmax, img_w, img_h)
    if split_x is None:
        split_x = infer_column_split_from_name(items, name_label, working_xmax, img_w)

    if DEBUG_LABELS:
        print("COLUMN SPLIT POSITION:", split_x)

    # EXTRACTION
    data = {"husband": {}, "wife": {}, "shared": {}}

    data["shared"]["Registry Number"] = extract_registry_number(items, img_w, img_h, working_xmax)

    if place_mar_label:
        lower_y = date_mar_label["y1"] - 8 if date_mar_label and date_mar_label["y1"] > place_mar_label["y2"] else place_mar_label["y2"] + 36
        if lower_y <= place_mar_label["y2"]:
            lower_y = place_mar_label["y2"] + 36
        region = tokens_in_region(
            items, min(place_mar_label["x2"], img_w * 0.18), working_xmax,
            place_mar_label["y1"] - 4, lower_y, min_score=0.12
        )
        kept = []
        for it in region:
            nt = norm_text(it["text"])
            if "PLACE" in nt and "MARRIAGE" in nt:
                continue
            if _is_place_noise_token(it["text"]):
                continue
            kept.append(it)
        place_text = normalize_place(join_tokens(kept))
        if place_text:
            data["shared"]["Place of Marriage"] = place_text

    marriage_date_text = ""
    if date_mar_label:
        region = tokens_in_region(
            items, date_mar_label["x1"], working_xmax,
            date_mar_label["y1"] - 14, date_mar_label["y2"] + 34, min_score=0.20
        )
        marriage_date_text = normalize_marriage_date_text(region)

    if not marriage_date_text:
        marriage_date_text = find_marriage_date_fallback(items, place_mar_label, working_xmax)

    if marriage_date_text:
        data["shared"]["Date of Marriage"] = marriage_date_text

    data["shared"]["Date of Registration"] = extract_date_of_registration(
        items, img_w, img_h, marriage_date_text
    )

    # Extract husband/wife NAMES - using improved function
    h, w = extract_names_from_band(items, name_label, dob_label, working_xmax, split_x, img_w, img_h)
    h, w = sanitize_person_name(h), sanitize_person_name(w)
    if h:
        data["husband"]["Name"] = h
    if w:
        data["wife"]["Name"] = w

    if dob_label:
        # Age is the last cell of each half — read past working_xmax so wife Age is kept.
        band_tokens = extract_band_tokens(
            items, dob_label, img_w * 0.999,
            next_label=pob_label, extra_bottom=56, extra_top=12, min_score=0.08
        )
        age_split = (split_x or img_w * 0.52) + img_w * 0.02
        left, right = split_left_right(band_tokens, age_split, dob_label["x2"])
        h_dob, h_age = extract_dob_and_age(left, marriage_date_text)
        w_dob, w_age = extract_dob_and_age(right, marriage_date_text)
        if h_dob:
            data["husband"]["Date of Birth"] = h_dob
        if h_age:
            data["husband"]["Age"] = h_age
        if w_dob:
            data["wife"]["Date of Birth"] = w_dob
        if w_age:
            data["wife"]["Age"] = w_age

    box_h_age, box_w_age = extract_form97_ages(
        items,
        img_w,
        img_h,
        split_x,
        dob_label,
        img=img,
    )
    hus_computed = compute_age_from_dates(
        data["husband"].get("Date of Birth") or "", marriage_date_text
    )
    wife_computed = compute_age_from_dates(
        data["wife"].get("Date of Birth") or "", marriage_date_text
    )
    data["husband"]["Age"] = _prefer_form97_age(
        box_h_age or data["husband"].get("Age") or "",
        hus_computed,
        data["husband"].get("Date of Birth") or "",
    )
    data["wife"]["Age"] = _prefer_form97_age(
        box_w_age or data["wife"].get("Age") or "",
        wife_computed,
        data["wife"].get("Date of Birth") or "",
    )
    if data["wife"].get("Age") and data["wife"]["Age"] == data["husband"].get("Age"):
        if wife_computed and wife_computed != data["husband"].get("Age"):
            data["wife"]["Age"] = wife_computed
        elif not wife_computed:
            data["wife"]["Age"] = ""

    h, w = extract_two_side_band(items, pob_label, working_xmax, split_x, next_label=sex_label, banned={"PLACE OF BIRTH"}, extra_bottom=28)
    if h:
        data["husband"]["Place of Birth"] = h
    if w:
        data["wife"]["Place of Birth"] = w

    h, w = extract_two_side_band(items, sex_label, working_xmax, split_x, next_label=cit_top_label, banned={"SEX", "MALE OR FEMALE"}, extra_bottom=20)
    if h:
        data["husband"]["Sex"] = normalize_sex_value(h)
    if w:
        data["wife"]["Sex"] = normalize_sex_value(w)

    h, w = extract_citizenship_pair(
        items, cit_top_label, working_xmax, split_x, religion_label, img_w, img_h
    )
    if h:
        data["husband"]["Citizenship"] = h
    if w:
        data["wife"]["Citizenship"] = w

    h, w = extract_two_side_band(items, religion_label, working_xmax, split_x, next_label=civil_label, banned={"RELIGION"}, extra_bottom=24)
    if h:
        data["husband"]["Religion"] = apply_common_word_fix(h)
    if w:
        data["wife"]["Religion"] = apply_common_word_fix(w)

    h, w = extract_two_side_band(items, civil_label, working_xmax, split_x, next_label=father_label, banned={"CIVIL STATUS"}, extra_bottom=24)
    if h:
        data["husband"]["Civil Status"] = h
    if w:
        data["wife"]["Civil Status"] = w

    h_f, w_f = extract_parent_pair(
        items, father_label, working_xmax, split_x,
        stop_label=father_cit_label or mother_label,
    )
    h_m, w_m = extract_parent_pair(
        items, mother_label, working_xmax, split_x,
        stop_label=mother_cit_label or place_mar_label,
    )
    h_f, h_m, w_f, w_m = resolve_duplicate_parents(
        items, father_label, mother_label, working_xmax, split_x, h_f, h_m, w_f, w_m
    )
    if h_f:
        data["husband"]["Father"] = h_f
    if w_f:
        data["wife"]["Father"] = w_f
    if h_m:
        data["husband"]["Mother"] = h_m
    if w_m:
        data["wife"]["Mother"] = w_m

    h, w = extract_two_side_band(items, father_cit_label, working_xmax, split_x, next_label=mother_label, banned={"CITIZENSHIP", "CITIZENAHIP"}, extra_bottom=24)
    if h:
        data["husband"]["Father Citizenship"] = apply_common_word_fix(h)
    if w:
        data["wife"]["Father Citizenship"] = apply_common_word_fix(w)

    h, w = extract_two_side_band(items, mother_cit_label, working_xmax, split_x, next_label=place_mar_label, banned={"CITIZENSHIP", "CITIZENAHIP"}, extra_bottom=24)
    if h:
        data["husband"]["Mother Citizenship"] = apply_common_word_fix(h)
    if w:
        data["wife"]["Mother Citizenship"] = apply_common_word_fix(w)

    out = {
        "Registry Number": data["shared"].get("Registry Number", ""),
        "Date of Registration": data["shared"].get("Date of Registration", ""),
        "Date of Marriage": data["shared"].get("Date of Marriage", ""),
        "Place of Marriage": data["shared"].get("Place of Marriage", ""),
        "Husband Name": data["husband"].get("Name", ""),
        "Husband Age": data["husband"].get("Age", ""),
        "Husband Citizenship": data["husband"].get("Citizenship", ""),
        "Husband Civil Status": data["husband"].get("Civil Status", ""),
        "Husband Father": data["husband"].get("Father", ""),
        "Husband Mother": data["husband"].get("Mother", ""),
        "Wife Name": data["wife"].get("Name", ""),
        "Wife Age": data["wife"].get("Age", ""),
        "Wife Citizenship": data["wife"].get("Citizenship", ""),
        "Wife Civil Status": data["wife"].get("Civil Status", ""),
        "Wife Father": data["wife"].get("Father", ""),
        "Wife Mother": data["wife"].get("Mother", ""),
    }
    before_template = {k: (out.get(k) or "") for k in out.keys()}
    # Fill missing values with template results (if any).
    for k, v in (tpl_map or {}).items():
        if v and not (out.get(k) or "").strip():
            out[k] = v
    for k, v in (box_map or {}).items():
        if not v:
            continue
        if k in ("Husband Name", "Wife Name"):
            incoming = collapse_repeated_name(sanitize_person_name(v))
            if not incoming:
                continue
            if len(incoming.split()) >= 2:
                out[k] = incoming
                continue
            out[k] = collapse_repeated_name(
                prefer_fuller_name(out.get(k) or "", incoming)
            )
            continue
        if k in {"Husband Citizenship", "Wife Citizenship"}:
            parsed = normalize_citizenship_value(v)
            if parsed:
                out[k] = parsed
            continue
        if k == "Place of Marriage":
            parsed = normalize_place(v)
            if parsed:
                out[k] = parsed
            continue
        if k in {"Husband Age", "Wife Age"}:
            parsed = _parse_age_value(v)
            if not parsed:
                continue
            dob_key = "husband" if k == "Husband Age" else "wife"
            parsed = _prefer_form97_age(
                parsed,
                compute_age_from_dates(data[dob_key].get("Date of Birth") or "", marriage_date_text),
                data[dob_key].get("Date of Birth") or "",
            )
            if not parsed:
                continue
            if k == "Wife Age" and parsed == (out.get("Husband Age") or "").strip():
                continue
            v = parsed
        out[k] = v
    linked_h, linked_w = extract_form97_ages(items, img_w, img_h, split_x, dob_label, img=img)
    hus_computed = compute_age_from_dates(
        data["husband"].get("Date of Birth") or "", marriage_date_text
    )
    wife_computed = compute_age_from_dates(
        data["wife"].get("Date of Birth") or "", marriage_date_text
    )
    out["Husband Age"] = _prefer_form97_age(
        linked_h or out.get("Husband Age") or "",
        hus_computed,
        data["husband"].get("Date of Birth") or "",
    )
    out["Wife Age"] = _prefer_form97_age(
        linked_w or out.get("Wife Age") or "",
        wife_computed,
        data["wife"].get("Date of Birth") or "",
    )
    if (out.get("Wife Age") or "").strip() == (out.get("Husband Age") or "").strip():
        if wife_computed and wife_computed != (out.get("Husband Age") or "").strip():
            out["Wife Age"] = wife_computed
        elif not linked_w:
            out["Wife Age"] = wife_computed or ""
    ordered_fields = [
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

