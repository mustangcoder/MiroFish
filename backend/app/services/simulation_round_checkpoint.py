"""OASIS 模拟在完整轮次边界上的可恢复快照。"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class SimulationRoundCheckpoint:
    """原子保存平台数据库快照、动作日志偏移和完成轮次。"""

    def __init__(self, simulation_dir: str | Path):
        self.simulation_dir = Path(simulation_dir)
        self.checkpoint_path = self.simulation_dir / "round_checkpoint.json"
        self._lock = threading.Lock()

    def _load(self):
        if not self.checkpoint_path.exists():
            return {"version": 1, "platforms": {}}
        with self.checkpoint_path.open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if payload.get("version") != 1 or not isinstance(payload.get("platforms"), dict):
            raise ValueError("模拟轮次检查点格式无效")
        return payload

    @staticmethod
    def _verify_database(path: Path):
        with sqlite3.connect(path) as connection:
            result = connection.execute("PRAGMA quick_check").fetchone()
        if not result or result[0] != "ok":
            raise RuntimeError(f"OASIS 数据库完整性检查失败: {path}")

    @staticmethod
    def _snapshot_database(database_path: Path, snapshot_path: Path):
        temporary = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        with sqlite3.connect(database_path) as source, sqlite3.connect(temporary) as destination:
            source.backup(destination)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, snapshot_path)

    def commit(self, platform: str, completed_round: int, total_actions: int,
               database_path: str | Path, action_log_path: str | Path):
        database_path = Path(database_path)
        action_log_path = Path(action_log_path)
        if not database_path.exists():
            raise FileNotFoundError(database_path)
        action_log_path.parent.mkdir(parents=True, exist_ok=True)
        action_log_path.touch(exist_ok=True)

        with self._lock:
            with action_log_path.open("ab") as stream:
                stream.flush()
                os.fsync(stream.fileno())
                log_offset = stream.tell()

            snapshot_path = self.simulation_dir / f"{platform}_simulation.checkpoint.db"
            self._snapshot_database(database_path, snapshot_path)
            self._verify_database(snapshot_path)

            payload = self._load()
            payload["platforms"][platform] = {
                "completed_round": completed_round,
                "total_actions": total_actions,
                "log_offset": log_offset,
                "database_snapshot": snapshot_path.name,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            temporary = self.checkpoint_path.with_suffix(".json.tmp")
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.checkpoint_path)

    def restore(self, platform: str, database_path: str | Path,
                action_log_path: str | Path):
        with self._lock:
            payload = self._load()
            checkpoint = payload["platforms"].get(platform)
            if checkpoint is None:
                return None

            snapshot_path = self.simulation_dir / checkpoint["database_snapshot"]
            self._verify_database(snapshot_path)
            database_path = Path(database_path)
            temporary = database_path.with_suffix(database_path.suffix + ".restore.tmp")
            shutil.copyfile(snapshot_path, temporary)
            os.replace(temporary, database_path)

            action_log_path = Path(action_log_path)
            action_log_path.parent.mkdir(parents=True, exist_ok=True)
            with action_log_path.open("a+b") as stream:
                stream.truncate(checkpoint["log_offset"])
                stream.flush()
                os.fsync(stream.fileno())

            return {
                "completed_round": int(checkpoint["completed_round"]),
                "total_actions": int(checkpoint["total_actions"]),
            }
