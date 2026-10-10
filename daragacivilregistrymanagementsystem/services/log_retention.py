"""Rolling retention for office queues and logs. Never deletes civil registry records or scans."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, or_

from models import AuditLog, EditRequest, PrintLog, PrintRequest, db

logger = logging.getLogger(__name__)

SETTINGS_NAME = "log_retention.json"
JOB_ID = "office_log_retention"
KEEP_CHOICES = (30, 90, 365)
OFFICE_TZ_NAME = "Asia/Manila"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "keep_days": 90,
}


def _office_tz():
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(OFFICE_TZ_NAME)
    except Exception:
        return timezone(timedelta(hours=8))


def _normalize_keep_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return 90
    if days in KEEP_CHOICES:
        return days
    return 90


def settings_path(base_dir: Path) -> Path:
    return Path(base_dir) / SETTINGS_NAME


def load_settings(base_dir: Path) -> dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    path = settings_path(base_dir)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                out.update(data)
        except (OSError, json.JSONDecodeError):
            pass
    out["keep_days"] = _normalize_keep_days(out.get("keep_days"))
    out["enabled"] = bool(out.get("enabled", True))
    return out


def save_settings(base_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    out = load_settings(base_dir)
    out["enabled"] = bool(settings.get("enabled", True))
    out["keep_days"] = _normalize_keep_days(settings.get("keep_days", out["keep_days"]))
    if settings.get("last_purge_at"):
        out["last_purge_at"] = str(settings.get("last_purge_at"))
    settings_path(base_dir).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def settings_from_form(form, base_dir: Path) -> dict[str, Any]:
    current = load_settings(base_dir)
    current["enabled"] = (form.get("log_retention_enabled") or "").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )
    current["keep_days"] = _normalize_keep_days(
        form.get("log_retention_days") or current.get("keep_days")
    )
    return current


def office_log_counts() -> dict[str, int]:
    """Live counts for the Office logs administration page."""
    return {
        "edit_pending": EditRequest.query.filter(func.upper(EditRequest.status) == "PENDING").count(),
        "edit_closed": EditRequest.query.filter(func.upper(EditRequest.status) != "PENDING").count(),
        "print_pending": PrintRequest.query.filter(func.upper(PrintRequest.status) == "PENDING").count(),
        "print_closed": PrintRequest.query.filter(func.upper(PrintRequest.status) != "PENDING").count(),
        "print_logs": PrintLog.query.count(),
        "audit_logs": AuditLog.query.count(),
    }


def retention_hint(settings: Optional[dict[str, Any]] = None) -> str:
    data = settings or DEFAULT_SETTINGS
    if not data.get("enabled"):
        return "Office logs are kept until an admin turns on log retention."
    days = _normalize_keep_days(data.get("keep_days"))
    if days >= 365:
        period = "1 year"
    elif days >= 90:
        period = "3 months"
    else:
        period = "1 month"
    return (
        f"Closed requests and office logs older than {period} are removed automatically. "
        "Pending work, unused approval codes, and civil registry records are never deleted."
    )


def purge_old_office_logs(keep_days: int = 90) -> dict[str, int]:
    """Delete closed queues and logs older than keep_days. Leaves open work and all Record rows."""
    days = _normalize_keep_days(keep_days)
    cutoff = datetime.utcnow() - timedelta(days=days)
    counts = {"edit_requests": 0, "print_requests": 0, "print_logs": 0, "audit_logs": 0}
    edit_status = func.upper(func.coalesce(EditRequest.status, ""))
    print_status = func.upper(func.coalesce(PrintRequest.status, ""))
    edit_closed_at = func.coalesce(
        EditRequest.code_used_at,
        EditRequest.reviewed_at,
        EditRequest.updated_at,
        EditRequest.requested_at,
    )
    print_closed_at = func.coalesce(
        PrintRequest.used_at,
        PrintRequest.reviewed_at,
        PrintRequest.requested_at,
    )

    counts["edit_requests"] = (
        db.session.query(EditRequest)
        .filter(edit_status.in_(("APPROVED", "REJECTED")))
        .filter(
            or_(
                edit_status == "REJECTED",
                EditRequest.code_used_at.isnot(None),
            )
        )
        .filter(edit_closed_at.isnot(None))
        .filter(edit_closed_at < cutoff)
        .delete(synchronize_session=False)
    )

    counts["print_requests"] = (
        db.session.query(PrintRequest)
        .filter(print_status.in_(("REJECTED", "APPROVED")))
        .filter(
            or_(
                print_status == "REJECTED",
                PrintRequest.used_at.isnot(None),
            )
        )
        .filter(print_closed_at.isnot(None))
        .filter(print_closed_at < cutoff)
        .delete(synchronize_session=False)
    )

    counts["print_logs"] = (
        db.session.query(PrintLog)
        .filter(PrintLog.printed_at.isnot(None))
        .filter(PrintLog.printed_at < cutoff)
        .delete(synchronize_session=False)
    )

    counts["audit_logs"] = (
        db.session.query(AuditLog)
        .filter(AuditLog.created_at.isnot(None))
        .filter(AuditLog.created_at < cutoff)
        .filter(AuditLog.action != "LOG_RETENTION_PURGE")
        .delete(synchronize_session=False)
    )

    db.session.commit()
    return counts


def run_scheduled_purge(app, base_dir: Path, audit_callback=None) -> dict[str, int]:
    settings = load_settings(base_dir)
    if not settings.get("enabled"):
        return {}
    with app.app_context():
        counts = purge_old_office_logs(int(settings.get("keep_days") or 90))
        total = sum(counts.values())
        settings["last_purge_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        save_settings(base_dir, settings)
        if total and audit_callback:
            audit_callback(
                "LOG_RETENTION_PURGE",
                " ".join(f"{key}={value}" for key, value in counts.items()),
            )
        elif total:
            logger.info("Log retention removed %s rows: %s", total, counts)
        return counts


def schedule_retention_job(scheduler, app, base_dir: Path, audit_callback=None, run_now: bool = False) -> None:
    """Daily 02:15 Asia/Manila check. Does not run on login or app start."""
    if scheduler is None:
        return

    def _job():
        try:
            run_scheduled_purge(app, base_dir, audit_callback=audit_callback)
        except Exception:
            logger.exception("Office log retention failed")

    try:
        scheduler.remove_job(JOB_ID)
    except Exception:
        pass
    scheduler.add_job(
        _job,
        "cron",
        hour=2,
        minute=15,
        id=JOB_ID,
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=900,
        timezone=_office_tz(),
    )
    if run_now:
        logger.info("Office log retention catch-up on start is disabled so recent activity is kept.")
