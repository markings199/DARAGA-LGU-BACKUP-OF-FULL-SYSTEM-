"""
Birth certificate OCR extraction.
Fixed implementation - addresses extraction issues with better field detection.
"""
import re
import difflib
from typing import Dict, Any, List

import cv2
from ocr.shared import run_ocr_on_image
from ocr.engine import extract_from_ocr_text, ocr_pages_to_text
from ocr.field_boxes import extract_field_boxes
from ocr.person_name import compose_first_middle_last
from ocr.sex import sex_from_ocr_text, sex_from_tokens


# =========================================================
# CONFIG
# =========================================================
CROP_LEFT = 60
CROP_RIGHT = 60
DEBUG_OCR_RAW = False
DEBUG_LABELS = False


# =========================================================
# PREPROCESS: CROP
# =========================================================
def load_cropped_birth_image(image_path, crop_left, crop_right):
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"Could not read image: {image_path}")

    _h, w = img.shape[:2]

    if crop_left < 0 or crop_right < 0:
        raise ValueError("Crop values must be 0 or greater.")

    if crop_left + crop_right >= w:
        raise ValueError("Left + right crop is too large for the image width.")

    return img[:, crop_left:w - crop_right]


def enhance_birth_image(img):
    if img is None:
        return img
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    enhanced = cv2.merge((clahe.apply(l_ch), a_ch, b_ch))
    return cv2.cvtColor(enhanced, cv2.COLOR_LAB2BGR)


# =========================================================
# CONSTANTS / FIXES
# =========================================================
MONTHS = {
    "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
    "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"
}

MONTH_FIXES = {
    "MABCK": "MARCH",
    "MARCK": "MARCH",
    "MARCHK": "MARCH",
    "JAMUARY": "JANUARY",
    "JANUARYY": "JANUARY",
}

OCR_WORD_FIXES = {
    "FILIFIRO": "FILIPINO",
    "PILIPINO": "FILIPINO",
    "FOMUTE": "FEMALE",
    "FOMALE": "FEMALE",
    "RELIBION": "RELIGION",
    "CETIZENSHIP": "CITIZENSHIP",
    "REGIATRY": "REGISTRY",
    "ROPUTFIC": "REPUBLIC",
    "PHLPPINS": "PHILIPPINES",
}

BAD_VALUE_KEYWORDS = {
    "DATE", "SIGNATURE", "NAME IN PRINT", "TITLE OF POSITION", "TITLE OR POSITION",
    "CITIZENSHIP", "RELIGION", "OCCUPATION", "AGE", "YEARS",
    "TOTAL NUMBER", "NO OF", "NO.", "PREPARED BY", "INFORMANT",
    "ATTENDANT", "CERTIFICATION", "RESIDENCE", "TYPE OF BIRTH",
    "BIRTH ORDER", "MULTIPLE", "BIRTHCHILD", "IF MULTIPLE",
    "FOR OCRG USE ONLY", "TO BE FILLED", "CIVIL REGISTRAR",
    "POPULATION REFERENCES", "REMARKS", "ANNOTATION",
    "HOUSE NO", "ST BARANGAY", "CITY MUNICIPALITY", "PROVINCE COUNTRY",
}

NATIONALITY_WORDS = {
    "FILIPINO", "FILIPINA", "AMERICAN", "CHINESE", "JAPANESE", "KOREAN",
    "INDIAN", "BRITISH", "AUSTRALIAN", "CANADIAN", "SPANISH",
}

NOT_A_PLACE = NATIONALITY_WORDS | {
    "MALE", "FEMALE", "UNKNOWN", "CATHOLIC", "CHRISTIAN", "MUSLIM",
    "IGLESIA", "RELIGION", "CITIZENSHIP", "SINGLE", "MARRIED",
}

# Form 102 items 5a/5b/5c/6 often bleed into Place of Birth OCR.
PLACE_OF_BIRTH_JUNK = {
    "TYPE OF BIRTH", "IF MULTIPLE", "BIRTH ORDER", "PREVIOUS", "FETAL",
    "INCLUDING FETAL", "WEIGHT AT BIRTH", "WEIGHT", "GRAMS", "GRAM",
    "SINGLE", "TWIN", "TRIPLET", "QUADRUPLET", "FIRST", "SECOND", "THIRD",
    "FOURTH", "FIFTH", "ETC", "5A", "5B", "5C", "CHILD WAS",
    "ORDER OF THIS", "LIVE BIRTHS", "STILLBORN", "BIRTHCHILD",
}

FORM_JUNK_NAME_MARKERS = {
    "TITLE", "POSITION", "SIGNATURE", "PRINT", "INFORMANT", "ATTENDANT",
    "PREPARED", "RECEIVED", "REGISTERED", "CERTIFY", "CERTIFICATION",
    "HOUSE", "BARANGAY", "MUNICIPALITY", "PROVINCE", "COUNTRY", "STREET",
    "HOSPITAL", "CLINIC", "INSTITUTION", "RELATIONSHIP", "OCCUPATION",
    "RELIGION", "CITIZENSHIP", "RESIDENCE", "WEIGHT", "GRAMS",
}

PLACE_HINTS = {
    "PHILIPPINES", "CITY", "PROVINCE", "MUNICIPAL", "MUNICIPALITY", "BARANGAY",
    "HOSPITAL", "CLINIC", "MEDICAL", "CENTER", "TOWN", "ALBAY", "DARAGA",
    "BICOL", "LEGAZPI", "BULACAN", "MANILA", "CEBU", "DAVAO", "QUEZON",
    "CAMARINES", "SORSOGON", "MASBATE", "CATANDUANES", "RIZAL", "LAGUNA",
    "BATANGAS", "ILOCOS", "PAMPANGA", "NUEVA", "ISABELA", "CAVITE",
    "STREET", "ROAD", "AVENUE", "PUROK", "SITIO", "BRGY", "DISTRICT",
}

PLACEHOLDER_MARKERS = {
    "DAY", "MONTH", "YEAR", "YESE", "YYYY", "MMDD", "FIRST", "MIDDLE", "LAST",
    "FIRSTNAME", "MIDDLENAME", "LASTNAME", "GIVENNAME",
}


def _is_placeholder_text(text: str) -> bool:
    raw = str(text or "")
    nt = norm_text(raw)
    if not nt:
        return True
    if re.search(r"\(\s*(DAY|MONTH|YEAR|HOUSE|CITY|NAME|FIRST|MIDDLE|LAST)", raw, flags=re.I):
        return True
    if "(" in raw and ")" in raw and any(
        tip in nt for tip in ("HOUSE", "BARANGAY", "MUNICIPALITY", "PROVINCE", "STREET", "CITY")
    ):
        return True
    if "DAY" in nt and "MONTH" in nt:
        return True
    if "TITLE" in nt and "POSITION" in nt:
        return True
    if "NAME IN PRINT" in nt:
        return True
    compact = nt.replace(" ", "")
    if compact in PLACEHOLDER_MARKERS or "DAYMONTH" in compact or "YESE" in compact:
        return True
    words = set(nt.split())
    if words & {"DAY", "MONTH", "YEAR", "YESE"} and not any(ch.isdigit() for ch in nt):
        return True
    return False


def _is_full_date(text: str) -> bool:
    return bool(parse_date_from_string(text))


def _is_citizenship(text: str) -> bool:
    nt = norm_text(text)
    if not nt:
        return False
    if nt in NATIONALITY_WORDS:
        return True
    return any(n in nt and len(nt) <= 24 for n in NATIONALITY_WORDS)


def _is_plausible_place(text: str) -> bool:
    nt = norm_text(text or "")
    if len(nt) < 5:
        return False
    if _is_placeholder_text(text):
        return False
    if nt in NOT_A_PLACE:
        return False
    if any(nt == w or nt.startswith(w + " ") for w in NOT_A_PLACE):
        return False
    if _is_citizenship(nt):
        return False
    if any(bad in nt for bad in (
        "INFORMANT", "SIGNATURE", "CERTIFY", "PRINT", "REMARKS",
        "ANNOTATION", "REGISTRY", "RELIGION", "TITLE", "POSITION",
    )):
        return False
    if "MARRIAGE OF PARENTS" in nt:
        return False
    # Printed Place of Birth prompt with no real locality.
    if ("HOUSE NO" in nt or "ST BARANGAY" in nt) and not any(
        hint in nt for hint in PLACE_HINTS if hint not in {"STREET", "ROAD", "AVENUE"}
    ):
        return False
    # Reject Type of Birth / Birth Order / Weight lines (Form 102 5a–5c, 6).
    if any(bad in nt for bad in PLACE_OF_BIRTH_JUNK):
        # Allow real places that only contain a street word "FIRST" rarely;
        # still reject when birth-order/weight markers dominate.
        junk_hits = sum(1 for bad in PLACE_OF_BIRTH_JUNK if bad in nt)
        if junk_hits >= 1 and (
            any(k in nt for k in (
                "SECOND", "THIRD", "ETC", "TWIN", "TRIPLET", "GRAM", "WEIGHT",
                "BIRTH ORDER", "TYPE OF BIRTH", "MULTIPLE", "5A", "5B", "5C",
            ))
            or re.search(r"\b\d{2,5}\s*G\b", nt)
            or re.search(r"\b\d{3,5}\b", nt)
        ):
            return False
        if junk_hits >= 2:
            return False
    # Reject date-like "places" (common OCR mix-up with marriage date).
    if parse_date_from_string(text):
        return False
    letters = alpha_count(nt)
    vowels = sum(ch in "AEIOU" for ch in nt)
    if letters and vowels / letters < 0.22:
        return False
    words = [w for w in nt.split() if w]
    has_hint = any(hint in nt for hint in PLACE_HINTS)
    # Long strings of form option words are not places.
    if not has_hint:
        return False
    if len(words) < 2 and len(nt) < 10:
        return False
    return True


_MARRIAGE_PLACE_PRINTED = re.compile(
    r"(?i)\b(?:20\s*[AB]\.?|MARRIAGE OF PARENTS|IF NOT MARRIED|ACCOMPLISH|"
    r"AFFIDAVIT|ACKNOWLEDG(?:E)?MENT|ADMISSION OF PATERNITY|AT THE BACK|"
    r"MONTH|DAY|YEAR|CITY/?\s*MUNICIPALITY|PROVINCE|COUNTRY)\b"
)


def sanitize_place_of_marriage_of_parents(text: str) -> str:
    """Keep locality from Form 102 item 20b; drop printed 20a/20b prompts."""
    raw = _collapse_repeated_words(clean_text(text or ""))
    if not raw:
        return ""
    raw = re.sub(r"\([^)]*\)", " ", raw)
    raw = _MARRIAGE_PLACE_PRINTED.sub(" ", raw)
    raw = re.sub(r"\b20\s*[AB]\b", " ", raw, flags=re.I)
    parsed = parse_date_from_string(raw)
    if parsed:
        raw = re.sub(re.escape(parsed), " ", raw, flags=re.I)
    raw = re.sub(r"\s+", " ", raw).strip(" .,-/")
    if not raw or _is_placeholder_text(raw) or parse_date_from_string(raw):
        return ""
    if _is_plausible_place(raw):
        return raw
    nt = norm_text(raw)
    if any(hint in nt for hint in PLACE_HINTS) and alpha_count(nt) >= 5:
        return raw
    return ""


def sanitize_place_of_birth(text: str) -> str:
    """Keep hospital/address text; cut Form 102 type/order/weight bleed."""
    raw = _collapse_repeated_words(clean_text(text or ""))
    if not raw:
        return ""
    # Drop parenthetical printed prompts.
    raw = re.sub(r"\([^)]*\)", " ", raw)
    # Hard cut before section 5a/5b/5c/6 content.
    cut = re.search(
        r"(?i)\b(?:5\s*[ABC]|TYPE OF BIRTH|IF MULTIPLE|BIRTH ORDER|WEIGHT(?:\s+AT\s+BIRTH)?|"
        r"PREVIOUS(?:\s+LIVE)?\s+BIRTHS|INCLUDING\s+FETAL|"
        r"SINGLE|TWIN|TRIPLET)\b",
        raw,
    )
    # Only cut on SINGLE/TWIN if it looks like the type-of-birth option line,
    # not part of an address (rare). Prefer cutting when followed by etc/order/weight.
    if cut:
        token = cut.group(0).upper().replace(" ", "")
        if token in {"SINGLE", "TWIN", "TRIPLET"}:
            after = raw[cut.end() : cut.end() + 40].upper()
            before = raw[max(0, cut.start() - 24) : cut.start()].upper()
            if not any(
                m in after or m in before
                for m in ("ETC", "SECOND", "THIRD", "FIRST", "WEIGHT", "GRAM", "TYPE", "MULTIPLE", "5A", "5B")
            ):
                cut = None
        if cut:
            raw = raw[: cut.start()]
    # Strip weight fragments that remain: "3200 g", "3 200 g"
    raw = re.sub(r"(?i)\b\d{1,2}\s+\d{2,4}\s*g(?:rams?)?\b", " ", raw)
    raw = re.sub(r"(?i)\b\d{3,5}\s*g(?:rams?)?\b", " ", raw)
    # Strip leftover option words at the end.
    words = []
    drop = {
        "ETC", "SINGLE", "TWIN", "TRIPLET", "FIRST", "SECOND", "THIRD", "FOURTH",
        "FIFTH", "GRAMS", "GRAM", "G", "WEIGHT", "ORDER", "TYPE", "BIRTH",
        "MULTIPLE", "CHILD", "WAS", "IF", "OF", "THE", "AND", "TO", "5A", "5B", "5C",
    }
    for w in re.sub(r"\s+", " ", raw).strip().split():
        nw = norm_text(w)
        if nw in drop:
            continue
        if re.fullmatch(r"\d+", nw):
            # Keep short house/lot numbers; years and heavy weights already cut above.
            if 1 <= len(nw) <= 4:
                words.append(w)
            continue
        words.append(w)
    cleaned = " ".join(words).strip(" .:-,/")
    if not _is_plausible_place(cleaned):
        return ""
    return cleaned


def _is_plausible_person_name(text: str) -> bool:
    nt = norm_text(text or "")
    if len(nt) < 5:
        return False
    if nt in NOT_A_PLACE or _is_citizenship(nt):
        return False
    if _is_placeholder_text(text):
        return False
    if any(marker in nt for marker in FORM_JUNK_NAME_MARKERS):
        return False
    if parse_date_from_string(text):
        return False
    words = [w for w in nt.split() if w]
    if not words:
        return False
    if len(words) < 2:
        return False
    for i in range(1, len(words)):
        if words[i] == words[i - 1] and len(words[i]) >= 4:
            return False
    for word in words:
        half = len(word) // 2
        if len(word) >= 8 and len(word) % 2 == 0 and word[:half] == word[half:]:
            return False
    letters = alpha_count(nt)
    vowels = sum(ch in "AEIOU" for ch in nt)
    if letters and vowels / letters < 0.16:
        return False
    return True


def _normalize_citizenship(text: str) -> str:
    nt = norm_text(text or "")
    if not nt:
        return ""
    for word in NATIONALITY_WORDS:
        if word in nt:
            return word.title() if word != "FILIPINO" else "FILIPINO"
    if _is_citizenship(nt):
        return nt
    return ""


def _collapse_repeated_words(text: str) -> str:
    words = [w for w in str(text or "").split() if w]
    out = []
    for word in words:
        if out and out[-1].upper() == word.upper():
            continue
        out.append(word)
    return " ".join(out)


def _clean_person_name(text: str) -> str:
    """Strip form chrome that often sticks to OCR name rows (Sex, months, citizenship)."""
    raw = _collapse_repeated_words(str(text or "").strip())
    if not raw:
        return ""
    drop = {
        "MALE", "FEMALE", "SEX", "UNKNOWN", "SINGLE", "TWIN", "TRIPLET",
        "FIRST", "MIDDLE", "LAST", "NAME", "MAIDEN", "OF", "THE", "CHILD",
        "MOTHER", "FATHER", "FILIPINO", "FILIPINA", "CITIZENSHIP", "RELIGION",
        "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER",
        "OCTOBER", "NOVEMBER", "DECEMBER", "JANUARY", "FEBRUARY",
        "JAN", "FEB", "MAR", "APR", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",
    }
    kept = []
    for w in raw.split():
        nw = norm_text(w)
        if not nw or nw in drop:
            continue
        if re.fullmatch(r"\d+[A-Z]?", nw):
            continue
        if len(nw) <= 1:
            continue
        kept.append(w)
    cleaned = _collapse_repeated_words(" ".join(kept))
    if not _is_plausible_person_name(cleaned):
        return ""
    return cleaned


# =========================================================
# HELPERS
# =========================================================
def clean_text(t: str) -> str:
    t = str(t).strip()
    t = t.replace("|", " ")
    t = re.sub(r"[._]{2,}", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def apply_word_fixes(text: str) -> str:
    out = str(text)
    for bad, good in OCR_WORD_FIXES.items():
        out = re.sub(rf"\b{re.escape(bad)}\b", good, out, flags=re.I)
    return out


def norm_text(t: str) -> str:
    t = clean_text(t).upper()
    t = apply_word_fixes(t)
    t = re.sub(r"[^A-Z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def compact_text(t: str) -> str:
    return re.sub(r"\s+", "", norm_text(t))


def alpha_count(t: str) -> int:
    return sum(ch.isalpha() for ch in str(t))


def tokens_in_region(items, xmin, xmax, ymin, ymax, min_score=0.20):
    out = []
    for it in items:
        if it["score"] < min_score:
            continue
        if xmin <= it["cx"] <= xmax and ymin <= it["cy"] <= ymax:
            out.append(it)
    out.sort(key=lambda x: (x["cy"], x["x1"]))
    return out


def group_rows(tokens, y_tol=12):
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


def pattern_score(text: str, patterns):
    nt = norm_text(text)
    ct = compact_text(text)

    best = 0.0
    for pat in patterns:
        p = norm_text(pat)
        pc = compact_text(pat)

        if all(tok in nt for tok in p.split()):
            best = max(best, 10.0 + len(p.split()))
            continue

        ratio = difflib.SequenceMatcher(None, ct, pc).ratio()
        if ratio >= 0.60:
            best = max(best, ratio * 10.0)

    return best


def find_best_label(items, patterns, x_min=None, x_max=None, y_min=None, y_max=None, min_score=0.20, min_match_score=5.8):
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

        ps = pattern_score(it["text"], patterns)
        if ps < min_match_score:
            continue

        tup = (ps, it["score"], -len(norm_text(it["text"])))
        if best is None or tup > best_tuple:
            best = it
            best_tuple = tup

    return best


def find_child_name_label(items, img_w, img_h, dob_label=None):
    """
    Form 102 item 1 is near the top. A bare OCR token 'NAME' also appears under
    Maiden Name (mother) ~y 0.27 — never pick that as the child label.
    """
    y_max = img_h * 0.20
    if dob_label is not None:
        y_max = min(y_max, max(dob_label["y1"] - 2, img_h * 0.10))

    candidates = []
    for it in items:
        if it["score"] < 0.15:
            continue
        if it["cx"] > img_w * 0.55:
            continue
        if it["cy"] < img_h * 0.04 or it["cy"] > y_max:
            continue

        nt = norm_text(it["text"])
        ct = compact_text(it["text"])
        if "MOTHER" in nt or "FATHER" in nt or "MAIDEN" in nt:
            continue
        if "CHILD" in nt and "NAME" in nt:
            candidates.append((0, it))
            continue
        if nt in {"1 NAME", "1NAME", "I NAME"} or ct in {"1NAME", "INAME"}:
            candidates.append((1, it))
            continue
        if re.fullmatch(r"[1I][ .]*NAME", nt):
            candidates.append((1, it))
            continue
        # Bare 'NAME' / '.NAME' only in the top child band (already limited by y_max).
        if nt == "NAME" or ct == "NAME":
            candidates.append((2, it))
            continue

    if candidates:
        candidates.sort(key=lambda pair: (pair[0], pair[1]["cy"], pair[1]["x1"]))
        return candidates[0][1]

    # Strict fallback — no fuzzy match on bare NAME (that hits the mother row).
    return find_best_label(
        items,
        patterns=["NAME OF CHILD", "NAME OF THE CHILD", "1 NAME", "1. NAME", "1.NAME"],
        x_min=0,
        x_max=img_w * 0.55,
        y_min=img_h * 0.04,
        y_max=y_max,
        min_match_score=6.5,
    )


def find_mother_name_label(items, img_w, img_h, y_min=None):
    """
    Locate 'Maiden name of mother' only.

    Fuzzy matching alone is unsafe: NAME OF CHILD ≈ NAME OF MOTHER (~0.78),
    which previously made Name of Mother copy the child's name.
    Prefer the 'NAME' token under MAIDEN when both are present (write-ins sit on that row).
    """
    y0 = y_min if y_min is not None else img_h * 0.20
    maiden = None
    name_under = None
    mother_named = None
    for it in items:
        if it["score"] < 0.15:
            continue
        if it["cx"] > img_w * 0.60:
            continue
        if it["cy"] < y0 or it["cy"] > img_h * 0.58:
            continue
        nt = norm_text(it["text"])
        ct = compact_text(it["text"])
        if "CHILD" in nt or "FATHER" in nt:
            continue
        if "MAIDEN" in nt:
            maiden = it if maiden is None or it["cy"] < maiden["cy"] else maiden
            continue
        if "NAME" in nt and "MOTHER" in nt:
            mother_named = it if mother_named is None or it["cy"] < mother_named["cy"] else mother_named
            continue
        if "MOTHER" in nt and mother_named is None:
            mother_named = it
            continue
    if maiden:
        # Use the NAME row just under/ beside MAIDEN — that is where First/Middle/Last sit.
        for it in items:
            if it["score"] < 0.15 or it["cx"] > img_w * 0.55:
                continue
            nt = norm_text(it["text"])
            ct = compact_text(it["text"])
            if nt != "NAME" and ct != "NAME":
                continue
            if it["cy"] < maiden["y1"] - 8 or it["cy"] > maiden["y2"] + img_h * 0.04:
                continue
            if name_under is None or it["cy"] < name_under["cy"]:
                name_under = it
        return name_under or maiden
    return mother_named


def find_father_name_label(items, img_w, img_h, y_min=None):
    """Locate father name label; never fuzzy-match mother/child name rows."""
    y0 = y_min if y_min is not None else img_h * 0.32
    candidates = []
    for it in items:
        if it["score"] < 0.15:
            continue
        if it["cx"] > img_w * 0.60:
            continue
        if it["cy"] < y0 or it["cy"] > img_h * 0.62:
            continue
        nt = norm_text(it["text"])
        ct = compact_text(it["text"])
        if "MOTHER" in nt or "CHILD" in nt or "MAIDEN" in nt:
            continue
        if "NAME OF FATHER" in nt or ("FATHER" in nt and "NAME" in nt):
            candidates.append((0, it))
            continue
        if "FATHER" in nt:
            candidates.append((1, it))
            continue
        # Form 102 uses 14. NAME; OCR often drops the leading 1 -> '4.NAME'.
        if re.fullmatch(r"(4|12|13|14)[ .]*NAME", nt) or ct in {
            "4NAME", "12NAME", "13NAME", "14NAME",
        }:
            candidates.append((2, it))
            continue
        if nt == "NAME" or ct == "NAME":
            candidates.append((3, it))
            continue
    if candidates:
        candidates.sort(key=lambda pair: (pair[0], pair[1]["cy"], pair[1]["x1"]))
        return candidates[0][1]
    return None


def find_label_below(items, anchor_label, patterns, y_min_offset=2, y_max_offset=95, x_max=None, min_match_score=5.8):
    if not anchor_label:
        return None

    return find_best_label(
        items,
        patterns=patterns,
        x_min=0,
        x_max=x_max,
        y_min=anchor_label["y2"] + y_min_offset,
        y_max=anchor_label["y2"] + y_max_offset,
        min_score=0.20,
        min_match_score=min_match_score
    )


def normalize_month_word(word: str) -> str:
    w = norm_text(word)
    if w in MONTH_FIXES:
        return MONTH_FIXES[w]
    if w in MONTHS:
        return w
    match = difflib.get_close_matches(w, list(MONTHS), n=1, cutoff=0.55)
    if match:
        return match[0]
    return w


def normalize_simple_text(text: str) -> str:
    text = apply_word_fixes(clean_text(text))

    # Filter out bad keywords
    for bad in BAD_VALUE_KEYWORDS:
        text = re.sub(rf"\b{re.escape(bad)}\b", " ", text, flags=re.I)

    text = re.sub(r"\b\d+[A-Z]?\.\s*", " ", text, flags=re.I)

    # Remove form noise patterns
    text = re.sub(r"\[.*?\]", " ", text)  # Remove bracketed content
    text = re.sub(r"TYPE\s*OF\s*BIRTH.*", " ", text, flags=re.I)
    text = re.sub(r"IF\s*MULTIPLE.*", " ", text, flags=re.I)
    text = re.sub(r"BIRTHCHILD.*", " ", text, flags=re.I)

    words = []
    for w in text.split():
        wn = norm_text(w)
        if len(wn) <= 1:
            continue
        if re.fullmatch(r"[A-Z]{1,2}", wn):
            continue
        words.append(w)

    text = " ".join(words)
    text = re.sub(r"\s+", " ", text).strip(" .:-")
    return text


def parse_date_from_string(text: str) -> str:
    raw = apply_word_fixes(clean_text(text)).upper()
    raw = raw.replace(",", " ").replace("/", " ").replace("-", " ")
    raw = re.sub(r"\s+", " ", raw).strip()

    month_pattern = (
        r"JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|"
        r"SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER|MABCK|MARCK|MARCHK|JAMUARY|"
        r"JAN|FEB|MARC?H?|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
    )

    # DD MONTH YYYY
    m = re.search(rf"\b(\d{{1,2}})\s+({month_pattern})\s+(\d{{4}})\b", raw)
    if m:
        try:
            day = str(int(m.group(1)))
        except ValueError:
            return ""
        month = normalize_month_word(m.group(2))
        year = m.group(3)
        return f"{day} {month} {year}"

    # MONTH DD YYYY
    m = re.search(rf"\b({month_pattern})\s+(\d{{1,2}})\s+(\d{{4}})\b", raw)
    if m:
        month = normalize_month_word(m.group(1))
        try:
            day = str(int(m.group(2)))
        except ValueError:
            return ""
        year = m.group(3)
        return f"{day} {month} {year}"

    # DD-MM-YYYY or DD/MM/YYYY normalized to spaces
    m = re.search(r"\b(\d{1,2})\s+(\d{1,2})\s+(\d{4})\b", raw)
    if m:
        try:
            day, mon, year = int(m.group(1)), int(m.group(2)), m.group(3)
        except ValueError:
            return ""
        if 1 <= mon <= 12:
            month_names = ["", "JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE",
                          "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
            month = month_names[mon]
            return f"{day} {month} {year}"

    return ""


def normalize_date_text(tokens) -> str:
    rows = group_rows(tokens, y_tol=14)

    found = []
    for row in rows:
        row_text = join_tokens(row)
        parsed = parse_date_from_string(row_text)
        if parsed:
            found.append((sum(t["cy"] for t in row) / len(row), parsed))

        for it in row:
            parsed_single = parse_date_from_string(it["text"])
            if parsed_single:
                found.append((it["cy"], parsed_single))

    if not found:
        return ""

    found.sort(key=lambda x: x[0])
    return found[-1][1]


def extract_registry_number(tokens) -> str:
    """Extract registry number from tokens; accepts multiple formats."""
    vals = []
    for it in tokens:
        t = clean_text(it["text"])
        t2 = re.sub(r"\s*-\s*", "-", t)
        t_compact = re.sub(r"\s+", "", t)

        # 1234 56789 or 1234-56789
        if re.fullmatch(r"\d{4}\s\d{3,}", t):
            vals.append((it["score"], t))
        if re.fullmatch(r"\d{4}\s*-\s*\d{3,}", t2) or re.fullmatch(r"\d{4}-\d{3,}", t_compact):
            normalized = re.sub(r"\s*-\s*", "-", t).strip()
            if normalized not in [v[1] for v in vals]:
                vals.append((it["score"], normalized))
        # NN-NNNNNN style
        if re.fullmatch(r"\d{2,8}-\d{2,8}", t2):
            vals.append((it["score"], t2))
        # PSA / municipal style: 34125-07-RE330
        if re.fullmatch(r"[A-Z0-9]{2,8}-\d{2,4}-[A-Z0-9]{2,10}", t2, flags=re.I):
            vals.append((it["score"] + 0.2, t2.upper()))
        if re.fullmatch(r"[A-Z0-9]{3,8}-\d{2,6}", t2, flags=re.I) and re.search(r"[A-Z]", t2, flags=re.I):
            vals.append((it["score"], t2.upper()))
        # digits only — keep as-is; do not invent a hyphen (202503321 → 20250-3321 is wrong)
        if re.fullmatch(r"\d{6,12}", t_compact):
            vals.append((it["score"] * 0.45, t_compact))

    if not vals:
        return ""

    vals.sort(key=lambda x: -x[0])
    return vals[0][1]


def infer_working_xmax(items, img_w, img_h):
    blockers = [
        find_best_label(items, ["FOR OCRG USE ONLY"], x_min=img_w * 0.55, y_min=0, y_max=img_h * 0.30, min_match_score=6.5),
        find_best_label(items, ["POPULATION REFERENCES NO"], x_min=img_w * 0.55, y_min=0, y_max=img_h * 0.30, min_match_score=6.0),
        find_best_label(items, ["TO BE FILLED UP AT THE OFFICE OF THE CIVIL REGISTRAR"], x_min=img_w * 0.55, y_min=0, y_max=img_h * 0.35, min_match_score=6.0),
        find_best_label(items, ["REMARKS ANNOTATION"], x_min=img_w * 0.55, y_min=0, y_max=img_h * 0.20, min_match_score=6.0),
    ]
    xs = [b["x1"] for b in blockers if b is not None]
    if xs:
        return min(xs) - 10
    return int(img_w * 0.95)


def helper_like_token(it):
    txt = clean_text(it["text"])
    nt = norm_text(txt)

    if "(" in txt or ")" in txt:
        return True
    if nt in {"FIRST", "MIDDLE", "LAST"}:
        return True
    return False


def infer_slot_centers(items, name_label, max_x):
    helper_tokens = tokens_in_region(
        items,
        name_label["x1"] + 35,
        max_x,
        name_label["y1"] - 8,
        name_label["y2"] + 14,
        min_score=0.20
    )

    candidates = [it for it in helper_tokens if helper_like_token(it)]
    candidates = sorted(candidates, key=lambda x: x["cx"])

    if len(candidates) >= 3:
        candidates = candidates[:3]
        return [c["cx"] for c in candidates]

    left = name_label["x1"] + 90
    right = max_x - 30
    width = max(90, right - left)
    return [
        left + width * 0.18,
        left + width * 0.46,
        left + width * 0.74,
    ]


def assign_tokens_to_slots(tokens, centers):
    first_parts, middle_parts, last_parts = [], [], []

    for it in sorted(tokens, key=lambda x: x["x1"]):
        t = clean_text(it["text"])
        nt = norm_text(t)

        if not t:
            continue
        if "(" in t or ")" in t:
            continue
        if re.fullmatch(r"\d+", nt):
            continue
        if alpha_count(t) < 2:
            continue
        if nt in {"NAME", "FIRST", "MIDDLE", "LAST"}:
            continue

        if any(k in nt for k in [
            "DATE OF BIRTH", "PLACE OF", "CITIZENSHIP", "RELIGION",
            "TYPE OF BIRTH", "BIRTH ORDER", "OCCUPATION",
            "PROVINCE", "CITY MUNICIPALITY", "FOR OCRG", "REGISTRY"
        ]):
            continue

        d = [abs(it["cx"] - c) for c in centers]
        slot = d.index(min(d))

        if slot == 0:
            first_parts.append(t)
        elif slot == 1:
            middle_parts.append(t)
        else:
            last_parts.append(t)

    parts = []
    if first_parts:
        parts.append(" ".join(first_parts))
    if middle_parts:
        parts.append(" ".join(middle_parts))
    if last_parts:
        parts.append(" ".join(last_parts))

    return normalize_simple_text(" ".join(parts))


def extract_psa_person_name(items, label, stop_y, working_xmax, exclude_keywords=None):
    """Read a Form 102 name row as First, Middle, Last."""
    if not label:
        return ""
    tokens = tokens_in_region(
        items,
        label["x1"] + 20,
        working_xmax,
        label["y1"] - 8,
        stop_y,
        min_score=0.18,
    )
    name = assign_tokens_to_slots(tokens, infer_slot_centers(items, label, working_xmax))
    if name:
        return name
    name = compose_first_middle_last(tokens, label["x1"] + 20, working_xmax)
    if name:
        return name
    return extract_name_field_v2(
        items, label, stop_y, working_xmax, exclude_keywords=exclude_keywords
    )


def row_has_field_keywords(row):
    nt = norm_text(join_tokens(row))
    bad_parts = [
        "FIRST", "MIDDLE", "LAST",
        "DATE OF BIRTH", "SEX", "PLACE OF", "CITIZENSHIP", "RELIGION",
        "TYPE OF BIRTH", "BIRTH ORDER", "OCCUPATION", "RESIDENCE",
        "REGISTRY", "PROVINCE", "CITY MUNICIPALITY",
        "FOR OCRG", "POPULATION REFERENCES"
    ]

    if nt == "NAME":
        return True

    return any(bp in nt for bp in bad_parts)


def extract_name_field_v2(items, label, stop_y, max_x, exclude_keywords=None):
    """
    Improved name extraction - looks for name tokens on/below the label row.
    Form 102 writes First / Middle / Last on the same row as the NAME label.
    """
    if exclude_keywords is None:
        exclude_keywords = []

    if not label:
        return ""

    # Prefer the write-in row (same band as the label), then a short band below.
    search_top = label["y1"] - 8
    search_bottom = min(max(stop_y + 10, label["y2"] + 36), label["y2"] + 70)

    region = tokens_in_region(
        items,
        label["x2"] + 2,
        max_x,
        search_top,
        search_bottom,
        min_score=0.15
    )

    name_tokens = []
    for it in region:
        txt = clean_text(it["text"])
        nt = norm_text(txt)

        if len(txt) < 2:
            continue
        if re.fullmatch(r"\d+", nt):
            continue
        if re.search(r"\d{4}", nt):
            continue
        if any(kw in nt for kw in exclude_keywords):
            continue
        if any(kw in nt for kw in BAD_VALUE_KEYWORDS):
            continue
        if nt in {"FIRST", "MIDDLE", "LAST", "NAME", "MAIDEN", "OF", "THE", "CHILD", "MOTHER", "FATHER"}:
            continue
        if _is_citizenship(nt):
            continue
        if _is_placeholder_text(txt):
            continue
        if any(marker in nt for marker in FORM_JUNK_NAME_MARKERS):
            continue

        name_tokens.append(it)

    def _best_name_from_tokens(tokens):
        if not tokens:
            return ""
        rows = group_rows(tokens, y_tol=12)
        best_row = None
        best_score = 0
        for row in rows:
            row_text = join_tokens(row)
            cleaned = normalize_simple_text(row_text)
            if not _is_plausible_person_name(cleaned):
                # Still allow partial rows with 2+ alpha words before final sanitize.
                words = [w for w in norm_text(cleaned).split() if len(w) > 1]
                if len(words) < 2 or alpha_count(cleaned) < 6:
                    continue
            alpha = alpha_count(row_text)
            score = alpha * len(row_text) / 100
            # Prefer rows closest to the label vertically.
            row_cy = sum(t["cy"] for t in row) / len(row)
            score -= abs(row_cy - label["cy"]) / 200.0
            if score > best_score:
                best_score = score
                best_row = row
        if not best_row:
            return ""
        return normalize_simple_text(join_tokens(best_row))

    same_row_name = _best_name_from_tokens(name_tokens)
    if same_row_name and _is_plausible_person_name(same_row_name):
        return same_row_name

    below_region = tokens_in_region(
        items,
        label["x1"] - 10,
        max_x,
        label["y2"] + 2,
        min(stop_y + 15, label["y2"] + 55),
        min_score=0.15,
    )
    below_tokens = []
    for it in below_region:
        txt = clean_text(it["text"])
        nt = norm_text(txt)
        if len(txt) < 2 or re.fullmatch(r"\d+", nt) or re.search(r"\d{4}", nt):
            continue
        if any(kw in nt for kw in exclude_keywords) or any(kw in nt for kw in BAD_VALUE_KEYWORDS):
            continue
        if nt in {"FIRST", "MIDDLE", "LAST", "NAME", "MAIDEN"}:
            continue
        if _is_citizenship(nt) or _is_placeholder_text(txt):
            continue
        if any(marker in nt for marker in FORM_JUNK_NAME_MARKERS):
            continue
        below_tokens.append(it)
    below_name = _best_name_from_tokens(below_tokens)

    candidates = [c for c in (same_row_name, below_name) if c]
    for cand in candidates:
        if _is_plausible_person_name(cand):
            return cand
    return candidates[0] if candidates else ""


def extract_best_name_in_band(items, y_min, y_max, x_min, x_max):
    region = tokens_in_region(items, x_min, x_max, y_min, y_max, min_score=0.14)
    best = ""
    best_score = 0
    for row in group_rows(region, y_tol=11):
        text = normalize_simple_text(join_tokens(row))
        if not text or _is_placeholder_text(text):
            continue
        nt = norm_text(text)
        if any(kw in nt for kw in (
            "DATE OF BIRTH", "PLACE OF", "CITIZENSHIP", "RELIGION", "SEX",
            "TYPE OF BIRTH", "REGISTRY", "CERTIFICATE", "REPUBLIC", "PHILIPPINES",
            "NAME OF", "MOTHER", "FATHER", "MARRIAGE",
        )):
            continue
        if not _is_plausible_person_name(text):
            continue
        score = alpha_count(text)
        if score > best_score:
            best_score = score
            best = text
    return best


def extract_bottom_row_text(tokens):
    rows = group_rows(tokens, y_tol=12)
    if not rows:
        return ""
    return join_tokens(rows[-1])


def extract_value_near_label(items, label, max_x, x_span=150, y_up=8, y_down=48):
    if not label:
        return ""

    region = tokens_in_region(
        items,
        label["x1"] + 20,
        min(max_x, label["x2"] + x_span),
        label["y1"] - y_up,
        label["y2"] + y_down,
        min_score=0.20
    )

    rows = group_rows(region, y_tol=10)
    best_text = ""
    best_penalty = None

    for row in rows:
        row_y = sum(t["cy"] for t in row) / len(row)
        dy = row_y - label["cy"]

        if dy < -12 or dy > y_down + 8:
            continue

        kept = []
        for it in row:
            nt = norm_text(it["text"])

            if nt in {"CITIZENSHIP", "RELIGION", "7", "8", "14", "15", "7.", "8.", "14.", "15."}:
                continue
            if any(bad in nt for bad in BAD_VALUE_KEYWORDS):
                continue
            if alpha_count(it["text"]) < 2:
                continue

            kept.append(it)

        txt = normalize_simple_text(join_tokens(kept))
        if not txt:
            continue

        penalty = abs(max(dy, 0))
        if best_penalty is None or penalty < best_penalty:
            best_penalty = penalty
            best_text = txt

    return best_text


def extract_date_field(items, dob_label, max_x):
    """Extract date of birth with improved logic."""
    if not dob_label:
        return ""

    region = tokens_in_region(
        items,
        dob_label["x2"] + 4,
        min(max_x, dob_label["x2"] + 420),
        dob_label["y1"] - 8,
        dob_label["y2"] + 28,
        min_score=0.15
    )
    parsed = normalize_date_text(region)
    if parsed:
        return parsed

    # First try the immediate area below the label
    region = tokens_in_region(
        items,
        dob_label["x1"] + 40,
        min(max_x, dob_label["x2"] + 280),
        dob_label["y1"] - 6,
        dob_label["y2"] + 44,
        min_score=0.15
    )

    parsed = normalize_date_text(region)
    if parsed:
        return parsed

    # Try with expanded region
    region = tokens_in_region(
        items,
        dob_label["x1"] + 10,
        min(max_x, dob_label["x2"] + 350),
        dob_label["y1"] - 10,
        dob_label["y2"] + 60,
        min_score=0.12
    )
    return normalize_date_text(region)


def extract_date_field_direct(items, label_y, label_x_start, max_x, img_h):
    """
    Direct date extraction - searches in a broader area below the label position.
    """
    # Search in a vertical band starting from label position
    search_top = label_y - 10
    search_bottom = label_y + 110

    region = tokens_in_region(
        items,
        0,  # Full width for DOB
        max_x,
        search_top,
        search_bottom,
        min_score=0.10  # Lower threshold
    )

    if DEBUG_OCR_RAW:
        print("DOB region tokens:")
        print(f"Region bounds: x=0 to {max_x}, y={search_top} to {search_bottom}")
        for it in region:
            print(f"  {it['text']} (score: {it['score']:.2f}) at ({it['cx']:.0f},{it['cy']:.0f})")

    if not region:
        return ""

    # Look for date patterns in the region
    parsed = normalize_date_text(region)
    if parsed:
        return parsed

    # Try parsing each token individually
    for it in region:
        parsed = parse_date_from_string(it["text"])
        if parsed:
            return parsed

    # Last resort: look at all text in the region
    all_text = join_tokens(region)
    return parse_date_from_string(all_text)


def detect_sex(items, sex_label, dob_label=None, img_h=None):
    def _in_upper_form(it):
        return img_h is None or it["cy"] <= img_h * 0.55

    if sex_label:
        xmax = dob_label["x1"] - 5 if dob_label else sex_label["x2"] + 320
        region = tokens_in_region(
            items,
            sex_label["x1"] - 20,
            xmax,
            sex_label["y1"] - 6,
            sex_label["y2"] + 70,
            min_score=0.15
        )
        value = sex_from_tokens(region)
        if value:
            return value

    upper_items = [it for it in items if _in_upper_form(it)]
    value = sex_from_tokens(upper_items)
    if value:
        return value

    return "Unknown"


def extract_place_of_birth(items, pob_label, type_birth_label, working_xmax):
    """Extract place of birth - look to the RIGHT of the label (like the guide)."""
    if not pob_label:
        return ""

    # Stop before Type of Birth / 5a so order/weight options do not leak in.
    next_y = type_birth_label["y1"] - 6 if type_birth_label else pob_label["y2"] + 28
    if next_y <= pob_label["y2"]:
        next_y = pob_label["y2"] + 28

    pob_region = tokens_in_region(
        items,
        pob_label["x2"] + 5,  # Right side of label
        working_xmax,
        pob_label["y1"] - 2,
        next_y,
        min_score=0.20
    )

    if not pob_region:
        pob_region = tokens_in_region(
            items,
            pob_label["x1"],
            working_xmax,
            pob_label["y2"] + 2,
            next_y if type_birth_label else pob_label["y2"] + 40,
            min_score=0.18,
        )

    if not pob_region:
        return ""

    kept = []
    for it in pob_region:
        nt = norm_text(it["text"])
        if any(bad in nt for bad in PLACE_OF_BIRTH_JUNK):
            continue
        if re.search(r"\b\d{3,5}\s*G\b", nt) or re.fullmatch(r"\d{3,5}", nt):
            continue
        kept.append(it)

    place_text = sanitize_place_of_birth(
        normalize_simple_text(extract_bottom_row_text(kept or pob_region))
    )
    return place_text


def extract_marriage_of_parents(items, marriage_label, working_xmax, img_w, img_h):
    """Form 102 items 20a DATE (left) and 20b PLACE (right), under Marriage of Parents."""
    mid = img_w * 0.48
    y0 = img_h * 0.48
    y1 = img_h * 0.66
    if marriage_label:
        y0 = max(img_h * 0.42, marriage_label["y1"] - 6)
        y1 = min(img_h * 0.72, max(marriage_label["y2"] + int(img_h * 0.12), y0 + 36))

    date_tokens = tokens_in_region(
        items, img_w * 0.03, mid + 8, y0, y1, min_score=0.10
    )
    place_tokens = tokens_in_region(
        items, mid - 8, working_xmax, y0, y1, min_score=0.10
    )

    def _drop_heading(toks):
        kept = []
        for it in toks:
            nt = norm_text(it.get("text") or "")
            if not nt:
                continue
            if any(
                marker in nt
                for marker in (
                    "MARRIAGE OF PARENTS",
                    "IF NOT MARRIED",
                    "AFFIDAVIT",
                    "PATERNITY",
                    "ATTENDANT",
                    "21A",
                    "21B",
                )
            ):
                continue
            kept.append(it)
        return kept

    date_tokens = _drop_heading(date_tokens)
    place_tokens = _drop_heading(place_tokens)
    date_text = normalize_date_text(date_tokens)
    place_blob = join_tokens(place_tokens) or extract_bottom_row_text(place_tokens)
    place_text = sanitize_place_of_marriage_of_parents(place_blob)
    return date_text, place_text


def find_date_in_form(items, img_h):
    """Fallback: Search entire form for date patterns."""
    dates = []
    for it in items:
        if it["score"] < 0.12:
            continue
        # DOB typically upper-middle
        if it["cy"] > img_h * 0.65:
            continue
        parsed = parse_date_from_string(it["text"])
        if parsed:
            dates.append((it["cy"], parsed))

    if dates:
        dates.sort(key=lambda x: x[0])
        return dates[1][1] if len(dates) > 1 else dates[0][1]  # Prefer 2nd (DOB after reg)
    return ""


def find_place_in_form(items, img_h, working_xmax):
    """Fallback: Search for place-like text in expected areas."""
    location_keywords = [
        "MANILA", "CEBU", "DAVAO", "QUEZON", "CITY", "PROVINCE", "TOWN", "MUNICIPAL",
        "BARANGAY", "RIZAL", "LAGUNA", "BATANGAS", "NUEVA", "ILOCOS", "VISAYAS",
        "METRO", "CALOOCAN", "PASAY", "MAKATI", "QC", "HOSPITAL", "CLINIC",
        "MEDICAL", "HEALTH", "CENTER", "ST.", "SAINT", "METROPOLITAN",
        "PHILIPPINES", "BULACAN", "ALBAY", "DARAGA", "BICOL", "LEGAZPI",
        "CAMARINES", "SORSOGON", "MASBATE", "CATANDUANES",
    ]

    candidates = []
    for it in items:
        if it["score"] < 0.12:
            continue
        # Look in middle portion
        if it["cy"] < img_h * 0.15 or it["cy"] > img_h * 0.55:
            continue

        txt = clean_text(it["text"])
        nt = norm_text(txt)

        # Skip if too short
        if len(nt) < 4:
            continue
        # Skip if contains year
        if re.search(r"\d{4}", nt):
            continue
        # Skip form labels
        if any(kw in nt for kw in BAD_VALUE_KEYWORDS):
            continue

        if not _is_plausible_place(txt):
            continue

        # Check for location keywords
        if any(loc in nt for loc in location_keywords):
            candidates.append((it["cy"], txt))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    return ""


def find_marriage_in_form(items, img_h, img_w):
    """Fallback: 20a/20b band — date on the left, place on the right."""
    dates = []
    places = []
    y_min = img_h * 0.46
    y_max = img_h * 0.68
    mid = img_w * 0.48

    for it in items:
        if it["score"] < 0.10:
            continue
        if it["cy"] < y_min or it["cy"] > y_max:
            continue
        txt = clean_text(it["text"])
        nt = norm_text(txt)
        if any(
            kw in nt
            for kw in (
                "FATHER", "MOTHER", "CITIZEN", "RELIGION", "CATHOLIC",
                "ATTENDANT", "AFFIDAVIT", "MARRIAGE OF PARENTS",
            )
        ):
            continue
        parsed = parse_date_from_string(txt)
        if parsed and it["cx"] <= mid + img_w * 0.08:
            dates.append((it["cy"], parsed))
            continue
        if it["cx"] < mid - img_w * 0.04:
            continue
        place = sanitize_place_of_marriage_of_parents(txt)
        if place:
            places.append((it["cy"], place, len(place)))

    date_result = ""
    place_result = ""
    if dates:
        dates.sort(key=lambda x: x[0])
        date_result = dates[0][1]
    if places:
        places.sort(key=lambda x: (-x[2], x[0]))
        place_result = places[0][1]
    return date_result, place_result


def find_remarks_label(items, img_w, img_h):
    """Find the REMARKS/ANNOTATION label."""
    return find_best_label(
        items,
        patterns=["REMARKS", "ANNOTATION", "REMARKS ANNOTATION"],
        x_min=img_w * 0.50,
        x_max=img_w * 0.98,
        y_min=img_h * 0.60,
        y_max=img_h * 0.95,
        min_match_score=5.5
    )


def extract_remarks(items, remarks_label, working_xmax):
    """Extract remarks from the form."""
    if not remarks_label:
        return ""

    # Search in the region below the label
    remarks_region = tokens_in_region(
        items,
        remarks_label["x1"] - 20,
        working_xmax,
        remarks_label["y2"] + 5,
        remarks_label["y2"] + 320,
        min_score=0.08
    )

    if not remarks_region:
        return ""

    # Get text from the region
    rows = group_rows(remarks_region, y_tol=12)

    # Get multi-row text, join first few substantial rows
    remark_rows = []
    for row in rows[:3]:  # First 3 rows
        text = normalize_simple_text(join_tokens(row))
        if text and len(text) > 3:
            nt = norm_text(text)
            if not any(kw in nt for kw in ["REMARKS", "ANNOTATION", "FOR OCR", "OFFICE", "REGISTRAR"]):
                remark_rows.append(text)

    return " ".join(remark_rows)


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


BIRTH_OUTPUT_KEYS = [
    "Registry Number",
    "Date of Registration",
    "Name of Child",
    "Sex",
    "Date of Birth",
    "Place of Birth",
    "Type of Birth",
    "Birth Order",
    "Name of Mother",
    "Citizenship of Mother",
    "Religion of Mother",
    "Name of Father",
    "Citizenship of Father",
    "Religion of Father",
    "Date of Marriage of Parents",
    "Place of Marriage of Parents",
    "Remarks",
]


def _sanitize_birth_fields(data: Dict[str, Any]) -> None:
    for name_key in ("Name of Child", "Name of Mother", "Name of Father"):
        val = _clean_person_name(data.get(name_key) or "")
        data[name_key] = val

    child = norm_text(data.get("Name of Child") or "")
    mother = norm_text(data.get("Name of Mother") or "")
    father = norm_text(data.get("Name of Father") or "")
    # Layout/box OCR sometimes latches mother onto the child name row.
    if child and mother and mother == child:
        data["Name of Mother"] = ""
        mother = ""
    if child and father and father == child:
        data["Name of Father"] = ""
        father = ""
    if mother and father and father == mother:
        data["Name of Father"] = ""

    for date_key in ("Date of Birth", "Date of Registration", "Date of Marriage of Parents"):
        val = (data.get(date_key) or "").strip()
        if not val:
            continue
        if _is_placeholder_text(val) or not parse_date_from_string(val):
            data[date_key] = ""
        else:
            data[date_key] = parse_date_from_string(val)

    # Registration date is often confused with Date of Birth when OCR reads the DOB band.
    dob = (data.get("Date of Birth") or "").strip()
    reg = (data.get("Date of Registration") or "").strip()
    if dob and reg and norm_text(dob) == norm_text(reg):
        data["Date of Registration"] = ""

    marriage_date = (data.get("Date of Marriage of Parents") or "").strip()
    if dob and marriage_date and norm_text(dob) == norm_text(marriage_date):
        data["Date of Marriage of Parents"] = ""

    for place_key in ("Place of Birth", "Place of Marriage of Parents"):
        val = (data.get(place_key) or "").strip()
        if not val:
            data[place_key] = ""
            continue
        if place_key == "Place of Birth":
            val = sanitize_place_of_birth(val)
        else:
            val = sanitize_place_of_marriage_of_parents(val)
        if not val:
            # Maybe OCR put a citizenship word here — do not keep junk as place.
            cit = _normalize_citizenship(data.get(place_key) or "")
            if cit:
                if not (data.get("Citizenship of Mother") or "").strip():
                    data["Citizenship of Mother"] = cit
                elif not (data.get("Citizenship of Father") or "").strip():
                    data["Citizenship of Father"] = cit
            data[place_key] = ""
        else:
            data[place_key] = val

    for cit_key in ("Citizenship of Mother", "Citizenship of Father"):
        cit = _normalize_citizenship(data.get(cit_key) or "")
        if cit:
            data[cit_key] = cit
        elif data.get(cit_key) and _is_plausible_place(data[cit_key]) and not _is_citizenship(data[cit_key]):
            data[cit_key] = ""

    sex = (data.get("Sex") or "").strip()
    if sex and sex.lower() == "unknown":
        data["Sex"] = "Unknown"


def _field_confidence(field_name: str, value: str, source: str = "layout") -> str:
    v = (value or "").strip()
    if not v:
        return "LOW"
    n = norm_text(v)
    if field_name == "Sex" and n == "UNKNOWN":
        return "LOW"
    if source == "template":
        return "MEDIUM"
    if field_name == "Sex":
        return "HIGH" if n in {"MALE", "FEMALE", "M", "F"} else "MEDIUM"
    if field_name in {"Date of Birth", "Date of Registration", "Date of Marriage of Parents"}:
        return "HIGH" if parse_date_from_string(v) else "MEDIUM"
    if field_name == "Registry Number":
        compact = n.replace(" ", "")
        if re.fullmatch(r"[A-Z0-9]{2,12}[-/][A-Z0-9]{2,12}(?:[-/][A-Z0-9]{2,12})?", compact):
            return "HIGH"
        return "MEDIUM"
    if field_name in {"Name of Child", "Name of Mother", "Name of Father"}:
        if not _is_plausible_person_name(v):
            return "LOW"
        return "HIGH" if alpha_count(v) >= 6 else "MEDIUM"
    if field_name in {"Place of Birth", "Place of Marriage of Parents"}:
        if not _is_plausible_place(v):
            return "LOW"
        return "HIGH" if alpha_count(v) >= 6 else "MEDIUM"
    if field_name in {"Citizenship of Mother", "Citizenship of Father"}:
        return "HIGH" if _is_citizenship(v) else "MEDIUM"
    if field_name == "Remarks":
        return "MEDIUM"
    return "MEDIUM"


def _template_birth_map(result) -> Dict[str, str]:
    tpl_map: Dict[str, str] = {}
    try:
        ocr_text = ocr_pages_to_text(result)
        tpl = extract_from_ocr_text(ocr_text, doc_type="birth", best_effort=True)
        fields = (tpl.get("fields") or {}) if isinstance(tpl, dict) else {}
        found = sum(1 for v in fields.values() if v)
        if not (tpl.get("template_path") and found >= 1):
            return {}

        def _v(k: str) -> str:
            val = fields.get(k)
            return str(val).strip() if val else ""

        tpl_map = {
            "Registry Number": _v("registry_number"),
            "Name of Child": _v("name_of_child"),
            "Sex": _v("sex"),
            "Date of Birth": _v("date_of_birth"),
            "Place of Birth": _v("place_of_birth"),
            "Name of Mother": _v("name_of_mother"),
            "Name of Father": _v("name_of_father"),
            "Citizenship of Mother": _v("citizenship_of_mother"),
            "Citizenship of Father": _v("citizenship_of_father"),
            "Date of Marriage of Parents": _v("date_of_marriage_of_parents"),
            "Place of Marriage of Parents": _v("place_of_marriage_of_parents"),
            "Date of Registration": _v("date_of_registration"),
        }
        sex = sex_from_ocr_text(tpl_map.get("Sex") or "")
        if sex:
            tpl_map["Sex"] = sex
    except Exception:
        return {}

    cleaned = {}
    for key, val in tpl_map.items():
        if not val or _is_placeholder_text(val):
            continue
        if "Date" in key and not parse_date_from_string(val):
            continue
        if key.startswith("Name of") and not _is_plausible_person_name(val):
            continue
        if key.startswith("Place of") and not _is_plausible_place(val):
            continue
        if "Citizenship" in key and not _normalize_citizenship(val):
            continue
        cleaned[key] = parse_date_from_string(val) if "Date" in key else val
    return cleaned


# =========================================================
# MAIN: extract_birth_data
# =========================================================
def extract_birth_data(img_path: str) -> Dict[str, Any]:
    img = load_cropped_birth_image(img_path, CROP_LEFT, CROP_RIGHT)
    result = run_ocr_on_image(img)
    try:
        box_map: Dict[str, str] = {}
        try:
            box_map = extract_field_boxes(img, "birth", pages=result)
        except Exception:
            box_map = {}
        tpl_map = _template_birth_map(result)

        img_h, img_w = img.shape[:2]
        items = build_items(result)

        if DEBUG_OCR_RAW:
            for it in items:
                print(f'{it["text"]} | ({it["x1"]},{it["y1"]})-({it["x2"]},{it["y2"]})')

        working_xmax = infer_working_xmax(items, img_w, img_h)

        # LABELS
        reg_label = find_best_label(
            items,
            patterns=["REGISTRY NO", "REGIATRY NO", "REGISTRY NO.", "REGISTRY", "REGIATRY", "REGISTRY NUMBER"],
            x_min=img_w * 0.25,
            y_min=0,
            y_max=img_h * 0.28,
            min_match_score=5.5
        )

        reg_date_label = find_best_label(
            items,
            patterns=["DATE REGISTRATION", "REGISTRATION DATE", "DATE OF REG", "REG DATE", "DATE REG"],
            x_min=img_w * 0.25,
            y_min=img_h * 0.02,
            y_max=img_h * 0.32,
            min_match_score=5.0
        )

        dob_label = find_best_label(
            items,
            patterns=["DATE OF BIRTH", "OATE OF BIRTH", "DATEOBIRTH", "DATE OFBIRTH", "3 DATE OF BIRTH", "3.DATE OF BIRTH", "3DATE OF BIRTH"],
            x_min=0,
            x_max=img_w * 0.85,
            y_min=img_h * 0.06,
            y_max=img_h * 0.42,
            min_match_score=5.0
        )

        name_label = find_child_name_label(items, img_w, img_h, dob_label=dob_label)

        sex_label = find_best_label(
            items,
            patterns=["2 SEX", "2. SEX", "SEX"],
            x_min=0,
            x_max=img_w * 0.55,
            y_min=img_h * 0.06,
            y_max=img_h * 0.40,
            min_match_score=5.0
        )

        pob_label = find_best_label(
            items,
            patterns=["PLACE OF BIRTH", "PLACE OF", "4 PLACE OF BIRTH", "4. PLACE OF BIRTH"],
            x_min=0,
            x_max=img_w * 0.38,
            y_min=img_h * 0.12,
            y_max=img_h * 0.42,
            min_match_score=5.5
        )

        type_birth_label = find_best_label(
            items,
            patterns=["TYPE OF BIRTH", "5A TYPE OF BIRTH", "5a TYPE OF BIRTH"],
            x_min=0,
            x_max=img_w * 0.50,
            y_min=img_h * 0.18,
            y_max=img_h * 0.42,
            min_match_score=6.0
        )

        mother_y_min = img_h * 0.20
        for anchor in (type_birth_label, pob_label, dob_label):
            if anchor:
                mother_y_min = max(mother_y_min, anchor["y2"] + 4)
        mother_label = find_mother_name_label(items, img_w, img_h, y_min=mother_y_min)

        mother_cit_label = find_label_below(
            items, mother_label,
            patterns=["CITIZENSHIP", "7 CITIZENSHIP", "7. CITIZENSHIP"],
            y_min_offset=2,
            y_max_offset=60,
            x_max=img_w * 0.45,
            min_match_score=6.0
        )

        mother_rel_label = find_best_label(
            items,
            patterns=["RELIGION", "8 RELIGION", "8. RELIGION"],
            x_min=img_w * 0.20,
            x_max=img_w * 0.75,
            y_min=mother_label["y1"] if mother_label else img_h * 0.22,
            y_max=(mother_label["y1"] + 100) if mother_label else img_h * 0.48,
            min_match_score=6.0
        )

        father_y_min = img_h * 0.32
        if mother_label:
            father_y_min = max(father_y_min, mother_label["y2"] + 8)
        father_label = find_father_name_label(items, img_w, img_h, y_min=father_y_min)

        father_cit_label = find_label_below(
            items, father_label,
            patterns=["CITIZENSHIP", "14 CITIZENSHIP", "14. CITIZENSHIP", "14. CETIZENSHIP"],
            y_min_offset=2,
            y_max_offset=70,
            x_max=img_w * 0.45,
            min_match_score=6.0
        )

        father_rel_label = find_best_label(
            items,
            patterns=["RELIGION", "15 RELIGION", "15. RELIGION", "15RELIBION"],
            x_min=img_w * 0.20,
            x_max=img_w * 0.75,
            y_min=father_label["y1"] if father_label else img_h * 0.34,
            y_max=(father_label["y1"] + 110) if father_label else img_h * 0.62,
            min_match_score=6.0
        )

        marriage_label = find_best_label(
            items,
            patterns=[
                "MARRIAGE OF PARENTS",
                "DATE AND PLACE OF MARRIAGE OF PARENTS",
                "20A DATE",
                "20B PLACE",
                "20a. DATE",
                "20b. PLACE",
                "IF NOT MARRIED",
                "DATE AND PLACE OF MARRIAGE",
                "PLACE OF MARRIAGE OF PARENTS",
            ],
            x_min=0,
            x_max=img_w * 0.92,
            y_min=img_h * 0.42,
            y_max=img_h * 0.72,
            min_match_score=4.8
        )

        remarks_label = find_remarks_label(items, img_w, img_h)

        # Initialize data dictionary
        data = {
            "Registry Number": "",
            "Name of Child": "",
            "Date of Birth": "",
            "Sex": "Unknown",
            "Place of Birth": "",
            "Name of Mother": "",
            "Citizenship of Mother": "",
            "Religion of Mother": "",
            "Name of Father": "",
            "Citizenship of Father": "",
            "Religion of Father": "",
            "Date of Marriage of Parents": "",
            "Place of Marriage of Parents": "",
            "Remarks": "",
        }

        # Registry Number and Date of Registration
        if reg_label:
            reg_tokens = tokens_in_region(
                items,
                reg_label["x1"],
                working_xmax,
                reg_label["y1"] - 12,
                reg_label["y2"] + 45,
                min_score=0.18
            )
            data["Registry Number"] = extract_registry_number(reg_tokens)

        if not data["Registry Number"] and items:
            header = tokens_in_region(items, 0, working_xmax, 0, int(img_h * 0.22), min_score=0.18)
            data["Registry Number"] = extract_registry_number(header)

        # Date of Registration
        reg_date = ""
        if reg_date_label:
            reg_date_region = tokens_in_region(
                items,
                reg_date_label["x1"],
                working_xmax,
                reg_date_label["y1"] - 10,
                reg_date_label["y2"] + 80,
                min_score=0.10
            )
            reg_date = normalize_date_text(reg_date_region)

        # Also check near registry number
        if reg_label and not reg_date:
            reg_date_region = tokens_in_region(
                items,
                reg_label["x1"],
                working_xmax,
                reg_label["y1"] - 40,
                reg_label["y2"] + 80,
                min_score=0.10
            )
            reg_date = normalize_date_text(reg_date_region)

        data["Date of Registration"] = reg_date

        # Name of Child is First | Middle | Last on Form 102 — keep that order.
        if name_label:
            next_y = min(
                [y for y in [
                    dob_label["y1"] if dob_label else None,
                    sex_label["y1"] if sex_label else None
                ] if y is not None] or [name_label["y2"] + 45]
            )
            data["Name of Child"] = extract_psa_person_name(
                items, name_label, next_y, working_xmax,
                exclude_keywords=["DATE OF BIRTH", "SEX", "PLACE OF BIRTH"]
            )
        if not data["Name of Child"]:
            y0 = name_label["y2"] if name_label else img_h * 0.08
            y1 = dob_label["y1"] if dob_label else img_h * 0.28
            data["Name of Child"] = extract_best_name_in_band(
                items, y0, max(y0 + 24, y1), img_w * 0.08, working_xmax
            )

        # Date of Birth - improved extraction
        if dob_label:
            # Try direct extraction below the label
            dob_date = extract_date_field_direct(
                items, dob_label["y2"], dob_label["x1"], working_xmax, img_h
            )
            if dob_date:
                data["Date of Birth"] = dob_date
            else:
                data["Date of Birth"] = extract_date_field(items, dob_label, working_xmax)

        # Fallback: search entire form for date
        if not data["Date of Birth"]:
            data["Date of Birth"] = find_date_in_form(items, img_h)

        # Sex - should be correct from original
        data["Sex"] = detect_sex(items, sex_label, dob_label, img_h=img_h)

        # Place of Birth - improved extraction
        if pob_label:
            data["Place of Birth"] = extract_place_of_birth(items, pob_label, type_birth_label, working_xmax)

        # Fallback: search for place
        if not data["Place of Birth"]:
            data["Place of Birth"] = find_place_in_form(items, img_h, working_xmax)

        # Mother
        if mother_label:
            next_y = min(
                [y for y in [
                    mother_cit_label["y1"] if mother_cit_label else None,
                    mother_rel_label["y1"] if mother_rel_label else None
                ] if y is not None] or [mother_label["y2"] + 45]
            )
            data["Name of Mother"] = extract_psa_person_name(
                items, mother_label, next_y, working_xmax,
                exclude_keywords=["CITIZENSHIP", "RELIGION", "NAME OF FATHER"]
            )
        if not data["Name of Mother"]:
            y0 = mother_label["y2"] if mother_label else img_h * 0.22
            y1 = father_label["y1"] if father_label else img_h * 0.48
            data["Name of Mother"] = extract_best_name_in_band(
                items, y0, max(y0 + 24, min(y1, y0 + 80)), img_w * 0.08, working_xmax
            )

        if mother_cit_label:
            data["Citizenship of Mother"] = _normalize_citizenship(
                extract_value_near_label(
                    items, mother_cit_label,
                    working_xmax,
                    x_span=140,
                    y_up=6,
                    y_down=55
                )
            )

        if mother_rel_label:
            data["Religion of Mother"] = extract_value_near_label(
                items, mother_rel_label,
                working_xmax,
                x_span=140,
                y_up=6,
                y_down=50
            )

        # Father
        if father_label:
            next_y = min(
                [y for y in [
                    father_cit_label["y1"] if father_cit_label else None,
                    father_rel_label["y1"] if father_rel_label else None
                ] if y is not None] or [father_label["y2"] + 45]
            )
            data["Name of Father"] = extract_psa_person_name(
                items, father_label, next_y, working_xmax,
                exclude_keywords=["CITIZENSHIP", "RELIGION", "MARRIAGE"]
            )
        if not data["Name of Father"]:
            y0 = father_label["y2"] if father_label else img_h * 0.36
            y1 = marriage_label["y1"] if marriage_label else img_h * 0.62
            data["Name of Father"] = extract_best_name_in_band(
                items, y0, max(y0 + 24, min(y1, y0 + 80)), img_w * 0.08, working_xmax
            )

        if father_cit_label:
            data["Citizenship of Father"] = _normalize_citizenship(
                extract_value_near_label(
                    items, father_cit_label,
                    working_xmax,
                    x_span=140,
                    y_up=6,
                    y_down=50
                )
            )

        if father_rel_label:
            data["Religion of Father"] = extract_value_near_label(
                items, father_rel_label,
                working_xmax,
                x_span=140,
                y_up=6,
                y_down=50
            )

        # Marriage of Parents (Form 102 items 20a DATE / 20b PLACE)
        date_mar, place_mar = extract_marriage_of_parents(
            items, marriage_label, working_xmax, img_w, img_h
        )
        data["Date of Marriage of Parents"] = date_mar
        data["Place of Marriage of Parents"] = place_mar

        # Fallback: search for marriage info
        if not data["Date of Marriage of Parents"] or not data["Place of Marriage of Parents"]:
            date_fb, place_fb = find_marriage_in_form(items, img_h, img_w)
            if not data["Date of Marriage of Parents"] and date_fb:
                data["Date of Marriage of Parents"] = date_fb
            if not data["Place of Marriage of Parents"] and place_fb:
                data["Place of Marriage of Parents"] = place_fb

        # Remarks
        data["Remarks"] = extract_remarks(items, remarks_label, working_xmax)

        _sanitize_birth_fields(data)

        before_template = {k: (data.get(k) or "") for k in BIRTH_OUTPUT_KEYS}
        for key, val in (tpl_map or {}).items():
            current = (data.get(key) or "").strip()
            if val and (not current or current.lower() == "unknown"):
                data[key] = val
        child_name = norm_text(data.get("Name of Child") or "")
        mother_name = norm_text(data.get("Name of Mother") or "")
        for key, val in (box_map or {}).items():
            if not val:
                continue
            val = str(val).strip()
            if key in {"Name of Child", "Name of Mother", "Name of Father"}:
                if not _is_plausible_person_name(val):
                    continue
                if key != "Name of Child" and child_name and norm_text(val) == child_name:
                    continue
                if key == "Name of Father" and mother_name and norm_text(val) == mother_name:
                    continue
            elif key in {"Place of Birth", "Place of Marriage of Parents"}:
                if key == "Place of Birth":
                    val = sanitize_place_of_birth(val)
                else:
                    val = sanitize_place_of_marriage_of_parents(val)
                if not val:
                    continue
            elif "Citizenship" in key:
                cit = _normalize_citizenship(val)
                if not cit:
                    continue
                val = cit
            elif "Date" in key:
                parsed = parse_date_from_string(val)
                if not parsed:
                    continue
                val = parsed
            elif _is_placeholder_text(val):
                continue
            data[key] = val
        _sanitize_birth_fields(data)

        out = {key: data.get(key, "") for key in BIRTH_OUTPUT_KEYS}
        confidence_map: Dict[str, str] = {}
        source_map: Dict[str, str] = {}
        for key in BIRTH_OUTPUT_KEYS:
            val = (out.get(key) or "").strip()
            if (box_map or {}).get(key) and val:
                source = "box"
            else:
                is_template_fill = (
                    not (before_template.get(key) or "").strip()
                    or (before_template.get(key) or "").strip().lower() == "unknown"
                ) and bool(val) and bool((tpl_map or {}).get(key))
                source = "template" if is_template_fill else "layout"
            source_map[key] = source
            confidence_map[key] = _field_confidence(key, val, source=source)
        out["__confidence__"] = confidence_map
        out["__source__"] = source_map
        return out
    except Exception:
        raise
