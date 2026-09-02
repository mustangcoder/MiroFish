"""环境准备任务的 SQLite 检查点存储。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models.database import unified_database_path


ACTIVE_STATUSES = ("pending", "running")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SimulationPrepareStore:
    """持久化准备运行和逐人设检查点。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or unified_database_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_prepare_runs (
                    run_id TEXT PRIMARY KEY,
                    simulation_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    total_profiles INTEGER NOT NULL DEFAULT 0,
                    completed_profiles INTEGER NOT NULL DEFAULT 0,
                    params_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_prepare_one_active_simulation
                ON simulation_prepare_runs(simulation_id)
                WHERE status IN ('pending', 'running')
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS simulation_prepare_profiles (
                    run_id TEXT NOT NULL,
                    entity_uuid TEXT NOT NULL,
                    entity_index INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    entity_type TEXT,
                    profile_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, entity_uuid),
                    FOREIGN KEY(run_id) REFERENCES simulation_prepare_runs(run_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_prepare_profiles_order ON simulation_prepare_profiles(run_id, entity_index)"
            )

    @staticmethod
    def _run_from_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["params"] = json.loads(result.pop("params_json"))
        return result

    def create_or_get_run(
        self,
        *,
        simulation_id: str,
        task_id: str,
        graph_id: str,
        input_fingerprint: str,
        total_profiles: int,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM simulation_prepare_runs
                WHERE simulation_id=? AND status IN ('pending', 'running')
                """,
                (simulation_id,),
            ).fetchone()
            if row is None:
                run_id = f"prepare_{uuid.uuid4().hex}"
                now = _now()
                connection.execute(
                    """
                    INSERT INTO simulation_prepare_runs (
                        run_id, simulation_id, task_id, graph_id, input_fingerprint,
                        status, stage, total_profiles, completed_profiles,
                        params_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'pending', 'profiles', ?, 0, ?, ?, ?)
                    """,
                    (
                        run_id,
                        simulation_id,
                        task_id,
                        graph_id,
                        input_fingerprint,
                        int(total_profiles),
                        json.dumps(params, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM simulation_prepare_runs WHERE run_id=?", (run_id,)
                ).fetchone()
        return self._run_from_row(row)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM simulation_prepare_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return self._run_from_row(row)

    def get_active_run(self, simulation_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM simulation_prepare_runs
                WHERE simulation_id=? AND status IN ('pending', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (simulation_id,),
            ).fetchone()
        return self._run_from_row(row)

    def save_profile(
        self,
        run_id: str,
        entity_uuid: str,
        entity_index: int,
        user_id: int,
        entity_type: str | None,
        profile: dict[str, Any],
    ) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO simulation_prepare_profiles (
                    run_id, entity_uuid, entity_index, user_id, entity_type,
                    profile_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, entity_uuid) DO UPDATE SET
                    entity_index=excluded.entity_index,
                    user_id=excluded.user_id,
                    entity_type=excluded.entity_type,
                    profile_json=excluded.profile_json,
                    updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    entity_uuid,
                    int(entity_index),
                    int(user_id),
                    entity_type,
                    json.dumps(profile, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE simulation_prepare_runs
                SET completed_profiles=(
                    SELECT COUNT(*) FROM simulation_prepare_profiles WHERE run_id=?
                ), status='running', updated_at=?
                WHERE run_id=?
                """,
                (run_id, now, run_id),
            )

    def load_completed_profiles(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT entity_uuid, entity_index, user_id, entity_type, profile_json
                FROM simulation_prepare_profiles
                WHERE run_id=? ORDER BY entity_index
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "entity_uuid": row["entity_uuid"],
                "entity_index": row["entity_index"],
                "user_id": row["user_id"],
                "entity_type": row["entity_type"],
                "profile": json.loads(row["profile_json"]),
            }
            for row in rows
        ]

    def update_run(self, run_id: str, **changes: Any) -> None:
        allowed = {"status", "stage", "total_profiles", "task_id", "error"}
        values = {key: value for key, value in changes.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE simulation_prepare_runs SET {assignments} WHERE run_id=?",
                (*values.values(), run_id),
            )

    def supersede_active(self, simulation_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE simulation_prepare_runs
                SET status='superseded', updated_at=?
                WHERE simulation_id=? AND status IN ('pending', 'running')
                """,
                (_now(), simulation_id),
            )

    def list_recoverable_runs(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM simulation_prepare_runs
                WHERE status IN ('pending', 'running') ORDER BY created_at
                """
            ).fetchall()
        return [self._run_from_row(row) for row in rows]
