"""One office folder per archived civil registry record (certificate + supporting papers)."""
from __future__ import annotations

import re

_DOC_TYPES = frozenset({"birth", "marriage", "death"})
_SLUG = re.compile(r"[^\w\s\-]+", re.UNICODE)
_SPACES = re.compile(r"[\s\-]+")


def person_slug(full_name: str, max_len: int = 48) -> str:
    primary = (full_name or "").split("/")[0].strip()
    cleaned = _SLUG.sub("", primary)
    cleaned = _SPACES.sub("_", cleaned).strip("_").lower()
    return (cleaned or "client")[:max_len]


def client_folder_rel(record) -> str:
    doc = (getattr(record, "document_type", None) or "record").strip().lower()
    if doc not in _DOC_TYPES:
        doc = "record"
    rid = int(getattr(record, "id", 0) or 0)
    return f"files/{doc}/{rid:05d}_{person_slug(getattr(record, 'full_name', '') or '')}"


def supporting_folder_rel(record) -> str:
    return f"{client_folder_rel(record)}/supporting"


def certificate_in_client_folder(image_path: str, folder_rel: str) -> bool:
    norm = (image_path or "").replace("\\", "/").strip().lower()
    folder = (folder_rel or "").replace("\\", "/").strip().lower().rstrip("/")
    if not norm or not folder:
        return False
    if not norm.startswith(folder + "/"):
        return False
    return "/supporting/" not in f"/{norm}/"
