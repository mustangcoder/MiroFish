"""初始化本地运行所需的 MiroFish 持久化数据。"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Mapping

from app.models.database import initialize_unified_database, unified_database_path
from app.models.task_store import TaskStore
from app.services.credential_cipher import CredentialCipher
from app.services.memory_backend_config_service import MemoryBackendConfigService
from app.services.model_config_store import ModelConfigStore
from app.services.simulation_prepare_store import SimulationPrepareStore


REQUIRED_TABLES = {
    "app_schema_migrations",
    "memory_backend_config",
    "model_config_state",
    "model_config_versions",
    "model_connection_protocols",
    "model_connections",
    "model_role_drafts",
    "model_test_runs",
    "project_model_snapshots",
    "simulation_prepare_profiles",
    "simulation_prepare_runs",
    "task_history",
}


def bootstrap(
    database_path: str | Path | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> dict:
    """幂等创建、迁移并校验本地数据库。"""
    path = Path(database_path or unified_database_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    key_path = path.parent / "model-config" / "master.key"
    cipher = CredentialCipher(key_path)

    ModelConfigStore(path, cipher)
    TaskStore(path)
    initialize_unified_database(path)
    model_store = ModelConfigStore(path, cipher)
    SimulationPrepareStore(path)
    memory_service = MemoryBackendConfigService(
        store=model_store,
        environment=environment if environment is not None else os.environ,
    )
    memory_service.initialize_from_environment()

    with sqlite3.connect(path, timeout=5) as connection:
        connection.execute("PRAGMA busy_timeout=5000")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        missing = REQUIRED_TABLES - tables
        if missing:
            raise RuntimeError(
                "缺少必要的 SQLite 表: " + ", ".join(sorted(missing))
            )
        connection.execute("BEGIN IMMEDIATE")
        connection.rollback()

    memory_config = memory_service.get_config() or {}
    return {
        "database": str(path.resolve()),
        "required_tables": sorted(REQUIRED_TABLES),
        "memory_backend": memory_config.get("backend"),
        "status": "ready",
    }


def main() -> int:
    print(json.dumps(bootstrap(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
