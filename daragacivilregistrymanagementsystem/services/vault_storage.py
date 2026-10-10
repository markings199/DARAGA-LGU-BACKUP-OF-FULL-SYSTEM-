"""Server-side document storage. Live files are never written to a client PC as a fallback."""
from __future__ import annotations

import errno
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "storage_settings.json"
UPLOAD_SUBDIRS = ("temp", "birth", "marriage", "death")
ALLOWED_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png"})
ALLOWED_UPLOAD_TOP = frozenset({"temp", "birth", "marriage", "death"})

DEFAULT_SETTINGS: dict[str, Any] = {
    "data_root": "",
}

# Windows: disk full / network share failures
_WIN_DISK_FULL = {39, 112}
_WIN_NETWORK = {53, 59, 64, 67, 1231, 1219, 1236, 51, 52}


def _clean_path(raw: str) -> str:
    dest = (raw or "").strip().strip('"').strip("'")
    if dest.startswith("//"):
        dest = "\\\\" + dest[2:]
    return dest


def env_data_root() -> str:
    return _clean_path(
        os.environ.get("DARAGA_DATA_ROOT") or os.environ.get("DARAGA_UPLOAD_ROOT") or ""
    )


def settings_path(base_dir: Path) -> Path:
    return base_dir / SETTINGS_FILENAME


def load_storage_settings(base_dir: Path) -> dict[str, Any]:
    path = settings_path(base_dir)
    merged = dict(DEFAULT_SETTINGS)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    merged["data_root"] = _clean_path(str(merged.get("data_root") or ""))[:1024]
    env = env_data_root()
    if env:
        merged["data_root"] = env
    return merged


def save_storage_settings(base_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    out["data_root"] = _clean_path(str(settings.get("data_root") or ""))[:1024]
    path = settings_path(base_dir)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def local_upload_dir(base_dir: Path) -> Path:
    """Uploads folder next to the application (used only when this machine is the server)."""
    return base_dir / "uploads"


def as_fs_path(raw: str) -> Path:
    text = _clean_path(raw)
    if text.startswith("//"):
        text = "\\\\" + text[2:].replace("/", "\\")
    return Path(text)


def configured_data_root(base_dir: Path, settings: Optional[dict[str, Any]] = None) -> str:
    settings = settings if settings is not None else load_storage_settings(base_dir)
    return _clean_path(str(settings.get("data_root") or ""))


def describe_storage_error(exc: BaseException, folder: Optional[Path] = None) -> str:
    """User-facing error when server storage cannot accept a file. Never suggests a client-disk fallback."""
    winerror = getattr(exc, "winerror", None)
    err_no = getattr(exc, "errno", None)
    where = f" ({folder})" if folder is not None else ""
    if winerror in _WIN_DISK_FULL or err_no in (errno.ENOSPC, errno.EDQUOT):
        return (
            "Server storage is full. The file was not saved. "
            "Free space on the office server and try again."
        )
    if winerror in _WIN_NETWORK or err_no in (errno.EHOSTUNREACH, errno.ENETUNREACH, errno.ETIMEDOUT):
        return (
            "The office server storage is unavailable. The file was not saved. "
            "Check that the server is on and the shared folder can be reached."
        )
    if err_no in (errno.EACCES, errno.EPERM) or winerror in (5, 19, 21, 32):
        return (
            f"This application is not allowed to write to server storage{where}. "
            "The file was not saved. Check folder permissions."
        )
    return f"Could not save to server storage{where}. The file was not saved. ({exc})"


def resolve_data_root(
    base_dir: Path, settings: Optional[dict[str, Any]] = None
) -> tuple[Optional[Path], Optional[str]]:
    """Dedicated office-server volume. Empty means use this application's host disk (the server process)."""
    raw = configured_data_root(base_dir, settings)
    if not raw:
        from services.lgu_office import LGU_LOCAL_DATA_DIR, is_lgu_office_server

        if is_lgu_office_server():
            return LGU_LOCAL_DATA_DIR, None
        return None, None
    if raw.startswith("//"):
        raw = "\\\\" + raw[2:].replace("/", "\\")
    path = Path(raw)
    if not (raw.startswith("\\\\") or path.is_absolute()):
        return None, (
            "Enter a full office-server path, for example "
            "\\\\DARAGA-SERVER\\CivilRegistryData or E:\\CivilRegistryData."
        )
    app_root = base_dir.resolve()
    try:
        candidate = path if raw.startswith("\\\\") else path.resolve()
        candidate.relative_to(app_root)
        return None, (
            "That folder is this application's program folder. Enter the office server share "
            "or another data drive so registry files are stored on server capacity, not mixed with program files."
        )
    except (ValueError, OSError):
        pass
    return path, None


def ensure_dir_writable(folder: Path) -> Optional[str]:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".daraga_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        return describe_storage_error(exc, folder)
    return None


def ensure_upload_layout(upload_dir: Path) -> None:
    upload_dir.mkdir(parents=True, exist_ok=True)
    for sub in UPLOAD_SUBDIRS:
        (upload_dir / sub).mkdir(parents=True, exist_ok=True)


def resolve_upload_dir(base_dir: Path, settings: Optional[dict[str, Any]] = None) -> Path:
    """Target uploads directory. If a data root is configured, never silently switch to the app folder."""
    root, err = resolve_data_root(base_dir, settings)
    if err:
        raw = configured_data_root(base_dir, settings)
        return as_fs_path(raw) / "uploads" if raw else local_upload_dir(base_dir)
    if root is None:
        return local_upload_dir(base_dir)
    return root / "uploads"


def prepare_upload_storage(
    base_dir: Path, settings: Optional[dict[str, Any]] = None
) -> tuple[Path, Optional[str]]:
    """
    Return (upload_dir, error). On error, upload_dir is still the intended server path.
    Callers must not write to a client fallback folder.
    """
    dest = resolve_upload_dir(base_dir, settings)
    root, cfg_err = resolve_data_root(base_dir, settings)
    if cfg_err:
        return dest, cfg_err
    check = root if root is not None else dest
    write_err = ensure_dir_writable(check)
    if write_err:
        return dest, write_err
    try:
        ensure_upload_layout(dest)
    except OSError as exc:
        return dest, describe_storage_error(exc, dest)
    return dest, None


def copy_upload_tree(source: Path, dest: Path) -> tuple[int, int]:
    """Copy files from source uploads into dest. Skips identical sizes. Returns (copied, failed)."""
    copied = 0
    failed = 0
    if not source.exists() or not source.is_dir():
        return 0, 0
    try:
        if source.resolve() == dest.resolve():
            return 0, 0
    except OSError:
        if str(source).rstrip("\\/").lower() == str(dest).rstrip("\\/").lower():
            return 0, 0
    for src_file in source.rglob("*"):
        if not src_file.is_file():
            continue
        try:
            rel = src_file.relative_to(source)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.stat().st_size == src_file.stat().st_size:
                continue
            shutil.copy2(src_file, target)
            copied += 1
        except OSError:
            logger.exception("Could not copy %s to server storage", src_file)
            failed += 1
    return copied, failed


def remove_local_uploads(local_dir: Path, dest: Path) -> int:
    """Delete app-folder upload files that already exist on the server copy."""
    removed = 0
    if not local_dir.exists():
        return 0
    try:
        if local_dir.resolve() == dest.resolve():
            return 0
    except OSError:
        return 0
    for src_file in list(local_dir.rglob("*")):
        if not src_file.is_file():
            continue
        try:
            rel = src_file.relative_to(local_dir)
            target = dest / rel
            if target.is_file():
                src_file.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def apply_vault(
    base_dir: Path,
    data_root: str,
    *,
    remove_local: bool = False,
) -> tuple[bool, str, Path, int, int, int]:
    """
    Save the server data path and place uploads there.
    Returns (ok, message, upload_dir, copied, failed, removed).
    """
    wanted = dict(DEFAULT_SETTINGS)
    wanted["data_root"] = _clean_path(data_root)
    root, err = resolve_data_root(base_dir, wanted)
    if err:
        return False, err, resolve_upload_dir(base_dir), 0, 0, 0
    previous = resolve_upload_dir(base_dir)
    if root is None:
        save_storage_settings(base_dir, wanted)
        dest = local_upload_dir(base_dir)
        write_err = ensure_dir_writable(dest)
        if write_err:
            return False, write_err, previous, 0, 0, 0
        try:
            ensure_upload_layout(dest)
        except OSError as exc:
            return False, describe_storage_error(exc, dest), dest, 0, 0, 0
        return True, f"Live files use this application server: {dest}", dest, 0, 0, 0

    write_err = ensure_dir_writable(root)
    if write_err:
        return False, write_err, previous, 0, 0, 0
    dest = root / "uploads"
    try:
        ensure_upload_layout(dest)
    except OSError as exc:
        return False, describe_storage_error(exc, dest), previous, 0, 0, 0

    copied = failed = removed = 0
    local = local_upload_dir(base_dir)
    if local.exists():
        copied, failed = copy_upload_tree(local, dest)
    save_storage_settings(base_dir, wanted)
    if remove_local and failed == 0:
        removed = remove_local_uploads(local, dest)
    if failed:
        msg = (
            f"Office server storage is set, but {failed} existing file(s) could not be copied. "
            f"Copied {copied}. New files will only be saved on the server. "
            "Existing records were not changed."
        )
        return False, msg, dest, copied, failed, removed
    parts = [f"Live files now use the office server: {dest}"]
    if copied:
        parts.append(f"Copied {copied} existing file(s) onto the server (no duplicates).")
    if removed:
        parts.append(f"Removed {removed} copy(ies) from the application folder.")
    return True, " ".join(parts), dest, copied, failed, removed


def vault_status(base_dir: Path, upload_dir: Path) -> dict[str, Any]:
    settings = load_storage_settings(base_dir)
    root, err = resolve_data_root(base_dir, settings)
    using_dedicated = root is not None and not err
    writable = False
    message = f"Live files use this application server ({upload_dir})."
    if err:
        message = err
    elif using_dedicated:
        root_text = str(root or "").replace("/", "\\")
        if root_text.startswith("\\\\"):
            writable = False
            message = (
                "Office data share is checked when files are saved. "
                "The Backup page does not wait for that share to load."
            )
        else:
            write_err = ensure_dir_writable(root) if root is not None else "Missing path"
            if write_err:
                message = write_err
            else:
                writable = True
                message = f"Live files use the office server ({upload_dir})."
    else:
        write_err = ensure_dir_writable(upload_dir)
        if write_err:
            message = write_err
        else:
            writable = True
    return {
        "data_root": settings.get("data_root") or "",
        "using_server": using_dedicated and writable,
        "using_app_host": (not using_dedicated) and writable and not err,
        "configured": bool(settings.get("data_root")),
        "writable": writable,
        "message": message,
        "upload_dir": str(upload_dir),
        "local_upload_dir": str(local_upload_dir(base_dir)),
    }
