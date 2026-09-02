from pathlib import Path

from app.models.database import initialize_unified_database
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
        assert "mirofish.db" in source or "unified_database_path" in source
