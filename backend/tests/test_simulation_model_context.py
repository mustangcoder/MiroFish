import pytest
from types import SimpleNamespace

from app.models.model_config import APIProtocol, AuthType, ModelCapability, ModelRole, ProviderVendor
from app.services.credential_cipher import CredentialCipher
from app.services.model_config_store import ModelConfigStore
from app.services.model_router import ModelRouter
from app.services.protocols.base import TextGenerationResult
from scripts import protocol_model_backend as backend_module
from scripts.protocol_model_backend import ResponsesModelBackend, create_simulation_model


def test_simulation_environment_contains_snapshot_context_window(tmp_path):
    store = ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key"))
    text = store.create_connection("文本", ProviderVendor.CUSTOM, APIProtocol.OPENAI_RESPONSES, AuthType.NONE, ModelCapability.TEXT_GENERATION, "https://example.com/v1", "")
    embed = store.create_connection("向量", ProviderVendor.CUSTOM, APIProtocol.OPENAI_EMBEDDINGS, AuthType.NONE, ModelCapability.EMBEDDING, "https://example.com/v1", "")
    store.save_draft({
        ModelRole.HIGH_CAPABILITY: {"connection_id": text.connection_id, "protocol": "openai_responses", "model": "gpt-5.6-sol", "context_window_tokens": 1_050_000},
        ModelRole.HIGH_THROUGHPUT: {"connection_id": text.connection_id, "protocol": "openai_responses", "model": "gpt-5.6-luna", "context_window_tokens": 900_000},
        ModelRole.EMBEDDING: {"connection_id": embed.connection_id, "protocol": "openai_embeddings", "model": "embed"},
    })
    store.apply_draft()

    environment = ModelRouter(store=store).build_simulation_environment()

    assert environment["LLM_CONTEXT_WINDOW_TOKENS"] == "900000"


def test_responses_backend_requires_context_window():
    with pytest.raises(ValueError, match="context_window_tokens"):
        ResponsesModelBackend("gpt-5.6-luna", "key", "https://example.com/v1", context_window_tokens=None)


def test_factory_passes_context_window_to_responses_backend():
    backend = create_simulation_model(
        "key", "https://example.com/v1", "gpt-5.6-luna", "openai_responses",
        context_window_tokens=1_050_000,
    )
    assert isinstance(backend, ResponsesModelBackend)
    assert backend.context_window_tokens == 1_050_000


def test_responses_backend_compacts_before_protocol_conversion(monkeypatch):
    captured = {}
    compacted = [{"role": "system", "content": "persona"}, {"role": "user", "content": "recent"}]

    def fake_compact(messages, tools, token_counter, context_window_tokens):
        captured["compaction_input"] = (messages, tools, context_window_tokens)
        return SimpleNamespace(messages=compacted, original_tokens=999_000, compacted_tokens=900_000, removed_groups=3, input_budget=945_000)

    monkeypatch.setattr(backend_module, "compact_messages", fake_compact)
    backend = ResponsesModelBackend("gpt-5.6-luna", "key", "https://example.com/v1", context_window_tokens=1_050_000)
    backend._client = SimpleNamespace(generate=lambda request: captured.setdefault("request", request) or None)
    backend._client.generate = lambda request: captured.update(request=request) or TextGenerationResult(
        text="ok", model="gpt-5.6-luna", usage=None,
        raw=SimpleNamespace(id="resp", model="gpt-5.6-luna", output=[], usage=None),
    )

    backend._run([{"role": "system", "content": "persona"}, {"role": "user", "content": "old"}])

    assert captured["compaction_input"][2] == 1_050_000
    assert captured["request"].messages == compacted
    assert captured["request"].truncation == "auto"
