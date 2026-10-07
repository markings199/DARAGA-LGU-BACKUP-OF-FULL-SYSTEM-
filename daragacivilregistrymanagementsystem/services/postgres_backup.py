"""Dump and restore PostgreSQL data for full ZIP backups (JSON, no pg_dump required)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import inspect

from models import (
    AuditLog,
    BackupRun,
    EditRequest,
    PrintLog,
    PrintRequest,
    Record,
    User,
    db,
)

MODELS = (
    User,
    Record,
    EditRequest,
    PrintRequest,
    AuditLog,
    PrintLog,
    BackupRun,
)

TABLE_ORDER = tuple(model.__tablename__ for model in MODELS)


def _jsonable(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return value


def _parse_value(value: Any, column) -> Any:
    if value is None or value == "":
        if column.nullable or column.default is not None or column.server_default is not None:
            if value == "" and getattr(column.type, "python_type", None) is str:
                return value
            return None if value is None else value
        return value
    type_name = type(column.type).__name__.lower()
    if "datetime" in type_name and isinstance(value, str):
        text = value.replace("T", " ").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def dump_database_json(dest: Path) -> None:
    """Write all application tables to dest as UTF-8 JSON."""
    payload = {"format": "daraga-postgres-json", "version": 1, "scope": "full", "tables": {}}
    for model in MODELS:
        rows = []
        for row in model.query.order_by(model.id).all():
            rows.append({col.name: _jsonable(getattr(row, col.name)) for col in model.__table__.columns})
        payload["tables"][model.__tablename__] = rows
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def dump_period_records_json(dest: Path, records: list[Any]) -> None:
    """Write only the given civil registry records (one day / week / month)."""
    rows = []
    for row in records:
        rows.append({col.name: _jsonable(getattr(row, col.name)) for col in Record.__table__.columns})
    payload = {
        "format": "daraga-postgres-json",
        "version": 1,
        "scope": "period",
        "tables": {"records": rows},
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _reset_postgres_identity(table: str) -> None:
    db.session.execute(
        db.text(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
        )
    )


def _as_int_id(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _prepare_tables_for_restore(tables: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Copy dump rows and drop/null foreign keys that point at users or records not in this backup."""
    prepared: dict[str, list[dict[str, Any]]] = {}
    for key, rows in (tables or {}).items():
        prepared[key] = [dict(row) for row in (rows or []) if isinstance(row, dict)]

    user_ids = {i for i in (_as_int_id(row.get("id")) for row in prepared.get("users") or []) if i is not None}
    record_ids = {i for i in (_as_int_id(row.get("id")) for row in prepared.get("records") or []) if i is not None}

    for row in prepared.get("audit_logs") or []:
        uid = _as_int_id(row.get("user_id"))
        if uid is not None and uid not in user_ids:
            row["user_id"] = None

    prepared["print_logs"] = [
        row
        for row in (prepared.get("print_logs") or [])
        if _as_int_id(row.get("user_id")) in user_ids and _as_int_id(row.get("record_id")) in record_ids
    ]

    cleaned_edits = []
    for row in prepared.get("edit_requests") or []:
        reviewed = _as_int_id(row.get("reviewed_by_id"))
        if reviewed is not None and reviewed not in user_ids:
            row["reviewed_by_id"] = None
        if _as_int_id(row.get("requested_by_id")) in user_ids and _as_int_id(row.get("record_id")) in record_ids:
            cleaned_edits.append(row)
    prepared["edit_requests"] = cleaned_edits

    cleaned_prints = []
    for row in prepared.get("print_requests") or []:
        reviewed = _as_int_id(row.get("reviewed_by_id"))
        if reviewed is not None and reviewed not in user_ids:
            row["reviewed_by_id"] = None
        if _as_int_id(row.get("requested_by_id")) in user_ids and _as_int_id(row.get("record_id")) in record_ids:
            cleaned_prints.append(row)
    prepared["print_requests"] = cleaned_prints

    return prepared


def _sanitize_orphaned_foreign_keys() -> None:
    """Second pass in the database in case any stale references remain."""
    statements = (
        """
        UPDATE audit_logs
        SET user_id = NULL
        WHERE user_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = audit_logs.user_id)
        """,
        """
        DELETE FROM print_logs
        WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = print_logs.user_id)
           OR NOT EXISTS (SELECT 1 FROM records r WHERE r.id = print_logs.record_id)
        """,
        """
        UPDATE edit_requests
        SET reviewed_by_id = NULL
        WHERE reviewed_by_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = edit_requests.reviewed_by_id)
        """,
        """
        DELETE FROM edit_requests
        WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = edit_requests.requested_by_id)
           OR NOT EXISTS (SELECT 1 FROM records r WHERE r.id = edit_requests.record_id)
        """,
        """
        UPDATE print_requests
        SET reviewed_by_id = NULL
        WHERE reviewed_by_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = print_requests.reviewed_by_id)
        """,
        """
        DELETE FROM print_requests
        WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = print_requests.requested_by_id)
           OR NOT EXISTS (SELECT 1 FROM records r WHERE r.id = print_requests.record_id)
        """,
    )
    for sql in statements:
        db.session.execute(db.text(sql))


def restore_period_documents_from_json(src: Path) -> int:
    """Merge one day's (or week's/month's) records. Does not delete other days' certificates."""
    payload = json.loads(src.read_text(encoding="utf-8"))
    tables = _prepare_tables_for_restore(payload.get("tables") or {})
    records = tables.get("records") or []
    columns = {col.name: col for col in Record.__table__.columns}
    dialect = db.engine.dialect.name
    loaded = 0
    try:
        for raw in records:
            kwargs = {}
            for name, col in columns.items():
                if name not in raw:
                    continue
                kwargs[name] = _parse_value(raw[name], col)
            rid = _as_int_id(kwargs.get("id"))
            existing = db.session.get(Record, rid) if rid is not None else None
            if existing is None:
                db.session.add(Record(**kwargs))
            else:
                for name, value in kwargs.items():
                    if name == "id":
                        continue
                    setattr(existing, name, value)
            loaded += 1
        if dialect == "postgresql":
            _reset_postgres_identity("records")
        db.session.commit()
        return loaded
    except Exception:
        db.session.rollback()
        raise


def restore_documents_from_json(src: Path) -> int:
    """Replace civil registry records only. Users and login stay; audit history is not replayed."""
    payload = json.loads(src.read_text(encoding="utf-8"))
    if str(payload.get("scope") or "").strip().lower() == "period":
        return restore_period_documents_from_json(src)
    tables = _prepare_tables_for_restore(payload.get("tables") or {})
    records = tables.get("records") or []
    if not records:
        raise RuntimeError("This backup has no civil registry documents to restore.")

    dialect = db.engine.dialect.name
    try:
        if dialect == "postgresql":
            db.session.execute(db.text("SET LOCAL lock_timeout = '8s'"))
            db.session.execute(
                db.text("TRUNCATE TABLE records RESTART IDENTITY CASCADE")
            )
        else:
            db.session.execute(db.text("DELETE FROM records"))
        db.session.flush()

        columns = {col.name: col for col in Record.__table__.columns}
        chunk: list[dict[str, Any]] = []
        loaded = 0
        chunk_size = 250
        for raw in records:
            kwargs = {}
            for name, col in columns.items():
                if name not in raw:
                    continue
                kwargs[name] = _parse_value(raw[name], col)
            chunk.append(kwargs)
            loaded += 1
            if len(chunk) >= chunk_size:
                db.session.bulk_insert_mappings(Record, chunk)
                db.session.flush()
                chunk = []
        if chunk:
            db.session.bulk_insert_mappings(Record, chunk)
            db.session.flush()

        if dialect == "postgresql":
            _reset_postgres_identity("records")
        db.session.commit()
        return loaded
    except Exception:
        db.session.rollback()
        raise


def restore_database_json(src: Path) -> int:
    """Replace current PostgreSQL rows with the dump. Returns number of rows loaded."""
    payload = json.loads(src.read_text(encoding="utf-8"))
    tables = _prepare_tables_for_restore(payload.get("tables") or {})
    dialect = db.engine.dialect.name

    if not tables.get("users"):
        raise RuntimeError(
            "This backup has no user accounts. Restore was cancelled so current logins are kept."
        )

    if dialect == "sqlite":
        db.session.execute(db.text("PRAGMA foreign_keys = OFF"))

    try:
        if dialect == "postgresql":
            joined = ", ".join(reversed(TABLE_ORDER))
            db.session.execute(db.text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))
        else:
            for table in reversed(TABLE_ORDER):
                db.session.execute(db.text(f"DELETE FROM {table}"))
        db.session.flush()

        loaded = 0
        model_by_table = {model.__tablename__: model for model in MODELS}
        chunk_size = 250
        for table in TABLE_ORDER:
            model = model_by_table[table]
            columns = {col.name: col for col in model.__table__.columns}
            chunk: list[dict[str, Any]] = []
            for raw in tables.get(table) or []:
                kwargs = {}
                for name, col in columns.items():
                    if name not in raw:
                        continue
                    kwargs[name] = _parse_value(raw[name], col)
                chunk.append(kwargs)
                loaded += 1
                if len(chunk) >= chunk_size:
                    db.session.bulk_insert_mappings(model, chunk)
                    db.session.flush()
                    chunk = []
            if chunk:
                db.session.bulk_insert_mappings(model, chunk)
                db.session.flush()

        _sanitize_orphaned_foreign_keys()
        db.session.flush()

        if dialect == "postgresql":
            inspector = inspect(db.engine)
            for table in TABLE_ORDER:
                col_names = {col["name"] for col in inspector.get_columns(table)}
                if "id" in col_names:
                    _reset_postgres_identity(table)

        db.session.commit()
        return loaded
    except Exception:
        db.session.rollback()
        raise
    finally:
        if dialect == "sqlite":
            try:
                db.session.execute(db.text("PRAGMA foreign_keys = ON"))
            except Exception:
                pass
