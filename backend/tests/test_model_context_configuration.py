import pytest

from app.models.model_config import (
    APIProtocol,
    AuthType,
    ModelCapability,
    ModelRole,
    ProviderVendor,
)
from app.services.credential_cipher import CredentialCipher
from app.services.model_config_service import ModelConfigService
from app.services.model_config_store import ModelConfigStore
from app.services.model_metadata import input_token_budget, known_context_window


def _store(tmp_path):
    return ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key"))


def _assignments(store, text_model, context_window_tokens=None):
    text = store.create_connection("文本", ProviderVendor.CUSTOM, APIProtocol.OPENAI_RESPONSES, AuthType.NONE, ModelCapability.TEXT_GENERATION, "https://example.com/v1", "")
    embedding = store.create_connection("向量", ProviderVendor.CUSTOM, APIProtocol.OPENAI_EMBEDDINGS, AuthType.NONE, ModelCapability.EMBEDDING, "https://example.com/v1", "")
    text_config = {"connection_id": text.connection_id, "protocol": APIProtocol.OPENAI_RESPONSES.value, "model": text_model}
    if context_window_tokens is not None:
        text_config["context_window_tokens"] = context_window_tokens
    return {
        ModelRole.HIGH_CAPABILITY: dict(text_config),
        ModelRole.HIGH_THROUGHPUT: dict(text_config),
        ModelRole.EMBEDDING: {"connection_id": embedding.connection_id, "protocol": APIProtocol.OPENAI_EMBEDDINGS.value, "model": "embed"},
    }


def test_known_gpt_56_models_have_documented_context():
    for model in ("gpt-5.6", "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"):
        assert known_context_window(model) == 1_050_000
    assert known_context_window("custom-model") is None


def test_dynamic_budget_reserves_ten_percent_with_bounds():
    assert input_token_budget(1_050_000) == 945_000
    assert input_token_budget(100_000) == 84_000
    assert input_token_budget(2_000_000) == 1_872_000
    with pytest.raises(ValueError, match="预留"):
        input_token_budget(10_000)


def test_known_model_context_is_filled_without_overwriting_manual_value(tmp_path):
    store = _store(tmp_path)
    service = ModelConfigService(store=store, environment={})

    normalized = service.validate_draft(_assignments(store, "gpt-5.6-luna"))
    assert normalized[ModelRole.HIGH_CAPABILITY]["context_window_tokens"] == 1_050_000

    manual = service.validate_draft(_assignments(store, "gpt-5.6-luna", 900_000))
    assert manual[ModelRole.HIGH_CAPABILITY]["context_window_tokens"] == 900_000


def test_unknown_text_model_requires_positive_context(tmp_path):
    store = _store(tmp_path)
    service = ModelConfigService(store=store, environment={})

    with pytest.raises(ValueError, match="最大上下文"):
        service.validate_draft(_assignments(store, "custom-model"))
    with pytest.raises(ValueError, match="最大上下文"):
        service.validate_draft(_assignments(store, "custom-model", 0))


def test_reopen_backfills_known_context_in_draft_version_and_snapshot(tmp_path):
    database = tmp_path / "models.db"
    cipher = CredentialCipher(tmp_path / "master.key")
    store = ModelConfigStore(database, cipher)
    assignments = _assignments(store, "gpt-5.6-sol")
    store.save_draft(assignments)
    store.apply_draft()
    store.get_or_create_project_snapshot("proj-1")

    reopened = ModelConfigStore(database, cipher)

    assert reopened.get_draft()[ModelRole.HIGH_CAPABILITY]["context_window_tokens"] == 1_050_000
    assert reopened.get_active_version().assignments[ModelRole.HIGH_THROUGHPUT]["context_window_tokens"] == 1_050_000
    assert reopened.get_project_snapshot("proj-1").assignments[ModelRole.HIGH_CAPABILITY]["context_window_tokens"] == 1_050_000
    assert reopened.get_state("model_context_window_version") == "1"


def test_apply_persists_autofilled_known_context(tmp_path):
    store = _store(tmp_path)
    service = ModelConfigService(store=store, environment={})
    assignments = _assignments(store, "gpt-5.6-luna")
    for connection in store.list_connections():
        protocols = []
        for item in connection.protocols:
            protocols.append({
                "protocol": item.protocol.value,
                "capability": item.capability.value,
                "source": "detected",
                "verification_status": "passed",
            })
        store.replace_connection_protocols(connection.connection_id, protocols)
    service.save_draft(assignments)

    version = service.apply_draft()

    assert version.assignments[ModelRole.HIGH_CAPABILITY]["context_window_tokens"] == 1_050_000
    assert store.get_draft()[ModelRole.HIGH_THROUGHPUT]["context_window_tokens"] == 1_050_000
