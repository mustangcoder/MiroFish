"""全流程恢复使用的通用 SQLite 运行与检查点存储。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..models.database import unified_database_path


ACTIVE_STATUSES = ("pending", "running", "recovering")
SENSITIVE_KEYS = {
    "api_key", "access_token", "refresh_token", "password", "secret",
    "authorization", "cookie", "oauth_token",
}


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower()
            if normalized in SENSITIVE_KEYS or normalized.endswith(("_api_key", "_password", "_token", "_secret")):
                return True
            if _contains_sensitive_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


class WorkflowRunStore:
    def __init__(self, path: str | Path | None = None, *, clock: Callable[[], datetime] | None = None):
        self.path = Path(path or unified_database_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_fingerprint TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    lease_owner TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    recovery_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_one_active
                ON workflow_runs(resource_type, resource_id, stage)
                WHERE status IN ('pending', 'running', 'recovering');
                CREATE INDEX IF NOT EXISTS idx_workflow_recoverable
                ON workflow_runs(status, updated_at);
                CREATE TABLE IF NOT EXISTS workflow_checkpoint_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence),
                    FOREIGN KEY(run_id) REFERENCES workflow_runs(run_id)
                );
            """)

    def _now(self) -> datetime:
        current = self.clock()
        return current if current.tzinfo else current.replace(tzinfo=timezone.utc)

    @staticmethod
    def _decode_run(row):
        if row is None:
            return None
        result = dict(row)
        result["checkpoint"] = json.loads(result.pop("checkpoint_json"))
        return result

    @staticmethod
    def _validate_payload(payload: dict[str, Any]):
        if _contains_sensitive_key(payload):
            raise ValueError("检查点包含敏感字段")

    def create_or_get_run(self, *, resource_type: str, resource_id: str, task_id: str,
                          stage: str, input_fingerprint: str, checkpoint: dict[str, Any] | None = None):
        checkpoint = checkpoint or {}
        self._validate_payload(checkpoint)
        now = self._now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM workflow_runs WHERE resource_type=? AND resource_id=? AND stage=? "
                "AND status IN ('pending','running','recovering')",
                (resource_type, resource_id, stage),
            ).fetchone()
            if row is None:
                run_id = f"workflow_{uuid.uuid4().hex}"
                connection.execute(
                    "INSERT INTO workflow_runs(run_id,resource_type,resource_id,task_id,stage,status,input_fingerprint,checkpoint_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,'pending',?,?,?,?)",
                    (run_id, resource_type, resource_id, task_id, stage, input_fingerprint,
                     json.dumps(checkpoint, ensure_ascii=False), now, now),
                )
                row = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode_run(row)

    def acquire_lease(self, run_id: str, owner: str, ttl_seconds: int) -> bool:
        now = self._now()
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE workflow_runs SET lease_owner=?,lease_expires_at=?,heartbeat_at=?,status='recovering',"
                "recovery_count=recovery_count+CASE WHEN lease_owner IS NULL OR lease_owner<>? THEN 1 ELSE 0 END,updated_at=? "
                "WHERE run_id=? AND status IN ('pending','running','recovering') "
                "AND (lease_owner IS NULL OR lease_owner=? OR lease_expires_at<?)",
                (owner, expires, now.isoformat(), owner, now.isoformat(), run_id, owner, now.isoformat()),
            )
        return cursor.rowcount == 1

    def heartbeat(self, run_id: str, owner: str, checkpoint: dict[str, Any] | None = None,
                  ttl_seconds: int = 30) -> bool:
        checkpoint = checkpoint or None
        if checkpoint is not None:
            self._validate_payload(checkpoint)
        now = self._now()
        fields = "heartbeat_at=?,lease_expires_at=?,updated_at=?"
        values: list[Any] = [now.isoformat(), (now + timedelta(seconds=ttl_seconds)).isoformat(), now.isoformat()]
        if checkpoint is not None:
            fields += ",checkpoint_json=?"
            values.append(json.dumps(checkpoint, ensure_ascii=False))
        values.extend([run_id, owner])
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE workflow_runs SET {fields} WHERE run_id=? AND lease_owner=?",
                values,
            )
        return cursor.rowcount == 1

    def append_checkpoint(self, run_id: str, event_type: str, payload: dict[str, Any]) -> int:
        self._validate_payload(payload)
        now = self._now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM workflow_checkpoint_events WHERE run_id=?",
                (run_id,),
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO workflow_checkpoint_events(run_id,sequence,event_type,payload_json,created_at) VALUES (?,?,?,?,?)",
                (run_id, sequence, event_type, json.dumps(payload, ensure_ascii=False), now),
            )
            connection.execute(
                "UPDATE workflow_runs SET checkpoint_json=?,updated_at=? WHERE run_id=?",
                (json.dumps(payload, ensure_ascii=False), now, run_id),
            )
        return sequence

    def list_checkpoint_events(self, run_id: str):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_checkpoint_events WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])} for row in rows]

    def list_recoverable(self):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_runs WHERE status IN ('pending','running','recovering') ORDER BY created_at"
            ).fetchall()
        return [self._decode_run(row) for row in rows]

    def get_run(self, run_id: str):
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode_run(row)

    def _finish(self, run_id: str, status: str, error: str | None = None):
        now = self._now().isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE workflow_runs SET status=?,error=?,lease_owner=NULL,lease_expires_at=NULL,"
                "updated_at=?,completed_at=? WHERE run_id=?",
                (status, error, now, now, run_id),
            )

    def complete(self, run_id: str):
        self._finish(run_id, "completed")

    def fail(self, run_id: str, error: str):
        self._finish(run_id, "failed", error)

    def supersede(self, run_id: str):
        self._finish(run_id, "superseded")

    def release_lease(self, run_id: str, owner: str):
        with self._connect() as connection:
            connection.execute(
                "UPDATE workflow_runs SET lease_owner=NULL,lease_expires_at=NULL WHERE run_id=? AND lease_owner=?",
                (run_id, owner),
            )
