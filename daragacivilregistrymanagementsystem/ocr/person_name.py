"""PSA civil-registry names are written First, Middle, Last."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

_SLOT_LABELS = {
    "FIRST", "MIDDLE", "LAST", "NAME", "MAIDEN", "OF", "THE", "CHILD",
    "MOTHER", "FATHER", "HUSBAND", "WIFE", "DECEASED", "INITIAL",
}

WIDE_FML_NAME_KEYS = frozenset(
    {
        "Name of Child",
        "Name of Mother",
        "Name of Father",
        "Name of Deceased",
        "Husband Father",
        "Husband Mother",
        "Wife Father",
        "Wife Mother",
    }
)


def _norm_word(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (text or "").upper())


def compose_first_middle_last(
    tokens: Sequence[Dict[str, Any]],
    x_min: Optional[float] = None,
    x_max: Optional[float] = None,
) -> str:
    """
    Join OCR tokens as First, then Middle, then Last.

    Horizontal PSA rows use left-to-right columns.
    Stacked First/Middle/Last blocks use top-to-bottom order.
    """
    usable: List[Dict[str, Any]] = []
    for it in tokens or []:
        t = re.sub(r"\s+", " ", str(it.get("text") or "").strip())
        if not t or "(" in t or ")" in t:
            continue
        nt = _norm_word(t)
        if not nt or nt in _SLOT_LABELS:
            continue
        if re.fullmatch(r"\d+", nt):
            continue
        if sum(ch.isalpha() for ch in t) < 2:
            continue
        usable.append(it)
    if not usable:
        return ""

    xs = [float(it["cx"]) for it in usable]
    ys = [float(it["cy"]) for it in usable]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    buckets: List[List[str]] = [[], [], []]

    stacked = y_span >= 18 and y_span >= max(18.0, x_span * 0.55)
    if stacked:
        y0, y1 = min(ys), max(ys)
        height = max(1.0, y1 - y0)
        ordered = sorted(usable, key=lambda p: float(p["cy"]))
        for it in ordered:
            rel = min(0.999, max(0.0, (float(it["cy"]) - y0) / height))
            slot = 0 if rel < 0.34 else 1 if rel < 0.67 else 2
            buckets[slot].append(re.sub(r"\s+", " ", str(it["text"]).strip()))
    else:
        left = float(x_min) if x_min is not None else min(float(it["x1"]) for it in usable)
        right = float(x_max) if x_max is not None else max(float(it["x2"]) for it in usable)
        width = max(1.0, right - left)
        ordered = sorted(usable, key=lambda p: float(p["x1"]))
        for it in ordered:
            rel = min(0.999, max(0.0, (float(it["cx"]) - left) / width))
            slot = 0 if rel < 0.34 else 1 if rel < 0.67 else 2
            buckets[slot].append(re.sub(r"\s+", " ", str(it["text"]).strip()))

    parts: List[str] = []
    seen = set()
    for bucket in buckets:
        if not bucket:
            continue
        piece = " ".join(bucket).strip()
        key = " ".join(_norm_word(w) for w in piece.split() if _norm_word(w))
        if not key or key in seen:
            continue
        seen.add(key)
        parts.append(piece)
    return " ".join(parts).strip()
