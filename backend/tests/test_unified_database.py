from pathlib import Path
import subprocess
import sys

from app.models import database as database_module
from app.models.database import (
    initialize_unified_database,
    migrate_legacy_unified_database,
    unified_database_path,
)
from app.models.model_config import APIProtocol, AuthType, ModelCapability, ProviderVendor
from app.models.task_store import TaskStore
from app.services.credential_cipher import CredentialCipher
from app.services.model_config_store import ModelConfigStore


def test_legacy_model_and_task_databases_migrate_idempotently(tmp_path):
    legacy_models = tmp_path / "model-config" / "models.db"
    legacy_tasks = tmp_path / "tasks" / "tasks.db"
    destination = tmp_path / "mirofish.db"
    source_store = ModelConfigStore(legacy_models, CredentialCipher(tmp_path / "master.key"))
    source_store.create_connection("Text", ProviderVendor.CUSTOM, APIProtocol.OPENAI_RESPONSES, AuthType.NONE, ModelCapability.TEXT_GENERATION, "https://example.com/v1", "")
    TaskStore(legacy_tasks).save([{
        "task_id": "task-1", "task_type": "prepare", "status": "completed",
        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        "progress": 100, "message": "done", "result": {}, "error": None,
        "metadata": {}, "progress_detail": {},
    }])
    ModelConfigStore(destination, CredentialCipher(tmp_path / "master.key"))
    TaskStore(destination)

    initialize_unified_database(destination, legacy_models, legacy_tasks)
    initialize_unified_database(destination, legacy_models, legacy_tasks)

    assert len(ModelConfigStore(destination, CredentialCipher(tmp_path / "master.key")).list_connections()) == 1
    assert [item["task_id"] for item in TaskStore(destination).load()] == ["task-1"]
    assert legacy_models.exists()
    assert legacy_tasks.exists()


def test_default_services_reference_unified_database_path():
    root = Path(__file__).resolve().parents[1] / "app"
    service = (root / "services/model_config_service.py").read_text()
    router = (root / "services/model_router.py").read_text()
    memory = (root / "services/memory_backend_config_service.py").read_text()
    task = (root / "models/task.py").read_text()

    for source in (service, router, memory, task):
        assert "mirofishplus.db" in source or "unified_database_path" in source


def test_default_database_uses_mirofishplus_brand(monkeypatch, tmp_path):
    monkeypatch.setattr(database_module.Config, "UPLOAD_FOLDER", str(tmp_path))

    assert unified_database_path() == tmp_path / "mirofishplus.db"


def test_legacy_unified_database_is_copied_once_and_retained(tmp_path):
    source = tmp_path / "mirofish.db"
    destination = tmp_path / "mirofishplus.db"
    with __import__("sqlite3").connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO records(value) VALUES ('legacy')")

    assert migrate_legacy_unified_database(destination, source) is True
    assert source.exists()
    with __import__("sqlite3").connect(destination) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "legacy"
        assert connection.execute(
            "SELECT 1 FROM app_schema_migrations WHERE migration_key='legacy_mirofish_database_v1'"
        ).fetchone()
        connection.execute("UPDATE records SET value='new-database-wins'")

    assert migrate_legacy_unified_database(destination, source) is False
    with __import__("sqlite3").connect(destination) as connection:
        assert connection.execute("SELECT value FROM records").fetchone()[0] == "new-database-wins"


def test_importing_database_module_does_not_create_the_new_database(tmp_path):
    code = """
import sys
from app.config import Config
Config.UPLOAD_FOLDER = sys.argv[1]
import app.models.database
assert 'app.models.task' not in sys.modules
"""

    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "mirofishplus.db").exists()
