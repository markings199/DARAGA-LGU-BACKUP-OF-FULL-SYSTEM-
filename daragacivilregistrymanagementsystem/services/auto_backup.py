"""Automatic full-system backup scheduler (daily / weekly / monthly / yearly)."""
from __future__ import annotations

import json
import logging
import os
import csv
import shutil
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any, Callable, Optional

from services.lgu_office import (
    LGU_LOCAL_BACKUP_DIR,
    default_backup_path_for_this_computer,
    is_lgu_office_server,
    lgu_backup_unc,
    this_computer_name,
)

logger = logging.getLogger(__name__)

SETTINGS_FILENAME = "auto_backup_settings.json"
AUTO_BACKUP_SUBDIR = "auto"
JOB_ID = "auto_full_backup"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "daily_enabled": True,
    "monthly_enabled": True,
    "yearly_enabled": True,
    "full_enabled": True,
    "daily_time": "19:00",
    "monthly_time": "19:00",
    "yearly_time": "19:00",
    "full_time": "19:05",
    "frequency": "daily",
    "time": "19:00",
    "weekday": 0,
    "day_of_month": 28,
    "month": 12,
    "retain_count": 10,
    "destination_path": "",
    "keep_local_copy": False,
    "last_run_at": "",
    "last_run_status": "",
    "last_run_message": "",
    "last_run_file": "",
}

VALID_FREQUENCIES = frozenset({"daily", "weekly", "monthly", "yearly"})
PERIOD_KINDS = frozenset({"daily", "weekly", "monthly", "yearly"})
MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
JOB_DAILY = "auto_period_backup_daily"
JOB_MONTHLY = "auto_period_backup_monthly"
JOB_YEARLY = "auto_period_backup_yearly"
# Allow late start after sleep / app restart so the day's backup still runs.
MISFIRE_GRACE_SECONDS = 6 * 60 * 60


def daily_backup_stamp(when: Optional[datetime] = None) -> str:
    """Calendar day used in backup filenames, e.g. 2026-09-01."""
    return (when or datetime.now()).strftime("%Y-%m-%d")


def daily_full_backup_filename(when: Optional[datetime] = None) -> str:
    return f"civil_registry_full_backup_{daily_backup_stamp(when)}.zip"


WEEKDAY_LABELS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)


def schedule_period_key(frequency: str, when: Optional[datetime] = None) -> str:
    now = when or datetime.now()
    freq = (frequency or "daily").strip().lower()
    if freq == "weekly":
        monday = now - timedelta(days=now.weekday())
        return monday.strftime("%G-W%V")
    if freq == "monthly":
        return now.strftime("%Y-%m")
    return daily_backup_stamp(now)


def full_backup_filename(frequency: str, when: Optional[datetime] = None) -> str:
    key = schedule_period_key(frequency, when).replace(":", "-")
    return f"civil_registry_full_backup_{key}.zip"


def schedule_summary(settings: dict[str, Any]) -> str:
    if not settings.get("enabled"):
        return "Automatic backup is off."
    freq = str(settings.get("frequency") or "daily")
    t = str(settings.get("time") or settings.get("full_time") or "20:00")
    if freq == "weekly":
        try:
            day = WEEKDAY_LABELS[int(settings.get("weekday", 0))]
        except (TypeError, ValueError, IndexError):
            day = "Monday"
        return f"Every {day} at {t}"
    if freq == "monthly":
        day = int(settings.get("day_of_month") or 1)
        return f"Day {day} of each month at {t}"
    return f"Every day at {t}"

_scheduler = None
_scheduler_lock = threading.Lock()
_runtime: dict[str, Any] = {}


def settings_path(base_dir: Path) -> Path:
    return base_dir / "backups" / SETTINGS_FILENAME


_PLACEHOLDER_DESTINATIONS = {
    r"\\servername\civilregistrybackups",
    r"//servername/civilregistrybackups",
}


def clean_destination_path(raw: str) -> str:
    """Normalize a server/share path. Example placeholder text is treated as empty."""
    dest = (raw or "").strip().strip('"').strip("'")
    if dest.startswith("//"):
        dest = "\\\\" + dest[2:]
    key = dest.replace("/", "\\").rstrip("\\").lower()
    if not dest or key in _PLACEHOLDER_DESTINATIONS:
        return ""
    return dest


def this_server_name() -> str:
    """Windows computer name of the machine running python app.py (the backup server)."""
    return this_computer_name()


def env_backup_root() -> str:
    return clean_destination_path(
        os.environ.get("DARAGA_BACKUP_ROOT") or os.environ.get("BACKUP_ROOT") or ""
    )


def local_backup_root(base_dir: Path) -> Path:
    """Backup folder on the computer that runs the application (the server), never on a browser PC."""
    path = base_dir / "backups"
    return path


def auto_backup_dir(base_dir: Path) -> Path:
    """Folder for full-system zip files on the configured backup volume."""
    root, err = resolve_backup_root(base_dir)
    if err:
        raise OSError(err)
    path = (root if root is not None else local_backup_root(base_dir)) / "Full"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_workstation_backup_path(raw: str, base_dir: Path) -> bool:
    """True when the path is this laptop/project folder or the Windows Downloads folder."""
    text = _norm_dest_text(raw)
    if not text:
        return True
    if text.startswith("\\\\"):
        return "\\downloads" in f"{text}\\"
    if "daragacivilregistrymanagementsystem" in text:
        return True
    if text.endswith("\\downloads") or "\\downloads\\" in f"{text}\\":
        return True
    try:
        resolved = Path(raw).resolve()
        home_downloads = (Path.home() / "Downloads").resolve()
        if resolved == home_downloads or home_downloads in resolved.parents:
            return True
        project = base_dir.resolve()
        if resolved == project or project in resolved.parents or resolved == (project / "backups"):
            return True
    except OSError:
        return True
    return False


def office_backup_destination(base_dir: Path, raw: str = "") -> str:
    """Always the Daraga LGU backup folder, never a staff laptop or this project's backups folder."""
    env = env_backup_root()
    if env:
        return env
    cleaned = clean_destination_path(raw)
    if cleaned and not _is_workstation_backup_path(cleaned, base_dir):
        return cleaned
    return default_backup_path_for_this_computer()


def resolve_server_backup_root(
    base_dir: Path, settings: Optional[dict[str, Any]] = None
) -> tuple[Optional[Path], Optional[str]]:
    """Daraga office folder: C:\\CivilRegistryBackups on the LGU server, or the office share from another PC."""
    settings = settings if settings is not None else load_settings(base_dir)
    raw = office_backup_destination(base_dir, str(settings.get("destination_path") or ""))
    if raw.startswith("//"):
        raw = "\\\\" + raw[2:].replace("/", "\\")
    path = Path(raw)
    if not (raw.startswith("\\\\") or path.is_absolute()):
        return None, (
            "Enter the Daraga office backup folder, for example "
            "C:\\CivilRegistryBackups or \\\\WIN-HIL0JNDFJIIN\\CivilRegistryBackups."
        )
    return path, None


def _norm_dest_text(value: str) -> str:
    return (value or "").strip().strip('"').strip("'").replace("/", "\\").rstrip("\\").lower()


def _is_backup_tree_folder(name: str) -> bool:
    n = (name or "").lower()
    if n in {"backups", "full", "monthly", "yearly"}:
        return True
    if n in {m.lower() for m in MONTH_NAMES}:
        return True
    return n.isdigit() and len(n) == 4


def relative_path_on_server(server_root: Path, relative: str) -> Path:
    """Map a local backup relative path onto the server without nesting duplicate folders."""
    parts = [p for p in Path(str(relative).replace("\\", "/")).parts if p not in ("", ".")]
    while len(parts) >= 2 and parts[0].lower() == "backups" and parts[1].lower() == "backups":
        parts = parts[1:]
    if parts and parts[0].lower() == "backups" and server_root.name.lower() == "backups":
        parts = parts[1:]
    if parts and _is_backup_tree_folder(parts[0]) and parts[0].lower() == server_root.name.lower():
        parts = parts[1:]
    return Path(*parts) if parts else Path(Path(str(relative).replace("\\", "/")).name)


def require_system_backup_root(base_dir: Path) -> tuple[Optional[Path], Optional[str]]:
    """Hold folder only when the Daraga LGU server is offline. Not the official archive."""
    path = pending_lgu_root(base_dir)
    write_err = ensure_backup_dir_writable(path)
    if write_err:
        return None, write_err
    return path, None


def daraga_setup_message() -> str:
    return (
        "Connect the Daraga LGU backup folder. Official backups are stored on the Daraga LGU "
        f"server ({default_backup_path_for_this_computer()}), not in an administrator Windows account."
    )


def pending_lgu_root(base_dir: Path) -> Path:
    """Temporary hold copies waiting to be sent to the Daraga LGU server."""
    path = local_backup_root(base_dir) / "_pending_lgu"
    path.mkdir(parents=True, exist_ok=True)
    return path


def probe_daraga_official(
    base_dir: Path,
    settings: Optional[dict[str, Any]] = None,
    *,
    wait: float = 4.0,
) -> tuple[Optional[Path], Optional[str]]:
    """Official archive: the configured Daraga LGU folder, never this project or a user account."""
    path, err = resolve_server_backup_root(base_dir, settings)
    if err or path is None:
        return None, err or daraga_setup_message()
    if _is_workstation_backup_path(str(path), base_dir):
        return None, daraga_setup_message()
    lgu, lgu_err = probe_lgu_backup_root(base_dir, settings, wait=wait)
    if lgu is not None and not lgu_err:
        return lgu, None
    return None, lgu_err or daraga_setup_message()


def _is_manual_backup_trigger(trigger: str) -> bool:
    return (trigger or "").strip().lower() in ("manual", "get_now")


def _finish_official_backup(
    staging: Path,
    rel: str,
    base_dir: Path,
    settings: Optional[dict[str, Any]] = None,
) -> tuple[str, str]:
    """Copy the zip onto the Daraga LGU server, then drop the local hold file."""
    status, extra = publish_backup_to_daraga(
        staging, rel, base_dir, settings=settings, retries=3
    )
    if status == "ok":
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
    return status, extra


_lgu_probe_lock = threading.Lock()
_lgu_probe_cache: dict[str, Any] = {
    "ok": False,
    "path": None,
    "err": "Daraga LGU folder is not online yet.",
    "at": 0.0,
    "busy": False,
}
_LGU_PROBE_TTL = 90.0


def _looks_like_unc(path: Path) -> bool:
    text = str(path or "").replace("/", "\\")
    return text.startswith("\\\\")


def probe_lgu_backup_root(
    base_dir: Path,
    settings: Optional[dict[str, Any]] = None,
    *,
    wait: float = 0.35,
) -> tuple[Optional[Path], Optional[str]]:
    """Daraga LGU folder when it can be written. Page loads must not wait on a dead share."""
    path, err = resolve_server_backup_root(base_dir, settings)
    if err or path is None:
        return None, err
    if _is_workstation_backup_path(str(path), base_dir):
        return None, None
    if not _looks_like_unc(path):
        write_err = ensure_backup_dir_writable(path)
        if write_err:
            return path, write_err
        return path, None

    now = time.monotonic()
    with _lgu_probe_lock:
        age = now - float(_lgu_probe_cache.get("at") or 0)
        if _lgu_probe_cache["at"] and age < _LGU_PROBE_TTL and not _lgu_probe_cache["busy"]:
            if _lgu_probe_cache["ok"]:
                return _lgu_probe_cache["path"], None
            return _lgu_probe_cache["path"], _lgu_probe_cache["err"]
        already_busy = bool(_lgu_probe_cache["busy"])
        if not already_busy:
            _lgu_probe_cache["busy"] = True

    if not already_busy:
        def _work() -> None:
            try:
                write_err = ensure_backup_dir_writable(path)
                with _lgu_probe_lock:
                    _lgu_probe_cache["ok"] = write_err is None
                    _lgu_probe_cache["path"] = path
                    _lgu_probe_cache["err"] = write_err or "Daraga LGU folder is not online yet."
                    _lgu_probe_cache["at"] = time.monotonic()
                    _lgu_probe_cache["busy"] = False
            except Exception as exc:
                with _lgu_probe_lock:
                    _lgu_probe_cache["ok"] = False
                    _lgu_probe_cache["path"] = path
                    _lgu_probe_cache["err"] = str(exc)
                    _lgu_probe_cache["at"] = time.monotonic()
                    _lgu_probe_cache["busy"] = False

        threading.Thread(target=_work, name="daraga-lgu-probe", daemon=True).start()

    if wait and wait > 0:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            with _lgu_probe_lock:
                if not _lgu_probe_cache["busy"] and _lgu_probe_cache["at"]:
                    if _lgu_probe_cache["ok"]:
                        return _lgu_probe_cache["path"], None
                    return _lgu_probe_cache["path"], _lgu_probe_cache["err"]
            time.sleep(0.05)

    return None, "Daraga LGU folder is not online yet."


def require_lgu_backup_root(
    base_dir: Path, settings: Optional[dict[str, Any]] = None
) -> tuple[Optional[Path], Optional[str]]:
    """Official Daraga LGU folder when it can be written."""
    return probe_daraga_official(base_dir, settings, wait=3.0)


def resolve_backup_root(
    base_dir: Path, settings: Optional[dict[str, Any]] = None
) -> tuple[Optional[Path], Optional[str]]:
    """Office-server backup volume. Does not fall back to a client disk when the share fails."""
    settings = settings if settings is not None else load_settings(base_dir)
    path, err = resolve_server_backup_root(base_dir, settings)
    if err:
        return None, err
    if path is not None:
        return path, None
    from services.vault_storage import resolve_data_root

    data_root, data_err = resolve_data_root(base_dir)
    if data_err:
        return None, data_err
    if data_root is not None:
        return data_root / "backups", None
    return local_backup_root(base_dir), None


def backup_storage_dir(base_dir: Path, settings: Optional[dict[str, Any]] = None) -> Path:
    """Primary automatic archive root on server storage. Never switches to a workstation folder when a server path is configured."""
    path, err = resolve_backup_root(base_dir, settings)
    if path is not None:
        return path
    return Path(default_backup_path_for_this_computer())


def backup_storage_display(base_dir: Path, settings: Optional[dict[str, Any]] = None) -> str:
    path, err = resolve_backup_root(base_dir, settings)
    if err:
        return err
    if path is None:
        return str(local_backup_root(base_dir))
    return str(path)


def server_backup_display(base_dir: Path, settings: Optional[dict[str, Any]] = None) -> str:
    path, err = resolve_server_backup_root(base_dir, settings)
    if err:
        return err
    if path is None:
        return "Not set"
    return str(path)


def ensure_backup_dir_writable(folder: Path) -> Optional[str]:
    """Create the folder and confirm this computer can write to it. Returns an error string or None."""
    try:
        os.makedirs(str(folder), exist_ok=True)
        probe = folder / ".civil_registry_backup_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
    except OSError as exc:
        from services.vault_storage import describe_storage_error

        return describe_storage_error(exc, folder)
    return None


def _safe_is_file(path: Path) -> bool:
    """True if this is a regular file. Offline network shares do not crash the page."""
    try:
        return path.is_file()
    except OSError:
        return False


def _safe_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _backup_search_roots(base_dir: Path) -> list[Path]:
    """Daraga LGU server first, then any hold copies waiting to be sent."""
    roots: list[Path] = []
    lgu, lgu_err = probe_lgu_backup_root(base_dir, wait=0.2)
    if lgu is not None and not lgu_err:
        roots.append(lgu)
    pending = pending_lgu_root(base_dir)
    if all(str(pending) != str(r) for r in roots):
        roots.append(pending)
    local = local_backup_root(base_dir)
    if all(str(local) != str(r) for r in roots):
        roots.append(local)
    return roots


def host_downloads_dir() -> Path:
    return Path.home() / "Downloads"


def unique_backup_path(path: Path) -> Path:
    """Keep the original file and write the next run as a new name."""
    if not _safe_exists(path):
        return path
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = path.with_name(f"{path.stem}_{stamp}{path.suffix}")
    n = 1
    while _safe_exists(candidate):
        candidate = path.with_name(f"{path.stem}_{stamp}_{n}{path.suffix}")
        n += 1
    return candidate


def copy_backup_to_host_downloads(zip_path: Path) -> tuple[Optional[Path], Optional[str]]:
    """Backups stay on the Daraga office server. Do not copy zips into a laptop Downloads folder."""
    if zip_path is None or not _safe_is_file(zip_path):
        return None, "Backup zip was not found on the Daraga office server."
    return zip_path, None


def _with_downloads_copy(msg: str, zip_path: Path) -> str:
    return msg


def describe_backup_copies(local_path: Path, server_status: str, server_detail: str) -> str:
    if server_status == "ok":
        if server_detail:
            return f"Saved on the Daraga LGU server ({server_detail})."
        return "Saved on the Daraga LGU server."
    if server_detail:
        return f"Not on the Daraga LGU server yet: {server_detail}"
    return "Not on the Daraga LGU server yet. Connect the office backup folder."


def drop_local_zip_if_server_only(source: Path, server_status: str, base_dir: Path) -> None:
    """No-op: backups stay on server storage. Workstation disks are not used as a safety copy."""
    return


_SHARE_OFFLINE_MARKERS = (
    "network path was not found",
    "network name cannot be found",
    "the network location cannot be reached",
    "office server storage is unavailable",
    "winerror 53",
    "error 53",
    "error 64",
    "error 67",
    "error 51",
    "error 1231",
)


def _winerror_of(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "winerror", None)
    try:
        return int(code) if code is not None else None
    except (TypeError, ValueError):
        return None


def _is_share_offline_error(exc: BaseException) -> bool:
    from services.vault_storage import _WIN_NETWORK

    code = _winerror_of(exc)
    if code in _WIN_NETWORK:
        return True
    text = str(exc or "").lower()
    return any(marker in text for marker in _SHARE_OFFLINE_MARKERS)


def _is_share_offline_message(text: str) -> bool:
    t = (text or "").lower()
    return any(marker in t for marker in _SHARE_OFFLINE_MARKERS)


def copy_zip_to_server(
    source: Path,
    relative: str,
    base_dir: Path,
    *,
    force: bool = False,
    settings: Optional[dict[str, Any]] = None,
    retries: int = 2,
) -> tuple[str, str]:
    """
    Publish a backup zip to the Daraga office server (priority archive).

    Returns (ok|skipped|failed, detail). Retries briefly when the share is flaky.
    Does not retry when the office PC is simply not on this network.
    """
    server_root, server_err = resolve_server_backup_root(base_dir, settings)
    if server_err:
        return "skipped", server_err
    if server_root is None or _is_workstation_backup_path(str(server_root), base_dir):
        return "skipped", "Daraga LGU folder is not configured yet."
    probe = ensure_backup_dir_writable(server_root)
    if probe and _is_share_offline_message(probe):
        return "skipped", probe

    last_error = probe or ""
    attempts = max(1, int(retries or 1))
    for attempt in range(1, attempts + 1):
        try:
            if not _safe_is_file(source):
                return "failed", f"Office server copy FAILED: source zip was missing ({source})."
            server_dest = server_root / relative_path_on_server(server_root, relative)
            dest_text = str(server_dest)
            if os.path.isdir(dest_text):
                return "failed", f"Office server copy FAILED: a folder already exists at {dest_text}."
            os.makedirs(str(server_dest.parent), exist_ok=True)
            try:
                if source.resolve() == Path(dest_text).resolve():
                    return "ok", dest_text
            except OSError as exc:
                if _is_share_offline_error(exc):
                    from services.vault_storage import describe_storage_error

                    return "skipped", describe_storage_error(exc, server_root)
                if os.path.normcase(os.path.normpath(str(source))) == os.path.normcase(
                    os.path.normpath(dest_text)
                ):
                    return "ok", dest_text
            shutil.copy2(str(source), dest_text)
            if not os.path.isfile(dest_text):
                last_error = f"file was not found after copy at {dest_text}"
            else:
                try:
                    src_size = os.path.getsize(str(source))
                    dest_size = os.path.getsize(dest_text)
                    if dest_size <= 0:
                        last_error = f"empty file after copy at {dest_text}"
                    elif src_size and dest_size != src_size:
                        last_error = f"size mismatch after copy at {dest_text}"
                    else:
                        return "ok", dest_text
                except OSError as exc:
                    if _is_share_offline_error(exc):
                        from services.vault_storage import describe_storage_error

                        return "skipped", describe_storage_error(exc, server_root)
                    last_error = str(exc)
        except OSError as exc:
            last_error = str(exc)
            if _is_share_offline_error(exc):
                from services.vault_storage import describe_storage_error

                return "skipped", describe_storage_error(exc, server_root)
            logger.warning(
                "Office server backup copy attempt %s/%s failed: %s",
                attempt,
                attempts,
                exc,
            )
        if attempt < attempts:
            try:
                time.sleep(1.2 * attempt)
            except Exception:
                pass
    return "failed", f"Office server copy FAILED after {attempts} tries: {last_error}"


def publish_backup_to_daraga(
    source: Path,
    relative: str,
    base_dir: Path,
    *,
    settings: Optional[dict[str, Any]] = None,
    retries: int = 2,
) -> tuple[str, str]:
    """Copy a backup zip onto the Daraga LGU folder whenever that disk or share is online."""
    settings = settings if settings is not None else load_settings(base_dir)
    status, detail = copy_zip_to_server(
        source,
        relative,
        base_dir,
        force=True,
        settings=settings,
        retries=retries,
    )
    if status == "ok":
        return status, detail
    if status == "skipped" or _is_share_offline_message(detail or ""):
        return "skipped", detail or "Daraga LGU folder is not online yet."
    lgu, lgu_err = probe_lgu_backup_root(base_dir, settings, wait=8.0)
    if lgu is None:
        return status, detail or lgu_err or "Daraga LGU folder is not online yet."
    if lgu_err:
        return status, lgu_err
    try:
        server_dest = lgu / relative_path_on_server(lgu, relative)
        os.makedirs(str(server_dest.parent), exist_ok=True)
        shutil.copy2(str(source), str(server_dest))
        if os.path.isfile(str(server_dest)) and os.path.getsize(str(server_dest)) > 0:
            return "ok", str(server_dest)
        return "failed", f"Daraga LGU copy did not finish at {server_dest}"
    except OSError as exc:
        logger.warning("Direct Daraga LGU copy failed: %s", exc)
        return "failed", str(exc)


def get_backup_readiness(base_dir: Path, settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Status for Backup: official archive is the Daraga LGU server folder."""
    settings = settings if settings is not None else load_settings(base_dir)
    lgu_path, lgu_err = probe_daraga_official(base_dir, settings, wait=0.35)
    host_name = this_server_name()
    server_writable = lgu_path is not None and not lgu_err
    intended, _intended_err = resolve_server_backup_root(base_dir, settings)
    display_path = str(
        (lgu_path if server_writable else None) or intended or default_backup_path_for_this_computer()
    )
    if server_writable:
        server_message = "Daraga LGU server connected. Official backups save on that server, not in an admin account."
    else:
        server_message = lgu_err or daraga_setup_message()
    return {
        "schedule_on": bool(settings.get("enabled")),
        "daily_on": bool(settings.get("daily_enabled", True)),
        "full_on": bool(settings.get("full_enabled", True)),
        "daily_time": str(settings.get("daily_time") or "20:00"),
        "full_time": str(settings.get("full_time") or "20:05"),
        "host_name": host_name,
        "server_configured": bool(intended),
        "server_writable": server_writable,
        "system_ready": server_writable,
        "server_path": display_path,
        "server_message": server_message,
        "local_path": display_path,
        "deploy_ready": bool(settings.get("enabled")) and server_writable,
    }


def sync_local_backups_to_server(base_dir: Path) -> tuple[int, int, Optional[str]]:
    """Copy existing local backup zips to the LGU server. Returns (copied, failed, error)."""
    lgu, lgu_err = probe_lgu_backup_root(base_dir, wait=2.0)
    if lgu is None or lgu_err:
        return 0, 0, lgu_err or "Daraga LGU folder is not online yet."
    local = local_backup_root(base_dir)
    copied = 0
    failed = 0
    for path in local.rglob("*.zip"):
        if not path.is_file():
            continue
        try:
            rel = str(path.relative_to(local)).replace("\\", "/")
        except ValueError:
            continue
        if rel.lower().startswith("_pending_lgu/"):
            rel = rel.split("/", 1)[-1]
        if not rel or rel.endswith("/"):
            continue
        status, extra = copy_zip_to_server(path, rel, base_dir, force=True)
        if status == "ok":
            copied += 1
        elif status == "failed":
            failed += 1
    return copied, failed, None


def load_settings(base_dir: Path) -> dict[str, Any]:
    path = settings_path(base_dir)
    if not path.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        merged.update(data)
    return _normalize_settings(merged)


def save_settings(base_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_settings(settings)
    path = settings_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False), encoding="utf-8")
    return normalized


def _normalize_settings(settings: dict[str, Any]) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    out.update(settings or {})
    out["enabled"] = bool(out.get("enabled"))
    freq = str(out.get("frequency") or "daily").strip().lower()
    if freq == "yearly":
        freq = "monthly"
    out["frequency"] = freq if freq in ("daily", "weekly", "monthly") else "daily"
    out["time"] = _parse_time(str(out.get("time") or out.get("full_time") or out.get("daily_time") or "20:00"))
    try:
        out["weekday"] = max(0, min(6, int(out.get("weekday", 6))))
    except (TypeError, ValueError):
        out["weekday"] = 6
    try:
        out["day_of_month"] = max(1, min(28, int(out.get("day_of_month", 1))))
    except (TypeError, ValueError):
        out["day_of_month"] = 1
    try:
        out["month"] = max(1, min(12, int(out.get("month", 1))))
    except (TypeError, ValueError):
        out["month"] = 1
    try:
        out["retain_count"] = max(1, min(100, int(out.get("retain_count", 10))))
    except (TypeError, ValueError):
        out["retain_count"] = 10
    dest = clean_destination_path(str(out.get("destination_path") or ""))
    if "\x00" in dest:
        dest = ""
    out["destination_path"] = dest[:1024]
    out["keep_local_copy"] = False
    out["daily_enabled"] = bool(out.get("daily_enabled", True))
    out["monthly_enabled"] = bool(out.get("monthly_enabled", True))
    out["yearly_enabled"] = bool(out.get("yearly_enabled", True))
    out["full_enabled"] = bool(out.get("full_enabled", True))
    out["daily_time"] = _parse_time(str(out.get("daily_time") or out.get("time") or "20:00"))
    out["monthly_time"] = _parse_time(str(out.get("monthly_time") or out.get("time") or "20:00"))
    out["yearly_time"] = _parse_time(str(out.get("yearly_time") or out.get("time") or "20:00"))
    out["full_time"] = _parse_time(str(out.get("full_time") or out.get("time") or "20:00"))
    for key in ("last_run_at", "last_run_status", "last_run_message", "last_run_file"):
        out[key] = str(out.get(key) or "").strip()
    return out


def _parse_time(value: str) -> str:
    raw = (value or "02:00").strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.strftime("%H:%M")
        except ValueError:
            continue
    return "02:00"


def _time_parts_value(value: str) -> tuple[int, int]:
    parsed = datetime.strptime(_parse_time(value), "%H:%M")
    return parsed.hour, parsed.minute


def _time_parts(settings: dict[str, Any]) -> tuple[int, int]:
    return _time_parts_value(str(settings.get("time") or "20:00"))


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _naive_local_to_utc_naive(dt: datetime) -> datetime:
    aware = dt.replace(tzinfo=_local_tz())
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def period_bounds(kind: str, when: Optional[datetime] = None) -> tuple[datetime, datetime, str]:
    """Return UTC-naive [start, end) and a period key for local office dates."""
    now = when or datetime.now()
    kind = (kind or "daily").strip().lower()
    start_local = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "monthly":
        start_local = start_local.replace(day=1)
        if start_local.month == 12:
            end_local = start_local.replace(year=start_local.year + 1, month=1)
        else:
            end_local = start_local.replace(month=start_local.month + 1)
        period_key = start_local.strftime("%Y-%m")
    elif kind == "yearly":
        start_local = start_local.replace(month=1, day=1)
        end_local = start_local.replace(year=start_local.year + 1)
        period_key = start_local.strftime("%Y")
    elif kind == "weekly":
        start_local = start_local - timedelta(days=start_local.weekday())
        end_local = start_local + timedelta(days=7)
        period_key = start_local.strftime("%G-W%V")
    else:
        end_local = start_local + timedelta(days=1)
        period_key = start_local.strftime("%Y-%m-%d")
    return _naive_local_to_utc_naive(start_local), _naive_local_to_utc_naive(end_local), period_key


def period_folder(root: Path, kind: str, when: datetime) -> Path:
    year = str(when.year)
    month_name = MONTH_NAMES[when.month - 1]
    if kind == "monthly":
        return root / year / "Monthly" / month_name
    if kind == "yearly":
        return root / year / "Yearly"
    if kind == "weekly":
        monday = when - timedelta(days=when.weekday())
        return root / year / "Weekly" / monday.strftime("%G-W%V")
    return root / year / month_name / f"{month_name}-{when.strftime('%d')}"


def period_filename(kind: str, period_key: str) -> str:
    return f"civil_registry_{kind}_{period_key}.zip"


def _record_to_manifest(record) -> dict[str, Any]:
    created = getattr(record, "created_at", None)
    return {
        "id": record.id,
        "document_type": record.document_type or "",
        "registry_number": record.registry_number or "",
        "full_name": record.full_name or "",
        "event_date": record.event_date or "",
        "created_at": created.strftime("%Y-%m-%d %H:%M") if created else "",
        "image_path": record.image_path or "",
    }


def write_period_documents_zip(dest_path: Path, records: list[Any], resolve_upload: Callable[[str], Optional[Path]]) -> int:
    """Copy that period's records and their scans into a zip. Does not change originals."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dump_tmp = dest_path.parent / f"{dest_path.stem}_database.json"
    copied = 0
    try:
        from services.postgres_backup import dump_period_records_json

        dump_period_records_json(dump_tmp, records)
        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(dump_tmp, "database.json")
            manifest_rows = []
            seen = set()
            for record in records:
                manifest_rows.append(_record_to_manifest(record))
                for attr in ("image_path", "image_front_path"):
                    rel = (getattr(record, attr, None) or "").strip().replace("\\", "/")
                    if not rel:
                        continue
                    full = resolve_upload(rel)
                    if full is None or not full.is_file():
                        continue
                    arcname = f"uploads/{rel.lstrip('/')}"
                    if arcname in seen:
                        continue
                    seen.add(arcname)
                    zf.write(full, arcname)
                    copied += 1
            buf = StringIO()
            writer = csv.DictWriter(
                buf,
                fieldnames=["id", "document_type", "registry_number", "full_name", "event_date", "created_at", "image_path"],
            )
            writer.writeheader()
            writer.writerows(manifest_rows)
            zf.writestr("manifest.csv", buf.getvalue())
            zf.writestr(
                "README.txt",
                "Period backup: only certificates scanned in this day, week, or month. "
                "Other days in the live system were not changed.\n",
            )
    finally:
        dump_tmp.unlink(missing_ok=True)
    return copied


def _save_backup_run(*, kind: str, period_key: str, status: str, file_rel: str, record_count: int, message: str) -> None:
    from models import BackupRun, db

    row = BackupRun(
        kind=kind,
        period_key=period_key,
        status=status,
        file_rel=file_rel or "",
        record_count=int(record_count or 0),
        message=message or "",
        created_at=datetime.utcnow(),
    )
    db.session.add(row)
    db.session.commit()


def list_backup_history(limit: int = 40) -> list[dict[str, Any]]:
    from models import BackupRun

    rows = BackupRun.query.order_by(BackupRun.created_at.desc()).limit(limit).all()
    items = []
    for row in rows:
        items.append(
            {
                "id": row.id,
                "kind": row.kind,
                "period_key": row.period_key,
                "status": row.status,
                "file_rel": row.file_rel or "",
                "record_count": row.record_count or 0,
                "message": row.message or "",
                "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            }
        )
    return items


def delete_backup_run(base_dir: Path, run_id: int) -> tuple[bool, str]:
    """Delete a history row and its zip on this system and the LGU server. Original records are not changed."""
    from models import BackupRun, db

    row = db.session.get(BackupRun, run_id)
    if row is None:
        return False, "Backup history entry not found."
    rel = (row.file_rel or "").replace("\\", "/").strip().lstrip("/")
    kind = (row.kind or "backup").strip()
    period = row.period_key or ""
    file_errors: list[str] = []
    if rel and ".." not in rel.split("/") and rel.lower().endswith(".zip"):
        for root in _backup_search_roots(base_dir):
            full = root / Path(rel)
            try:
                full.relative_to(root)
            except ValueError:
                continue
            if not _safe_is_file(full):
                continue
            try:
                full.unlink()
            except OSError as exc:
                logger.warning("Could not delete backup file %s: %s", full, exc)
                file_errors.append(str(full))
    db.session.delete(row)
    db.session.commit()
    settings = load_settings(base_dir)
    if rel and settings.get("last_run_file") == rel:
        settings["last_run_file"] = ""
        save_settings(base_dir, settings)
    label = f"{kind} {period}".strip()
    if file_errors:
        return True, f'History "{label}" was removed, but some zip files could not be deleted.'
    return True, f'Backup "{label}" was deleted.'


def resolve_period_backup_path(base_dir: Path, file_rel: str) -> tuple[Optional[Path], Optional[str]]:
    rel = (file_rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in rel.split("/") or not rel.lower().endswith(".zip"):
        return None, "Invalid backup file."
    for root in _backup_search_roots(base_dir):
        full = root / Path(rel)
        try:
            full.relative_to(root)
        except ValueError:
            continue
        if _safe_is_file(full):
            return full, None
    return None, "Backup file not found."


def run_period_backup(
    *,
    kind: str,
    base_dir: Path,
    force: bool = False,
    trigger: str = "schedule",
    when: Optional[datetime] = None,
    audit_callback: Optional[Callable[[str, str], None]] = None,
) -> tuple[bool, str, str]:
    """Back up only documents added in the local daily/monthly/yearly window. Never edits records."""
    kind = (kind or "daily").strip().lower()
    if kind not in PERIOD_KINDS:
        kind = "daily"
    now = when or datetime.now()
    start_utc, end_utc, period_key = period_bounds(kind, now)
    settings_now = load_settings(base_dir)
    wait = 5.0 if _is_manual_backup_trigger(trigger) else 2.0
    lgu, lgu_err = probe_daraga_official(base_dir, settings_now, wait=wait)
    if lgu_err and _is_manual_backup_trigger(trigger):
        return False, lgu_err, ""
    filename = period_filename(kind, period_key)
    rel = str((period_folder(Path("_root"), kind, now) / filename).relative_to(Path("_root"))).replace("\\", "/")
    hold_root = pending_lgu_root(base_dir)
    dest = hold_root / Path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _safe_exists(dest) and force:
        dest = unique_backup_path(dest)
        try:
            rel = str(dest.relative_to(hold_root)).replace("\\", "/")
        except ValueError:
            rel = dest.name

    try:
        if lgu is not None and not lgu_err:
            official = lgu / relative_path_on_server(lgu, rel)
            if _safe_exists(official) and not force:
                msg = f"This {kind} backup already exists. {describe_backup_copies(official, 'ok', str(official))}"
                from models import BackupRun
                last = (
                    BackupRun.query.filter_by(kind=kind, period_key=period_key)
                    .order_by(BackupRun.created_at.desc())
                    .first()
                )
                if last is None or last.status == "error":
                    _save_backup_run(kind=kind, period_key=period_key, status="skipped", file_rel=rel, record_count=0, message=msg)
                return True, msg, rel
        if _safe_exists(dest) and not force:
            server_status, extra = _finish_official_backup(dest, rel, base_dir, settings_now)
            msg = f"This {kind} backup already exists. {describe_backup_copies(dest, server_status, extra)}"
            from models import BackupRun
            last = (
                BackupRun.query.filter_by(kind=kind, period_key=period_key)
                .order_by(BackupRun.created_at.desc())
                .first()
            )
            if last is None or last.status == "error":
                _save_backup_run(kind=kind, period_key=period_key, status="skipped", file_rel=rel, record_count=0, message=msg)
            return True, msg, rel

        from models import Record

        records = (
            Record.query.filter(Record.created_at >= start_utc, Record.created_at < end_utc)
            .order_by(Record.created_at.asc())
            .all()
        )
        resolve_upload = _runtime.get("resolve_upload")
        if not callable(resolve_upload):
            raise RuntimeError("Backup file resolver is not ready. Restart the application.")

        try:
            write_period_documents_zip(dest, records, resolve_upload)
        except OSError as exc:
            from services.vault_storage import describe_storage_error

            raise RuntimeError(describe_storage_error(exc, dest)) from exc
        server_status, extra = _finish_official_backup(dest, rel, base_dir, settings_now)
        copies = describe_backup_copies(dest, server_status, extra)
        if server_status != "ok" and _is_manual_backup_trigger(trigger):
            raise RuntimeError(copies)
        if not records:
            msg = f"{kind.title()} backup complete. No new scans. {copies}"
        else:
            scan_word = "scan" if len(records) == 1 else "scans"
            msg = f"{kind.title()} backup complete ({len(records)} {scan_word}). {copies}"
        run_status = "success" if server_status == "ok" else "partial"
        _save_backup_run(
            kind=kind,
            period_key=period_key,
            status=run_status,
            file_rel=rel,
            record_count=len(records),
            message=msg,
        )
        settings = load_settings(base_dir)
        settings["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        settings["last_run_status"] = run_status
        settings["last_run_message"] = msg
        settings["last_run_file"] = rel
        save_settings(base_dir, settings)
        if audit_callback:
            audit_callback(
                "AUTO_BACKUP",
                f"kind={kind} period={period_key} records={len(records)} file={rel} server={server_status}",
            )
        return True, msg, rel
    except Exception as exc:
        logger.exception("Date-based backup failed")
        settings = load_settings(base_dir)
        settings["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        settings["last_run_status"] = "error"
        settings["last_run_message"] = str(exc)
        settings["last_run_file"] = ""
        save_settings(base_dir, settings)
        try:
            _save_backup_run(kind=kind, period_key=period_key, status="error", file_rel="", record_count=0, message=str(exc))
        except Exception:
            logger.exception("Could not write backup history")
        if audit_callback:
            audit_callback("AUTO_BACKUP_FAILED", f"kind={kind} period={period_key} error={exc}")
        return False, str(exc), ""


def write_full_backup_zip(
    dest_path: Path,
    *,
    base_dir: Path,
    upload_dir: Path,
) -> None:
    """Write a full backup zip (PostgreSQL dump + uploads) to dest_path."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dump_tmp = dest_path.parent / f"{dest_path.stem}_database.json"
    try:
        with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
            from services.postgres_backup import dump_database_json

            dump_database_json(dump_tmp)
            zf.write(dump_tmp, "database.json")
            extra_files = (
                base_dir / "backups" / SETTINGS_FILENAME,
                base_dir / "storage_settings.json",
                base_dir / "log_retention.json",
            )
            for extra in extra_files:
                if extra.is_file():
                    try:
                        zf.write(extra, extra.name)
                    except OSError:
                        logger.warning("Could not add %s to full backup", extra)
            if upload_dir.exists():
                upload_root = Path(upload_dir)
                try:
                    nested_uploads = (upload_root / "uploads").resolve()
                except OSError:
                    nested_uploads = upload_root / "uploads"
                for root, dirs, files in os.walk(upload_dir):
                    root_path = Path(root)
                    try:
                        root_resolved = root_path.resolve()
                    except OSError:
                        root_resolved = root_path
                    try:
                        dirs[:] = [d for d in dirs if (root_resolved / d) != nested_uploads]
                    except OSError:
                        pass
                    if root_resolved == nested_uploads:
                        continue
                    for fname in files:
                        full = Path(root) / fname
                        try:
                            rel = full.relative_to(upload_root)
                        except ValueError:
                            continue
                        arc = "uploads/" + str(rel).replace("\\", "/")
                        if "/uploads/uploads/" in f"/{arc}/":
                            continue
                        zf.write(full, arc)
    finally:
        dump_tmp.unlink(missing_ok=True)


def run_full_backup(
    *,
    base_dir: Path,
    force: bool = False,
    trigger: str = "schedule",
    when: Optional[datetime] = None,
    audit_callback: Optional[Callable[[str, str], None]] = None,
) -> tuple[bool, str, str]:
    """Save a full system zip (database + scans) on office-server storage only."""
    now = when or datetime.now()
    settings_now = load_settings(base_dir)
    period_key = schedule_period_key(str(settings_now.get("frequency") or "daily"), now)
    wait = 5.0 if _is_manual_backup_trigger(trigger) else 2.0
    lgu, lgu_err = probe_daraga_official(base_dir, settings_now, wait=wait)
    if lgu_err and _is_manual_backup_trigger(trigger):
        return False, lgu_err, ""
    filename = full_backup_filename(str(settings_now.get("frequency") or "daily"), now)
    rel = f"Full/{filename}"
    hold_root = pending_lgu_root(base_dir)
    dest = hold_root / "Full" / filename
    dest.parent.mkdir(parents=True, exist_ok=True)
    if _safe_exists(dest) and force:
        dest = unique_backup_path(dest)
        rel = f"Full/{dest.name}"
    upload_dir = _runtime.get("upload_dir") or (base_dir / "uploads")

    try:
        if lgu is not None and not lgu_err:
            official = lgu / relative_path_on_server(lgu, rel)
            if _safe_exists(official) and not force:
                msg = f"This full backup already exists. {describe_backup_copies(official, 'ok', str(official))}"
                from models import BackupRun
                last = (
                    BackupRun.query.filter_by(kind="full", period_key=period_key)
                    .order_by(BackupRun.created_at.desc())
                    .first()
                )
                if last is None or last.status == "error":
                    _save_backup_run(kind="full", period_key=period_key, status="skipped", file_rel=rel, record_count=0, message=msg)
                return True, msg, rel
        if _safe_exists(dest) and not force:
            server_status, extra = _finish_official_backup(dest, rel, base_dir, settings_now)
            copies = describe_backup_copies(dest, server_status, extra)
            msg = f"This full backup already exists. {copies}"
            from models import BackupRun
            last = (
                BackupRun.query.filter_by(kind="full", period_key=period_key)
                .order_by(BackupRun.created_at.desc())
                .first()
            )
            if last is None or last.status == "error":
                _save_backup_run(kind="full", period_key=period_key, status="skipped", file_rel=rel, record_count=0, message=msg)
            return True, msg, rel

        from models import Record

        try:
            write_full_backup_zip(
                dest,
                base_dir=base_dir,
                upload_dir=Path(upload_dir),
            )
        except OSError as exc:
            from services.vault_storage import describe_storage_error

            raise RuntimeError(describe_storage_error(exc, dest)) from exc
        server_status, extra = _finish_official_backup(dest, rel, base_dir, settings_now)
        copies = describe_backup_copies(dest, server_status, extra)
        count = Record.query.count()
        if server_status != "ok":
            if _is_manual_backup_trigger(trigger):
                raise RuntimeError(copies)
            run_status = "partial"
            msg = (
                "Full backup is waiting to be sent to the Daraga LGU server. "
                f"{extra}"
            )
        else:
            run_status = "success"
            msg = f"Full backup complete. {copies}"
        _save_backup_run(
            kind="full",
            period_key=period_key,
            status=run_status,
            file_rel=rel,
            record_count=count,
            message=msg,
        )
        settings = load_settings(base_dir)
        settings["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        settings["last_run_status"] = run_status
        settings["last_run_message"] = msg
        settings["last_run_file"] = rel
        save_settings(base_dir, settings)
        _cleanup_old_backups(base_dir, int(settings.get("retain_count") or 10))
        if audit_callback:
            audit_callback("AUTO_BACKUP", f"kind=full period={period_key} file={rel} server={server_status}")
        return True, msg, rel
    except Exception as exc:
        logger.exception("Full backup failed")
        settings = load_settings(base_dir)
        settings["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        settings["last_run_status"] = "error"
        settings["last_run_message"] = str(exc)
        settings["last_run_file"] = ""
        save_settings(base_dir, settings)
        try:
            _save_backup_run(kind="full", period_key=period_key, status="error", file_rel="", record_count=0, message=str(exc))
        except Exception:
            logger.exception("Could not record full backup failure")
        if audit_callback:
            audit_callback("AUTO_BACKUP_FAILED", f"kind=full error={exc}")
        return False, str(exc), ""


def resolve_auto_backup_path(base_dir: Path, filename: str) -> tuple[Optional[Path], Optional[str]]:
    """Validate backup filename and return path under backups/auto/, or an error message."""
    safe_name = Path(filename).name
    if not safe_name.lower().endswith(".zip") or safe_name != filename:
        return None, "Invalid backup file."
    folder = auto_backup_dir(base_dir).resolve()
    full_path = (folder / safe_name).resolve()
    try:
        full_path.relative_to(folder)
    except ValueError:
        return None, "Invalid backup file."
    if not full_path.is_file():
        return None, "Backup file not found."
    return full_path, None


def delete_auto_backup(base_dir: Path, filename: str) -> tuple[bool, str]:
    """Delete one automatic backup zip. Clears last_run_file in settings if it pointed at this file."""
    path, err = resolve_auto_backup_path(base_dir, filename)
    if err:
        return False, err
    try:
        path.unlink()
    except OSError as exc:
        logger.warning("Could not delete auto-backup %s: %s", path, exc)
        return False, f"Could not delete backup: {exc}"

    settings = load_settings(base_dir)
    rel = f"{AUTO_BACKUP_SUBDIR}/{path.name}"
    if settings.get("last_run_file") in (rel, path.name):
        settings["last_run_file"] = ""
        save_settings(base_dir, settings)
    return True, path.name


def list_auto_backups(base_dir: Path, limit: int = 15) -> list[dict[str, Any]]:
    try:
        folder = auto_backup_dir(base_dir)
    except OSError:
        return []
    items: list[dict[str, Any]] = []
    try:
        paths = list(folder.glob("*.zip"))
    except OSError:
        return []
    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0
    for path in sorted(paths, key=_mtime, reverse=True):
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append(
            {
                "filename": path.name,
                "size_bytes": stat.st_size,
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            }
        )
        if len(items) >= limit:
            break
    return items


def _cleanup_old_backups(base_dir: Path, retain_count: int) -> None:
    try:
        folder = auto_backup_dir(base_dir)
    except OSError:
        return
    backups = sorted(folder.glob("civil_registry_full_backup_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[retain_count:]:
        try:
            old.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not delete old auto-backup %s: %s", old, exc)


def run_auto_backup(
    *,
    base_dir: Path,
    upload_dir: Path,
    audit_callback: Optional[Callable[[str, str], None]] = None,
    trigger: str = "schedule",
    kind: str = "daily",
    force: bool = False,
) -> tuple[bool, str, str]:
    """Run a date-based document backup or a full system zip. Original records are never changed."""
    if (kind or "").strip().lower() == "full":
        return run_full_backup(
            base_dir=base_dir,
            force=force,
            trigger=trigger,
            audit_callback=audit_callback,
        )
    return run_period_backup(
        kind=kind,
        base_dir=base_dir,
        force=force,
        trigger=trigger,
        audit_callback=audit_callback,
    )


def scheduled_backup_kind(settings: Optional[dict[str, Any]] = None) -> str:
    freq = str((settings or {}).get("frequency") or "daily").strip().lower()
    if freq in PERIOD_KINDS:
        return freq
    return "daily"


def prepare_lgu_office_backup(base_dir: Path) -> None:
    """Point Auto at the Daraga LGU server folder and turn it on. Never use this laptop's Downloads or project folder."""
    settings = load_settings(base_dir)
    dest = clean_destination_path(str(settings.get("destination_path") or ""))
    office = office_backup_destination(base_dir, dest)
    changed = False
    if dest != office:
        settings["destination_path"] = office
        changed = True
    if not settings.get("enabled"):
        settings["enabled"] = True
        settings["frequency"] = "daily"
        settings["time"] = str(settings.get("time") or "19:00")
        changed = True
    if str(settings.get("frequency") or "").strip().lower() not in ("daily", "weekly", "monthly"):
        settings["frequency"] = "daily"
        changed = True
    if changed:
        save_settings(base_dir, settings)
    err = ensure_backup_dir_writable(Path(office))
    if err:
        if _is_share_offline_message(err):
            logger.info(
                "Daraga LGU server is not on this network yet. Official backups wait until %s can be reached.",
                lgu_backup_unc(),
            )
        else:
            logger.warning("Daraga LGU backup folder not writable: %s", err)


def apply_schedule(app) -> None:
    """Schedule a period backup (that day's / week's / month's scans) on the LGU server."""
    global _scheduler
    if _scheduler is None:
        return

    from apscheduler.triggers.cron import CronTrigger

    base_dir = _runtime["base_dir"]
    settings = load_settings(base_dir)

    for job_id in (JOB_ID, JOB_DAILY, JOB_MONTHLY, JOB_YEARLY):
        if _scheduler.get_job(job_id):
            _scheduler.remove_job(job_id)

    if not settings.get("enabled"):
        logger.info("Automatic backup is disabled.")
        return

    kind = scheduled_backup_kind(settings)

    def _job():
        with app.app_context():
            run_auto_backup(
                base_dir=_runtime["base_dir"],
                upload_dir=_runtime["upload_dir"],
                audit_callback=_runtime.get("audit_callback"),
                trigger="schedule",
                kind=kind,
            )

    job_defaults = {
        "misfire_grace_time": MISFIRE_GRACE_SECONDS,
        "coalesce": True,
        "max_instances": 1,
    }
    hour, minute = _time_parts_value(str(settings.get("time") or settings.get("full_time") or "20:00"))
    freq = str(settings.get("frequency") or "daily")
    if freq == "weekly":
        trigger = CronTrigger(day_of_week=int(settings.get("weekday") or 0), hour=hour, minute=minute)
    elif freq == "monthly":
        trigger = CronTrigger(day=int(settings.get("day_of_month") or 1), hour=hour, minute=minute)
    else:
        trigger = CronTrigger(hour=hour, minute=minute)

    _scheduler.add_job(
        _job,
        trigger,
        id=JOB_ID,
        replace_existing=True,
        **job_defaults,
    )
    logger.info(
        "Automatic period backup scheduled: kind=%s time=%s:%02d (misfire_grace=%ss)",
        kind,
        hour,
        minute,
        MISFIRE_GRACE_SECONDS,
    )


def _backup_period_already_done(kind: str, period_key: str, base_dir: Path) -> bool:
    """True if this period already has a usable zip (history or on disk)."""
    from models import BackupRun

    last = (
        BackupRun.query.filter_by(kind=kind, period_key=period_key)
        .order_by(BackupRun.created_at.desc())
        .first()
    )
    if last is not None and (last.status or "") in ("success", "partial", "skipped"):
        if last.file_rel:
            path, _err = resolve_period_backup_path(base_dir, last.file_rel)
            if path is not None:
                return True
        if (last.status or "") == "skipped":
            return True

    local_root = backup_storage_dir(base_dir)
    if kind == "full":
        settings = load_settings(base_dir)
        candidate = auto_backup_dir(base_dir) / full_backup_filename(
            str(settings.get("frequency") or "daily")
        )
        return _safe_is_file(candidate)

    try:
        if kind == "daily":
            when = datetime.strptime(period_key, "%Y-%m-%d")
        elif kind == "weekly":
            when = datetime.strptime(period_key + "-1", "%G-W%V-%u")
        elif kind == "monthly":
            when = datetime.strptime(period_key, "%Y-%m")
        else:
            when = datetime.strptime(period_key, "%Y")
    except ValueError:
        when = datetime.now()
    folder = period_folder(local_root, kind, when)
    return _safe_is_file(folder / period_filename(kind, period_key))


def _scheduled_time_passed_today(time_hhmm: str, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    hour, minute = _time_parts_value(str(time_hhmm or "20:00"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= due


_catchup_started = False


def schedule_catch_up_missed_backups(app) -> None:
    """Run missed daily backup after Flask is listening so startup is not blocked."""
    global _catchup_started
    if _catchup_started:
        return
    _catchup_started = True

    def _run():
        time.sleep(12)
        try:
            catch_up_missed_backups(app)
        except Exception:
            logger.exception("Backup catch-up failed")

    threading.Thread(target=_run, name="daraga-backup-catchup", daemon=True).start()


def catch_up_missed_backups(app) -> None:
    """If the app starts after the scheduled time and that backup is missing, run it once."""
    base_dir = _runtime.get("base_dir")
    upload_dir = _runtime.get("upload_dir")
    if base_dir is None or upload_dir is None:
        return
    settings = load_settings(base_dir)
    if not settings.get("enabled"):
        return
    _sys, sys_err = require_system_backup_root(base_dir)
    if sys_err:
        logger.warning("Catch-up skipped: %s", sys_err)
        return
    try:
        copied, failed, sync_err = sync_local_backups_to_server(base_dir)
        if copied:
            logger.info("Copied %s in-system backup(s) to the Daraga LGU server", copied)
        if sync_err:
            logger.info("Daraga copy not available yet: %s", sync_err)
        elif failed:
            logger.warning("Could not copy %s in-system backup(s) to Daraga", failed)
    except Exception:
        logger.exception("Could not sync in-system backups to Daraga")

    now = datetime.now()
    audit = _runtime.get("audit_callback")
    freq = str(settings.get("frequency") or "daily")
    run_time = str(settings.get("time") or settings.get("full_time") or "20:00")
    if not _scheduled_time_passed_today(run_time, now):
        return
    if freq == "weekly" and now.weekday() != int(settings.get("weekday") or 0):
        return
    if freq == "monthly" and now.day != int(settings.get("day_of_month") or 1):
        return
    kind = scheduled_backup_kind(settings)
    _start, _end, period_key = period_bounds(kind, now)
    try:
        with app.app_context():
            if _backup_period_already_done(kind, period_key, base_dir):
                return
            logger.info("Catch-up: running missed %s backup for %s", kind, period_key)
            run_auto_backup(
                base_dir=base_dir,
                upload_dir=upload_dir,
                audit_callback=audit,
                trigger="catchup",
                kind=kind,
                force=False,
            )
    except Exception:
        logger.exception("Catch-up period backup failed")


def init_auto_backup_scheduler(
    app,
    *,
    base_dir: Path,
    upload_dir: Path,
    audit_callback: Optional[Callable[[str, str], None]] = None,
    resolve_upload: Optional[Callable[[str], Optional[Path]]] = None,
) -> None:
    """Start background scheduler (call once when the app starts)."""
    global _scheduler
    with _scheduler_lock:
        _runtime["base_dir"] = base_dir
        _runtime["upload_dir"] = upload_dir
        _runtime["audit_callback"] = audit_callback
        if resolve_upload is not None:
            _runtime["resolve_upload"] = resolve_upload
        try:
            prepare_lgu_office_backup(base_dir)
        except Exception:
            logger.exception("Could not apply Daraga office backup defaults")

        if _scheduler is not None:
            apply_schedule(app)
            try:
                from services.log_retention import schedule_retention_job

                schedule_retention_job(
                    _scheduler,
                    app,
                    base_dir,
                    audit_callback=audit_callback,
                    run_now=False,
                )
            except Exception:
                logger.exception("Office log retention scheduler failed")
            schedule_catch_up_missed_backups(app)
            return

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            logger.warning("APScheduler is not installed; automatic backup is unavailable.")
            return

        _scheduler = BackgroundScheduler(
            daemon=True,
            job_defaults={
                "misfire_grace_time": MISFIRE_GRACE_SECONDS,
                "coalesce": True,
                "max_instances": 1,
            },
        )
        _scheduler.start()
        apply_schedule(app)
        try:
            from services.log_retention import schedule_retention_job

            schedule_retention_job(
                _scheduler,
                app,
                base_dir,
                audit_callback=audit_callback,
                run_now=False,
            )
        except Exception:
            logger.exception("Office log retention scheduler failed")
        schedule_catch_up_missed_backups(app)


def settings_from_form(form, base_dir: Path) -> dict[str, Any]:
    """Build settings dict from an HTML form."""
    current = load_settings(base_dir)
    current["enabled"] = (form.get("auto_backup_enabled") or "").strip().lower() in ("1", "true", "on", "yes")
    freq = str(form.get("auto_backup_frequency") or current.get("frequency") or "daily").strip().lower()
    current["frequency"] = freq if freq in ("daily", "weekly", "monthly") else "daily"
    current["time"] = _parse_time(form.get("auto_backup_time") or current.get("time") or "20:00")
    current["daily_time"] = current["time"]
    current["monthly_time"] = current["time"]
    current["yearly_time"] = current["time"]
    current["full_time"] = current["time"]
    current["full_enabled"] = True
    current["daily_enabled"] = current["frequency"] == "daily"
    current["monthly_enabled"] = current["frequency"] == "monthly"
    current["yearly_enabled"] = False
    try:
        current["weekday"] = int(form.get("auto_backup_weekday") or current.get("weekday") or 0)
    except ValueError:
        current["weekday"] = 0
    try:
        current["day_of_month"] = int(form.get("auto_backup_day") or 1)
    except ValueError:
        current["day_of_month"] = 1
    current["destination_path"] = office_backup_destination(
        base_dir, form.get("auto_backup_destination") or ""
    )
    current["keep_local_copy"] = False
    return _normalize_settings(current)


def frequency_label(frequency: str) -> str:
    labels = {
        "daily": "Every day",
        "weekly": "Every week",
        "monthly": "Every month",
        "yearly": "Every year",
    }
    return labels.get(frequency, frequency)
