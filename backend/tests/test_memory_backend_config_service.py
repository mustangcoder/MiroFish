from app.config import Config
from app.services.credential_cipher import CredentialCipher
from app.services.memory_backend_config_service import MemoryBackendConfigService
from app.services.model_config_store import ModelConfigStore
import pytest


def create_service(tmp_path, environment=None):
    store = ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key"))
    return MemoryBackendConfigService(store=store, environment=environment or {})


def test_environment_config_is_imported_to_sqlite_only_once(tmp_path):
    service = create_service(tmp_path, {
        "ZEP_BACKEND": "cloud",
        "ZEP_API_KEY": "first-zep-key",
        "NEO4J_URI": "bolt://first:7687",
        "NEO4J_USER": "first-user",
        "NEO4J_PASSWORD": "first-password",
    })
    service.initialize_from_environment()

    restarted = MemoryBackendConfigService(store=service.store, environment={
        "ZEP_BACKEND": "graphiti",
        "ZEP_API_KEY": "second-zep-key",
        "NEO4J_URI": "bolt://second:7687",
        "NEO4J_USER": "second-user",
        "NEO4J_PASSWORD": "second-password",
    })
    restarted.initialize_from_environment()

    assert restarted.get_config()["backend"] == "cloud"
    assert restarted.get_secrets()["zep_api_key"] == "first-zep-key"


def test_apply_graphiti_config_updates_runtime_and_keeps_masked_password(tmp_path):
    service = create_service(tmp_path)
    service.save_config({
        "backend": "graphiti",
        "neo4j_uri": "bolt://custom:7687",
        "neo4j_user": "custom-user",
        "neo4j_password": "custom-password",
    })
    service.save_config({
        "backend": "graphiti",
        "neo4j_uri": "bolt://renamed:7687",
        "neo4j_user": "renamed-user",
        "neo4j_password": "",
    })
    service.apply_runtime_config()

    assert Config.ZEP_BACKEND == "graphiti"
    assert Config.NEO4J_URI == "bolt://renamed:7687"
    assert Config.NEO4J_USER == "renamed-user"
    assert Config.NEO4J_PASSWORD == "custom-password"


def test_model_draft_with_empty_connection_has_readable_validation_error(tmp_path):
    from app.services.model_config_service import ModelConfigService

    service = ModelConfigService(store=create_service(tmp_path).store, environment={})

    with pytest.raises(ValueError, match="模型角色配置不完整"):
        service.validate_draft({
            "embedding": {"connection_id": "", "model": "embed"},
            "high_capability": {"connection_id": "", "model": "strong"},
            "high_throughput": {"connection_id": "", "model": "fast"},
        })
