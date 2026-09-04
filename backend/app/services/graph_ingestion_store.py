"""模拟动作写入图谱的 SQLite 幂等账本。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..models.database import unified_database_path


class GraphIngestionStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or unified_database_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS graph_ingestion_batches (
                    batch_key TEXT PRIMARY KEY,
                    simulation_id TEXT NOT NULL,
                    graph_id TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    round_num INTEGER NOT NULL,
                    activity_count INTEGER NOT NULL,
                    character_count INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    episode_uuid TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_graph_ingestion_sim_status "
                "ON graph_ingestion_batches(simulation_id,status)"
            )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def claim(self, batch_key, simulation_id, graph_id, platform, round_num,
              activity_count, character_count):
        now = self._now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO graph_ingestion_batches("
                "batch_key,simulation_id,graph_id,platform,round_num,activity_count,"
                "character_count,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,'pending',?,?)",
                (batch_key, simulation_id, graph_id, platform, round_num,
                 activity_count, character_count, now, now),
            )
            cursor = connection.execute(
                "UPDATE graph_ingestion_batches SET status='writing',attempt_count=attempt_count+1,"
                "error=NULL,updated_at=? WHERE batch_key=? AND status IN ('pending','failed_retryable')",
                (now, batch_key),
            )
        return cursor.rowcount == 1

    def mark_written(self, batch_key, episode_uuid):
        with self._connect() as connection:
            connection.execute(
                "UPDATE graph_ingestion_batches SET status='written',episode_uuid=?,error=NULL,updated_at=? "
                "WHERE batch_key=?",
                (episode_uuid, self._now(), batch_key),
            )

    def mark_failed(self, batch_key, error, *, retryable):
        status = "failed_retryable" if retryable else "failed_ambiguous"
        with self._connect() as connection:
            connection.execute(
                "UPDATE graph_ingestion_batches SET status=?,error=?,updated_at=? WHERE batch_key=?",
                (status, str(error), self._now(), batch_key),
            )

    def get(self, batch_key):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM graph_ingestion_batches WHERE batch_key=?", (batch_key,)
            ).fetchone()
        return dict(row) if row else None

    def list_incomplete(self, simulation_id):
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM graph_ingestion_batches WHERE simulation_id=? AND status<>'written' "
                "ORDER BY round_num,created_at",
                (simulation_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def has_retryable(self, simulation_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM graph_ingestion_batches WHERE simulation_id=? "
                "AND status IN ('pending','writing','failed_retryable') LIMIT 1",
                (simulation_id,),
            ).fetchone()
        return row is not None

    def recover_abandoned_writes(self, simulation_id, *, deterministic):
        status = "failed_retryable" if deterministic else "failed_ambiguous"
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE graph_ingestion_batches SET status=?,error=?,updated_at=? "
                "WHERE simulation_id=? AND status='writing'",
                (
                    status,
                    "进程在图谱写入确认前中断",
                    self._now(),
                    simulation_id,
                ),
            )
        return cursor.rowcount
