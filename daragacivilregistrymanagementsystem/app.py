
import json
import logging
import os
import sys
import time

# Quiet Paddle / oneDNN before those libraries load.
os.environ.setdefault("GLOG_minloglevel", "3")
os.environ.setdefault("GLOG_v", "0")
os.environ.setdefault("FLAGS_minloglevel", "3")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
os.environ.setdefault("KMP_WARNINGS", "0")
os.environ.setdefault("OMP_DISPLAY_ENV", "FALSE")
os.environ.setdefault("WERKZEUG_DEBUG_PIN", "off")
os.environ.setdefault("FLASK_SKIP_DOTENV", "1")
_cpu_n = os.cpu_count() or 4
_ocr_threads = "2" if _cpu_n >= 4 else "1"
os.environ.setdefault("OCR_CPU_THREADS", _ocr_threads)
os.environ.setdefault("OMP_NUM_THREADS", _ocr_threads)
os.environ.setdefault("MKL_NUM_THREADS", _ocr_threads)
os.environ.setdefault("OPENBLAS_NUM_THREADS", _ocr_threads)
os.environ.setdefault("NUMEXPR_NUM_THREADS", _ocr_threads)
os.environ.setdefault("FLAGS_omp_num_threads", _ocr_threads)


class _QuietConsole:
    """Drop Windows/Paddle CLI noise such as: INFO: Could not find files for the given pattern(s)."""

    _DROP = (
        "Could not find files for the given pattern(s).",
        "No ccache found",
        "Debugger PIN",
        "Debugger is active",
        "WARNING: This is a development server",
        "Running on all addresses",
        "Running on http://",
        "Press CTRL+C to quit",
        "* Serving Flask app",
    )

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def write(self, text):
        if not text:
            return 0
        sample = text.decode("utf-8", "replace") if isinstance(text, (bytes, bytearray)) else text
        if any(marker in sample for marker in self._DROP):
            return len(text)
        return self._wrapped.write(text)

    def writelines(self, lines):
        for line in lines:
            self.write(line)

    def flush(self):
        return self._wrapped.flush()

    def isatty(self):
        return bool(getattr(self._wrapped, "isatty", lambda: False)())

    def __getattr__(self, name):
        return getattr(self._wrapped, name)


if not isinstance(sys.stdout, _QuietConsole):
    sys.stdout = _QuietConsole(sys.stdout)
if not isinstance(sys.stderr, _QuietConsole):
    sys.stderr = _QuietConsole(sys.stderr)

import atexit
import base64
import hashlib
import hmac
import random
import re
import secrets
import shutil
import threading
import traceback
import uuid
import zipfile
from datetime import datetime, timedelta
from functools import wraps
from typing import Optional
from io import BytesIO
from pathlib import Path

from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, send_file, jsonify
from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from models import db, User, Record, RecordAttachment, EditRequest, AuditLog, PrintLog, PrintRequest, BackupRun, NotificationRead
from services.record_files import (
    certificate_in_client_folder,
    client_folder_rel,
    supporting_folder_rel,
)
from services.lgu_office import LGU_LOCAL_DATA_DIR
from services.vault_storage import (
    apply_vault,
    vault_status,
    prepare_upload_storage,
    describe_storage_error,
    local_upload_dir,
    ensure_upload_layout,
    ALLOWED_IMAGE_EXTS,
    ALLOWED_UPLOAD_TOP,
)
from services.log_retention import (
    load_settings as load_log_retention_settings,
    save_settings as save_log_retention_settings,
    settings_from_form as log_retention_from_form,
    retention_hint,
    office_log_counts,
)
from services.auto_backup import (
    auto_backup_dir,
    apply_schedule,
    schedule_summary,
    server_backup_display,
    backup_storage_display,
    resolve_backup_root,
    daily_backup_stamp,
    daily_full_backup_filename,
    delete_auto_backup,
    delete_backup_run,
    ensure_backup_dir_writable,
    office_backup_destination,
    env_backup_root,
    get_backup_readiness,
    init_auto_backup_scheduler,
    list_auto_backups,
    list_backup_history,
    load_settings as load_auto_backup_settings,
    _norm_dest_text,
    resolve_auto_backup_path,
    resolve_period_backup_path,
    resolve_server_backup_root,
    run_auto_backup,
    run_full_backup,
    run_period_backup,
    save_settings as save_auto_backup_settings,
    copy_backup_to_host_downloads,
    this_server_name,
    require_lgu_backup_root,
    scheduled_backup_kind,
    settings_from_form,
    sync_local_backups_to_server,
    write_full_backup_zip,
)
from ocr.birth import extract_birth_data
from ocr.marriage import extract_marriage_data
from ocr.death import extract_death_data
from ocr.worker import (
    get_result as _ocr_worker_get_result,
    set_below_normal_priority,
    start_ocr_worker,
    stop_ocr_worker,
    submit_detect_job,
    submit_ocr_job,
    worker_is_alive,
)
from services.certification_print import (
    cert_field,
    cert_issue_date,
    form_meta,
    normalize_print_format,
    print_format_label,
    print_format_short_label,
    PRINT_FORMAT_CERTIFICATION,
    PRINT_FORMAT_ORIGINAL,
    PRINT_FORMAT_BOTH,
)
from services.document_annotation import (
    FORM_KEYS as ANNOTATION_FORM_KEYS,
    HIDDEN_METADATA_KEYS,
    KINDS as ANNOTATION_KINDS,
    apply_to_data as apply_annotation_to_data,
    form_defaults as annotation_form_defaults,
    from_data as document_annotation_from_data,
    from_form as annotation_from_form,
    public_fields as public_record_fields,
)
from services.civil_registry_reports import (
    BIRTH_EXCEL_HEADERS,
    DEATH_EXCEL_HEADERS,
    MARRIAGE_EXCEL_HEADERS,
    birth_excel_row,
    birth_row,
    build_xlsx,
    collect_filter_options,
    register_print_sheets,
    death_excel_row,
    death_row,
    load_record_fields,
    marriage_excel_rows,
    marriage_row,
    record_is_reportable,
    row_matches_filters,
    row_matches_find,
)
try:
    from services.scanner_wia import list_scanners, scan_to_file
except ImportError:
    list_scanners = lambda: []
    def scan_to_file(*args, **kwargs):
        return False, "Scanner support not available (install pywin32 on Windows)."


BASE_DIR = Path(__file__).resolve().parent


def _load_env_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(BASE_DIR / ".env")
    load_dotenv(BASE_DIR.parent / ".env")


_load_env_files()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
ALLOWED_ATTACHMENT_EXTS = frozenset({".jpg", ".jpeg", ".png", ".pdf", ".webp", ".tif", ".tiff"})
ATTACHMENT_KINDS = (
    "Valid ID",
    "Affidavit",
    "Authorization",
    "PSA / court paper",
    "Other",
)
MAX_ATTACHMENTS_PER_UPLOAD = 12

UPLOAD_DIR, _UPLOAD_STORAGE_ERROR = prepare_upload_storage(BASE_DIR)
if _UPLOAD_STORAGE_ERROR:
    logging.getLogger(__name__).warning("Server storage not ready: %s", _UPLOAD_STORAGE_ERROR)


def _require_server_storage() -> Optional[str]:
    """Refuse writes unless the configured server volume is usable. Never falls back to a client disk."""
    global UPLOAD_DIR, _UPLOAD_STORAGE_ERROR
    dest, err = prepare_upload_storage(BASE_DIR)
    UPLOAD_DIR = dest
    _UPLOAD_STORAGE_ERROR = err
    return err


def _safe_path_under(root: Path, norm: str) -> Path | None:
    if not norm:
        return None
    try:
        upload_root = root.resolve()
        full = (root / norm).resolve()
        full.relative_to(upload_root)
        return full
    except (ValueError, OSError):
        full = root / norm
        root_text = str(root).replace("/", "\\").rstrip("\\").lower()
        full_text = str(full).replace("/", "\\").rstrip("\\").lower()
        if full_text == root_text or full_text.startswith(root_text + "\\"):
            return full
        return None


def _normalize_upload_relpath(rel: str) -> str:
    """Keep paths relative to uploads/ (birth/foo.jpg), never uploads/uploads/..."""
    raw = (rel or "").replace("\\", "/").strip()
    if not raw:
        return ""
    while raw.startswith("./"):
        raw = raw[2:]
    parts = [p for p in raw.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return ""
    while parts and parts[0].lower() == "uploads":
        parts = parts[1:]
    return "/".join(parts)


_REGISTRY_IN_NAME = re.compile(r"(20\d{2}-\d{6})", re.I)


def _upload_candidate_roots() -> list[Path]:
    """Folders that may hold certificate scans for this office copy."""
    roots: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path).replace("/", "\\").rstrip("\\").lower()
        if not key or key in seen:
            return
        seen.add(key)
        roots.append(path)

    add(UPLOAD_DIR)
    add(local_upload_dir(BASE_DIR))
    add(LGU_LOCAL_DATA_DIR / "uploads")
    add(Path(r"C:\DaragaCivilRegistry") / "uploads")
    try:
        for extra in BASE_DIR.glob("_restore_uploads_*"):
            if extra.is_dir():
                add(extra)
    except OSError:
        pass
    try:
        desktop = Path.home() / "Desktop"
        for extra in desktop.glob("DaragaCivilRegistry_Package_*/DaragaCivilRegistry/uploads"):
            if extra.is_dir():
                add(extra)
    except OSError:
        pass
    # Same-PC copies of this system (dev folder or another install) so Search still shows scans.
    try:
        for item in Path("C:/").iterdir():
            if not item.is_dir():
                continue
            name = item.name.lower()
            if "daraga" not in name or "civil" not in name:
                continue
            add(item / "uploads")
            add(item / "daragacivilregistrymanagementsystem" / "uploads")
    except OSError:
        pass
    return roots


def _existing_file_for_rel(norm: str) -> Path | None:
    for root in _upload_candidate_roots():
        candidate = _safe_path_under(root, norm)
        if candidate is not None and candidate.is_file():
            return candidate
    return None


def _iter_scan_files(root: Path):
    for sub in ("birth", "marriage", "death", "files", "temp"):
        folder = root / sub
        if not folder.is_dir():
            continue
        try:
            for item in folder.rglob("*"):
                if item.is_file() and item.suffix.lower() in ALLOWED_IMAGE_EXTS:
                    yield item
        except OSError:
            continue


def _path_to_upload_rel(found: Path) -> str:
    for root in _upload_candidate_roots():
        try:
            return _normalize_upload_relpath(found.relative_to(root).as_posix())
        except (ValueError, OSError):
            continue
    return _normalize_upload_relpath(found.name)


def _file_matching_registry(norm: str, registry: str = "", doc_type: str = "") -> Path | None:
    """Find a scan when it was renamed, moved into a client folder, or lives in another copy."""
    name = Path(norm or "").name
    match = _REGISTRY_IN_NAME.search(name or "") or _REGISTRY_IN_NAME.search(norm or "")
    if not match and registry:
        match = _REGISTRY_IN_NAME.search(registry)
    needle = match.group(1) if match else ""
    want_type = (doc_type or "").strip().lower()
    name_l = name.lower()
    hits: list[Path] = []
    for root in _upload_candidate_roots():
        for item in _iter_scan_files(root):
            try:
                in_type = want_type in ("", "record") or want_type in item.as_posix().replace("\\", "/").lower()
                if needle and needle in item.name and in_type:
                    hits.append(item)
                elif name_l and item.name.lower() == name_l:
                    hits.append(item)
            except OSError:
                continue
    if not hits:
        return None
    exact = [item for item in hits if item.name.lower() == name_l] if name_l else []
    if exact:
        exact.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
        return exact[0]
    hits.sort(key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True)
    return hits[0]


def _upload_file_path(rel: str, *, for_write: bool = False) -> Path | None:
    """Resolve a stored image path under server uploads. Legacy and restore folders are read-only."""
    norm = _normalize_upload_relpath(rel)
    if not norm:
        return None
    primary = _safe_path_under(UPLOAD_DIR, norm)
    if for_write:
        return primary
    found = _existing_file_for_rel(norm)
    if found is not None:
        return found
    matched = _file_matching_registry(norm)
    if matched is not None:
        return matched
    return primary


def _repair_record_image_path(record: Record) -> str:
    """Point the record at the scan file if it was moved or this copy uses another uploads folder."""
    rel = _normalize_upload_relpath(record.image_path or record.image_front_path or "")
    found = _upload_file_path(rel) if rel else None
    if found is None or not found.is_file():
        found = _file_matching_registry(
            rel,
            registry=record.registry_number or "",
            doc_type=record.document_type or "",
        )
    if found is None or not found.is_file():
        return rel
    new_rel = _path_to_upload_rel(found)
    if new_rel and new_rel != rel:
        record.image_path = new_rel
        record.image_front_path = new_rel
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
        return new_rel
    return rel or new_rel


def _existing_upload_relpath(rel: str) -> str:
    """Return a safe uploads-relative path only if that file is still on disk."""
    path = _upload_file_path(rel)
    if path is None or not path.is_file():
        return ""
    return _normalize_upload_relpath(rel)


def _ensure_record_client_folder(record: Record) -> str:
    """Create this record's office folder: files/{type}/{id}_{name}/supporting/."""
    rel = client_folder_rel(record)
    folder = _upload_file_path(rel, for_write=True)
    if folder is not None:
        (folder / "supporting").mkdir(parents=True, exist_ok=True)
    return rel


def _place_certificate_in_client_folder(record: Record) -> str:
    """Keep the original certificate inside this client's folder. Safe to call many times."""
    folder_rel = _ensure_record_client_folder(record)
    current = _normalize_upload_relpath(record.image_path or record.image_front_path or "")
    if not current or certificate_in_client_folder(current, folder_rel):
        return folder_rel
    src = _upload_file_path(current)
    if src is None or not src.is_file():
        return folder_rel
    dest_rel = f"{folder_rel}/{src.name}"
    dest = _upload_file_path(dest_rel, for_write=True)
    if dest is None:
        return folder_rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and src.resolve() != dest.resolve():
            dest = dest.with_name(f"{dest.stem}_{uuid.uuid4().hex[:6]}{dest.suffix}")
            dest_rel = f"{folder_rel}/{dest.name}"
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
            if dest.is_file() and dest.stat().st_size > 0:
                record.image_path = dest_rel
                record.image_front_path = dest_rel
                db.session.commit()
                try:
                    src.unlink()
                except OSError:
                    pass
    except OSError:
        db.session.rollback()
    return folder_rel


def _list_record_attachments(record_id: int):
    return (
        RecordAttachment.query.filter_by(record_id=record_id)
        .order_by(RecordAttachment.created_at.desc(), RecordAttachment.id.desc())
        .all()
    )


def _supporting_template_vars(record: Record) -> dict:
    _repair_record_image_path(record)
    return {
        "attachments": _list_record_attachments(record.id),
        "attachment_kinds": ATTACHMENT_KINDS,
        "client_folder": _place_certificate_in_client_folder(record),
    }


def _safe_attachment_filename(original: str, kind: str) -> str:
    ext = Path(original or "").suffix.lower()
    if ext not in ALLOWED_ATTACHMENT_EXTS:
        return ""
    stem = secure_filename(Path(original).stem) or "document"
    kind_part = secure_filename((kind or "other").lower().replace("/", "-")) or "other"
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{kind_part}_{stem[:40]}_{uuid.uuid4().hex[:6]}{ext}"


def _flatten_nested_uploads() -> None:
    """Move files out of uploads/uploads/ into uploads/ and remove the duplicate folder."""
    nested = UPLOAD_DIR / "uploads"
    if not nested.is_dir():
        return
    for src in nested.rglob("*"):
        if not src.is_file():
            continue
        dest = UPLOAD_DIR / src.relative_to(nested)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            continue
        try:
            shutil.move(str(src), str(dest))
        except OSError:
            pass
    shutil.rmtree(nested, ignore_errors=True)


try:
    _flatten_nested_uploads()
except OSError:
    pass

DEFAULT_POSTGRES_URI = "postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/daraga_civil_registry"


def _normalize_database_uri(uri: str) -> str:
    uri = (uri or "").strip()
    if uri.startswith("postgres://"):
        return "postgresql+psycopg2://" + uri[len("postgres://") :]
    if uri.startswith("postgresql://"):
        return "postgresql+psycopg2://" + uri[len("postgresql://") :]
    return uri


def resolve_database_uri() -> str:
    """PostgreSQL via DATABASE_URL in .env. Heroku postgres:// is normalized."""
    uri = (os.environ.get("DATABASE_URL") or "").strip()
    if not uri:
        uri = DEFAULT_POSTGRES_URI
    uri = _normalize_database_uri(uri)
    if uri.lower().startswith("sqlite"):
        raise RuntimeError(
            "This copy uses PostgreSQL only. Set DATABASE_URL in .env "
            "(see .env.example). SQLite is not supported here."
        )
    return uri


def _safe_database_display(uri: str | None = None) -> str:
    raw = resolve_database_uri() if uri is None else (uri or "")
    try:
        from sqlalchemy.engine.url import make_url
        return make_url(raw).render_as_string(hide_password=True)
    except Exception:
        return raw.split("@")[-1] if "@" in raw else raw


def _ddl_column_type(typ: str) -> str:
    return typ.replace("DATETIME", "TIMESTAMP")


def _existing_columns(table: str) -> set[str]:
    r = db.session.execute(
        db.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = :t"
        ),
        {"t": table},
    )
    return {row[0] for row in r.fetchall()}


app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = os.environ.get("SECRET_KEY", "change-this-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = resolve_database_uri()
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024

db.init_app(app)


DOCUMENT_TYPES = {
    "birth": "Birth Certificate",
    "marriage": "Marriage Certificate",
    "death": "Death Certificate",
}

DATE_FIELD_KEYS = {
    "Date of Birth",
    "Date of Death",
    "Date of Marriage",
    "Date of Registration",
    "Date of Marriage of Parents",
}
SEX_FIELD_KEYS = {"Sex"}

REQUIRED_REVIEW_FIELDS = {
    "birth": (
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
    ),
    "marriage": (
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
    ),
    "death": (
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
    ),
}

# JSON keys used for mother's name across document types (stored in Record.data_json).
MOTHER_NAME_JSON_KEYS = (
    "Name of Mother",   # birth
    "Husband Mother",   # marriage
    "Wife Mother",      # marriage
)


def _parse_doc_type_filter_arg(value):
    """Return birth|marriage|death if valid, else None (show all)."""
    if not value:
        return None
    v = (value or "").strip().lower()
    return v if v in DOCUMENT_TYPES else None


def _parse_request_progress_arg(value):
    """Staff monitor filter: pending | complete, else None (all)."""
    v = (value or "").strip().lower()
    return v if v in ("pending", "complete") else None


def _filter_staff_edit_progress(query, progress: str | None):
    if progress == "pending":
        return query.filter(
            or_(
                EditRequest.status == "PENDING",
                and_(EditRequest.status == "APPROVED", EditRequest.code_used_at.is_(None)),
            )
        )
    if progress == "complete":
        return query.filter(
            or_(
                EditRequest.status == "REJECTED",
                EditRequest.code_used_at.isnot(None),
            )
        )
    return query


def _filter_staff_print_progress(query, progress: str | None):
    if progress == "pending":
        return query.filter(
            or_(
                PrintRequest.status == "PENDING",
                and_(PrintRequest.status == "APPROVED", PrintRequest.used_at.is_(None)),
            )
        )
    if progress == "complete":
        return query.filter(
            or_(
                PrintRequest.status == "REJECTED",
                PrintRequest.used_at.isnot(None),
            )
        )
    return query


ADMIN_REQUEST_PAGE_SIZE = 50


def _admin_request_query_args(tab, doc_type=None, progress=None):
    args = {"tab": tab}
    if doc_type:
        args["doc_type"] = doc_type
    if progress:
        args["status"] = progress
    return args


def _paged_admin_requests(model, doc_type, progress, page_arg):
    """Load one page of edit/print requests with real totals for large queues."""
    q = model.query
    if doc_type:
        q = q.filter(model.record.has(Record.document_type == doc_type))
    all_count = q.order_by(None).count()
    pending_count = q.filter(model.status == "PENDING").order_by(None).count()
    listed = q
    if progress == "pending":
        listed = listed.filter(model.status == "PENDING")
    elif progress == "complete":
        listed = listed.filter(model.status != "PENDING")
    try:
        page = int(page_arg or 1)
    except (TypeError, ValueError):
        page = 1
    page = max(1, page)
    per_page = ADMIN_REQUEST_PAGE_SIZE
    total = listed.order_by(None).count()
    pages = max(1, (total + per_page - 1) // per_page) if total else 1
    if page > pages:
        page = pages
    rows = (
        listed.options(joinedload(model.record), joinedload(model.requested_by))
        .order_by(model.requested_at.desc(), model.id.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    start = 0 if total == 0 else (page - 1) * per_page + 1
    end = min(total, page * per_page)
    pager = {
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "total": total,
        "start": start,
        "end": end,
        "has_prev": page > 1,
        "has_next": page < pages,
    }
    counts = {
        "all": all_count,
        "pending": pending_count,
        "complete": max(0, all_count - pending_count),
    }
    return rows, counts, pager


def _admin_edit_requests_redirect():
    """Redirect back to edit requests tab, preserving optional doc_type filter."""
    dt = _parse_doc_type_filter_arg(request.form.get("doc_type") or request.args.get("doc_type"))
    if dt:
        return redirect(url_for("administration", tab="edit_requests", doc_type=dt))
    return redirect(url_for("administration", tab="edit_requests"))


def _admin_print_requests_redirect():
    """Redirect back to print requests tab, preserving optional doc_type filter."""
    dt = _parse_doc_type_filter_arg(request.form.get("doc_type") or request.args.get("doc_type"))
    if dt:
        return redirect(url_for("administration", tab="print_requests", doc_type=dt))
    return redirect(url_for("administration", tab="print_requests"))


def _is_admin_user(user) -> bool:
    return bool(user and (user.role or "").upper() == "ADMIN")


def _new_approval_code() -> str:
    return "".join(random.choices("0123456789", k=5))


def _parsed_approval_code() -> str:
    return "".join(ch for ch in (request.form.get("approval_code") or "") if ch.isdigit())


def _staff_active_print_approval(user_id: int, record_id: int):
    """Return an approved, unused print request for this staff member and record."""
    return (
        PrintRequest.query.filter_by(
            record_id=record_id,
            requested_by_id=user_id,
            status="APPROVED",
        )
        .filter(PrintRequest.used_at.is_(None))
        .order_by(PrintRequest.reviewed_at.desc())
        .first()
    )


def _staff_print_code_unlocked(user, record_id: int) -> bool:
    if not user or (user.role or "").upper() != "STAFF":
        return False
    try:
        allowed_request_id = int(session.get("allowed_print_request_id"))
        allowed_record_id = int(session.get("allowed_print_record_id"))
    except (TypeError, ValueError):
        return False
    if allowed_record_id != record_id:
        return False
    approval = db.session.get(PrintRequest, allowed_request_id)
    return bool(
        approval
        and approval.record_id == record_id
        and approval.requested_by_id == user.id
        and (approval.status or "").upper() == "APPROVED"
        and approval.used_at is None
        and (approval.approval_code or "").strip()
    )


def user_can_print_record(user, record_id: int) -> bool:
    if not user:
        return False
    if _is_admin_user(user):
        return True
    if (user.role or "").upper() != "STAFF":
        return False
    if not _staff_print_code_unlocked(user, record_id):
        return False
    record = db.session.get(Record, record_id)
    if not record:
        return False
    return not missing_required_review_fields(
        record.document_type, _record_print_payload(record)
    )


def user_can_print_during_review(user) -> bool:
    """OCR review forms (before save): admins only."""
    return _is_admin_user(user)


def _create_staff_print_request_after_ocr(user, record) -> bool:
    """Save-time print request from OCR review. Admin still must approve. Does not print."""
    if not user or not record or (user.role or "").upper() != "STAFF":
        return False
    pending = PrintRequest.query.filter_by(
        record_id=record.id, requested_by_id=user.id, status="PENDING"
    ).first()
    if pending:
        return False
    has_scan = bool((record.image_path or record.image_front_path or "").strip())
    print_format = PRINT_FORMAT_BOTH if has_scan else PRINT_FORMAT_CERTIFICATION
    req = PrintRequest(
        record_id=record.id,
        requested_by_id=user.id,
        reason="Requested after scan and OCR review.",
        print_format=print_format,
    )
    db.session.add(req)
    db.session.commit()
    _log_audit(
        user.id,
        "PRINT_REQUEST_SUBMIT",
        f"record_id={record.id} request_id={req.id} print_format={print_format} source=ocr_review",
    )
    return True


# Available in every Jinja template (context processors alone can fail to apply in some run setups).
app.jinja_env.globals["document_types"] = DOCUMENT_TYPES
app.jinja_env.globals["cert_field"] = cert_field
app.jinja_env.globals["get_form_meta"] = form_meta
app.jinja_env.globals["normalize_print_format"] = normalize_print_format
app.jinja_env.globals["print_format_label"] = print_format_label
app.jinja_env.globals["print_format_short_label"] = print_format_short_label
app.jinja_env.globals["PRINT_FORMAT_ORIGINAL"] = PRINT_FORMAT_ORIGINAL
app.jinja_env.globals["PRINT_FORMAT_CERTIFICATION"] = PRINT_FORMAT_CERTIFICATION
app.jinja_env.globals["PRINT_FORMAT_BOTH"] = PRINT_FORMAT_BOTH
app.jinja_env.globals["document_annotation"] = document_annotation_from_data
app.jinja_env.globals["public_record_fields"] = public_record_fields
app.jinja_env.globals["annotation_form_defaults"] = annotation_form_defaults
app.jinja_env.globals["annotation_kinds"] = ANNOTATION_KINDS

METADATA_DISPLAY_LABELS = {
    "Husband Citizenship": "Husband Nationality",
    "Wife Citizenship": "Wife Nationality",
    "Citizenship of Mother": "Nationality of Mother",
    "Citizenship of Father": "Nationality of Father",
}


def metadata_display_label(key: str) -> str:
    return METADATA_DISPLAY_LABELS.get(key or "", key or "")


def required_review_fields(doc_type: str) -> tuple[str, ...]:
    return REQUIRED_REVIEW_FIELDS.get((doc_type or "").strip().lower(), ())


def is_required_review_field(doc_type: str, key: str) -> bool:
    return bool(key) and key in required_review_fields(doc_type)


def _field_has_review_value(submitted: dict, key: str) -> bool:
    raw = submitted.get(key)
    if raw is None:
        return False
    return bool(str(raw).strip())


def missing_required_review_fields(doc_type: str, submitted: dict) -> list[str]:
    data = submitted or {}
    return [key for key in required_review_fields(doc_type) if not _field_has_review_value(data, key)]


def _record_print_payload(record, data=None) -> dict:
    """Record JSON plus registry-number fallback used when checking print-ready metadata."""
    if isinstance(data, dict):
        payload = dict(data)
    else:
        try:
            raw = json.loads(record.data_json) if getattr(record, "data_json", None) else {}
            payload = dict(raw) if isinstance(raw, dict) else {}
        except Exception:
            payload = {}
    if not str(payload.get("Registry Number") or "").strip():
        fallback = (getattr(record, "registry_number", None) or "").strip()
        if fallback:
            payload["Registry Number"] = fallback
    return payload


def missing_print_metadata_labels(record, data=None) -> list[str]:
    payload = _record_print_payload(record, data)
    return [
        metadata_display_label(key)
        for key in missing_required_review_fields(getattr(record, "document_type", ""), payload)
    ]


_FLASH_ERROR_MARKERS = (
    "fail",
    "invalid",
    "blocked",
    "not found",
    "incorrect",
    "could not",
    "cannot",
    "expired",
    "not allowed",
    "required",
    "corrupted",
    "unable",
)
_FLASH_SUCCESS_MARKERS = (
    "saved",
    "approved",
    "created",
    "updated",
    "deleted",
    "restored",
    "accepted",
    "sent",
    "success",
    "reset",
    "copied",
)
_FLASH_TITLES = {
    "success": "Done",
    "error": "Action needed",
    "warning": "Please check",
    "info": "Notice",
}


def flash_tone(category: str | None, message: str | None = None) -> str:
    cat = (category or "").strip().lower()
    if cat in {"success", "error", "warning", "info"}:
        return cat
    if cat in {"danger", "fatal"}:
        return "error"
    text = (message or "").lower()
    if any(marker in text for marker in _FLASH_ERROR_MARKERS):
        return "error"
    if any(marker in text for marker in _FLASH_SUCCESS_MARKERS):
        return "success"
    return "info"


def flash_title(tone: str | None) -> str:
    return _FLASH_TITLES.get((tone or "").strip().lower(), "Notice")


app.jinja_env.globals["metadata_display_label"] = metadata_display_label
app.jinja_env.globals["flash_tone"] = flash_tone
app.jinja_env.globals["flash_title"] = flash_title
app.jinja_env.globals["DATE_FIELD_KEYS"] = DATE_FIELD_KEYS
app.jinja_env.globals["SEX_FIELD_KEYS"] = SEX_FIELD_KEYS
app.jinja_env.globals["required_review_fields"] = required_review_fields
app.jinja_env.globals["is_required_review_field"] = is_required_review_field
app.jinja_env.globals["missing_required_review_fields"] = missing_required_review_fields


def date_input_value(raw: str) -> str:
    """Convert stored OCR dates into YYYY-MM-DD for <input type='date'>."""
    from services.civil_registry_reports import split_date
    day, month, year = split_date(raw or "")
    if day and month and year and len(str(year)) == 4:
        try:
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        except ValueError:
            return ""
    text = (raw or "").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    return ""


app.jinja_env.globals["date_input_value"] = date_input_value


def sex_choice(raw: str) -> str:
    """Map OCR / stored sex text to Male or Female for radio buttons."""
    from ocr.sex import sex_from_ocr_text
    mapped = sex_from_ocr_text(raw or "")
    if mapped:
        return mapped
    text = (raw or "").strip().upper()
    if not text:
        return ""
    if text in {"M", "MALE", "LALAKE", "BOY"}:
        return "Male"
    if text in {"F", "FEMALE", "BABAE", "GIRL"}:
        return "Female"
    return ""


app.jinja_env.globals["sex_choice"] = sex_choice


@app.context_processor
def inject_template_globals():
    """Expose helpers in every template (backup if jinja_env.globals are not applied)."""
    return {
        "cert_date": cert_issue_date(),
        "cert_field": cert_field,
        "get_form_meta": form_meta,
        "normalize_print_format": normalize_print_format,
        "print_format_label": print_format_label,
        "print_format_short_label": print_format_short_label,
        "PRINT_FORMAT_ORIGINAL": PRINT_FORMAT_ORIGINAL,
        "PRINT_FORMAT_CERTIFICATION": PRINT_FORMAT_CERTIFICATION,
        "PRINT_FORMAT_BOTH": PRINT_FORMAT_BOTH,
        "document_types": DOCUMENT_TYPES,
        "document_annotation": document_annotation_from_data,
        "public_record_fields": public_record_fields,
        "annotation_form_defaults": annotation_form_defaults,
        "annotation_kinds": ANNOTATION_KINDS,
        "metadata_display_label": metadata_display_label,
        "flash_tone": flash_tone,
        "flash_title": flash_title,
        "user_photo_url": _user_photo_url,
    }


@app.template_global("print_format_short_label")
def _jinja_print_format_short_label(print_format, document_type="birth"):
    return print_format_short_label(print_format, document_type)

_OCR_JOBS = {}
_OCR_JOBS_LOCK = threading.Lock()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        def _login_block(message: str):
            if wants_json:
                return jsonify({"ok": False, "error": message}), 401
            flash(message)
            return redirect(url_for("login"))

        if not session.get("user_id"):
            return _login_block("Please log in to continue.")
        user = db.session.get(User, session["user_id"])
        if not user:
            session.clear()
            return _login_block("Please log in to continue.")
        expected = int(getattr(user, "session_version", 0) or 0)
        if int(session.get("session_version") or 0) != expected:
            session.clear()
            return _login_block("You were signed out. Please log in again.")
        return f(*args, **kwargs)

    return wrapper


def get_current_user():
    if not session.get("user_id"):
        return None
    return db.session.get(User, session["user_id"])


def _parse_requested_doc_type():
    raw = (request.form.get("document_type") or "").strip().lower()
    if raw in DOCUMENT_TYPES:
        return raw
    if raw in ("", "auto"):
        return "auto"
    return None


def _straighten_saved_scan(save_path) -> None:
    """Level a crooked scan/upload so Document preview and OCR see a straight certificate."""
    if save_path is None:
        return
    try:
        from ocr.deskew import straighten_document_file

        straighten_document_file(save_path)
    except Exception:
        app.logger.exception("Could not straighten scan %s", save_path)


def _upload_preview_url(rel: str) -> str:
    rel = _normalize_upload_relpath(rel or "")
    url = url_for("serve_upload", path=rel)
    path = _upload_file_path(rel)
    if path is not None and path.is_file():
        return f"{url}?v={int(path.stat().st_mtime)}"
    return url


def _save_ocr_image_from_request(straighten: bool = True):
    file = request.files.get("image")
    image_filename_form = (request.form.get("image_filename") or "").strip()

    storage_err = _require_server_storage()
    if storage_err:
        return None, None, storage_err

    if file and file.filename:
        content_len = request.content_length
        if content_len is not None and content_len > MAX_UPLOAD_BYTES + 1024 * 1024:
            return None, None, "The file is too large to store on the server. It was not saved."
        original_name = Path(file.filename).name
        ext = os.path.splitext(original_name)[1].lower()
        if ext not in ALLOWED_IMAGE_EXTS:
            return None, None, "Only JPG and PNG images are supported."
        subdir = UPLOAD_DIR / "temp"
        stem = _sanitize_filename_part(Path(original_name).stem, 36)
        save_path = subdir / f"scan_{uuid.uuid4().hex[:10]}_{stem}{ext}"
        try:
            subdir.mkdir(parents=True, exist_ok=True)
            file.save(save_path)
        except OSError as exc:
            return None, None, describe_storage_error(exc, subdir)
        if not save_path.is_file() or save_path.stat().st_size <= 0:
            try:
                save_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None, None, "The file could not be saved on the server. The upload was not marked as successful."
        if save_path.stat().st_size > MAX_UPLOAD_BYTES:
            try:
                save_path.unlink(missing_ok=True)
            except OSError:
                pass
            return None, None, "The file is too large to store on the server. It was not saved."
        if straighten:
            _straighten_saved_scan(save_path)
        image_filename = f"temp/{save_path.name}"
        return save_path, image_filename, None
    if image_filename_form:
        safe_path = Path(image_filename_form)
        if safe_path.is_absolute() or ".." in image_filename_form:
            return None, None, "Invalid image path."
        image_filename = _normalize_upload_relpath(image_filename_form)
        save_path = _upload_file_path(image_filename)
        if save_path is None or not save_path.exists():
            return None, None, "Scanned image no longer found. Please scan again."
        if straighten:
            _straighten_saved_scan(save_path)
        return save_path, image_filename, None
    return None, None, "Please scan from printer or choose an image file first."


def _parse_ocr_input_from_request():
    doc_type = _parse_requested_doc_type()
    if doc_type is None:
        return None, None, None, "Please select a valid document type."
    save_path, image_filename, err = _save_ocr_image_from_request()
    if err:
        return None, None, None, err
    return doc_type, save_path, image_filename, None


def _set_staged_scan(image_filename: str, doc_type: str = "", original_name: str = "") -> None:
    """Keep the uploaded/scanned certificate until Cancel OCR, cancel review, or save."""
    rel = _normalize_upload_relpath(image_filename or "")
    if not rel:
        return
    prev = session.get("scan_staged") if isinstance(session.get("scan_staged"), dict) else {}
    prev_rel = _normalize_upload_relpath(prev.get("image_filename") or "")
    session["scan_staged"] = {
        "image_filename": rel,
        "doc_type": (doc_type or prev.get("doc_type") or "").strip(),
        "original_name": (original_name or prev.get("original_name") or Path(rel).name).strip(),
    }
    session.modified = True
    if prev_rel and prev_rel != rel and prev_rel.startswith("temp/"):
        old_path = _upload_file_path(prev_rel)
        if old_path is not None and old_path.exists():
            try:
                old_path.unlink()
            except OSError:
                pass


def _clear_staged_scan(delete_temp_file: bool = False) -> None:
    staged = session.pop("scan_staged", None)
    session.modified = True
    if not delete_temp_file or not isinstance(staged, dict):
        return
    rel = _normalize_upload_relpath(staged.get("image_filename") or "")
    if not rel or not rel.startswith("temp/"):
        return
    path = _upload_file_path(rel)
    if path is not None and path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _unsaved_ocr_review() -> dict | None:
    """Finished OCR that is still waiting for Confirm and save (or Cancel review)."""
    _prune_ocr_jobs()
    job_id = (session.get("last_ocr_job_id") or "").strip()
    if not job_id:
        return None
    user_id = session.get("user_id")
    with _OCR_JOBS_LOCK:
        job = _OCR_JOBS.get(job_id)
        if not job or job.get("status") != "done":
            return None
        if job.get("user_id") not in (None, user_id):
            return None
    return {
        "job_id": job_id,
        "review_url": url_for("ocr_result", job_id=job_id),
        "code": "unsaved_review",
        "error": (
            "This document is not saved yet. Open the review, check the fields, "
            "and save it — or cancel the review — before scanning another document."
        ),
    }


def _new_scan_conflict(incoming_rel: str = "", for_current: bool = False) -> dict | None:
    """
    Block replacing a document that is still in progress.

    for_current=True allows OCR / type-detect on the file already on the scan desk.
    """
    if _get_active_ocr_for_page():
        return {
            "code": "ocr_running",
            "error": "OCR is still running. Wait for it to finish, or cancel it, before adding another document.",
        }
    review = _unsaved_ocr_review()
    if review:
        return review
    staged = _get_staged_scan()
    if not staged:
        return None
    inc = _normalize_upload_relpath(incoming_rel or "")
    if for_current and (not inc or inc == staged.get("image_filename")):
        return None
    return {
        "code": "staged_document",
        "error": (
            "A document is already loaded. Run OCR and save it, or click Cancel OCR "
            "to discard it, before adding another document."
        ),
    }


def _json_scan_conflict(conflict: dict | None, status: int = 409):
    if not conflict:
        return None
    payload = {
        "ok": False,
        "error": conflict.get("error") or "Finish the current document first.",
        "code": conflict.get("code") or "scan_locked",
    }
    if conflict.get("review_url"):
        payload["review_url"] = conflict["review_url"]
    if conflict.get("job_id"):
        payload["job_id"] = conflict["job_id"]
    return payload, status


def _get_staged_scan() -> dict | None:
    staged = session.get("scan_staged")
    if not isinstance(staged, dict):
        return None
    rel = _normalize_upload_relpath(staged.get("image_filename") or "")
    if not rel or not _existing_upload_relpath(rel):
        session.pop("scan_staged", None)
        session.modified = True
        return None
    return {
        "image_filename": rel,
        "image_url": _upload_preview_url(rel),
        "doc_type": (staged.get("doc_type") or "").strip(),
        "original_name": (staged.get("original_name") or Path(rel).name).strip(),
    }


def _ocr_field_guides() -> dict:
    from ocr.field_boxes import all_metadata_field_guides

    return all_metadata_field_guides()


def _get_active_ocr_for_page() -> dict | None:
    """Running OCR job payload for Scan Workflow (preview + busy UI)."""
    _prune_ocr_jobs()
    user_id = session.get("user_id")
    job_id = session.get("last_ocr_job_id")
    with _OCR_JOBS_LOCK:
        job = _OCR_JOBS.get(job_id) if job_id else None
        if job and job.get("user_id") not in (None, user_id):
            job = None
        if not job or job.get("status") not in ("queued", "running"):
            return None
        image_filename = job.get("image_filename") or ""
        payload = {
            "job_id": job_id,
            "status": job.get("status"),
            "progress": _job_display_progress(job),
            "message": job.get("message", ""),
            "doc_type": job.get("doc_type") or "",
            "image_filename": image_filename,
        }
        if image_filename:
            payload["image_url"] = _upload_preview_url(image_filename)
        return payload


def _set_ocr_job(job_id: str, **fields):
    with _OCR_JOBS_LOCK:
        job = _OCR_JOBS.get(job_id)
        if not job or job.get("status") == "cancelled":
            return
        job.update(fields)
        job["updated_at"] = datetime.utcnow().isoformat()


def _parse_job_time(value: str):
    try:
        return datetime.fromisoformat(value or "")
    except (TypeError, ValueError):
        return None


def _job_display_progress(job: dict) -> int:
    """Keep the percent moving while Paddle is still reading the page."""
    try:
        raw = int(job.get("progress") or 1)
    except (TypeError, ValueError):
        raw = 1
    status = job.get("status") or ""
    if status == "done":
        return 100
    if status not in ("queued", "running"):
        return raw
    started = _parse_job_time(job.get("created_at") or "")
    if started is None:
        return max(raw, 8)
    elapsed = max(0.0, (datetime.utcnow() - started).total_seconds())
    estimated = min(88, 12 + int(elapsed * 2))
    return max(raw, estimated)


def _clear_ocr_job(job_id: str, user_id=None) -> bool:
    """Remove an OCR job from memory. Returns True if a job was cleared."""
    with _OCR_JOBS_LOCK:
        job = _OCR_JOBS.get(job_id)
        if not job:
            return False
        if user_id is not None and job.get("user_id") not in (None, user_id):
            return False
        job["status"] = "cancelled"
        _OCR_JOBS.pop(job_id, None)
    if session.get("last_ocr_job_id") == job_id:
        session.pop("last_ocr_job_id", None)
        session.modified = True
    return True


def _consume_ocr_session(job_id=None) -> None:
    """Drop a finished OCR review and clear Scan Workflow preview after save."""
    user_id = session.get("user_id")
    jid = (job_id or session.get("last_ocr_job_id") or "").strip()
    with _OCR_JOBS_LOCK:
        if jid:
            _OCR_JOBS.pop(jid, None)
        for key, job in list(_OCR_JOBS.items()):
            if job.get("user_id") == user_id and job.get("status") == "done":
                _OCR_JOBS.pop(key, None)
    if session.get("last_ocr_job_id"):
        session.pop("last_ocr_job_id", None)
        session.modified = True
    # Saved (or consumed) reviews must not leave the certificate in Document preview.
    _clear_staged_scan(delete_temp_file=True)


def _prune_ocr_jobs(max_age_hours: int = 2, max_jobs: int = 40, stuck_minutes: int = 8):
    """Drop finished/stale OCR sessions so memory does not grow during a workday."""
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=max_age_hours)
    stuck_cutoff = now - timedelta(minutes=stuck_minutes)
    with _OCR_JOBS_LOCK:
        stale = []
        for job_id, job in _OCR_JOBS.items():
            created_dt = _parse_job_time(job.get("created_at") or "") or cutoff
            updated_dt = _parse_job_time(job.get("updated_at") or "") or created_dt
            status = job.get("status")
            if created_dt < cutoff:
                stale.append(job_id)
            elif status in ("queued", "running") and updated_dt < stuck_cutoff:
                stale.append(job_id)
            elif status == "cancelled":
                stale.append(job_id)
        for job_id in stale:
            _OCR_JOBS.pop(job_id, None)
        if len(_OCR_JOBS) > max_jobs:
            ordered = sorted(
                _OCR_JOBS.items(),
                key=lambda item: item[1].get("created_at") or "",
            )
            for job_id, _job in ordered[: len(_OCR_JOBS) - max_jobs]:
                _OCR_JOBS.pop(job_id, None)


_OCR_LISTENER_STARTED = False


_DETECT_WAITS = {}
_DETECT_WAITS_LOCK = threading.Lock()


def _register_detect_wait(job_id: str):
    holder = {"event": threading.Event(), "payload": None}
    with _DETECT_WAITS_LOCK:
        _DETECT_WAITS[job_id] = holder
    return holder


def _finish_detect_wait(job_id: str, payload: dict) -> None:
    with _DETECT_WAITS_LOCK:
        holder = _DETECT_WAITS.get(job_id)
    if holder:
        holder["payload"] = payload
        holder["event"].set()


def _pop_detect_wait(job_id: str):
    with _DETECT_WAITS_LOCK:
        return _DETECT_WAITS.pop(job_id, None)


def _detect_document_type_sync(
    save_path: Path, prefer_worker: bool = True, ocr_fallback: bool = True
) -> dict:
    from ocr.detect import detect_from_image
    from ocr.engine import document_type_label

    visual = detect_from_image(str(save_path), ocr_fallback=False)
    doc_type = (visual.get("doc_type") or "").strip().lower()
    if doc_type in DOCUMENT_TYPES:
        return {
            "doc_type": doc_type,
            "label": visual.get("label") or DOCUMENT_TYPES[doc_type],
            "confidence": visual.get("confidence") or "high",
            "reason": visual.get("reason") or "",
        }
    if not ocr_fallback:
        return {
            "doc_type": None,
            "label": "",
            "confidence": "none",
            "reason": visual.get("reason") or "Could not identify the document type",
        }

    job_id = f"detect-{uuid.uuid4().hex}"
    holder = _register_detect_wait(job_id)
    queued = False
    if prefer_worker and (worker_is_alive() or start_ocr_worker()):
        _ensure_ocr_listener()
        queued = submit_detect_job(job_id, str(save_path))
    if prefer_worker and queued:
        holder["event"].wait(timeout=8)
        _pop_detect_wait(job_id)
        payload = holder.get("payload") or {}
        found = (payload.get("doc_type") or "").strip().lower()
        if found in DOCUMENT_TYPES:
            return {
                "doc_type": found,
                "label": payload.get("label") or DOCUMENT_TYPES[found],
                "confidence": payload.get("confidence") or "medium",
                "reason": payload.get("reason") or "",
            }
        return {
            "doc_type": None,
            "label": "",
            "confidence": "none",
            "reason": payload.get("reason") or "Could not identify the document type",
        }

    _pop_detect_wait(job_id)
    result = detect_from_image(str(save_path), ocr_fallback=True)
    found = (result.get("doc_type") or "").strip().lower()
    if found not in DOCUMENT_TYPES:
        found = None
    return {
        "doc_type": found,
        "label": result.get("label") or document_type_label(found),
        "confidence": result.get("confidence") or ("none" if not found else "medium"),
        "reason": result.get("reason") or "",
    }


def _apply_ocr_worker_message(msg: dict) -> None:
    if not msg:
        return
    kind = msg.get("type")
    job_id = msg.get("job_id") or ""
    if kind == "detect_done" and job_id:
        _finish_detect_wait(job_id, msg)
        return
    if kind == "boot_error":
        print(msg.get("error") or "OCR worker failed to start.", flush=True)
        return
    if kind == "progress" and job_id:
        _set_ocr_job(
            job_id,
            status="running",
            progress=msg.get("progress") or 20,
            message=msg.get("message") or "OCR running...",
        )
        return
    if kind == "done" and job_id:
        _set_ocr_job(
            job_id,
            status="done",
            progress=100,
            message="OCR complete.",
            doc_type=msg.get("doc_type"),
            image_filename=msg.get("image_filename"),
            data=msg.get("data"),
        )
        return
    if kind == "error" and job_id:
        err = msg.get("error") or "OCR failed."
        print(err, flush=True)
        _set_ocr_job(
            job_id,
            status="error",
            progress=100,
            message="OCR failed.",
            error=err,
        )


def _ocr_worker_listener():
    while True:
        try:
            msg = _ocr_worker_get_result(timeout=0.15)
        except Exception:
            msg = None
        if not msg:
            if not worker_is_alive():
                threading.Event().wait(0.8)
            continue
        _apply_ocr_worker_message(msg)


def _ensure_ocr_listener():
    global _OCR_LISTENER_STARTED
    if _OCR_LISTENER_STARTED:
        return
    _OCR_LISTENER_STARTED = True
    threading.Thread(target=_ocr_worker_listener, daemon=True, name="ocr-listener").start()


def _run_ocr_job(job_id: str, doc_type: str, save_path: Path, image_filename: str):
    try:
        _set_ocr_job(job_id, status="running", progress=20, message="OCR started...")
        if doc_type not in DOCUMENT_TYPES:
            _set_ocr_job(job_id, progress=28, message="Identifying document type...")
            detected = _detect_document_type_sync(save_path, prefer_worker=False)
            doc_type = detected.get("doc_type")
            if doc_type not in DOCUMENT_TYPES:
                _set_ocr_job(
                    job_id,
                    status="error",
                    progress=100,
                    message="Could not identify document type.",
                    error=(
                        "Could not identify whether this is a birth, marriage, "
                        "or death certificate. Choose the type and run OCR again."
                    ),
                )
                return
            _set_ocr_job(job_id, doc_type=doc_type)
        if doc_type == "birth":
            _set_ocr_job(job_id, progress=45, message="Extracting birth fields...")
            data = extract_birth_data(str(save_path))
        elif doc_type == "marriage":
            _set_ocr_job(job_id, progress=45, message="Extracting marriage fields...")
            data = extract_marriage_data(str(save_path))
        else:
            _set_ocr_job(job_id, progress=45, message="Extracting death fields...")
            data = extract_death_data(str(save_path))
        with _OCR_JOBS_LOCK:
            job = _OCR_JOBS.get(job_id)
            if not job or job.get("status") == "cancelled":
                return
        _set_ocr_job(
            job_id,
            status="done",
            progress=100,
            message="OCR complete.",
            doc_type=doc_type,
            image_filename=image_filename,
            data=data,
        )
    except Exception:
        err = traceback.format_exc()
        print(err, flush=True)
        with _OCR_JOBS_LOCK:
            job = _OCR_JOBS.get(job_id)
            if not job or job.get("status") == "cancelled":
                return
        _set_ocr_job(
            job_id,
            status="error",
            progress=100,
            message="OCR failed.",
            error=err,
        )


def _start_ocr_job(doc_type: str, save_path: Path, image_filename: str) -> str:
    """Queue OCR off the web request thread so other pages stay responsive."""
    job_id = uuid.uuid4().hex
    _prune_ocr_jobs()
    now_iso = datetime.utcnow().isoformat()
    with _OCR_JOBS_LOCK:
        _OCR_JOBS[job_id] = {
            "status": "queued",
            "progress": 1,
            "message": "Queued OCR job...",
            "doc_type": doc_type,
            "image_filename": image_filename,
            "data": None,
            "error": None,
            "user_id": session.get("user_id"),
            "created_at": now_iso,
            "updated_at": now_iso,
        }
    queued = False
    if worker_is_alive() or start_ocr_worker():
        _ensure_ocr_listener()
        queued = submit_ocr_job(job_id, doc_type, str(save_path), image_filename)
    if not queued:
        threading.Thread(
            target=_run_ocr_job,
            args=(job_id, doc_type, save_path, image_filename),
            daemon=True,
            name=f"ocr-job-{job_id[:8]}",
        ).start()
    return job_id


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please log in to continue.")
            return redirect(url_for("login"))
        user = get_current_user()
        if not user or (user.role or "").upper() != "ADMIN":
            flash("Administration access is for admins only.")
            return redirect(url_for("index"))
        return f(*args, **kwargs)

    return wrapper


def _log_audit(user_id, action: str, details: str = ""):
    try:
        log = AuditLog(user_id=user_id, action=action, details=details)
        db.session.add(log)
        db.session.commit()
    except Exception:
        db.session.rollback()


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        ajax = request.headers.get("X-Login-Intent") == "ajax"

        if not username or not password:
            if ajax:
                return jsonify({"ok": False, "error": "Please enter username and password."}), 400
            flash("Please enter username and password.")
            return redirect(url_for("login"))

        try:
            user = _find_user_by_username(username)
        except Exception:
            db.session.rollback()
            message = "Database is not running. Start PostgreSQL, then try again."
            if ajax:
                return jsonify({"ok": False, "error": message}), 503
            flash(message)
            return redirect(url_for("login"))
        if not user:
            if ajax:
                return jsonify({"ok": False, "error": "Invalid username or password."}), 401
            flash("Invalid username or password.")
            return redirect(url_for("login"))
        try:
            if not check_password_hash(user.password_hash or "", password):
                if ajax:
                    return jsonify({"ok": False, "error": "Invalid username or password."}), 401
                flash("Invalid username or password.")
                return redirect(url_for("login"))
        except ValueError:
            if ajax:
                return jsonify({"ok": False, "error": "Invalid username or password."}), 401
            flash("Invalid username or password.")
            return redirect(url_for("login"))

        session["user_id"] = user.id
        session["username"] = user.username
        session["role"] = user.role
        session["session_version"] = int(getattr(user, "session_version", 0) or 0)
        user.last_login_at = datetime.utcnow()
        db.session.commit()
        _remember_password_vault(user, password)
        if ajax:
            role = (user.role or "").strip().lower()
            role_label = "Administrator" if role == "admin" else "Staff"
            return jsonify(
                {
                    "ok": True,
                    "redirect": url_for("index"),
                    "username": user.username,
                    "role": role,
                    "role_label": role_label,
                }
            )
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def _bump_session_version(user, keep_current_session: bool = False) -> None:
    user.session_version = int(getattr(user, "session_version", 0) or 0) + 1
    if keep_current_session and session.get("user_id") == user.id:
        session["session_version"] = int(user.session_version)


MAX_AVATAR_BYTES = 2 * 1024 * 1024
AVATAR_MAX_PX = 512


def _avatar_rel_for_user(user_id: int) -> str:
    return f"avatars/u_{int(user_id)}.jpg"


def _user_photo_file(user) -> Path | None:
    rel = _normalize_upload_relpath(getattr(user, "photo_path", None) or "")
    if not rel.lower().startswith("avatars/"):
        return None
    path = _upload_file_path(rel)
    if path is not None and path.is_file():
        return path
    return None


def _user_photo_url(user) -> str:
    if not user or not getattr(user, "photo_path", None):
        return ""
    path = _user_photo_file(user)
    if path is None:
        return ""
    return url_for("account_photo", user_id=user.id, v=int(path.stat().st_mtime))


def _delete_user_photo_file(user) -> None:
    path = _user_photo_file(user)
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def _save_user_photo(user, storage) -> str | None:
    raw = storage.read(MAX_AVATAR_BYTES + 1)
    if not raw:
        return "Choose a photo to upload."
    if len(raw) > MAX_AVATAR_BYTES:
        return "Photo must be 2 MB or smaller."
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        return "Photo tools are not installed on this computer."
    try:
        img = Image.open(BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return "Use a JPEG or PNG photo."
    if (img.format or "").upper() not in ("JPEG", "PNG", "WEBP"):
        return "Use a JPEG or PNG photo."
    if img.width < 32 or img.height < 32:
        return "Photo is too small."
    if img.width * img.height > 25_000_000:
        return "Photo is too large."
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")
    side = min(img.size)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))
    img.thumbnail((AVATAR_MAX_PX, AVATAR_MAX_PX))
    rel = _avatar_rel_for_user(user.id)
    dest = _upload_file_path(rel, for_write=True)
    if dest is None:
        return "Could not save the photo."
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.stem + ".tmp.jpg")
        img.save(tmp, format="JPEG", quality=85, optimize=True)
        tmp.replace(dest)
    except OSError as exc:
        return describe_storage_error(exc, dest)
    user.photo_path = rel
    return None


@app.route("/account")
@login_required
def account():
    user = get_current_user()
    role = ((user.role if user else "") or "").upper()
    staff_queue = None
    if user and role == "STAFF":
        staff_queue = {
            "edit_pending": EditRequest.query.filter_by(requested_by_id=user.id, status="PENDING").count(),
            "edit_ready": (
                EditRequest.query.filter_by(requested_by_id=user.id, status="APPROVED")
                .filter(EditRequest.code_used_at.is_(None))
                .count()
            ),
            "print_pending": PrintRequest.query.filter_by(requested_by_id=user.id, status="PENDING").count(),
            "print_ready": (
                PrintRequest.query.filter_by(requested_by_id=user.id, status="APPROVED")
                .filter(PrintRequest.used_at.is_(None))
                .count()
            ),
        }
    return render_template(
        "account.html",
        current_user=user,
        nav_active="account",
        staff_queue=staff_queue,
    )


@app.route("/account/sessions/revoke", methods=["POST"])
@login_required
def account_revoke_sessions():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    _bump_session_version(user, keep_current_session=True)
    db.session.commit()
    _log_audit(user.id, "SESSION_REVOKE", f"username={user.username}")
    flash("Other devices were signed out. This computer is still signed in.", "success")
    return redirect(url_for("account"))


@app.route("/account/password", methods=["POST"])
@login_required
def account_change_password():
    user = get_current_user()
    current_password = request.form.get("current_password") or ""
    new_password = request.form.get("new_password") or ""
    confirm_password = request.form.get("confirm_password") or ""
    if not user or not check_password_hash(user.password_hash, current_password):
        flash("Current password is incorrect.", "error")
        return redirect(url_for("account"))
    if len(new_password) < 6:
        flash("New password must be at least 6 characters.", "error")
        return redirect(url_for("account"))
    if new_password != confirm_password:
        flash("New password and confirmation do not match.", "error")
        return redirect(url_for("account"))
    if check_password_hash(user.password_hash, new_password):
        flash("Choose a password that is different from the current one.", "error")
        return redirect(url_for("account"))
    user.password_hash = _safe_hash(new_password)
    _set_password_vault(user, new_password)
    _bump_session_version(user, keep_current_session=True)
    db.session.commit()
    _log_audit(user.id, "PASSWORD_CHANGE", f"username={user.username}")
    flash("Password updated. Other devices were signed out.", "success")
    return redirect(url_for("account"))


@app.route("/account/photo", methods=["POST"])
@login_required
def account_upload_photo():
    user = get_current_user()
    if not user:
        return redirect(url_for("login"))
    storage_err = _require_server_storage()
    if storage_err:
        flash(storage_err, "error")
        return redirect(url_for("account"))
    if (request.form.get("remove_photo") or "").strip():
        _delete_user_photo_file(user)
        user.photo_path = ""
        db.session.commit()
        _log_audit(user.id, "ACCOUNT_PHOTO_REMOVE", f"username={user.username}")
        flash("Photo removed.")
        return redirect(url_for("account"))
    file = request.files.get("photo")
    if not file or not (file.filename or "").strip():
        flash("Choose a photo to upload.")
        return redirect(url_for("account"))
    err = _save_user_photo(user, file)
    if err:
        flash(err, "error")
        return redirect(url_for("account"))
    db.session.commit()
    _log_audit(user.id, "ACCOUNT_PHOTO_UPLOAD", f"username={user.username}")
    flash("Photo saved.")
    return redirect(url_for("account"))


@app.route("/account/photo/<int:user_id>")
@login_required
def account_photo(user_id):
    user = db.session.get(User, user_id)
    if not user:
        return ("", 404)
    path = _user_photo_file(user)
    if path is None:
        return ("", 404)
    rel = _normalize_upload_relpath(user.photo_path or "")
    if rel != _avatar_rel_for_user(user.id):
        return ("", 404)
    resp = send_file(path, as_attachment=False, mimetype="image/jpeg", conditional=True)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.route("/", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        review = _unsaved_ocr_review()
        if review:
            flash(review["error"])
            return redirect(review["review_url"])
        incoming = _normalize_upload_relpath((request.form.get("image_filename") or "").strip())
        new_file = bool(request.files.get("image") and request.files["image"].filename)
        conflict = _new_scan_conflict(incoming, for_current=not new_file)
        if conflict:
            flash(conflict["error"])
            return redirect(conflict.get("review_url") or url_for("index"))
        doc_type, save_path, image_filename, err = _parse_ocr_input_from_request()
        if err:
            flash(err)
            return redirect(url_for("index"))
        if doc_type not in DOCUMENT_TYPES:
            flash("Identify the document type before running OCR.")
            return redirect(url_for("index"))
        _set_staged_scan(image_filename, doc_type=doc_type)
        job_id = _start_ocr_job(doc_type, save_path, image_filename)
        session["last_ocr_job_id"] = job_id
        session.modified = True
        return redirect(url_for("index"))
    active_ocr = _get_active_ocr_for_page()
    staged_scan = _get_staged_scan()
    unsaved_review = _unsaved_ocr_review()
    scan_preview = None
    if active_ocr and active_ocr.get("image_url"):
        scan_preview = {
            "image_filename": active_ocr.get("image_filename") or "",
            "image_url": active_ocr.get("image_url"),
            "doc_type": (active_ocr.get("doc_type") or "").strip(),
            "original_name": Path(active_ocr.get("image_filename") or "").name,
        }
    elif staged_scan:
        scan_preview = staged_scan
    return render_template(
        "index.html",
        document_types=DOCUMENT_TYPES,
        current_user=get_current_user(),
        nav_active="scan",
        active_ocr=active_ocr,
        staged_scan=staged_scan,
        scan_preview=scan_preview,
        ocr_field_guides=_ocr_field_guides(),
        unsaved_review=unsaved_review,
    )


@app.route("/api/scanners", methods=["GET"])
@login_required
def api_scanners():
    """Return list of physical scanners detected (Windows WIA)."""
    scanners = list_scanners()
    return {"scanners": scanners}


@app.route("/api/scan", methods=["POST"])
@login_required
def api_scan():
    """Perform a scan from the selected physical scanner and return the image path."""
    storage_err = _require_server_storage()
    if storage_err:
        return {"ok": False, "error": storage_err}, 507
    blocked = _json_scan_conflict(_new_scan_conflict("", for_current=False))
    if blocked:
        return blocked
    device_index = request.form.get("device_index", type=int)
    if not device_index:
        return {"ok": False, "error": "Detect a scanner first, then choose a device."}, 400
    temp_dir = UPLOAD_DIR / "temp"
    try:
        ok, path_or_error = scan_to_file(device_index=device_index, output_dir=temp_dir, save_as_png=True)
    except OSError as exc:
        return {"ok": False, "error": describe_storage_error(exc, temp_dir)}, 507
    if not ok:
        return {"ok": False, "error": path_or_error}, 400
    # path_or_error is absolute; we need a path relative to UPLOAD_DIR for image_filename
    rel = os.path.relpath(path_or_error, UPLOAD_DIR)
    if rel.startswith("..") or os.path.isabs(rel):
        return {"ok": False, "error": "Invalid scan path"}, 500
    rel = _normalize_upload_relpath(rel.replace("\\", "/"))
    _straighten_saved_scan(Path(path_or_error))
    _set_staged_scan(rel, original_name=Path(rel).name)
    return {"ok": True, "image_url": _upload_preview_url(rel), "image_filename": rel}


@app.route("/api/scan/stage", methods=["POST"])
@login_required
def api_scan_stage():
    """Persist an uploaded/scanned image in the session until Cancel OCR."""
    new_file = bool(request.files.get("image") and request.files["image"].filename)
    incoming = _normalize_upload_relpath((request.form.get("image_filename") or "").strip())
    blocked = _json_scan_conflict(_new_scan_conflict(incoming, for_current=not new_file))
    if blocked:
        return blocked
    save_path, image_filename, err = _save_ocr_image_from_request(straighten=False)
    if err:
        return {"ok": False, "error": err}, 400
    doc_type = (request.form.get("document_type") or "").strip()
    if doc_type not in ("birth", "marriage", "death"):
        doc_type = ""
    file = request.files.get("image")
    original_name = Path(file.filename).name if file and file.filename else Path(image_filename).name
    _set_staged_scan(image_filename, doc_type=doc_type, original_name=original_name)
    return {
        "ok": True,
        "image_filename": image_filename,
        "image_url": _upload_preview_url(image_filename),
        "original_name": original_name,
        "doc_type": doc_type,
    }


@app.route("/api/detect-document-type", methods=["POST"])
@login_required
def api_detect_document_type():
    """Identify birth, marriage, or death from the top certificate heading only."""
    new_file = bool(request.files.get("image") and request.files["image"].filename)
    incoming = _normalize_upload_relpath((request.form.get("image_filename") or "").strip())
    blocked = _json_scan_conflict(_new_scan_conflict(incoming, for_current=not new_file))
    if blocked:
        return blocked
    save_path, image_filename, err = _save_ocr_image_from_request(straighten=False)
    if err:
        return {"ok": False, "error": err}, 400
    detected = _detect_document_type_sync(save_path, prefer_worker=False, ocr_fallback=True)
    doc_type = detected.get("doc_type") or ""
    if doc_type not in ("birth", "marriage", "death"):
        doc_type = ""
    _set_staged_scan(image_filename, doc_type=doc_type)
    return {
        "ok": True,
        "image_filename": image_filename,
        "image_url": _upload_preview_url(image_filename),
        "doc_type": detected.get("doc_type"),
        "label": detected.get("label") or "",
        "confidence": detected.get("confidence") or "none",
        "reason": detected.get("reason") or "",
    }


@app.route("/api/ocr/start", methods=["POST"])
@login_required
def api_ocr_start():
    review = _unsaved_ocr_review()
    if review:
        return _json_scan_conflict(review)
    incoming = _normalize_upload_relpath((request.form.get("image_filename") or "").strip())
    new_file = bool(request.files.get("image") and request.files["image"].filename)
    blocked = _json_scan_conflict(_new_scan_conflict(incoming, for_current=not new_file))
    if blocked:
        return blocked
    doc_type, save_path, image_filename, err = _parse_ocr_input_from_request()
    if err:
        return {"ok": False, "error": err}, 400

    if doc_type not in DOCUMENT_TYPES:
        return {"ok": False, "error": "Identify the document type before running OCR."}, 400
    _set_staged_scan(image_filename, doc_type=doc_type)
    job_id = _start_ocr_job(doc_type, save_path, image_filename)
    session["last_ocr_job_id"] = job_id
    session.modified = True
    return {"ok": True, "job_id": job_id}


@app.route("/api/ocr/field-guides", methods=["GET"])
@login_required
def api_ocr_field_guides():
    """Write-in boxes for Document preview targeting, mapped onto the staged scan."""
    doc_type = (request.args.get("doc_type") or "").strip().lower()
    if doc_type not in ("birth", "marriage", "death"):
        staged = _get_staged_scan()
        job = _get_active_ocr_for_page()
        doc_type = ((job or {}).get("doc_type") or (staged or {}).get("doc_type") or "").strip().lower()
    if doc_type not in ("birth", "marriage", "death"):
        return {"ok": True, "doc_type": "", "fields": []}

    rel = _normalize_upload_relpath(request.args.get("image") or "")
    if not rel:
        staged = _get_staged_scan()
        job = _get_active_ocr_for_page()
        rel = (job or {}).get("image_filename") or (staged or {}).get("image_filename") or ""
        rel = _normalize_upload_relpath(rel)
    path = _upload_file_path(rel) if rel else None
    from ocr.field_boxes import align_field_guides_to_image, metadata_field_guides

    try:
        if path is not None and path.is_file():
            fields = align_field_guides_to_image(doc_type, str(path))
        else:
            fields = metadata_field_guides(doc_type)
    except Exception:
        fields = metadata_field_guides(doc_type)
    return {"ok": True, "doc_type": doc_type, "fields": fields}


@app.route("/api/ocr/status/<job_id>", methods=["GET"])
@login_required
def api_ocr_status(job_id):
    user_id = session.get("user_id")
    with _OCR_JOBS_LOCK:
        job = _OCR_JOBS.get(job_id)
        if not job or job.get("user_id") not in (None, user_id):
            return {"ok": True, "status": "cancelled", "progress": 0, "message": "OCR cancelled."}
        payload = {
            "ok": True,
            "job_id": job_id,
            "status": job.get("status"),
            "progress": _job_display_progress(job),
            "message": job.get("message", ""),
        }
        if job.get("status") == "done":
            payload["redirect_url"] = url_for("ocr_result", job_id=job_id)
        if job.get("status") == "error":
            payload["error"] = "OCR failed while processing the document."
        return payload


@app.route("/api/ocr/active", methods=["GET"])
@login_required
def api_ocr_active():
    """Resume the current user's in-progress or last completed OCR job in the top bar."""
    _prune_ocr_jobs()
    user_id = session.get("user_id")
    job_id = session.get("last_ocr_job_id")
    with _OCR_JOBS_LOCK:
        job = _OCR_JOBS.get(job_id) if job_id else None
        if job and job.get("user_id") not in (None, user_id):
            job = None
        if not job:
            candidates = [
                (jid, j)
                for jid, j in _OCR_JOBS.items()
                if j.get("user_id") == user_id
                and j.get("status") in ("queued", "running")
            ]
            if not candidates:
                if session.get("last_ocr_job_id"):
                    session.pop("last_ocr_job_id", None)
                    session.modified = True
                return {"ok": True, "active": False}
            job_id, job = max(candidates, key=lambda item: item[1].get("created_at") or "")
        status = job.get("status")
        if status in ("error", "cancelled"):
            if session.get("last_ocr_job_id") == job_id:
                session.pop("last_ocr_job_id", None)
                session.modified = True
            return {"ok": True, "active": False}
        if status == "done":
            return {
                "ok": True,
                "active": False,
                "completed": True,
                "job_id": job_id,
                "redirect_url": url_for("ocr_result", job_id=job_id),
            }
        payload = {
            "ok": True,
            "active": True,
            "job_id": job_id,
            "status": status,
            "progress": _job_display_progress(job),
            "message": job.get("message", ""),
            "doc_type": job.get("doc_type"),
            "image_filename": job.get("image_filename") or "",
        }
        if job.get("image_filename"):
            payload["image_url"] = _upload_preview_url(job["image_filename"])
        return payload


@app.route("/api/ocr/cancel", methods=["POST"])
@login_required
def api_ocr_cancel():
    """Cancel the current user's OCR job and unlock the scan page."""
    user_id = session.get("user_id")
    payload = request.get_json(silent=True) or {}
    job_id = (payload.get("job_id") or request.form.get("job_id") or session.get("last_ocr_job_id") or "").strip()
    cleared = False
    if job_id:
        cleared = _clear_ocr_job(job_id, user_id=user_id)
    # Also drop any other in-progress jobs for this user (stuck after restart / hang).
    with _OCR_JOBS_LOCK:
        extra = [
            jid
            for jid, j in list(_OCR_JOBS.items())
            if j.get("user_id") == user_id and j.get("status") in ("queued", "running")
        ]
        for jid in extra:
            _OCR_JOBS[jid]["status"] = "cancelled"
            _OCR_JOBS.pop(jid, None)
            cleared = True
    session.pop("last_ocr_job_id", None)
    _clear_staged_scan(delete_temp_file=True)
    session.modified = True
    return {"ok": True, "cancelled": True, "cleared": cleared}


def _record_notice_label(record) -> str:
    if not record:
        return "a record"
    kind = (record.document_type or "record").capitalize()
    reg = (record.registry_number or "").strip()
    if reg:
        return f"{kind} · {reg}"
    return f"{kind} record #{record.id}"


def _notice_iso(dt) -> str:
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_notice_iso(raw: str):
    text = (raw or "").strip().replace("Z", "").replace("T", " ")[:19]
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _admin_seen_since(key: str):
    """Items newer than last visit to this tab. First visit uses the last 24 hours."""
    floor = datetime.utcnow() - timedelta(hours=24)
    seen = _parse_notice_iso(session.get(f"admin_seen_{key}") or "")
    if seen is None:
        return floor
    return seen


def _mark_admin_tab_seen(tab: str) -> None:
    key = {
        "print_logs": "print_logs",
        "audit": "audit",
        "office_logs": "office_logs",
        "automatic_backup": "backup",
        "users": "users",
    }.get(tab)
    if not key:
        return
    session[f"admin_seen_{key}"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    session.modified = True


def _admin_attention_badges() -> dict:
    """Counts Admin must check: pending staff requests, new logs, backup errors."""
    pending_edit = EditRequest.query.filter_by(status="PENDING").count()
    pending_print = PrintRequest.query.filter_by(status="PENDING").count()
    since_print = _admin_seen_since("print_logs")
    since_audit = _admin_seen_since("audit")
    since_office = _admin_seen_since("office_logs")
    since_users = _admin_seen_since("users")
    print_logs_new = PrintLog.query.filter(PrintLog.printed_at > since_print).count()
    staff_ids = [row[0] for row in db.session.query(User.id).filter(db.func.upper(User.role) == "STAFF").all()]
    audit_q = AuditLog.query.filter(AuditLog.created_at > since_audit)
    if staff_ids:
        audit_q = audit_q.filter(AuditLog.user_id.in_(staff_ids))
    audit_new = audit_q.count()
    office_q = AuditLog.query.filter(AuditLog.created_at > since_office)
    if staff_ids:
        office_q = office_q.filter(AuditLog.user_id.in_(staff_ids))
    office_logs_new = office_q.count() + PrintLog.query.filter(PrintLog.printed_at > since_office).count()
    users_new = User.query.filter(User.created_at > since_users).count()
    backup_alert = 0
    try:
        from services.auto_backup import load_settings as load_backup_settings

        backup_settings = load_backup_settings(BASE_DIR)
        if str(backup_settings.get("last_run_status") or "").lower() == "error":
            last_dt = _parse_notice_iso(str(backup_settings.get("last_run_at") or ""))
            seen_dt = _parse_notice_iso(session.get("admin_seen_backup") or "")
            if seen_dt is None or last_dt is None or last_dt > seen_dt:
                backup_alert = 1
    except Exception:
        backup_alert = 0
    dashboard = pending_edit + pending_print
    return {
        "dashboard": dashboard,
        "edit_requests": pending_edit,
        "print_requests": pending_print,
        "print_logs": print_logs_new,
        "audit": audit_new,
        "users": users_new,
        "reports": 0,
        "backup": backup_alert,
        "office_logs": office_logs_new,
        "admin_pending_edit_requests": pending_edit,
        "admin_pending_print_requests": pending_print,
        "total": dashboard + print_logs_new + audit_new + backup_alert,
    }


def _collect_notifications(user) -> dict:
    role = (user.role or "").upper()
    items = []
    badges = {}

    if role == "ADMIN":
        pending_edits = (
            EditRequest.query.filter_by(status="PENDING")
            .order_by(EditRequest.requested_at.desc())
            .limit(12)
            .all()
        )
        pending_prints = (
            PrintRequest.query.filter_by(status="PENDING")
            .order_by(PrintRequest.requested_at.desc())
            .limit(12)
            .all()
        )
        for req in pending_edits:
            who = req.requested_by.username if req.requested_by else "Staff"
            items.append({
                "id": f"edit-{req.id}",
                "kind": "edit",
                "title": "New edit request",
                "body": f"{who} asked to edit {_record_notice_label(req.record)}",
                "detail": (req.reason or "").strip(),
                "url": url_for("administration", tab="edit_requests"),
                "at": _notice_iso(req.requested_at),
            })
        for req in pending_prints:
            who = req.requested_by.username if req.requested_by else "Staff"
            items.append({
                "id": f"print-{req.id}",
                "kind": "print",
                "title": "New print request",
                "body": f"{who} asked to print {_record_notice_label(req.record)}",
                "detail": (req.reason or "").strip(),
                "url": url_for("administration", tab="print_requests"),
                "at": _notice_iso(req.requested_at),
            })
        badges = _admin_attention_badges()
        try:
            recent_prints = (
                PrintLog.query.order_by(PrintLog.printed_at.desc()).limit(6).all()
            )
            for log in recent_prints:
                if log.printed_at and log.printed_at <= _admin_seen_since("print_logs"):
                    continue
                who = log.user.username if log.user else "Staff"
                items.append({
                    "id": f"plog-{log.id}",
                    "kind": "print-log",
                    "title": "Staff printed a record",
                    "body": f"{who} printed {_record_notice_label(log.record)}",
                    "url": url_for("administration", tab="print_logs"),
                    "at": _notice_iso(log.printed_at),
                })
        except Exception:
            pass
        try:
            staff_ids = [row[0] for row in db.session.query(User.id).filter(db.func.upper(User.role) == "STAFF").all()]
            audit_rows = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(12).all()
            since_audit = _admin_seen_since("audit")
            for row in audit_rows:
                if staff_ids and row.user_id not in staff_ids:
                    continue
                if row.created_at and row.created_at <= since_audit:
                    continue
                actor = "Staff"
                try:
                    actor = db.session.get(User, row.user_id).username if row.user_id else "Staff"
                except Exception:
                    pass
                items.append({
                    "id": f"audit-{row.id}",
                    "kind": "audit",
                    "title": "Staff activity",
                    "body": f"{actor}: {row.action}",
                    "detail": (row.details or "").strip(),
                    "url": url_for("administration", tab="audit"),
                    "at": _notice_iso(row.created_at),
                })
        except Exception:
            pass
        if int(badges.get("backup") or 0):
            items.append({
                "id": "backup-error",
                "kind": "backup",
                "title": "Backup needs attention",
                "body": "The last backup did not finish. Open Backup to check.",
                "url": url_for("administration", tab="automatic_backup"),
                "at": _notice_iso(datetime.utcnow()),
            })
        badges["total"] = int(badges.get("total") or 0)

    elif role == "STAFF":
        pending_edits = (
            EditRequest.query.filter_by(requested_by_id=user.id, status="PENDING")
            .order_by(EditRequest.requested_at.desc())
            .limit(8)
            .all()
        )
        ready_edits = (
            EditRequest.query.filter_by(requested_by_id=user.id, status="APPROVED")
            .filter(EditRequest.code_used_at.is_(None))
            .order_by(EditRequest.reviewed_at.desc())
            .limit(8)
            .all()
        )
        pending_prints = (
            PrintRequest.query.filter_by(requested_by_id=user.id, status="PENDING")
            .order_by(PrintRequest.requested_at.desc())
            .limit(8)
            .all()
        )
        ready_prints = (
            PrintRequest.query.filter_by(requested_by_id=user.id, status="APPROVED")
            .filter(PrintRequest.used_at.is_(None))
            .order_by(PrintRequest.reviewed_at.desc())
            .limit(8)
            .all()
        )
        rejected_edits = (
            EditRequest.query.filter_by(requested_by_id=user.id, status="REJECTED")
            .order_by(EditRequest.reviewed_at.desc())
            .limit(6)
            .all()
        )
        rejected_prints = (
            PrintRequest.query.filter_by(requested_by_id=user.id, status="REJECTED")
            .order_by(PrintRequest.reviewed_at.desc())
            .limit(6)
            .all()
        )
        for req in ready_edits:
            items.append({
                "id": f"edit-ready-{req.id}",
                "kind": "edit-ready",
                "title": "Edit approved",
                "body": f"Use your 5-digit code on {_record_notice_label(req.record)}",
                "url": url_for("record_detail", record_id=req.record_id) + "#staff-edit-code",
                "at": _notice_iso(req.reviewed_at or req.requested_at),
            })
        for req in ready_prints:
            items.append({
                "id": f"print-ready-{req.id}",
                "kind": "print-ready",
                "title": "Print approved",
                "body": f"Type the 5-digit print code for {_record_notice_label(req.record)}",
                "url": url_for("record_detail", record_id=req.record_id) + "#staff-print-code",
                "at": _notice_iso(req.reviewed_at or req.requested_at),
            })
        for req in pending_edits:
            items.append({
                "id": f"edit-wait-{req.id}",
                "kind": "edit",
                "title": "Edit request pending",
                "body": f"Waiting for admin on {_record_notice_label(req.record)}",
                "url": url_for("my_edit_requests"),
                "at": _notice_iso(req.requested_at),
            })
        for req in pending_prints:
            items.append({
                "id": f"print-wait-{req.id}",
                "kind": "print",
                "title": "Print request pending",
                "body": f"Waiting for admin on {_record_notice_label(req.record)}",
                "url": url_for("my_print_requests"),
                "at": _notice_iso(req.requested_at),
            })
        for req in rejected_edits:
            items.append({
                "id": f"edit-rej-{req.id}",
                "kind": "edit-rejected",
                "title": "Edit request rejected",
                "body": f"Admin rejected {_record_notice_label(req.record)}",
                "detail": (req.rejection_reason or "").strip() or "No reason given",
                "url": url_for("my_edit_requests"),
                "at": _notice_iso(req.reviewed_at or req.requested_at),
            })
        for req in rejected_prints:
            items.append({
                "id": f"print-rej-{req.id}",
                "kind": "print-rejected",
                "title": "Print request rejected",
                "body": f"Admin rejected {_record_notice_label(req.record)}",
                "detail": (req.rejection_reason or "").strip() or "No reason given",
                "url": url_for("my_print_requests"),
                "at": _notice_iso(req.reviewed_at or req.requested_at),
            })
        badges = {
            "staff_my_requests_pending": EditRequest.query.filter_by(requested_by_id=user.id, status="PENDING").count(),
            "staff_my_requests_approved_ready": (
                EditRequest.query.filter_by(requested_by_id=user.id, status="APPROVED")
                .filter(EditRequest.code_used_at.is_(None))
                .count()
            ),
            "staff_print_pending": PrintRequest.query.filter_by(requested_by_id=user.id, status="PENDING").count(),
            "staff_print_requests_approved_ready": (
                PrintRequest.query.filter_by(requested_by_id=user.id, status="APPROVED")
                .filter(PrintRequest.used_at.is_(None))
                .count()
            ),
        }
        badges["total"] = (
            badges["staff_my_requests_approved_ready"]
            + badges["staff_print_requests_approved_ready"]
            + badges["staff_my_requests_pending"]
            + badges["staff_print_pending"]
        )

    items.sort(key=lambda row: row.get("at") or "", reverse=True)
    items = items[:20]
    try:
        read_map = {
            row.notice_id: bool(row.is_read)
            for row in NotificationRead.query.filter_by(user_id=user.id).all()
        }
    except Exception:
        db.session.rollback()
        read_map = {}
    for item in items:
        item["read"] = bool(read_map.get(item.get("id") or "", False))
    unread = sum(1 for item in items if not item.get("read"))
    return {"items": items, "badges": badges, "count": unread}


def _notice_id_ok(notice_id: str) -> bool:
    return bool(notice_id) and bool(re.match(r"^[A-Za-z0-9._:-]{1,128}$", notice_id))


def _set_notice_read(user_id: int, notice_id: str, is_read: bool) -> None:
    row = NotificationRead.query.filter_by(user_id=user_id, notice_id=notice_id).first()
    if row is None:
        row = NotificationRead(user_id=user_id, notice_id=notice_id, is_read=is_read)
        db.session.add(row)
    else:
        row.is_read = is_read
    row.updated_at = datetime.utcnow()


def _notifications_payload(user):
    role = (user.role or "").upper()
    data = _collect_notifications(user)
    return {
        "ok": True,
        "role": role,
        "count": data["count"],
        "items": data["items"],
        "badges": data["badges"],
    }


@app.route("/api/notifications", methods=["GET"])
@login_required
def api_notifications():
    """Polled by the header bell (offline-safe, no websockets)."""
    user = get_current_user()
    if not user:
        return {"ok": False}, 401
    return _notifications_payload(user)


@app.route("/api/notifications/read", methods=["POST"])
@login_required
def api_notification_read():
    """Mark one header notification as read or unread."""
    user = get_current_user()
    if not user:
        return {"ok": False}, 401
    payload = request.get_json(silent=True) or {}
    notice_id = str(payload.get("id") or "").strip()
    if not _notice_id_ok(notice_id):
        return {"ok": False}, 400
    _set_notice_read(user.id, notice_id, bool(payload.get("read", True)))
    db.session.commit()
    return _notifications_payload(user)


@app.route("/api/notifications/read-all", methods=["POST"])
@login_required
def api_notifications_read_all():
    """Mark every currently listed notification as read."""
    user = get_current_user()
    if not user:
        return {"ok": False}, 401
    data = _collect_notifications(user)
    for item in data.get("items") or []:
        notice_id = str(item.get("id") or "").strip()
        if _notice_id_ok(notice_id):
            _set_notice_read(user.id, notice_id, True)
    db.session.commit()
    return _notifications_payload(user)


@app.route("/workflow/autofilled-form")
@login_required
def autofilled_form_latest():
    """Sidebar link: open the unsaved OCR review, or a blank form if nothing was scanned."""
    job_id = session.get("last_ocr_job_id")
    if job_id:
        with _OCR_JOBS_LOCK:
            job = _OCR_JOBS.get(job_id)
            status = job.get("status") if job else None
            image_filename = (job.get("image_filename") if job else "") or ""
        if job and status in ("queued", "running"):
            flash("OCR is still running. Wait for it to finish, then open Auto-Filled Form.")
            return redirect(url_for("index"))
        if job and status == "done":
            if image_filename and not _existing_upload_relpath(image_filename):
                flash("The scanned image for this review is missing. Scan the document again.")
                return redirect(url_for("index"))
            return redirect(url_for("ocr_result", job_id=job_id))
        session.pop("last_ocr_job_id", None)
        session.modified = True

    doc_type = (request.args.get("doc_type") or "birth").strip().lower()
    if doc_type not in DOCUMENT_TYPES:
        doc_type = "birth"
    return render_template(
        f"form_{doc_type}.html",
        doc_type=doc_type,
        doc_label=DOCUMENT_TYPES[doc_type],
        data={},
        image_filename="",
        blank_entry=True,
        current_user=get_current_user(),
        nav_active="form",
        can_print=user_can_print_during_review(get_current_user()),
    )


@app.route("/workflow/autofilled-form/cancel", methods=["POST"])
@login_required
def autofilled_form_cancel():
    """
    Cancel the current auto-filled review session without saving.
    Clears the session pointer and Document preview so Scan Workflow starts clean.
    """
    job_id = session.get("last_ocr_job_id")
    if job_id:
        with _OCR_JOBS_LOCK:
            _OCR_JOBS.pop(job_id, None)
    session.pop("last_ocr_job_id", None)
    _clear_staged_scan(delete_temp_file=True)
    session.modified = True
    flash("Auto-filled form review cancelled.")
    return redirect(url_for("index"))


@app.route("/ocr/result/<job_id>", methods=["GET"])
@login_required
def ocr_result(job_id):
    with _OCR_JOBS_LOCK:
        job = _OCR_JOBS.get(job_id)
        if not job:
            flash("OCR session not found. Please run OCR again.")
            return redirect(url_for("index"))
        if job.get("status") == "error":
            flash("OCR failed. Please try another image.")
            return redirect(url_for("index"))
        if job.get("status") != "done":
            flash("OCR is still processing. Please wait.")
            return redirect(url_for("index"))
        if job.get("user_id") not in (None, session.get("user_id")):
            flash("OCR session not found. Please run OCR again.")
            return redirect(url_for("index"))
        doc_type = job.get("doc_type")
        stored_image = job.get("image_filename") or ""
        image_filename = _existing_upload_relpath(stored_image)
        data = job.get("data") or {}

    if stored_image and not image_filename:
        flash("That scan was already saved (or the image is missing). Scan a new document to open Auto-Filled Form.")
        _consume_ocr_session(job_id)
        return redirect(url_for("index"))

    if doc_type not in DOCUMENT_TYPES:
        flash("OCR session is invalid. Please run OCR again.")
        return redirect(url_for("index"))
    session["last_ocr_job_id"] = job_id
    session.modified = True

    return render_template(
        f"form_{doc_type}.html",
        doc_type=doc_type,
        doc_label=DOCUMENT_TYPES[doc_type],
        data=data,
        image_filename=image_filename,
        current_user=get_current_user(),
        nav_active="form",
        can_print=user_can_print_during_review(get_current_user()),
    )


def _full_name_from_data(doc_type: str, data: dict) -> str:
    if doc_type == "birth":
        return (data.get("Name of Child") or "").strip()
    if doc_type == "marriage":
        h = (data.get("Husband Name") or "").strip()
        w = (data.get("Wife Name") or "").strip()
        return " / ".join(x for x in (h, w) if x)
    if doc_type == "death":
        return (data.get("Name of Deceased") or "").strip()
    return ""


def _event_date_from_data(doc_type: str, data: dict) -> str:
    if doc_type == "birth":
        return (data.get("Date of Birth") or "").strip()
    if doc_type == "marriage":
        return (data.get("Date of Marriage") or "").strip()
    if doc_type == "death":
        return (data.get("Date of Death") or "").strip()
    return ""


def _is_registry_metadata_key(key: str) -> bool:
    """True if this form/JSON key is a registry-number field (OCR uses varying labels)."""
    nk = re.sub(r"[^a-zA-Z0-9]", "", (key or "")).upper()
    if nk in ("REGISTRYNUMBER", "REGISTRYNO"):
        return True
    # "Date of Registration" → DATEOFREGISTRATION contains REGISTRY as substring — exclude
    if "REGISTRATION" in nk:
        return False
    return "REGISTRY" in nk and "NUMBER" in nk


def _strip_hidden_metadata(data: dict | None) -> dict:
    """Drop Page/Book fields — they are not on Civil Registry Form 1A / 2A / 3A."""
    if not data:
        return {}
    return {k: v for k, v in data.items() if str(k) not in HIDDEN_METADATA_KEYS}


def _submitted_from_request_form():
    """Build flat dict from POST; last value wins per field (fixes duplicate keys / MultiDict quirks)."""
    skip = {
        "csrf_token",
        "submit",
        "clear_annotation",
        "after_save",
        "job_id",
    } | set(ANNOTATION_FORM_KEYS) | set(HIDDEN_METADATA_KEYS)
    submitted = {}
    for key in request.form:
        if key in skip:
            continue
        values = request.form.getlist(key)
        if not values:
            submitted[key] = ""
        else:
            last = values[-1]
            submitted[key] = (last or "").strip() if isinstance(last, str) else last
    return _strip_hidden_metadata(submitted)


def _registry_number_from_submitted(submitted: dict) -> str:
    """Read registry number from canonical or any OCR key variant."""
    if not submitted:
        return ""
    v = submitted.get("Registry Number")
    if v is not None:
        v = v.strip() if isinstance(v, str) else str(v).strip()
    else:
        v = ""
    if v:
        return v
    best = ""
    for k, val in submitted.items():
        if _is_registry_metadata_key(k):
            if val is None:
                s = ""
            else:
                s = val.strip() if isinstance(val, str) else str(val).strip()
            if len(s) > len(best):
                best = s
    return best


def _normalize_submitted_registry_keys(submitted: dict) -> dict:
    """Keep a single 'Registry Number' key so saves update the indexed column correctly."""
    if not submitted:
        return submitted
    reg = _registry_number_from_submitted(submitted)
    out = {k: v for k, v in submitted.items() if not _is_registry_metadata_key(k)}
    out["Registry Number"] = reg
    return out


def _sync_json_data_from_record_columns(record: Record, data: dict | None) -> dict:
    """
    Merge indexed columns into the JSON field dict so UI matches Archiving / Search tables.
    Staff edits update columns; OCR may leave stale values in data_json — this aligns them.
    """
    out = dict(data) if data else {}
    reg = (record.registry_number or "").strip()
    if reg:
        for k in list(out.keys()):
            if _is_registry_metadata_key(k):
                del out[k]
        out["Registry Number"] = reg
    fn = (record.full_name or "").strip()
    ed = (record.event_date or "").strip()
    dt = record.document_type
    if dt == "birth":
        if fn:
            out["Name of Child"] = fn
        if ed:
            out["Date of Birth"] = ed
    elif dt == "death":
        if fn:
            out["Name of Deceased"] = fn
        if ed:
            out["Date of Death"] = ed
    elif dt == "marriage":
        if fn:
            if " / " in fn:
                parts = [p.strip() for p in fn.split("/", 1)]
                if len(parts) >= 2:
                    out["Husband Name"] = parts[0]
                    out["Wife Name"] = parts[1]
                else:
                    out["Husband Name"] = fn
            else:
                out["Husband Name"] = fn
        if ed:
            out["Date of Marriage"] = ed
    return out


def _repair_record_json_from_columns(record: Record) -> bool:
    """
    If stored JSON drifts from registry_number / full_name / event_date, rewrite data_json to match.
    Returns True if the database row was updated.
    """
    try:
        raw = json.loads(record.data_json) if record.data_json else {}
    except Exception:
        raw = {}
    synced = _sync_json_data_from_record_columns(record, raw)
    if json.dumps(synced, sort_keys=True, ensure_ascii=False) == json.dumps(
        raw, sort_keys=True, ensure_ascii=False
    ):
        return False
    try:
        s = json.dumps(synced, ensure_ascii=False)
        record.data_json = s
        record.extracted_json = s
        record.corrected_json = s
        record.updated_at = datetime.utcnow()
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False


def _load_record_data_json(record: Record) -> dict:
    """Parse data_json and align with indexed columns (may persist a repair)."""
    try:
        data = json.loads(record.data_json) if record.data_json else {}
    except Exception:
        data = {}
    _repair_record_json_from_columns(record)
    try:
        fresh = json.loads(record.data_json) if record.data_json else {}
    except Exception:
        fresh = {}
    # Always merge columns into the dict (registry/name/date) so forms and UI match Archiving
    # even if the stored JSON row was not updated by repair.
    merged = _sync_json_data_from_record_columns(record, fresh)
    # If indexed column was never set but JSON has a registry, persist it once (Archiving uses the column).
    if not (record.registry_number or "").strip():
        rn = (_registry_number_from_submitted(merged) or "").strip()
        if rn:
            record.registry_number = rn
            try:
                db.session.commit()
            except Exception:
                db.session.rollback()
    return _strip_hidden_metadata(merged)


def _sanitize_filename_part(s: str, max_len: int = 40) -> str:
    """Make a string safe for use in filenames: alphanumeric, hyphen, underscore only."""
    if not s or not isinstance(s, str):
        return "Unknown"
    s = s.strip()
    s = re.sub(r"[^a-zA-Z0-9\-_.\s]", "", s)
    s = re.sub(r"[\s]+", "_", s)
    s = s.strip("_") or "Unknown"
    return s[:max_len] if len(s) > max_len else s


def _parse_last_first(full_name: str):
    """Parse full name into (last_name, first_name). Handles 'LastName, FirstName' or 'FirstName LastName'."""
    full_name = (full_name or "").strip()
    if not full_name:
        return "Unknown", "Unknown"
    if "," in full_name:
        parts = [p.strip() for p in full_name.split(",", 1)]
        if len(parts) >= 2:
            return _sanitize_filename_part(parts[0]), _sanitize_filename_part(parts[1])
        return _sanitize_filename_part(parts[0]), "Unknown"
    words = full_name.split()
    if not words:
        return "Unknown", "Unknown"
    if len(words) == 1:
        return _sanitize_filename_part(words[0]), "Unknown"
    last_name = words[-1]
    first_name = " ".join(words[:-1])
    return _sanitize_filename_part(last_name), _sanitize_filename_part(first_name)


def _archive_image_filename(doc_type: str, full_name: str, registry_number: str, current_path: str) -> str:
    """
    Build an archive filename: LastName_FirstName_RegistryNumber_birth|marriage|death.ext
    For marriage, full_name may be 'Husband / Wife'; we use the first person for the filename.
    """
    # For marriage, take first name only (e.g. husband) for the file name
    primary_name = (full_name or "").split("/")[0].strip() if full_name else ""
    last_name, first_name = _parse_last_first(primary_name)
    reg_part = _sanitize_filename_part(registry_number, max_len=30) if registry_number else "NOREG"
    ext = Path(current_path).suffix.lower() if current_path else ".jpg"
    if ext not in (".jpg", ".jpeg", ".png"):
        ext = ".jpg"
    return f"{last_name}_{first_name}_{reg_part}_{doc_type}{ext}"


def _find_duplicate_record(
    doc_type: str,
    registry_number: str,
    full_name: str,
    event_date: str,
    exclude_record_id: int = None,
):
    """
    Find an existing duplicate record for the same document type.
    Same registry number always blocks, even if spacing or dashes differ.
    """
    q = Record.query.filter(Record.document_type == doc_type)
    if exclude_record_id:
        q = q.filter(Record.id != exclude_record_id)

    want = _normalize_registry_key(registry_number)
    if want:
        for rec in q.filter(Record.registry_number.isnot(None)).all():
            if _normalize_registry_key(rec.registry_number) == want:
                return rec

    name = (full_name or "").strip()
    evt = (event_date or "").strip()
    if name and evt:
        return q.filter(
            db.func.lower(db.func.trim(Record.full_name)) == name.lower(),
            db.func.lower(db.func.trim(Record.event_date)) == evt.lower(),
        ).first()
    return None


def _normalize_registry_key(reg: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (reg or "").upper())


def _apply_mother_name_filter(query, mother_name: str):
    """Filter records whose stored JSON contains mother_name in a mother-related field."""
    term = (mother_name or "").strip()
    if not term:
        return query
    clauses = [
        Record.data_json.ilike(f'%"{key}"%{term}%')
        for key in MOTHER_NAME_JSON_KEYS
    ]
    return query.filter(or_(*clauses))


@app.route("/search", methods=["GET", "POST"])
@login_required
def search_records():
    results = []
    total_matches = 0
    results_capped = False
    registry_number = (request.args.get("registry_number") or request.form.get("registry_number") or "").strip()
    full_name = (request.args.get("full_name") or request.form.get("full_name") or "").strip()
    mother_name = (request.args.get("mother_name") or request.form.get("mother_name") or "").strip()
    document_type = (request.args.get("document_type") or request.form.get("document_type") or "").strip().lower()
    event_date = (request.args.get("event_date") or request.form.get("event_date") or "").strip()

    if request.method == "POST" or registry_number or full_name or mother_name or document_type or event_date:
        q = Record.query
        if registry_number:
            q = q.filter(Record.registry_number.ilike(f"%{registry_number}%"))
        if full_name:
            q = q.filter(Record.full_name.ilike(f"%{full_name}%"))
        q = _apply_mother_name_filter(q, mother_name)
        if document_type and document_type in DOCUMENT_TYPES:
            q = q.filter(Record.document_type == document_type)
        if event_date:
            q = q.filter(Record.event_date.ilike(f"%{event_date}%"))
        total_matches = q.order_by(None).count()
        results = q.order_by(Record.created_at.desc()).limit(1000).all()
        results_capped = total_matches > 1000

    search_performed = request.method == "POST" or bool(
        registry_number or full_name or mother_name or document_type or event_date
    )

    return render_template(
        "search.html",
        current_user=get_current_user(),
        nav_active="search",
        results=results,
        total_matches=total_matches,
        results_capped=results_capped,
        search_performed=search_performed,
        registry_number=registry_number,
        full_name=full_name,
        mother_name=mother_name,
        document_type=document_type,
        event_date=event_date,
        document_types=DOCUMENT_TYPES,
    )


@app.route("/record/<int:record_id>")
@login_required
def record_detail(record_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    data = _load_record_data_json(record)
    display_registry_number = ""
    if isinstance(data, dict):
        v = data.get("Registry Number")
        if v is not None and str(v).strip():
            display_registry_number = str(v).strip()
        if not display_registry_number:
            display_registry_number = (_registry_number_from_submitted(data) or "").strip()
    if not display_registry_number:
        display_registry_number = (record.registry_number or "").strip()
    user = get_current_user()
    role = (user.role or "").upper() if user else ""
    # Staff: unused approved edit codes (full request history is on My Edit Requests)
    my_requests = []
    print_unlock_req = None
    can_print = False
    if role == "STAFF":
        my_requests = (
            EditRequest.query.filter_by(
                record_id=record_id, requested_by_id=user.id, status="APPROVED"
            )
            .filter(EditRequest.code_used_at.is_(None))
            .order_by(EditRequest.requested_at.desc())
            .all()
        )
        print_unlock_req = _staff_active_print_approval(user.id, record_id)
        can_print = user_can_print_record(user, record_id)
    elif role == "ADMIN":
        can_print = True
    approved_print_format = None
    if role == "STAFF" and can_print:
        approval = _staff_active_print_approval(user.id, record_id)
        if approval:
            approved_print_format = normalize_print_format(getattr(approval, "print_format", None))
    missing_print_fields = missing_required_review_fields(
        record.document_type, _record_print_payload(record, data)
    )
    return render_template(
        "record_detail.html",
        record=record,
        data=data,
        missing_print_fields=missing_print_fields,
        display_registry_number=display_registry_number,
        current_user=user,
        nav_active="search",
        my_requests=my_requests,
        print_unlock_req=print_unlock_req,
        document_types=DOCUMENT_TYPES,
        can_print=can_print,
        cert_date=cert_issue_date(),
        form_meta=form_meta(record.document_type),
        approved_print_format=approved_print_format,
        annotation_form=annotation_form_defaults(data),
        **_supporting_template_vars(record),
    )


@app.route("/record/<int:record_id>/annotation", methods=["POST"])
@login_required
def record_save_annotation(record_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    user = get_current_user()
    if not user or (user.role or "").upper() != "ADMIN":
        flash("Only administrators can save official document annotations.")
        return redirect(url_for("record_detail", record_id=record_id))
    data = _load_record_data_json(record)
    annotation = None
    if not (request.form.get("clear_annotation") or "").strip():
        annotation = annotation_from_form(request.form)
    data = apply_annotation_to_data(data, annotation)
    payload = json.dumps(data, ensure_ascii=False)
    record.data_json = payload
    record.extracted_json = payload
    record.corrected_json = payload
    record.updated_at = datetime.utcnow()
    db.session.commit()
    _log_audit(
        user.id,
        "RECORD_ANNOTATION",
        f"record_id={record_id} {'cleared' if not annotation else 'saved'}",
    )
    flash("Annotation removed." if not annotation else "Document annotation saved.")
    return redirect(url_for("record_detail", record_id=record_id))


@app.route("/record/<int:record_id>/attachments", methods=["POST"])
@login_required
def record_add_attachment(record_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    user = get_current_user()
    if not user:
        flash("Please log in.")
        return redirect(url_for("login"))
    storage_err = _require_server_storage()
    if storage_err:
        flash(storage_err, "error")
        return redirect(url_for("record_detail", record_id=record_id) + "#rd-supporting")
    kind = (request.form.get("attachment_kind") or "Other").strip()
    if kind not in ATTACHMENT_KINDS:
        kind = "Other"
    uploads = [f for f in request.files.getlist("attachment") if f and (f.filename or "").strip()]
    if not uploads:
        flash("Choose a file to attach.")
        return redirect(url_for("record_detail", record_id=record_id) + "#rd-supporting")
    if len(uploads) > MAX_ATTACHMENTS_PER_UPLOAD:
        flash(f"Attach up to {MAX_ATTACHMENTS_PER_UPLOAD} files at a time.")
        uploads = uploads[:MAX_ATTACHMENTS_PER_UPLOAD]
    folder_rel = _place_certificate_in_client_folder(record)
    support_rel = supporting_folder_rel(record)
    saved = 0
    skipped = 0
    for file in uploads:
        original = Path(file.filename).name
        stored_name = _safe_attachment_filename(original, kind)
        if not stored_name:
            skipped += 1
            continue
        dest_rel = f"{support_rel}/{stored_name}"
        dest = _upload_file_path(dest_rel, for_write=True)
        if dest is None:
            skipped += 1
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            file.save(str(dest))
            size = dest.stat().st_size if dest.is_file() else 0
            if size <= 0 or size > MAX_UPLOAD_BYTES:
                dest.unlink(missing_ok=True)
                skipped += 1
                continue
            row = RecordAttachment(
                record_id=record.id,
                original_name=original[:255],
                stored_path=dest_rel,
                kind=kind,
                size_bytes=size,
                uploaded_by_id=user.id,
            )
            db.session.add(row)
            saved += 1
        except OSError as exc:
            flash(describe_storage_error(exc, dest), "error")
            skipped += 1
    if saved:
        db.session.commit()
        _log_audit(
            user.id,
            "Add supporting document",
            f"Record {record.id} · {saved} file(s) · {folder_rel}",
        )
        flash(f"{saved} file{'s' if saved != 1 else ''} attached.")
    else:
        db.session.rollback()
    if skipped and not saved:
        flash("Use a PDF or image under 25 MB.")
    elif skipped:
        flash(f"{skipped} file(s) skipped.")
    return redirect(url_for("record_detail", record_id=record_id) + "#rd-supporting")


@app.route("/record/<int:record_id>/attachments/<int:attachment_id>")
@login_required
def record_get_attachment(record_id, attachment_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    row = db.session.get(RecordAttachment, attachment_id)
    if not row or row.record_id != record.id:
        flash("Supporting document not found.")
        return redirect(url_for("record_detail", record_id=record_id))
    folder_rel = client_folder_rel(record)
    stored = _normalize_upload_relpath(row.stored_path)
    if not stored.lower().startswith(folder_rel.lower() + "/"):
        return ("", 404)
    full = _upload_file_path(stored)
    if full is None or not full.is_file():
        flash("File not found.")
        return redirect(url_for("record_detail", record_id=record_id) + "#rd-supporting")
    as_download = (request.args.get("download") or "").strip() in ("1", "true", "yes")
    resp = send_file(
        full,
        as_attachment=as_download,
        download_name=row.original_name or full.name,
        conditional=True,
    )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/record/<int:record_id>/attachments/<int:attachment_id>/view")
@login_required
def record_view_attachment(record_id, attachment_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    row = db.session.get(RecordAttachment, attachment_id)
    if not row or row.record_id != record.id:
        flash("Supporting document not found.")
        return redirect(url_for("record_detail", record_id=record_id) + "#rd-supporting")
    folder_rel = client_folder_rel(record)
    stored = _normalize_upload_relpath(row.stored_path)
    if not stored.lower().startswith(folder_rel.lower() + "/"):
        return ("", 404)
    full = _upload_file_path(stored)
    if full is None or not full.is_file():
        flash("File not found.")
        return redirect(url_for("record_detail", record_id=record_id) + "#rd-supporting")
    return render_template(
        "attachment_view.html",
        record=record,
        attachment=row,
        current_user=get_current_user(),
        nav_active="search",
    )


@app.route("/record/<int:record_id>/attachments/<int:attachment_id>/delete", methods=["POST"])
@login_required
def record_delete_attachment(record_id, attachment_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    user = get_current_user()
    if not user:
        flash("Please log in.")
        return redirect(url_for("login"))
    row = db.session.get(RecordAttachment, attachment_id)
    if not row or row.record_id != record.id:
        flash("Supporting document not found.")
        return redirect(url_for("record_detail", record_id=record_id) + "#rd-supporting")
    full = _upload_file_path(row.stored_path, for_write=True)
    original = row.original_name
    db.session.delete(row)
    db.session.commit()
    if full is not None:
        try:
            full.unlink(missing_ok=True)
        except OSError:
            pass
    _log_audit(user.id, "Remove supporting document", f"Record {record.id} · {original}")
    flash("File removed.")
    return redirect(url_for("record_detail", record_id=record_id) + "#rd-supporting")


@app.route("/record/<int:record_id>/log-print", methods=["POST"])
@login_required
def record_log_print(record_id):
    """Log that staff printed this record's document. Requires admin approval for staff."""
    record = db.session.get(Record, record_id)
    if not record:
        return jsonify({"ok": False, "error": "Record not found"}), 404
    user = get_current_user()
    if not user:
        return jsonify({"ok": False, "error": "Not logged in"}), 403
    if not user_can_print_record(user, record_id):
        return jsonify({"ok": False, "error": "Admin approval is required before you can print."}), 403
    try:
        missing_keys = missing_required_review_fields(
            record.document_type, _record_print_payload(record)
        )
        missing_labels = [metadata_display_label(key) for key in missing_keys]
    except Exception:
        missing_keys = []
        missing_labels = []
    if missing_labels:
        return jsonify({
            "ok": False,
            "error": "Fill the missing Extracted metadata fields before printing: " + ", ".join(missing_labels) + ".",
            "missing": missing_labels,
            "missing_keys": missing_keys,
        }), 400
    payload = request.get_json(silent=True) or {}
    print_type = (payload.get("print_type") or "original").strip().lower()
    if print_type not in ("original", "certification", "both"):
        print_type = "original"
    if (user.role or "").upper() == "STAFF":
        approval = _staff_active_print_approval(user.id, record_id)
        if approval:
            allowed = normalize_print_format(getattr(approval, "print_format", None))
            if print_type != allowed:
                label = print_format_label(allowed, record.document_type)
                return jsonify({
                    "ok": False,
                    "error": f"Your approved request is for: {label}.",
                }), 403
    try:
        log = PrintLog(record_id=record_id, user_id=user.id)
        db.session.add(log)
        if (user.role or "").upper() == "STAFF":
            approval = _staff_active_print_approval(user.id, record_id)
            if approval:
                approval.used_at = datetime.utcnow()
                session.pop("allowed_print_request_id", None)
                session.pop("allowed_print_record_id", None)
        db.session.commit()
        _log_audit(
            user.id,
            "PRINT_DOCUMENT",
            f"record_id={record_id} print_type={print_type} registry={record.registry_number or ''}",
        )
        return jsonify({"ok": True})
    except Exception:
        db.session.rollback()
        return jsonify({"ok": False, "error": "Failed to log print"}), 500


@app.route("/record/<int:record_id>/request-print", methods=["GET", "POST"])
@login_required
def request_print(record_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    user = get_current_user()
    if not user or (user.role or "").upper() != "STAFF":
        flash("Only staff can submit print requests.")
        return redirect(url_for("record_detail", record_id=record_id))
    if _staff_active_print_approval(user.id, record_id):
        flash("This print request is already approved. Type the 5-digit code to print.")
        return redirect(url_for("my_print_requests"))
    pending = PrintRequest.query.filter_by(
        record_id=record_id, requested_by_id=user.id, status="PENDING"
    ).first()
    missing_print_fields = missing_print_metadata_labels(record)
    if missing_print_fields:
        flash(
            "Extracted metadata is incomplete. Request an edit first so Admin can fill: "
            + ", ".join(missing_print_fields)
            + ".",
            "error",
        )
        return redirect(url_for("request_edit", record_id=record_id))
    if request.method == "POST":
        reason = (request.form.get("reason") or "").strip()
        print_format = normalize_print_format(request.form.get("print_format"))
        if not reason:
            flash("Please provide a reason for the print request.")
            return redirect(url_for("request_print", record_id=record_id))
        if print_format in (PRINT_FORMAT_ORIGINAL, PRINT_FORMAT_BOTH) and not (record.image_path or "").strip():
            flash("This record has no scanned image on file. Choose the certification form instead.")
            return redirect(url_for("request_print", record_id=record_id))
        if pending:
            flash("You already have a pending print request for this record.")
            return redirect(url_for("record_detail", record_id=record_id))
        req = PrintRequest(
            record_id=record_id,
            requested_by_id=user.id,
            reason=reason,
            print_format=print_format,
        )
        db.session.add(req)
        db.session.commit()
        _log_audit(
            user.id,
            "PRINT_REQUEST_SUBMIT",
            f"record_id={record_id} request_id={req.id} print_format={print_format}",
        )
        flash("Print request sent. Wait for admin approval before printing.")
        return redirect(url_for("record_detail", record_id=record_id))
    return render_template(
        "request_print.html",
        record=record,
        current_user=user,
        nav_active="search",
        document_types=DOCUMENT_TYPES,
        has_pending=pending is not None,
        missing_print_fields=missing_print_fields,
        **_supporting_template_vars(record),
    )


@app.route("/record/<int:record_id>/request-edit", methods=["GET", "POST"])
@login_required
def request_edit(record_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    user = get_current_user()
    if not user or (user.role or "").upper() != "STAFF":
        flash("Only staff can submit edit requests.")
        return redirect(url_for("record_detail", record_id=record_id))
    data = _load_record_data_json(record)
    if request.method == "POST":
        reason = (request.form.get("reason") or "").strip()
        fields_requested = (request.form.get("fields_requested") or "").strip()
        if not reason:
            flash("Please provide a reason for the edit request.")
            return redirect(url_for("request_edit", record_id=record_id))
        req = EditRequest(
            record_id=record_id,
            requested_by_id=user.id,
            reason=reason,
            fields_requested=fields_requested or ", ".join(data.keys()) if data else "",
        )
        db.session.add(req)
        db.session.commit()
        _log_audit(user.id, "EDIT_REQUEST_SUBMIT", f"record_id={record_id} request_id={req.id}")
        flash("Edit request sent. Wait for admin approval and a 5-digit code to edit.")
        return redirect(url_for("record_detail", record_id=record_id))
    return render_template(
        "request_edit.html",
        record=record,
        data=data,
        current_user=user,
        nav_active="search",
        **_supporting_template_vars(record),
    )


def _records_scanned_by_year() -> dict:
    """Documents saved into the system, grouped by Philippine calendar year."""
    year_col = db.func.extract("year", Record.created_at + db.text("interval '8 hours'"))
    rows = (
        db.session.query(year_col, Record.document_type, db.func.count(Record.id))
        .group_by(year_col, Record.document_type)
        .all()
    )
    years: dict[str, dict[str, int]] = {}
    all_counts = {"birth": 0, "marriage": 0, "death": 0}
    for year_val, doc_type, count in rows:
        if year_val is None:
            continue
        year_key = str(int(year_val))
        kind = (doc_type or "").strip().lower()
        if kind not in all_counts:
            continue
        n = int(count or 0)
        bucket = years.setdefault(year_key, {"birth": 0, "marriage": 0, "death": 0})
        bucket[kind] += n
        all_counts[kind] += n
    now_year_n = (datetime.utcnow() + timedelta(hours=8)).year
    now_year = str(now_year_n)
    empty = {"birth": 0, "marriage": 0, "death": 0}
    end_year = now_year_n + 15
    future = [str(y) for y in range(now_year_n, end_year + 1)]
    for key in future:
        years.setdefault(key, dict(empty))
    past_with_scans = sorted(
        (
            key
            for key, counts in years.items()
            if key.isdigit()
            and int(key) < now_year_n
            and (counts.get("birth", 0) + counts.get("marriage", 0) + counts.get("death", 0)) > 0
        ),
        key=int,
    )
    year_list = past_with_scans + future
    return {
        "years": years,
        "year_list": year_list,
        "all": all_counts,
        "default_year": now_year,
    }


@app.route("/administration")
@admin_required
def administration():
    tab = (request.args.get("tab") or "dashboard").strip().lower()
    if tab == "backup_restore":
        return redirect(url_for("administration", tab="automatic_backup"))
    if tab not in ("dashboard", "audit", "edit_requests", "print_requests", "users", "print_logs", "automatic_backup", "office_logs"):
        tab = "dashboard"
    _mark_admin_tab_seen(tab)
    request_doc_type = _parse_doc_type_filter_arg(request.args.get("doc_type"))
    request_progress = _parse_request_progress_arg(request.args.get("status"))
    progress_counts = {"all": 0, "pending": 0, "complete": 0}
    request_pager = {"page": 1, "pages": 1, "total": 0, "start": 0, "end": 0, "has_prev": False, "has_next": False, "per_page": ADMIN_REQUEST_PAGE_SIZE}
    request_list_args = _admin_request_query_args(tab, request_doc_type, request_progress)
    edit_requests_list = []
    print_requests_list = []
    if tab == "edit_requests":
        edit_requests_list, progress_counts, request_pager = _paged_admin_requests(
            EditRequest, request_doc_type, request_progress, request.args.get("page")
        )
        request_list_args = _admin_request_query_args(tab, request_doc_type, request_progress)
    elif tab == "print_requests":
        print_requests_list, progress_counts, request_pager = _paged_admin_requests(
            PrintRequest, request_doc_type, request_progress, request.args.get("page")
        )
        request_list_args = _admin_request_query_args(tab, request_doc_type, request_progress)
    audit_logs = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(100).all() if tab == "audit" else []
    users_list = []
    password_target = None
    if tab == "users":
        users_list = User.query.order_by(User.username).all()
        users_list.sort(
            key=lambda u: (
                0 if (u.role or "").upper() == "ADMIN" else 1,
                (u.username or "").lower(),
            )
        )
        set_id = request.args.get("set", type=int)
        if set_id:
            password_target = next((u for u in users_list if u.id == set_id), None)
    print_logs = []
    if tab == "print_logs":
        print_logs = (
            PrintLog.query
            .order_by(PrintLog.printed_at.desc())
            .limit(100)
            .all()
        )
    pending_count = EditRequest.query.filter_by(status="PENDING").count()
    pending_print_count = PrintRequest.query.filter_by(status="PENDING").count()
    # Dashboard stats
    birth_count = Record.query.filter_by(document_type="birth").count()
    marriage_count = Record.query.filter_by(document_type="marriage").count()
    death_count = Record.query.filter_by(document_type="death").count()
    total_records = birth_count + marriage_count + death_count
    users_count = User.query.count()
    staff_count = User.query.filter(db.func.upper(User.role) == "STAFF").count()
    admin_users_count = User.query.filter(db.func.upper(User.role) == "ADMIN").count()
    audit_recent_count = AuditLog.query.count()
    backup_tabs = tab == "automatic_backup"
    auto_backup_settings = load_auto_backup_settings(BASE_DIR) if backup_tabs else None
    auto_backup_files = []
    backup_history = []
    backup_readiness = None
    if tab == "automatic_backup":
        try:
            backup_history = list_backup_history()
        except Exception:
            backup_history = []
        try:
            backup_readiness = get_backup_readiness(BASE_DIR, auto_backup_settings)
        except Exception:
            backup_readiness = None
    try:
        scan_year_stats = _records_scanned_by_year()
    except Exception:
        now_year_n = (datetime.utcnow() + timedelta(hours=8)).year
        scan_year_stats = {
            "years": {str(now_year_n): {"birth": 0, "marriage": 0, "death": 0}},
            "year_list": [str(y) for y in range(now_year_n, now_year_n + 16)],
            "all": {"birth": 0, "marriage": 0, "death": 0},
            "default_year": str(now_year_n),
        }
    return render_template(
        "administration.html",
        current_user=get_current_user(),
        nav_active="admin",
        tab=tab,
        edit_requests=edit_requests_list,
        print_requests=print_requests_list,
        audit_logs=audit_logs,
        users_list=users_list,
        pending_count=pending_count,
        pending_print_count=pending_print_count,
        admin_tab_badges=_admin_attention_badges(),
        birth_count=birth_count,
        marriage_count=marriage_count,
        death_count=death_count,
        total_records=total_records,
        users_count=users_count,
        staff_count=staff_count,
        admin_users_count=admin_users_count,
        audit_recent_count=audit_recent_count,
        document_types=DOCUMENT_TYPES,
        print_logs=print_logs,
        request_doc_type=request_doc_type,
        request_progress=request_progress,
        progress_counts=progress_counts,
        request_pager=request_pager,
        request_list_args=request_list_args,
        auto_backup_settings=auto_backup_settings,
        auto_backup_files=auto_backup_files,
        backup_history=backup_history,
        backup_readiness=backup_readiness,
        auto_backup_folder=backup_storage_display(BASE_DIR, auto_backup_settings) if backup_tabs else "",
        auto_backup_server_folder=server_backup_display(BASE_DIR, auto_backup_settings) if backup_tabs else "",
        vault_info=vault_status(BASE_DIR, UPLOAD_DIR) if backup_tabs else None,
        backup_schedule_hint=schedule_summary(auto_backup_settings) if backup_tabs and auto_backup_settings else "",
        log_retention=load_log_retention_settings(BASE_DIR),
        log_retention_hint=retention_hint(load_log_retention_settings(BASE_DIR)),
        scan_year_stats=scan_year_stats,
        office_log_stats=office_log_counts() if tab in ("office_logs", "dashboard") else None,
        today_backup_stamp=daily_backup_stamp(),
        revealed_password=session.pop("admin_password_reveal", None) if tab == "users" else None,
        password_target=password_target,
    )


def _civil_registry_report_context():
    doc_type = (request.args.get("doc_type") or "birth").strip().lower()
    if doc_type not in DOCUMENT_TYPES:
        doc_type = "birth"
    year = (request.args.get("year") or "").strip()
    try:
        year_n = int(year) if year else 0
        year = str(year_n) if year_n >= 2026 else ""
    except ValueError:
        year = ""
    barangay = (request.args.get("barangay") or "").strip()
    location = ""

    q = Record.query.filter(Record.document_type == doc_type).order_by(
        Record.created_at.asc(), Record.id.asc()
    )
    builders = {"birth": birth_row, "death": death_row, "marriage": marriage_row}
    builder = builders[doc_type]
    all_rows = []
    for record in q.all():
        if not record_is_reportable(record):
            continue
        data = load_record_fields(record)
        all_rows.append(builder(record, data))
    year_options, barangay_options, location_options = collect_filter_options(all_rows)
    filtered_rows = [row for row in all_rows if row_matches_filters(row, year, barangay, location)]
    find_q = (request.args.get("q") or "").strip()[:80]
    book_count = len(filtered_rows)
    if find_q:
        filtered_rows = [row for row in filtered_rows if row_matches_find(row, find_q)]
    try:
        per_page = int(request.args.get("per_page") or 25)
    except ValueError:
        per_page = 25
    total = len(filtered_rows)
    if per_page <= 0:
        per_page = max(total, 1)
        per_page_arg = 0
    else:
        per_page = max(1, min(per_page, 200))
        per_page_arg = per_page
    try:
        page = int(request.args.get("page") or 1)
    except ValueError:
        page = 1
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    start = (page - 1) * per_page
    rows = filtered_rows[start:start + per_page]
    register_pages = register_print_sheets(rows)
    focus = (request.args.get("focus") or "").strip() in {"1", "true", "yes"}
    excel_args = {"doc_type": doc_type}
    if year:
        excel_args["year"] = year
    if barangay:
        excel_args["barangay"] = barangay
    if location:
        excel_args["location"] = location
    query_args = dict(excel_args)
    if find_q:
        query_args["q"] = find_q
    query_args["per_page"] = per_page_arg
    if focus:
        query_args["focus"] = "1"
    enter_focus_args = dict(query_args)
    enter_focus_args["focus"] = "1"
    exit_focus_args = {k: v for k, v in query_args.items() if k != "focus"}
    if page > 1:
        enter_focus_args["page"] = page
        exit_focus_args["page"] = page
    doc_links = {
        "birth": {**{k: v for k, v in query_args.items() if k != "doc_type"}, "doc_type": "birth"},
        "death": {**{k: v for k, v in query_args.items() if k != "doc_type"}, "doc_type": "death"},
        "marriage": {**{k: v for k, v in query_args.items() if k != "doc_type"}, "doc_type": "marriage"},
    }
    return {
        "doc_type": doc_type,
        "year": year,
        "barangay": barangay,
        "location": location,
        "year_options": year_options,
        "barangay_options": barangay_options,
        "location_options": location_options,
        "rows": rows,
        "filtered_rows": filtered_rows,
        "filtered_count": total,
        "find_q": find_q,
        "book_count": book_count,
        "page": page,
        "per_page": per_page_arg,
        "total_pages": total_pages,
        "focus": focus,
        "query_args": query_args,
        "enter_focus_args": enter_focus_args,
        "exit_focus_args": exit_focus_args,
        "doc_links": doc_links,
        "register_pages": register_pages,
        "empty_rows": register_pages[0]["pad"],
        "page_left": 1,
        "page_right": 2,
        "excel_args": excel_args,
    }


@app.route("/administration/reports")
@admin_required
def civil_registry_reports():
    """Admin-only official Birth / Death / Marriage registers from saved archive records."""
    ctx = _civil_registry_report_context()
    return render_template(
        "reports.html",
        current_user=get_current_user(),
        nav_active="reports",
        document_types=DOCUMENT_TYPES,
        **ctx,
    )


@app.route("/administration/reports/excel")
@admin_required
def civil_registry_reports_excel():
    """Excel download of the same filtered official-register rows shown on screen."""
    ctx = _civil_registry_report_context()
    doc_type = ctx["doc_type"]
    rows = ctx["filtered_rows"]
    if doc_type == "death":
        sheets = {
            "Register of Death": (DEATH_EXCEL_HEADERS, [death_excel_row(r) for r in rows]),
        }
        filename = "register_of_death.xlsx"
    elif doc_type == "marriage":
        excel_rows = []
        for r in rows:
            excel_rows.extend(marriage_excel_rows(r))
        sheets = {
            "Register of Marriages": (MARRIAGE_EXCEL_HEADERS, excel_rows),
        }
        filename = "register_of_marriages.xlsx"
    else:
        sheets = {
            "Register of Live Births": (BIRTH_EXCEL_HEADERS, [birth_excel_row(r) for r in rows]),
        }
        filename = "register_of_live_births.xlsx"
    if year := ctx["year"]:
        filename = filename.replace(".xlsx", f"_{year}.xlsx")
    payload = build_xlsx(sheets)
    return send_file(
        BytesIO(payload),
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/administration/reports/pdf")
@admin_required
def civil_registry_reports_pdf():
    """Download the same official register form shown on screen as a PDF."""
    from services.report_pdf import build_register_pdf

    ctx = _civil_registry_report_context()
    doc_type = ctx["doc_type"]
    filename = {
        "death": "register_of_death.pdf",
        "marriage": "register_of_marriages.pdf",
    }.get(doc_type, "register_of_live_births.pdf")
    if ctx.get("year"):
        filename = filename.replace(".pdf", f"_{ctx['year']}.pdf")
    try:
        payload = build_register_pdf(doc_type, ctx["filtered_rows"])
    except Exception as exc:
        flash(f"Could not build PDF: {exc}", "error")
        return redirect(url_for("civil_registry_reports", **ctx["query_args"]))
    return send_file(
        BytesIO(payload),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=filename,
    )


@app.route("/administration/users/create", methods=["POST"])
@admin_required
def administration_create_user():
    """Create a new system user with a chosen role (ADMIN or STAFF)."""
    admin = get_current_user()
    username = _normalize_username(request.form.get("username") or "")
    password = (request.form.get("password") or "").strip()
    if request.form.get("generate_password") or not password:
        password = _issue_office_password()
    role = (request.form.get("role") or "").strip().upper()

    if not username:
        flash("Username is required.")
        return redirect(url_for("administration", tab="users"))
    if len(username) > 64:
        flash("Username must be 64 characters or fewer.")
        return redirect(url_for("administration", tab="users"))
    if len(password) < 6:
        flash("Password must be at least 6 characters.")
        return redirect(url_for("administration", tab="users"))
    if role not in ("ADMIN", "STAFF"):
        flash("Invalid role. Choose Administrator or Staff.")
        return redirect(url_for("administration", tab="users"))
    existing = _find_user_by_username(username)
    if existing:
        flash(f'Username "{existing.username}" is already in use (names are unique even if capital letters differ).')
        return redirect(url_for("administration", tab="users"))

    user = User(
        username=username,
        password_hash=_safe_hash(password),
        role=role,
        password_vault=_encrypt_password(password),
    )
    db.session.add(user)
    db.session.commit()
    _log_audit(admin.id if admin else None, "USER_CREATE", f"username={username} role={role}")
    session["admin_password_reveal"] = {
        "username": username,
        "password": password,
        "role": role,
        "kind": "created",
    }
    session.modified = True
    flash(f'Account "{username}" was created. Give them the login below now — it will not be shown again.', "success")
    return redirect(url_for("administration", tab="users"))


def _admin_target_user(user_id):
    admin = get_current_user()
    user = db.session.get(User, user_id)
    if not user:
        flash("User not found.")
        return admin, None
    if admin and user.id == admin.id:
        flash("Use My account to change your own password. You cannot force-logout or delete yourself here.")
        return admin, None
    return admin, user


@app.route("/administration/users/<int:user_id>/reset-password", methods=["POST"])
@admin_required
def administration_reset_password(user_id):
    admin, user = _admin_target_user(user_id)
    if not user:
        return redirect(url_for("administration", tab="users"))
    password = (request.form.get("password") or "").strip()
    if request.form.get("generate_password") or not password:
        password = _issue_office_password()
    if len(password) < 6:
        flash("Password must be at least 6 characters, or use Generate.")
        return redirect(url_for("administration", tab="users", set=user.id))
    user.password_hash = _safe_hash(password)
    _set_password_vault(user, password)
    _bump_session_version(user)
    db.session.commit()
    _log_audit(admin.id if admin else None, "USER_PASSWORD_RESET", f"username={user.username}")
    session["admin_password_reveal"] = {
        "username": user.username,
        "password": password,
        "role": user.role,
        "kind": "reset",
    }
    session.modified = True
    flash(f'Password for "{user.username}" was reset. They must log in again. Give them the new password below.', "success")
    return redirect(url_for("administration", tab="users"))


@app.route("/administration/users/<int:user_id>/view-password", methods=["POST"])
@admin_required
def administration_view_password(user_id):
    """Passwords are not retrieved. Open the Set password panel instead."""
    admin, user = _admin_target_user(user_id)
    if not user:
        return redirect(url_for("administration", tab="users"))
    return redirect(url_for("administration", tab="users", set=user.id))


@app.route("/administration/users/<int:user_id>/force-logout", methods=["POST"])
@admin_required
def administration_force_logout(user_id):
    admin, user = _admin_target_user(user_id)
    if not user:
        return redirect(url_for("administration", tab="users"))
    _bump_session_version(user)
    db.session.commit()
    _log_audit(admin.id if admin else None, "USER_FORCE_LOGOUT", f"username={user.username}")
    flash(f'"{user.username}" was signed out of all sessions.', "success")
    return redirect(url_for("administration", tab="users"))


@app.route("/administration/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def administration_delete_user(user_id):
    """Delete a user account. Related history is reassigned to the deleting admin."""
    admin, user = _admin_target_user(user_id)
    if not user:
        return redirect(url_for("administration", tab="users"))

    if (user.role or "").upper() == "ADMIN":
        remaining_admins = (
            User.query.filter(db.func.upper(User.role) == "ADMIN", User.id != user.id).count()
        )
        if remaining_admins <= 0:
            flash("Cannot delete this account. At least one ADMIN account must remain.")
            return redirect(url_for("administration", tab="users"))

    owner_id = admin.id if admin else user.id
    EditRequest.query.filter_by(requested_by_id=user.id).update(
        {EditRequest.requested_by_id: owner_id}, synchronize_session=False
    )
    EditRequest.query.filter_by(reviewed_by_id=user.id).update(
        {EditRequest.reviewed_by_id: owner_id}, synchronize_session=False
    )
    PrintRequest.query.filter_by(requested_by_id=user.id).update(
        {PrintRequest.requested_by_id: owner_id}, synchronize_session=False
    )
    PrintRequest.query.filter_by(reviewed_by_id=user.id).update(
        {PrintRequest.reviewed_by_id: owner_id}, synchronize_session=False
    )
    PrintLog.query.filter_by(user_id=user.id).update(
        {PrintLog.user_id: owner_id}, synchronize_session=False
    )
    AuditLog.query.filter_by(user_id=user.id).update(
        {AuditLog.user_id: None}, synchronize_session=False
    )
    RecordAttachment.query.filter_by(uploaded_by_id=user.id).update(
        {RecordAttachment.uploaded_by_id: owner_id}, synchronize_session=False
    )
    NotificationRead.query.filter_by(user_id=user.id).delete(synchronize_session=False)

    username = user.username
    role = (user.role or "").upper()
    _delete_user_photo_file(user)
    db.session.delete(user)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Could not delete user %s", username)
        flash(f'Could not delete "{username}". Related records are still linked to this account.', "error")
        return redirect(url_for("administration", tab="users"))
    _log_audit(admin.id if admin else None, "USER_DELETE", f"username={username} role={role}")
    flash(f'User "{username}" was deleted. Their history was kept under your account.', "success")
    return redirect(url_for("administration", tab="users"))


@app.route("/administration/backup")
@admin_required
def administration_backup():
    """Create a full zip backup on office-server storage, then download it if the user asked to."""
    ok, message, rel_file = run_auto_backup(
        base_dir=BASE_DIR,
        upload_dir=UPLOAD_DIR,
        audit_callback=_auto_backup_audit,
        trigger="download",
        kind="full",
        force=True,
    )
    if not ok:
        flash(f"Full backup failed: {message}", "error")
        return redirect(url_for("administration", tab="automatic_backup"))
    full_path, err = resolve_period_backup_path(BASE_DIR, rel_file)
    if err or full_path is None:
        flash(err or "Full backup file not found.", "error")
        return redirect(url_for("administration", tab="automatic_backup"))
    _log_audit(session.get("user_id"), "BACKUP_FULL", full_path.name)
    dest, copy_err = copy_backup_to_host_downloads(full_path)
    if dest is not None:
        flash(f"Full backup saved on the Daraga server.", "success")
    else:
        flash(copy_err or message, "error" if copy_err else "success")
    return redirect(url_for("administration", tab="automatic_backup"))


def _auto_backup_audit(action: str, details: str) -> None:
    _log_audit(None, action, details)


@app.route("/administration/auto-backup/settings", methods=["POST"])
@admin_required
def administration_auto_backup_settings():
    """Save automatic backup schedule even if the Daraga share is not reachable from this laptop yet."""
    settings = settings_from_form(request.form, BASE_DIR)
    previous_dest = _norm_dest_text(load_auto_backup_settings(BASE_DIR).get("destination_path") or "")
    folder, dest_error = resolve_server_backup_root(BASE_DIR, settings)
    reach_error = dest_error
    if folder is not None and not dest_error:
        reach_error = ensure_backup_dir_writable(folder)
    save_auto_backup_settings(BASE_DIR, settings)
    copied = failed = 0
    new_dest = _norm_dest_text(settings.get("destination_path") or "")
    path_changed = bool(folder is not None and not reach_error and new_dest and new_dest != previous_dest)
    if path_changed:
        copied, failed, _sync_err = sync_local_backups_to_server(BASE_DIR)
    try:
        init_auto_backup_scheduler(
            app,
            base_dir=BASE_DIR,
            upload_dir=UPLOAD_DIR,
            audit_callback=_auto_backup_audit,
            resolve_upload=_upload_file_path,
        )
        apply_schedule(app)
    except Exception as exc:
        flash(f"Settings saved, but scheduler could not start: {exc}", "error")
        return redirect(url_for("administration", tab="automatic_backup"))
    status = "enabled" if settings.get("enabled") else "disabled"
    dest_note = settings.get("destination_path") or "not set"
    _log_audit(
        session.get("user_id"),
        "AUTO_BACKUP_SETTINGS",
        f"status={status} frequency={settings.get('frequency')} dest={dest_note}",
    )
    flash(f"Schedule saved. {schedule_summary(settings)}", "success")
    if copied:
        flash(f"Copied {copied} backup(s) to the Daraga server.", "success")
    if failed:
        flash(f"{failed} backup(s) could not be copied to Daraga yet.", "error")
    return redirect(url_for("administration", tab="automatic_backup"))


def _persist_server_path_from_form() -> Optional[str]:
    """Remember the Daraga LGU server folder as the official backup location."""
    settings = load_auto_backup_settings(BASE_DIR)
    raw = office_backup_destination(BASE_DIR, request.form.get("auto_backup_destination") or "")
    trial = dict(settings)
    trial["destination_path"] = raw
    folder, dest_error = resolve_server_backup_root(BASE_DIR, trial)
    settings["destination_path"] = raw
    save_auto_backup_settings(BASE_DIR, settings)
    if dest_error:
        return dest_error
    if folder is not None:
        write_err = ensure_backup_dir_writable(folder)
        if write_err:
            return write_err
    return None


def _flash_backup_result(ok: bool, message: str) -> None:
    text = message or "Backup finished."
    if not ok:
        flash(f"Backup failed: {text}", "error")
        return
    flash(text, "error" if ("backup failed" in text.lower() or text.lower().startswith("backup failed")) else "success")


def _run_requested_backup(kind: str, trigger: str):
    settings = load_auto_backup_settings(BASE_DIR)
    kind = (kind or "").strip().lower()
    if kind not in ("daily", "weekly", "monthly", "yearly", "full"):
        kind = scheduled_backup_kind(settings)
    return run_auto_backup(
        base_dir=BASE_DIR,
        upload_dir=UPLOAD_DIR,
        audit_callback=_auto_backup_audit,
        trigger=trigger,
        kind=kind,
        force=True,
    ), kind


@app.route("/administration/auto-backup/run-now", methods=["POST"])
@admin_required
def administration_auto_backup_run_now():
    """Create a backup immediately on office-server storage (no workstation copy)."""
    dest_err = _persist_server_path_from_form()
    if dest_err:
        flash(
            dest_err + " The zip will still be created and held until you use Send to Daraga server.",
            "error",
        )
    (ok, message, rel_file), kind = _run_requested_backup(request.form.get("backup_kind"), "manual")
    _flash_backup_result(ok, message or f"Backup created: {rel_file}")
    return redirect(url_for("administration", tab="automatic_backup"))


@app.route("/administration/auto-backup/get-now", methods=["POST"])
@admin_required
def administration_auto_backup_get_now():
    """Send waiting backup zips to the Daraga LGU server (official archive)."""
    dest_err = _persist_server_path_from_form()
    if dest_err:
        flash(dest_err, "error")
    copied, failed, err = sync_local_backups_to_server(BASE_DIR)
    if copied:
        flash(f"Sent {copied} backup(s) to the Daraga LGU server.", "success")
        _log_audit(session.get("user_id"), "AUTO_BACKUP_GET", f"copied={copied} failed={failed}")
    if failed:
        flash(f"{failed} backup(s) could not be copied to the Daraga LGU server.", "error")
    if err and not copied:
        flash(err, "error")
    if not copied and not failed and not err:
        flash("The Daraga LGU server already has the current backups, or there is nothing waiting to send.", "success")
    return redirect(url_for("administration", tab="automatic_backup"))


@app.route("/administration/auto-backup/retry/<int:run_id>", methods=["POST"])
@admin_required
def administration_auto_backup_retry(run_id):
    """Retry a failed date-based backup for the same period."""
    row = db.session.get(BackupRun, run_id)
    if row is None:
        flash("Backup history entry not found.", "error")
        return redirect(url_for("administration", tab="automatic_backup"))
    kind = (row.kind or "daily").strip().lower()
    when = None
    try:
        if kind == "full" or kind == "daily":
            when = datetime.strptime(row.period_key, "%Y-%m-%d")
        elif kind == "weekly":
            when = datetime.strptime(row.period_key + "-1", "%G-W%V-%u")
        elif kind == "monthly":
            when = datetime.strptime(row.period_key + "-01", "%Y-%m-%d")
        else:
            when = datetime.strptime(row.period_key + "-01-01", "%Y-%m-%d")
    except ValueError:
        when = datetime.now()
    if kind == "full":
        ok, message, rel_file = run_full_backup(
            base_dir=BASE_DIR,
            force=True,
            trigger="retry",
            when=when,
            audit_callback=_auto_backup_audit,
        )
    else:
        ok, message, rel_file = run_period_backup(
            kind=kind,
            base_dir=BASE_DIR,
            force=True,
            trigger="retry",
            when=when,
            audit_callback=_auto_backup_audit,
        )
    if ok:
        flash(message or f"Backup created: {rel_file}", "success")
    else:
        flash(f"Backup failed: {message}", "error")
    return redirect(url_for("administration", tab="automatic_backup"))


@app.route("/administration/auto-backup/history/<int:run_id>/delete", methods=["POST"])
@admin_required
def administration_auto_backup_delete_run(run_id):
    """Admin-only: delete one backup zip and its history row. Original registry records are unchanged."""
    ok, message = delete_backup_run(BASE_DIR, run_id)
    user = get_current_user()
    if ok:
        _log_audit(
            session.get("user_id"),
            "AUTO_BACKUP_DELETED",
            f"run_id={run_id} by={user.username if user else '—'}",
        )
        flash(message, "success")
    else:
        flash(message, "error")
    return redirect(url_for("administration", tab="automatic_backup"))


@app.route("/administration/auto-backup/download-period")
@admin_required
def administration_auto_backup_download_period():
    """Send a history zip from the Daraga LGU server. Does not copy it into laptop Downloads."""
    file_rel = request.args.get("path") or ""
    full_path, err = resolve_period_backup_path(BASE_DIR, file_rel)
    if err or full_path is None:
        flash(err or "Backup file not found.", "error")
        return redirect(url_for("administration", tab="automatic_backup"))
    try:
        return send_file(full_path, as_attachment=True, download_name=full_path.name)
    except OSError:
        flash("Backup file is not available right now.", "error")
        return redirect(url_for("administration", tab="automatic_backup"))


@app.route("/administration/auto-backup/download/<path:filename>")
@admin_required
def administration_auto_backup_download(filename):
    """Download a previously saved automatic backup from backups/auto/."""
    full_path, err = resolve_auto_backup_path(BASE_DIR, filename)
    if err:
        flash(err)
        return redirect(url_for("administration", tab="automatic_backup"))
    folder = auto_backup_dir(BASE_DIR)
    return send_from_directory(folder, full_path.name, as_attachment=True)


@app.route("/administration/auto-backup/delete/<path:filename>", methods=["POST"])
@admin_required
def administration_auto_backup_delete(filename):
    """Delete a previously saved automatic backup from backups/auto/."""
    ok, message = delete_auto_backup(BASE_DIR, filename)
    user = get_current_user()
    if ok:
        _auto_backup_audit("AUTO_BACKUP_DELETED", f"file={message} by={user.username if user else '—'}")
        flash(f'Backup "{message}" was deleted.', "success")
    else:
        flash(message, "error")
    return redirect(url_for("administration", tab="automatic_backup"))


@app.route("/administration/office-logs/settings", methods=["POST"])
@admin_required
def administration_office_logs_settings():
    """Save rolling retention for edit/print queues and logs. Does not touch registry records."""
    settings = save_log_retention_settings(BASE_DIR, log_retention_from_form(request.form, BASE_DIR))
    _log_audit(
        session.get("user_id"),
        "LOG_RETENTION_SETTINGS",
        f"enabled={bool(settings.get('enabled'))} keep_days={settings.get('keep_days')}",
    )
    flash("Office log settings saved.", "success")
    return redirect(url_for("administration", tab="office_logs"))


@app.route("/administration/restore", methods=["POST"])
@admin_required
def administration_restore():
    """Restore database and scans from a full backup zip. Live scans stay in place until the zip is verified."""
    storage_err = _require_server_storage()
    if storage_err:
        return _restore_reply(False, storage_err)
    if "backup_file" not in request.files:
        return _restore_reply(False, "Please select a backup file (.zip) to restore.")
    file = request.files["backup_file"]
    if not file or file.filename == "":
        return _restore_reply(False, "Please select a backup file.")
    if not (file.filename or "").lower().endswith(".zip"):
        return _restore_reply(False, "Only .zip backup files are allowed.")

    incoming = UPLOAD_DIR.parent / f"_restore_upload_{uuid.uuid4().hex}.zip"
    work_root = UPLOAD_DIR.parent
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    unique = uuid.uuid4().hex[:8]
    tmp_dump = work_root / f"_restore_{stamp}_{unique}.json"
    staging = work_root / f"_restore_uploads_{unique}"
    extracted = False
    try:
        incoming.parent.mkdir(parents=True, exist_ok=True)
        file.save(str(incoming))
        with zipfile.ZipFile(incoming, "r") as zf:
            names = zf.namelist()
            dump_member = _zip_database_member(names)
            if not dump_member:
                lowered = {_zip_norm_name(n).lower() for n in names}
                if any(n.endswith("civil_registry.db") or n == "civil_registry.db" for n in lowered):
                    return _restore_reply(
                        False,
                        "This zip is a SQLite backup from the original offline project. "
                        "This copy uses PostgreSQL only. Import that file with: "
                        "python scripts/migrate_sqlite_to_postgres.py --sqlite path-to-civil_registry.db",
                    )
                return _restore_reply(False, "Invalid backup: zip must contain database.json.")
            with zf.open(dump_member) as src, open(tmp_dump, "wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            _extract_uploads_from_zip(zf, names, staging)
            extracted = True
        try:
            incoming.unlink(missing_ok=True)
        except OSError:
            pass
        incoming = None
        return _restore_postgres_backup(
            file.filename,
            tmp_dump,
            staging,
            (request.form.get("restore_mode") or "documents").strip().lower(),
        )
    except zipfile.BadZipFile:
        return _restore_reply(False, "Invalid or corrupted zip file.")
    except OSError as e:
        return _restore_reply(False, describe_storage_error(e, work_root) or str(e))
    except Exception as e:
        return _restore_reply(False, f"Restore failed: {e}. Existing documents were not replaced.")
    finally:
        if incoming is not None:
            try:
                incoming.unlink(missing_ok=True)
            except OSError:
                pass
        if not extracted:
            tmp_dump.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)


def _restore_wants_json() -> bool:
    try:
        return request.headers.get("X-Daraga-Restore") == "1"
    except RuntimeError:
        return False


@app.errorhandler(413)
def _request_too_large(_e):
    message = "The backup zip is too large (maximum 2 GB)."
    if _restore_wants_json():
        return jsonify({"ok": False, "message": message}), 413
    flash(message, "error")
    return redirect(url_for("administration", tab="automatic_backup"))


@app.errorhandler(Exception)
def _unhandled_error(exc):
    """Keep the demo on a normal page instead of Internal Server Error."""
    if isinstance(exc, HTTPException):
        return exc
    logging.getLogger(__name__).exception("Unhandled application error")
    message = "Something went wrong. Please try again."
    try:
        wants_json = (
            request.headers.get("X-Daraga-Restore") == "1"
            or request.headers.get("X-Login-Intent") == "ajax"
            or (request.path or "").startswith("/api/")
            or "application/json" in (request.headers.get("Accept") or "")
        )
        if wants_json:
            return jsonify({"ok": False, "error": message, "message": message}), 500
        flash(message, "error")
        if session.get("user_id"):
            target = request.referrer or url_for("index")
            return redirect(target)
        return redirect(url_for("login"))
    except Exception:
        return message, 500


def _restore_reply(ok: bool, message: str, extra=None):
    extra = extra or {}
    redirect_to = url_for("administration", tab="automatic_backup")
    if _restore_wants_json():
        if ok:
            flash(message, "success")
        payload = {"ok": ok, "message": message, "redirect": redirect_to}
        payload.update(extra)
        return jsonify(payload), (200 if ok else 400)
    flash(message, "success" if ok else "error")
    return redirect(redirect_to)


def _zip_norm_name(name: str) -> str:
    return (name or "").replace("\\", "/").strip()


def _zip_database_member(names) -> str:
    preferred = ""
    for n in names:
        norm = _zip_norm_name(n).rstrip("/")
        if Path(norm).name.lower() != "database.json":
            continue
        if norm.lower() == "database.json":
            return n
        if not preferred:
            preferred = n
    return preferred


def _zip_upload_rel(member_name: str) -> str:
    """Map a zip member onto birth|marriage|death|temp/... regardless of backslashes or wrapper folders."""
    norm = _zip_norm_name(member_name)
    if not norm or norm.endswith("/"):
        return ""
    lower = f"/{norm.lower()}/"
    if "/__macosx/" in lower:
        return ""
    base = Path(norm).name.lower()
    if base in {"database.json", "readme.txt", "manifest.csv", ".ds_store"} or base.startswith("."):
        return ""
    parts = [p for p in norm.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        return ""
    wrappers = {"full", "backups", "auto", "backup", "uploads"}
    while len(parts) >= 2 and parts[0].lower() in wrappers:
        parts = parts[1:]
    if parts and parts[0].lower() not in ALLOWED_UPLOAD_TOP and len(parts) >= 2:
        rest = parts[1:]
        while rest and rest[0].lower() in wrappers:
            rest = rest[1:]
        if rest and rest[0].lower() in ALLOWED_UPLOAD_TOP:
            parts = rest
    rel = _normalize_upload_relpath("/".join(parts))
    if not rel:
        return ""
    top = rel.split("/", 1)[0].lower()
    if top not in ALLOWED_UPLOAD_TOP:
        return ""
    if Path(rel).suffix.lower() not in ALLOWED_IMAGE_EXTS:
        return ""
    return rel


def _clear_directory_contents(folder: Path) -> None:
    if not folder.exists():
        return
    for item in list(folder.iterdir()):
        try:
            if item.is_file() or item.is_symlink():
                item.unlink(missing_ok=True)
            else:
                shutil.rmtree(item, ignore_errors=True)
        except OSError:
            pass


def _copy_tree_replace(source: Path, dest: Path) -> None:
    """Copy source into dest, replacing existing files. Avoids WinError 183 on Windows."""
    dest.mkdir(parents=True, exist_ok=True)
    _clear_directory_contents(dest)
    shutil.copytree(source, dest, dirs_exist_ok=True)


def _extract_uploads_from_zip(zf, names, staging: Path) -> int:
    """Write scan images from the zip into a staging folder. Live uploads are not touched."""
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    written = 0
    for name in names:
        rel = _zip_upload_rel(name)
        if not rel:
            continue
        dest = staging / Path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(name) as src, open(dest, "wb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        written += 1
    nested = staging / "uploads"
    if nested.is_dir():
        for src in nested.rglob("*"):
            if not src.is_file():
                continue
            dest = staging / src.relative_to(nested)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                try:
                    shutil.move(str(src), str(dest))
                except OSError:
                    pass
        shutil.rmtree(nested, ignore_errors=True)
    ensure_upload_layout(staging)
    return written


def _swap_upload_dir(staging: Path) -> None:
    """Replace live uploads with the restored tree after records are already loaded."""
    global UPLOAD_DIR
    parent = UPLOAD_DIR.parent
    live = UPLOAD_DIR
    old = parent / f"_uploads_replaced_{uuid.uuid4().hex[:8]}"
    renamed_live = False
    try:
        if live.exists():
            live.rename(old)
            renamed_live = True
        staging.rename(live)
        UPLOAD_DIR = live
        shutil.rmtree(old, ignore_errors=True)
    except OSError:
        if renamed_live:
            try:
                if not live.exists():
                    old.rename(live)
            except OSError:
                pass
        _copy_tree_replace(staging, live)
        UPLOAD_DIR = live
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(old, ignore_errors=True)
    ensure_upload_layout(UPLOAD_DIR)
    _flatten_nested_uploads()


def _resync_session_after_restore() -> None:
    """Keep the current admin signed in after user rows are replaced from the dump."""
    db.session.expire_all()
    uid = session.get("user_id")
    user = db.session.get(User, uid) if uid else None
    if user is None:
        uname = (session.get("username") or "").strip()
        if uname:
            user = _find_user_by_username(uname)
    if user is None:
        return
    session["user_id"] = user.id
    session["username"] = user.username
    session["role"] = user.role
    session["session_version"] = int(getattr(user, "session_version", 0) or 0)
    session.modified = True


def _merge_restored_uploads(staging: Path) -> int:
    """Copy restored scans into live storage. Does not delete or rename the uploads folder."""
    copied = 0
    if not staging.exists():
        return 0
    ensure_upload_layout(UPLOAD_DIR)
    for src in staging.rglob("*"):
        if not src.is_file():
            continue
        rel = _normalize_upload_relpath(src.relative_to(staging).as_posix())
        dest = _upload_file_path(rel, for_write=True)
        if dest is None:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        copied += 1
    shutil.rmtree(staging, ignore_errors=True)
    _flatten_nested_uploads()
    return copied


def _restore_postgres_backup(filename, tmp_dump, staging, mode="documents"):
    merged = False
    full = mode == "full"
    files_n = 0
    if staging.exists():
        files_n = sum(1 for p in staging.rglob("*") if p.is_file())
    try:
        from services.postgres_backup import restore_database_json, restore_documents_from_json

        try:
            payload = json.loads(tmp_dump.read_text(encoding="utf-8"))
            scope = str(payload.get("scope") or "full").strip().lower()
        except (OSError, json.JSONDecodeError) as exc:
            return _restore_reply(False, f"Invalid backup database.json: {exc}")

        if full and scope == "period":
            return _restore_reply(
                False,
                "This file is a daily (or weekly/monthly) document backup. "
                "Use Restore documents, not Full rebuild, so other days are not wiped.",
            )
        if full:
            loaded_n = restore_database_json(tmp_dump)
        else:
            loaded_n = restore_documents_from_json(tmp_dump)
        copied_n = _merge_restored_uploads(staging)
        merged = True
        _resync_session_after_restore()
        _log_audit(
            session.get("user_id"),
            "RESTORE_DATA",
            f"mode={'full' if full else 'documents'} scope={scope} from {filename} rows={loaded_n} files={copied_n or files_n}",
        )
        extra = {"records": loaded_n, "files": copied_n or files_n, "mode": "full" if full else "documents"}
        scans = copied_n or files_n
        if full:
            msg = f"Full rebuild complete. Loaded {loaded_n} row(s) and {scans} scan(s)."
        elif scope == "period":
            if scans:
                msg = f"Restored {loaded_n} document(s) and {scans} scan(s). Other days in the registry were kept."
            else:
                msg = f"Restored {loaded_n} document(s). Other days in the registry were kept."
        elif scans:
            msg = f"Restored {loaded_n} document(s) and {scans} scan(s) from this backup."
        else:
            msg = f"Restored {loaded_n} document(s) from this backup."
        return _restore_reply(True, msg, extra)
    except Exception as e:
        db.session.rollback()
        _resync_session_after_restore()
        return _restore_reply(
            False,
            f"Restore failed: {e}. Existing documents were not replaced.",
        )
    finally:
        Path(tmp_dump).unlink(missing_ok=True)
        if not merged:
            shutil.rmtree(staging, ignore_errors=True)


@app.route("/administration/print-request/<int:request_id>/approve", methods=["POST"])
@admin_required
def print_request_approve(request_id):
    req = db.session.get(PrintRequest, request_id)
    if not req or req.status != "PENDING":
        flash("Request not found or already processed.")
        return _admin_print_requests_redirect()
    missing_labels = missing_print_metadata_labels(req.record) if req.record else []
    if missing_labels:
        flash(
            "Cannot approve print until extracted metadata is complete: "
            + ", ".join(missing_labels)
            + ". Ask staff to request an edit first.",
            "error",
        )
        return _admin_print_requests_redirect()
    code = _new_approval_code()
    req.status = "APPROVED"
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by_id = session.get("user_id")
    req.rejection_reason = ""
    req.used_at = None
    req.approval_code = code
    db.session.commit()
    _log_audit(session["user_id"], "PRINT_REQUEST_APPROVE", f"request_id={request_id} record_id={req.record_id} code={code}")
    flash(f"Print approved. Give this 5-digit code to staff: {code}", "success")
    return _admin_print_requests_redirect()


@app.route("/administration/print-request/<int:request_id>/reject", methods=["POST"])
@admin_required
def print_request_reject(request_id):
    req = db.session.get(PrintRequest, request_id)
    if not req or req.status != "PENDING":
        flash("Request not found or already processed.")
        return _admin_print_requests_redirect()
    req.status = "REJECTED"
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by_id = session.get("user_id")
    req.rejection_reason = (request.form.get("rejection_reason") or "").strip()
    db.session.commit()
    _log_audit(session["user_id"], "PRINT_REQUEST_REJECT", f"request_id={request_id} record_id={req.record_id}")
    flash("Print request rejected.")
    return _admin_print_requests_redirect()


@app.route("/administration/edit-request/<int:request_id>/approve", methods=["POST"])
@admin_required
def edit_request_approve(request_id):
    req = db.session.get(EditRequest, request_id)
    if not req or req.status != "PENDING":
        flash("Request not found or already processed.")
        return _admin_edit_requests_redirect()
    code = _new_approval_code()
    req.status = "APPROVED"
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by_id = session.get("user_id")
    req.approval_code = code
    # Ensure a newly approved request is always unlockable by staff.
    req.code_used_at = None
    req.rejection_reason = ""
    db.session.commit()
    _log_audit(session["user_id"], "EDIT_REQUEST_APPROVE", f"request_id={request_id} record_id={req.record_id} code={code}")
    flash(f"Approved. Give this 5-digit code to staff: {code}", "success")
    return _admin_edit_requests_redirect()


@app.route("/administration/edit-request/<int:request_id>/reject", methods=["POST"])
@admin_required
def edit_request_reject(request_id):
    req = db.session.get(EditRequest, request_id)
    if not req or req.status != "PENDING":
        flash("Request not found or already processed.")
        return _admin_edit_requests_redirect()
    req.status = "REJECTED"
    req.reviewed_at = datetime.utcnow()
    req.reviewed_by_id = session.get("user_id")
    req.rejection_reason = (request.form.get("rejection_reason") or "").strip()
    db.session.commit()
    _log_audit(session["user_id"], "EDIT_REQUEST_REJECT", f"request_id={request_id} record_id={req.record_id}")
    flash("Edit request rejected.")
    return _admin_edit_requests_redirect()


@app.route("/record/<int:record_id>/verify-code", methods=["POST"])
@login_required
def record_verify_code(record_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    user = get_current_user()
    if not user or (user.role or "").upper() != "STAFF":
        flash("Only staff can use approval codes.")
        return redirect(url_for("record_detail", record_id=record_id))
    code = _parsed_approval_code()
    if len(code) != 5:
        flash("Please type the 5-digit code from admin.")
        next_url = request.form.get("next") or url_for("record_detail", record_id=record_id)
        return redirect(next_url)
    req = EditRequest.query.filter_by(
        record_id=record_id,
        requested_by_id=user.id,
        status="APPROVED",
        approval_code=code,
    ).filter(EditRequest.code_used_at.is_(None)).first()
    if not req:
        flash("Invalid or already-used code for this record.")
        next_url = request.form.get("next") or url_for("record_detail", record_id=record_id)
        return redirect(next_url)
    session["allowed_edit_request_id"] = req.id
    session["allowed_edit_record_id"] = record_id
    flash("Code accepted. You can now edit the record.", "success")
    return redirect(url_for("record_edit", record_id=record_id))


@app.route("/record/<int:record_id>/verify-print-code", methods=["POST"])
@login_required
def record_verify_print_code(record_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    user = get_current_user()
    if not user or (user.role or "").upper() != "STAFF":
        flash("Only staff can use print codes.")
        return redirect(url_for("record_detail", record_id=record_id))
    missing_labels = missing_print_metadata_labels(record)
    if missing_labels:
        flash(
            "Extracted metadata is incomplete. Request an edit first so Admin can fill: "
            + ", ".join(missing_labels)
            + ".",
            "error",
        )
        return redirect(url_for("request_edit", record_id=record_id))
    code = _parsed_approval_code()
    next_url = request.form.get("next") or url_for("record_detail", record_id=record_id)
    if len(code) != 5:
        flash("Please type the 5-digit print code from admin.")
        return redirect(next_url)
    req = PrintRequest.query.filter_by(
        record_id=record_id,
        requested_by_id=user.id,
        status="APPROVED",
        approval_code=code,
    ).filter(PrintRequest.used_at.is_(None)).first()
    if not req:
        flash("Invalid or already-used print code for this record.")
        return redirect(next_url)
    session["allowed_print_request_id"] = req.id
    session["allowed_print_record_id"] = record_id
    flash("Code accepted. You can now print this document.", "success")
    return redirect(url_for("record_detail", record_id=record_id))


@app.route("/record/<int:record_id>/edit", methods=["GET", "POST"])
@login_required
def record_edit(record_id):
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return redirect(url_for("search_records"))
    user = get_current_user()
    if not user:
        flash("Please log in to continue.")
        return redirect(url_for("login"))
    role = (user.role or "").upper()
    is_admin = role == "ADMIN"
    is_staff = role == "STAFF"
    if not is_admin and not is_staff:
        flash("You do not have permission to edit records.")
        return redirect(url_for("record_detail", record_id=record_id))
    req = None
    if is_staff:
        allowed_request_id = session.get("allowed_edit_request_id")
        try:
            allowed_record_id = int(session.get("allowed_edit_record_id"))
        except (TypeError, ValueError):
            allowed_record_id = None
        if allowed_record_id != record_id or not allowed_request_id:
            flash("Enter the 5-digit approval code on the record page first.")
            return redirect(url_for("record_detail", record_id=record_id))
        req = db.session.get(EditRequest, allowed_request_id)
        if (
            not req
            or req.record_id != record_id
            or req.code_used_at
            or (req.status or "").upper() != "APPROVED"
            or req.requested_by_id != user.id
        ):
            session.pop("allowed_edit_request_id", None)
            session.pop("allowed_edit_record_id", None)
            flash("This edit link has expired. Request a new code from admin.")
            return redirect(url_for("record_detail", record_id=record_id))
    if request.method == "POST":
        existing = _load_record_data_json(record)
        submitted = _submitted_from_request_form()
        submitted = _normalize_submitted_registry_keys(submitted)
        if record.document_type == "death":
            submitted.pop("Name of Mother", None)
        if (request.form.get("clear_annotation") or "").strip():
            annotation = None if is_admin else document_annotation_from_data(existing)
        elif not is_admin or "annotation_text" not in request.form:
            annotation = document_annotation_from_data(existing)
        else:
            annotation = annotation_from_form(request.form) or document_annotation_from_data(existing)
        submitted = apply_annotation_to_data(submitted, annotation)
        if not public_record_fields(submitted):
            flash("No data to save.")
            return redirect(url_for("record_edit", record_id=record_id))
        missing = missing_required_review_fields(record.document_type, submitted)
        if missing:
            labels = ", ".join(metadata_display_label(key) for key in missing)
            flash(f"Fill every required field before saving: {labels}.", "error")
            display_registry_number = (_registry_number_from_submitted(submitted) or record.registry_number or "").strip()
            _repair_record_image_path(record)
            return render_template(
                "record_edit.html",
                record=record,
                data=submitted,
                display_registry_number=display_registry_number,
                current_user=user,
                nav_active="search",
                document_types=DOCUMENT_TYPES,
                annotation_form=annotation_form_defaults(submitted),
                missing_fields=missing,
            )
        updated_registry_number = _registry_number_from_submitted(submitted)
        updated_full_name = _full_name_from_data(record.document_type, submitted)
        updated_event_date = _event_date_from_data(record.document_type, submitted)
        prior_registry_number = (record.registry_number or "").strip()
        prior_full_name = (record.full_name or "").strip()
        prior_event_date = (record.event_date or "").strip()
        identity_changed = (
            _normalize_registry_key(updated_registry_number) != _normalize_registry_key(prior_registry_number)
            or (not updated_registry_number and (updated_full_name != prior_full_name or updated_event_date != prior_event_date))
        )
        duplicate = _find_duplicate_record(
            doc_type=record.document_type,
            registry_number=updated_registry_number,
            full_name=updated_full_name if identity_changed else "",
            event_date=updated_event_date if identity_changed else "",
            exclude_record_id=record.id,
        )
        if duplicate:
            flash(
                f"Update blocked: this document already exists in archiving (Record ID: {duplicate.id}). "
                "Duplicate registry numbers are not allowed."
            )
            return redirect(url_for("record_edit", record_id=record_id))
        data_json_str = json.dumps(submitted, ensure_ascii=False)
        record.data_json = data_json_str
        record.extracted_json = data_json_str
        record.corrected_json = data_json_str
        record.registry_number = updated_registry_number or None
        record.full_name = updated_full_name
        record.event_date = updated_event_date
        record.updated_at = datetime.utcnow()
        if req:
            req.code_used_at = record.updated_at
            session.pop("allowed_edit_request_id", None)
            session.pop("allowed_edit_record_id", None)
        db.session.commit()
        db.session.refresh(record)
        _log_audit(user.id, "RECORD_EDIT", f"record_id={record_id} by={role}")
        flash("Record updated successfully.")
        return redirect(url_for("record_detail", record_id=record_id))
    data = _load_record_data_json(record)
    data = _normalize_submitted_registry_keys(dict(data))
    display_registry_number = ""
    if isinstance(data, dict):
        v = data.get("Registry Number")
        if v is not None and str(v).strip():
            display_registry_number = str(v).strip()
        if not display_registry_number:
            display_registry_number = (_registry_number_from_submitted(data) or "").strip()
    if not display_registry_number:
        display_registry_number = (record.registry_number or "").strip()
    _repair_record_image_path(record)
    return render_template(
        "record_edit.html",
        record=record,
        data=data,
        display_registry_number=display_registry_number,
        current_user=user,
        nav_active="search",
        document_types=DOCUMENT_TYPES,
        annotation_form=annotation_form_defaults(data),
    )


def _archiving_records_query(doc_type: str, registry_q: str = ""):
    q = Record.query.filter_by(document_type=doc_type).order_by(Record.created_at.desc())
    needle = (registry_q or "").strip()
    if needle:
        q = q.filter(Record.registry_number.ilike(f"%{needle}%"))
    return q


def _redirect_archiving():
    """Return redirect to archiving, preserving type and registry search."""
    dt = _parse_doc_type_filter_arg(request.form.get("doc_type") or request.args.get("doc_type"))
    registry = (request.form.get("registry") or request.args.get("registry") or "").strip()
    kwargs = {}
    if dt:
        kwargs["doc_type"] = dt
        if registry:
            kwargs["registry"] = registry
    return redirect(url_for("archiving", **kwargs)) if kwargs else redirect(url_for("archiving"))


@app.route("/archiving")
@login_required
def archiving():
    """Archiving page: all saved records separated by Birth, Marriage, Death."""
    archive_doc_type = _parse_doc_type_filter_arg(request.args.get("doc_type"))
    archive_registry_q = (request.args.get("registry") or "").strip()
    if not archive_doc_type:
        archive_registry_q = ""

    birth_total = Record.query.filter_by(document_type="birth").count()
    marriage_total = Record.query.filter_by(document_type="marriage").count()
    death_total = Record.query.filter_by(document_type="death").count()

    birth_filter = archive_registry_q if archive_doc_type == "birth" else ""
    marriage_filter = archive_registry_q if archive_doc_type == "marriage" else ""
    death_filter = archive_registry_q if archive_doc_type == "death" else ""

    archive_list_limit = 1000
    birth_q = _archiving_records_query("birth", birth_filter)
    marriage_q = _archiving_records_query("marriage", marriage_filter)
    death_q = _archiving_records_query("death", death_filter)
    birth_shown = birth_q.count()
    marriage_shown = marriage_q.count()
    death_shown = death_q.count()
    birth_records = birth_q.limit(archive_list_limit).all()
    marriage_records = marriage_q.limit(archive_list_limit).all()
    death_records = death_q.limit(archive_list_limit).all()
    return render_template(
        "archiving.html",
        birth_records=birth_records,
        marriage_records=marriage_records,
        death_records=death_records,
        birth_total=birth_total,
        marriage_total=marriage_total,
        death_total=death_total,
        birth_shown=birth_shown,
        marriage_shown=marriage_shown,
        death_shown=death_shown,
        document_types=DOCUMENT_TYPES,
        archive_doc_type=archive_doc_type,
        archive_registry_q=archive_registry_q,
        current_user=get_current_user(),
        nav_active="archiving",
    )


@app.route("/record/<int:record_id>/delete", methods=["POST"])
@login_required
def record_delete(record_id):
    """Delete a record (admin only). Removes related edit requests and optionally the stored image."""
    user = get_current_user()
    if not user or (user.role or "").upper() != "ADMIN":
        flash("Only administrators can delete records.")
        return _redirect_archiving()
    record = db.session.get(Record, record_id)
    if not record:
        flash("Record not found.")
        return _redirect_archiving()
    doc_type = record.document_type
    # Remove related rows first so print logs / requests cannot block the delete.
    for log in PrintLog.query.filter_by(record_id=record_id).all():
        db.session.delete(log)
    for req in PrintRequest.query.filter_by(record_id=record_id).all():
        db.session.delete(req)
    for req in EditRequest.query.filter_by(record_id=record_id).all():
        db.session.delete(req)
    for att in RecordAttachment.query.filter_by(record_id=record_id).all():
        att_path = _upload_file_path(att.stored_path, for_write=True)
        if att_path is not None and att_path.is_file():
            try:
                att_path.unlink()
            except OSError:
                pass
        db.session.delete(att)
    db.session.flush()
    folder_rel = client_folder_rel(record)
    folder_path = _upload_file_path(folder_rel, for_write=True)
    # Optionally remove stored image file
    for path_attr in ("image_path", "image_front_path"):
        rel_path = getattr(record, path_attr, None) or ""
        if rel_path and not rel_path.startswith(".."):
            full_path = _upload_file_path(rel_path)
            if full_path is not None and full_path.exists():
                try:
                    full_path.unlink()
                except OSError:
                    pass
    if folder_path is not None and folder_path.is_dir():
        shutil.rmtree(folder_path, ignore_errors=True)
    db.session.delete(record)
    db.session.commit()
    _log_audit(user.id, "RECORD_DELETE", f"record_id={record_id} type={doc_type}")
    flash("Record deleted successfully.", "success")
    return _redirect_archiving()


@app.route("/my-edit-requests")
@login_required
def my_edit_requests():
    """Staff page: list all edit requests submitted by the current user."""
    user = get_current_user()
    if not user or (user.role or "").upper() != "STAFF":
        flash("This page is for staff only.")
        return redirect(url_for("index"))
    request_doc_type = _parse_doc_type_filter_arg(request.args.get("doc_type"))
    request_progress = _parse_request_progress_arg(request.args.get("status"))
    requests_q = EditRequest.query.filter_by(requested_by_id=user.id)
    if request_doc_type:
        requests_q = requests_q.join(Record, EditRequest.record_id == Record.id).filter(
            Record.document_type == request_doc_type
        )
    progress_counts = {
        "all": requests_q.count(),
        "pending": _filter_staff_edit_progress(requests_q, "pending").count(),
        "complete": _filter_staff_edit_progress(requests_q, "complete").count(),
    }
    requests_list = requests_q.order_by(EditRequest.requested_at.desc()).all()
    return render_template(
        "my_edit_requests.html",
        current_user=user,
        nav_active="my_edit_requests",
        requests_list=requests_list,
        document_types=DOCUMENT_TYPES,
        request_doc_type=request_doc_type,
        request_progress=request_progress,
        progress_counts=progress_counts,
    )


@app.route("/my-print-requests")
@login_required
def my_print_requests():
    """Staff page: list all print requests submitted by the current user."""
    user = get_current_user()
    if not user or (user.role or "").upper() != "STAFF":
        flash("This page is for staff only.")
        return redirect(url_for("index"))
    request_doc_type = _parse_doc_type_filter_arg(request.args.get("doc_type"))
    request_progress = _parse_request_progress_arg(request.args.get("status"))
    requests_q = PrintRequest.query.filter_by(requested_by_id=user.id)
    if request_doc_type:
        requests_q = requests_q.join(Record, PrintRequest.record_id == Record.id).filter(
            Record.document_type == request_doc_type
        )
    progress_counts = {
        "all": requests_q.count(),
        "pending": _filter_staff_print_progress(requests_q, "pending").count(),
        "complete": _filter_staff_print_progress(requests_q, "complete").count(),
    }
    requests_list = requests_q.order_by(PrintRequest.requested_at.desc()).all()
    return render_template(
        "my_print_requests.html",
        current_user=user,
        nav_active="my_print_requests",
        requests_list=requests_list,
        document_types=DOCUMENT_TYPES,
        request_doc_type=request_doc_type,
        request_progress=request_progress,
        progress_counts=progress_counts,
    )


@app.route("/uploads/<path:path>")
@login_required
def serve_upload(path):
    """Serve uploaded certificate images from server storage (view only; not a client save)."""
    rel = _normalize_upload_relpath(path)
    if not rel:
        return ("", 404)
    parts = [p for p in rel.split("/") if p]
    if not parts or parts[0].lower() not in ALLOWED_UPLOAD_TOP:
        return ("", 404)
    if Path(rel).suffix.lower() not in ALLOWED_IMAGE_EXTS:
        return ("", 404)
    full = _upload_file_path(rel)
    if full is None or not full.is_file():
        full = _file_matching_registry(rel)
    if full is None or not full.is_file():
        return ("", 404)
    resp = send_file(full, as_attachment=False, conditional=True)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.route("/confirm/<doc_type>", methods=["POST"])
@login_required
def confirm(doc_type):
    if doc_type not in DOCUMENT_TYPES:
        return redirect(url_for("index"))

    submitted = _submitted_from_request_form()
    image_filename = submitted.pop("image_filename", None) or request.form.get("image_filename")
    if doc_type == "death":
        submitted.pop("Name of Mother", None)
    submitted = _normalize_submitted_registry_keys(submitted)
    user = get_current_user()
    if user and (user.role or "").upper() == "ADMIN":
        submitted = apply_annotation_to_data(submitted, annotation_from_form(request.form))

    missing = missing_required_review_fields(doc_type, submitted)
    if missing:
        labels = ", ".join(metadata_display_label(key) for key in missing)
        flash(f"Fill every required field before saving: {labels}.", "error")
        return render_template(
            f"form_{doc_type}.html",
            doc_type=doc_type,
            doc_label=DOCUMENT_TYPES[doc_type],
            data=submitted,
            image_filename=image_filename,
            current_user=get_current_user(),
            nav_active="form",
            can_print=user_can_print_during_review(get_current_user()),
            missing_fields=missing,
        )

    registry_number = _registry_number_from_submitted(submitted)
    full_name = _full_name_from_data(doc_type, submitted)
    event_date = _event_date_from_data(doc_type, submitted)
    duplicate = _find_duplicate_record(
        doc_type=doc_type,
        registry_number=registry_number,
        full_name=full_name,
        event_date=event_date,
    )
    if duplicate:
        flash(
            f"Save blocked: this document is already archived (Record ID: {duplicate.id}). "
            "Duplicate documents are not allowed."
        )
        return render_template(
            f"form_{doc_type}.html",
            doc_type=doc_type,
            doc_label=DOCUMENT_TYPES[doc_type],
            data=submitted,
            image_filename=image_filename,
            current_user=get_current_user(),
            nav_active="form",
            can_print=user_can_print_during_review(get_current_user()),
        )

    # Auto-rename file for archive: LastName_FirstName_RegistryNumber_birth|marriage|death.ext
    if image_filename and not (Path(image_filename).is_absolute() or ".." in image_filename):
        storage_err = _require_server_storage()
        if storage_err:
            flash(storage_err, "error")
            return render_template(
                f"form_{doc_type}.html",
                doc_type=doc_type,
                doc_label=DOCUMENT_TYPES[doc_type],
                data=submitted,
                image_filename=image_filename,
                current_user=get_current_user(),
                nav_active="form",
                can_print=user_can_print_during_review(get_current_user()),
            )
        image_filename = _normalize_upload_relpath(image_filename)
        source_path = _upload_file_path(image_filename)
        if source_path is not None and source_path.exists():
            new_basename = _archive_image_filename(doc_type, full_name, registry_number, image_filename)
            dest_path = _upload_file_path(f"{doc_type}/{new_basename}", for_write=True)
            if dest_path is None:
                flash("Invalid archive path. The record was not saved.", "error")
                return render_template(
                    f"form_{doc_type}.html",
                    doc_type=doc_type,
                    doc_label=DOCUMENT_TYPES[doc_type],
                    data=submitted,
                    image_filename=image_filename,
                    current_user=get_current_user(),
                    nav_active="form",
                    can_print=user_can_print_during_review(get_current_user()),
                )
            if dest_path.exists() and source_path.resolve() != dest_path.resolve():
                dest_path = dest_path.with_name(
                    f"{dest_path.stem}_{uuid.uuid4().hex[:8]}{dest_path.suffix}"
                )
            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                if source_path.resolve() != dest_path.resolve():
                    shutil.copy2(source_path, dest_path)
                    image_filename = f"{doc_type}/{dest_path.name}"
                    if "temp" in str(source_path) or source_path != dest_path:
                        try:
                            source_path.unlink()
                        except OSError:
                            pass
                else:
                    image_filename = f"{doc_type}/{dest_path.name}"
            except OSError as exc:
                flash(describe_storage_error(exc, dest_path), "error")
                return render_template(
                    f"form_{doc_type}.html",
                    doc_type=doc_type,
                    doc_label=DOCUMENT_TYPES[doc_type],
                    data=submitted,
                    image_filename=image_filename,
                    current_user=get_current_user(),
                    nav_active="form",
                    can_print=user_can_print_during_review(get_current_user()),
                )
            if dest_path is None or not dest_path.is_file() or dest_path.stat().st_size <= 0:
                flash(
                    "The certificate file could not be stored on the server. The record was not saved.",
                    "error",
                )
                return render_template(
                    f"form_{doc_type}.html",
                    doc_type=doc_type,
                    doc_label=DOCUMENT_TYPES[doc_type],
                    data=submitted,
                    image_filename=image_filename,
                    current_user=get_current_user(),
                    nav_active="form",
                    can_print=user_can_print_during_review(get_current_user()),
                )

    data_json_str = json.dumps(submitted, ensure_ascii=False)
    image_front = image_filename or ""
    record = Record(
        document_type=doc_type,
        registry_number=registry_number or None,
        full_name=full_name or None,
        event_date=event_date or None,
        data_json=data_json_str,
        extracted_json=data_json_str,
        corrected_json=data_json_str,
        image_path=image_filename or None,
        image_front_path=image_front,
    )
    db.session.add(record)
    db.session.commit()
    _place_certificate_in_client_folder(record)
    _consume_ocr_session()

    user = get_current_user()
    want_print = (request.form.get("after_save") or "").strip().lower() == "request_print"
    if want_print and _create_staff_print_request_after_ocr(user, record):
        flash(
            f"{DOCUMENT_TYPES[doc_type]} saved. Print request sent. Wait for Admin to approve or reject."
        )
        return redirect(url_for("record_detail", record_id=record.id))

    flash(f"{DOCUMENT_TYPES[doc_type]} data has been saved. View it in Archiving or Search Records. Scan a new document to start another Auto-Filled Form.")
    return render_template(
        "confirm.html",
        doc_type=doc_type,
        doc_label=DOCUMENT_TYPES[doc_type],
        data=public_record_fields(submitted),
        image_filename=image_filename or None,
        current_user=get_current_user(),
        nav_active="scan",
        record_id=record.id,
    )


def _safe_hash(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256")


def _normalize_username(username: str) -> str:
    return " ".join((username or "").split())


def _find_user_by_username(username: str):
    key = _normalize_username(username)
    if not key:
        return None
    return User.query.filter(db.func.lower(User.username) == key.lower()).first()


def _issue_office_password(length: int = 10) -> str:
    """Readable temporary password for civil registry staff (no ambiguous characters)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
    return "".join(secrets.choice(alphabet) for _ in range(max(8, length)))


def _vault_key() -> bytes:
    secret = str(app.secret_key or "change-this-secret-key").encode("utf-8")
    return hashlib.sha256(b"daraga-staff-password-vault|" + secret).digest()


def _encrypt_password(plain: str) -> str:
    raw = (plain or "").encode("utf-8")
    if not raw:
        return ""
    key = _vault_key()
    iv = os.urandom(16)
    stream = bytearray()
    counter = 0
    while len(stream) < len(raw):
        stream.extend(hmac.new(key, iv + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    cipher = bytes(a ^ b for a, b in zip(raw, stream[: len(raw)]))
    mac = hmac.new(key, iv + cipher, hashlib.sha256).digest()[:16]
    return "v1:" + base64.urlsafe_b64encode(iv + mac + cipher).decode("ascii")


def _decrypt_password(token: str) -> str:
    blob = (token or "").strip()
    if not blob.startswith("v1:"):
        return ""
    try:
        packed = base64.urlsafe_b64decode(blob[3:].encode("ascii"))
    except Exception:
        return ""
    if len(packed) < 33:
        return ""
    iv, mac, cipher = packed[:16], packed[16:32], packed[32:]
    key = _vault_key()
    expect = hmac.new(key, iv + cipher, hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(mac, expect):
        return ""
    stream = bytearray()
    counter = 0
    while len(stream) < len(cipher):
        stream.extend(hmac.new(key, iv + counter.to_bytes(8, "big"), hashlib.sha256).digest())
        counter += 1
    plain = bytes(a ^ b for a, b in zip(cipher, stream[: len(cipher)]))
    try:
        return plain.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _set_password_vault(user, password: str) -> None:
    if not user:
        return
    user.password_vault = _encrypt_password(password) if password else None


def _remember_password_vault(user, password: str) -> None:
    """Keep a recoverable copy after a successful login if one is not stored yet."""
    if not user or not password:
        return
    if (getattr(user, "password_vault", None) or "").strip():
        return
    try:
        _set_password_vault(user, password)
        db.session.commit()
    except Exception:
        db.session.rollback()


def _migrate_records():
    """Add any missing columns to records table (for DBs created before schema updates)."""
    try:
        existing = _existing_columns("records")
    except Exception:
        return
    if not existing:
        return
    adds = [
        ("event_date", "VARCHAR(32)"),
        ("data_json", "TEXT"),
        ("extracted_json", "TEXT DEFAULT '{}'"),
        ("corrected_json", "TEXT DEFAULT '{}'"),
        ("image_path", "VARCHAR(512)"),
        ("image_front_path", "VARCHAR(512) DEFAULT ''"),
        ("status", "VARCHAR(32) DEFAULT 'archived'"),
        ("updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP"),
    ]
    for col, typ in adds:
        if col not in existing:
            db.session.execute(db.text(f"ALTER TABLE records ADD COLUMN {col} {_ddl_column_type(typ)}"))
            db.session.commit()


def _migrate_edit_requests():
    """Add any missing columns to edit_requests table (for DBs created before schema updates)."""
    try:
        existing = _existing_columns("edit_requests")
    except Exception:
        return
    if not existing:
        return
    adds = [
        ("record_id", "INTEGER"),
        ("requested_by_id", "INTEGER"),
        ("status", "VARCHAR(32) DEFAULT 'PENDING'"),
        ("reason", "TEXT DEFAULT ''"),
        ("fields_requested", "VARCHAR(512) DEFAULT ''"),
        ("requested_at", "TIMESTAMP"),
        ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ("reviewed_at", "TIMESTAMP"),
        ("reviewed_by_id", "INTEGER"),
        ("approval_code", "VARCHAR(10) DEFAULT ''"),
        ("code_used_at", "TIMESTAMP"),
        ("rejection_reason", "TEXT DEFAULT ''"),
        ("updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
    ]
    for col, typ in adds:
        if col not in existing:
            db.session.execute(db.text(f"ALTER TABLE edit_requests ADD COLUMN {col} {_ddl_column_type(typ)}"))
            db.session.commit()


def _migrate_print_requests():
    """Add any missing columns to print_requests table (for DBs created before schema updates)."""
    try:
        existing = _existing_columns("print_requests")
    except Exception:
        return
    if not existing:
        return
    adds = [
        ("record_id", "INTEGER"),
        ("requested_by_id", "INTEGER"),
        ("status", "VARCHAR(32) DEFAULT 'PENDING'"),
        ("reason", "TEXT DEFAULT ''"),
        ("requested_at", "TIMESTAMP"),
        ("reviewed_at", "TIMESTAMP"),
        ("reviewed_by_id", "INTEGER"),
        ("rejection_reason", "TEXT DEFAULT ''"),
        ("used_at", "TIMESTAMP"),
        ("print_format", "VARCHAR(32) DEFAULT 'original'"),
        ("approval_code", "VARCHAR(10) DEFAULT ''"),
    ]
    for col, typ in adds:
        if col not in existing:
            db.session.execute(db.text(f"ALTER TABLE print_requests ADD COLUMN {col} {_ddl_column_type(typ)}"))
            db.session.commit()
    try:
        pending = (
            PrintRequest.query.filter_by(status="APPROVED")
            .filter(PrintRequest.used_at.is_(None))
            .all()
        )
        changed = False
        for req in pending:
            if not (req.approval_code or "").strip():
                req.approval_code = _new_approval_code()
                changed = True
        if changed:
            db.session.commit()
    except Exception:
        db.session.rollback()


def _migrate_users():
    """Add session_version so admins can force-logout existing accounts."""
    try:
        existing = _existing_columns("users")
    except Exception:
        return
    if not existing:
        return
    if "session_version" not in existing:
        db.session.execute(
            db.text("ALTER TABLE users ADD COLUMN session_version INTEGER DEFAULT 0 NOT NULL")
        )
        db.session.commit()
        existing.add("session_version")
    if "password_vault" not in existing:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN password_vault TEXT"))
        db.session.commit()
        existing.add("password_vault")
    if "last_login_at" not in existing:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN last_login_at TIMESTAMP"))
        db.session.commit()
        existing.add("last_login_at")
    if "photo_path" not in existing:
        db.session.execute(db.text("ALTER TABLE users ADD COLUMN photo_path VARCHAR(256) DEFAULT '' NOT NULL"))
        db.session.commit()
        existing.add("photo_path")
    try:
        db.session.execute(
            db.text(
                "CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_idx "
                "ON users (LOWER(username))"
            )
        )
        db.session.commit()
    except Exception:
        db.session.rollback()


def _ensure_database_ready() -> None:
    """Wait while PostgreSQL is starting, then fail with setup steps if it never comes up."""
    uri = app.config.get("SQLALCHEMY_DATABASE_URI") or ""
    last_exc = None
    for attempt in range(1, 31):
        try:
            with app.app_context():
                db.session.execute(db.text("SELECT 1"))
                db.session.commit()
            if attempt > 1:
                print("  PostgreSQL is ready.", flush=True)
            return
        except Exception as exc:
            last_exc = exc
            text = str(exc).lower()
            starting = "starting up" in text or "the database system is starting" in text
            refused = "connection refused" in text or "could not connect" in text
            if attempt == 1:
                if starting:
                    print("  PostgreSQL is still starting. Waiting…", flush=True)
                elif refused:
                    print("  Waiting for PostgreSQL on 127.0.0.1:5432…", flush=True)
            if starting or refused:
                time.sleep(2)
                continue
            break
    print("=" * 62, flush=True)
    print("  Cannot connect to PostgreSQL.", flush=True)
    print(f"  Tried    {_safe_database_display(uri)}", flush=True)
    print("-" * 62, flush=True)
    print("  1. Install and start PostgreSQL for Windows.", flush=True)
    print("  2. Copy .env.example to .env and set DATABASE_URL.", flush=True)
    print("  3. From daragacivilregistrymanagementsystem/ run:", flush=True)
    print("       python scripts/setup_postgres.py", flush=True)
    print(f"  Error: {last_exc}", flush=True)
    print("=" * 62, flush=True)
    raise SystemExit(1) from last_exc


def _bootstrap_users():
    with app.app_context():
        db.create_all()
        try:
            _migrate_records()
        except Exception:
            pass
        try:
            _migrate_edit_requests()
        except Exception:
            pass
        try:
            _migrate_print_requests()
        except Exception:
            pass
        try:
            _migrate_users()
        except Exception:
            pass
        default_passwords = [
            ("admin", "admin123", "ADMIN"),
            ("staff", "staff123", "STAFF"),
        ]
        for username, password, role in default_passwords:
            user = _find_user_by_username(username)
            if user:
                if not (getattr(user, "password_vault", None) or "").strip():
                    try:
                        if check_password_hash(user.password_hash or "", password):
                            _set_password_vault(user, password)
                    except ValueError:
                        pass
                continue
            db.session.add(
                User(
                    username=username,
                    password_hash=_safe_hash(password),
                    role=role,
                    password_vault=_encrypt_password(password),
                )
            )
        db.session.commit()


def _set_console_title(title: str) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW(title)
    except Exception:
        pass


def _lan_ipv4() -> str:
    """Best-effort Wi-Fi/LAN address other devices can use on the same network."""
    import socket
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass
    return ""


def _print_startup_banner(host: str, port: int) -> None:
    local_url = f"http://127.0.0.1:{port}"
    lan_ip = _lan_ipv4()
    line = "=" * 62
    print(line, flush=True)
    print("  MUNICIPALITY OF DARAGA (LOCSIN), ALBAY", flush=True)
    print("  Office of the Municipal Civil Registrar", flush=True)
    print("  Civil Registry Management System", flush=True)
    print("-" * 62, flush=True)
    print("  Status   Ready", flush=True)
    print("  Database PostgreSQL", flush=True)
    if (os.environ.get("SECRET_KEY") or "").strip() in ("", "change-this-secret-key"):
        print("  Warning  Set SECRET_KEY in .env before office go-live", flush=True)
    print(f"  This PC  {local_url}", flush=True)
    try:
        from services.lgu_office import is_lgu_office_server, lgu_server_computer_name

        if is_lgu_office_server():
            print(f"  Office   {lgu_server_computer_name()} (daily backup on this server)", flush=True)
        else:
            print(f"  Office   Connect on {lgu_server_computer_name()} for production", flush=True)
    except Exception:
        pass
    if lan_ip:
        print(f"  Network  http://{lan_ip}:{port}", flush=True)
        print("  Other devices: same Wi-Fi, open the Network URL", flush=True)
    print("  Stop     Press Ctrl+C", flush=True)
    print(line, flush=True)
    print("", flush=True)


if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.scheduler").setLevel(logging.WARNING)
    _set_console_title("Daraga Civil Registry")
    _host = "0.0.0.0"
    _port = 5001
    _use_reloader = os.environ.get("FLASK_USE_RELOADER", "").strip().lower() in ("1", "true", "yes")
    _flask_debug = os.environ.get("FLASK_DEBUG", "").strip().lower() in ("1", "true", "yes")
    _print_startup_banner(_host, _port)
    _ensure_database_ready()
    _bootstrap_users()
    init_auto_backup_scheduler(
        app,
        base_dir=BASE_DIR,
        upload_dir=UPLOAD_DIR,
        audit_callback=_auto_backup_audit,
        resolve_upload=_upload_file_path,
    )
    try:
        from services.auto_backup import get_backup_readiness, load_settings as load_backup_settings, schedule_summary

        _bs = load_backup_settings(BASE_DIR)
        _br = get_backup_readiness(BASE_DIR, _bs)
        print(f"  Backup   {'ON' if _bs.get('enabled') else 'OFF'} — {schedule_summary(_bs)}", flush=True)
        print(f"  Folder   {_br.get('server_path') or 'not ready'}", flush=True)
        if not _br.get("server_writable"):
            print(f"  Warning  {_br.get('server_message') or 'Backup folder is not writable'}", flush=True)
    except Exception as exc:
        print(f"  Backup   could not report status ({exc})", flush=True)
    atexit.register(stop_ocr_worker)
    if start_ocr_worker():
        _ensure_ocr_listener()
    try:
        import flask.cli as flask_cli
        flask_cli.show_server_banner = lambda *args, **kwargs: None
    except Exception:
        pass
    try:
        if _flask_debug:
            app.run(host=_host, port=_port, debug=_flask_debug, use_reloader=_use_reloader, threaded=True)
        else:
            try:
                from waitress import serve

                serve(
                    app,
                    host=_host,
                    port=_port,
                    threads=8,
                    channel_timeout=3600,
                    ident="DaragaCivilRegistry",
                )
            except ImportError:
                app.run(host=_host, port=_port, debug=False, use_reloader=False, threaded=True)
    except KeyboardInterrupt:
        print("\nDaraga Civil Registry stopped.", flush=True)
        stop_ocr_worker()
        os._exit(0)
