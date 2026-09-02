"""Shared SQLite path and idempotent legacy database migration."""

from __future__ import annotations

import sqlite3
import os
import tempfile
import threading
from pathlib import Path

from ..config import Config

_migration_lock = threading.Lock()

MODEL_TABLES = (
    "model_connections",
    "model_connection_protocols",
    "model_role_drafts",
    "model_config_versions",
    "model_config_state",
    "project_model_snapshots",
    "model_test_runs",
    "memory_backend_config",
)
TASK_TABLES = ("task_history",)


def unified_database_path() -> Path:
    return Path(Config.UPLOAD_FOLDER) / "mirofishplus.db"


def legacy_unified_database_path(destination: Path | None = None) -> Path:
    destination = Path(destination or unified_database_path())
    return destination.parent / "mirofish.db"


def _user_table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        table: connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        for table in tables
    }


def migrate_legacy_unified_database(
    destination: str | Path | None = None,
    source: str | Path | None = None,
) -> bool:
    """首次启动时一致性复制旧统一库，保留源文件。"""
    destination = Path(destination or unified_database_path())
    source = Path(source or legacy_unified_database_path(destination))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _migration_lock:
        if destination.exists() or not source.exists() or source.resolve() == destination.resolve():
            return False
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".mirofishplus-", suffix=".db", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with sqlite3.connect(source, timeout=5) as source_connection:
                source_connection.execute("PRAGMA busy_timeout=5000")
                source_counts = _user_table_counts(source_connection)
                with sqlite3.connect(temporary, timeout=5) as target_connection:
                    source_connection.backup(target_connection)
                    target_connection.commit()
                    journal_mode = target_connection.execute(
                        "PRAGMA journal_mode=DELETE"
                    ).fetchone()[0]
                    if str(journal_mode).lower() != "delete":
                        raise RuntimeError("SQLite backup could not leave WAL mode")
                    integrity = target_connection.execute("PRAGMA integrity_check").fetchone()[0]
                    if integrity != "ok":
                        raise RuntimeError(f"SQLite backup integrity check failed: {integrity}")
                    target_counts = _user_table_counts(target_connection)
                    if target_counts != source_counts:
                        raise RuntimeError("SQLite backup row count mismatch")
                    target_connection.execute(
                        "CREATE TABLE IF NOT EXISTS app_schema_migrations (migration_key TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                    )
                    target_connection.execute(
                        "INSERT OR IGNORE INTO app_schema_migrations(migration_key, applied_at) VALUES ('legacy_mirofish_database_v1', datetime('now'))"
                    )
                    target_connection.commit()
            os.replace(temporary, destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            Path(f"{temporary}-wal").unlink(missing_ok=True)
            Path(f"{temporary}-shm").unlink(missing_ok=True)
            raise
        return True


def _table_exists(connection, schema: str, table: str) -> bool:
    return connection.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _copy_legacy(connection, source: Path, marker: str, tables) -> None:
    if not source.exists() or source.resolve() == Path(connection.execute("PRAGMA database_list").fetchone()[2]).resolve():
        return
    if connection.execute(
        "SELECT 1 FROM app_schema_migrations WHERE migration_key=?", (marker,)
    ).fetchone():
        return
    connection.execute("ATTACH DATABASE ? AS legacy", (str(source),))
    try:
        connection.execute("BEGIN IMMEDIATE")
        for table in tables:
            if not _table_exists(connection, "legacy", table) or not _table_exists(connection, "main", table):
                continue
            source_columns = [row[1] for row in connection.execute(f"PRAGMA legacy.table_info({table})")]
            target_columns = {row[1] for row in connection.execute(f"PRAGMA main.table_info({table})")}
            columns = [column for column in source_columns if column in target_columns]
            if not columns:
                continue
            names = ",".join(f'"{column}"' for column in columns)
            connection.execute(
                f'INSERT OR IGNORE INTO main."{table}" ({names}) SELECT {names} FROM legacy."{table}"'
            )
            source_count = connection.execute(f'SELECT COUNT(*) FROM legacy."{table}"').fetchone()[0]
            target_count = connection.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0]
            if target_count < source_count:
                raise RuntimeError(f"SQLite migration count mismatch for {table}")
        connection.execute(
            "INSERT INTO app_schema_migrations(migration_key, applied_at) VALUES (?, datetime('now'))",
            (marker,),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute("DETACH DATABASE legacy")


def initialize_unified_database(destination=None, legacy_models=None, legacy_tasks=None) -> Path:
    destination = Path(destination or unified_database_path())
    destination.parent.mkdir(parents=True, exist_ok=True)
    upload_root = destination.parent
    legacy_models = Path(legacy_models or upload_root / "model-config" / "models.db")
    legacy_tasks = Path(legacy_tasks or upload_root / "tasks" / "tasks.db")
    with _migration_lock, sqlite3.connect(destination, timeout=5) as connection:
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS app_schema_migrations (migration_key TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.commit()
        _copy_legacy(connection, legacy_models, "legacy_model_config_v1", MODEL_TABLES)
        _copy_legacy(connection, legacy_tasks, "legacy_task_history_v1", TASK_TABLES)
    return destination
