"""Rolling retention for office queues and logs. Never deletes civil registry records or scans."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import and_, or_

from models import AuditLog, EditRequest, PrintLog, PrintRequest, db

logger = logging.getLogger(__name__)

SETTINGS_NAME = "log_retention.json"
JOB_ID = "office_log_retention"
KEEP_CHOICES = (30, 90, 365)

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "keep_days": 30,
}


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
    try:
        days = int(out.get("keep_days") or 30)
    except (TypeError, ValueError):
        days = 30
    if days not in KEEP_CHOICES:
        days = 30
    out["keep_days"] = days
    out["enabled"] = bool(out.get("enabled", True))
    return out


def save_settings(base_dir: Path, settings: dict[str, Any]) -> dict[str, Any]:
    out = load_settings(base_dir)
    out["enabled"] = bool(settings.get("enabled", True))
    try:
        days = int(settings.get("keep_days") or out["keep_days"])
    except (TypeError, ValueError):
        days = 30
    out["keep_days"] = days if days in KEEP_CHOICES else 30
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
    try:
        days = int(form.get("log_retention_days") or current.get("keep_days") or 30)
    except (TypeError, ValueError):
        days = 30
    current["keep_days"] = days if days in KEEP_CHOICES else 30
    return current


def office_log_counts() -> dict[str, int]:
    """Live counts for the Office logs administration page."""
    return {
        "edit_pending": EditRequest.query.filter_by(status="PENDING").count(),
        "edit_closed": EditRequest.query.filter(EditRequest.status != "PENDING").count(),
        "print_pending": PrintRequest.query.filter_by(status="PENDING").count(),
        "print_closed": PrintRequest.query.filter(PrintRequest.status != "PENDING").count(),
        "print_logs": PrintLog.query.count(),
        "audit_logs": AuditLog.query.count(),
    }


def retention_hint(settings: Optional[dict[str, Any]] = None) -> str:
    data = settings or DEFAULT_SETTINGS
    if not data.get("enabled"):
        return "Office logs are kept until an admin turns on log retention."
    days = int(data.get("keep_days") or 30)
    if days >= 365:
        period = "1 year"
    elif days >= 90:
        period = "3 months"
    else:
        period = "1 month"
    return (
        f"Closed requests and office logs older than {period} are removed automatically. "
        "Birth, marriage, and death records are never deleted."
    )


def purge_old_office_logs(keep_days: int = 30) -> dict[str, int]:
    """Delete closed queues and logs older than keep_days. Leaves PENDING work and all Record rows."""
    days = max(1, int(keep_days or 30))
    cutoff = datetime.utcnow() - timedelta(days=days)
    counts = {"edit_requests": 0, "print_requests": 0, "print_logs": 0, "audit_logs": 0}

    counts["edit_requests"] = (
        db.session.query(EditRequest)
        .filter(EditRequest.status != "PENDING")
        .filter(
            or_(
                EditRequest.updated_at < cutoff,
                and_(EditRequest.updated_at.is_(None), EditRequest.requested_at < cutoff),
            )
        )
        .delete(synchronize_session=False)
    )

    counts["print_requests"] = (
        db.session.query(PrintRequest)
        .filter(PrintRequest.status.in_(("REJECTED", "APPROVED")))
        .filter(PrintRequest.requested_at < cutoff)
        .filter(
            or_(
                PrintRequest.status == "REJECTED",
                PrintRequest.used_at != None,  # noqa: E711
            )
        )
        .delete(synchronize_session=False)
    )

    counts["print_logs"] = (
        db.session.query(PrintLog)
        .filter(PrintLog.printed_at < cutoff)
        .delete(synchronize_session=False)
    )

    counts["audit_logs"] = (
        db.session.query(AuditLog)
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
        counts = purge_old_office_logs(int(settings.get("keep_days") or 30))
        total = sum(counts.values())
        if total and audit_callback:
            audit_callback(
                "LOG_RETENTION_PURGE",
                " ".join(f"{key}={value}" for key, value in counts.items()),
            )
        elif total:
            logger.info("Log retention removed %s rows: %s", total, counts)
        return counts


def schedule_retention_job(scheduler, app, base_dir: Path, audit_callback=None, run_now: bool = False) -> None:
    """Daily 02:15 check: drop office logs older than the configured window."""
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
        misfire_grace_time=3600,
    )
    if run_now:
        try:
            run_scheduled_purge(app, base_dir, audit_callback=audit_callback)
        except Exception:
            logger.exception("Office log retention catch-up failed")
