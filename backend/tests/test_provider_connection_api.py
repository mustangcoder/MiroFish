import pytest

from app import create_app
from app.services.credential_cipher import CredentialCipher
from app.services.model_config_service import ModelConfigService
from app.services.model_config_store import ModelConfigStore
from app.models.model_config import APIProtocol, AuthType, ModelCapability, ModelRole, ProviderVendor


def make_service(tmp_path):
    store = ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key"))
    service = ModelConfigService(store=store, environment={})
    service.initialize_from_environment = lambda: None
    return service


def test_provider_catalog_exposes_vendor_protocol_defaults(tmp_path, monkeypatch):
    from app.api import model_settings

    monkeypatch.setattr(model_settings, "_service", lambda: make_service(tmp_path))
    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().get("/api/settings/models/provider-catalog")

    assert response.status_code == 200
    deepseek = next(item for item in response.get_json()["data"] if item["vendor"] == "deepseek")
    assert deepseek["protocols"] == ["openai_chat_completions", "anthropic_messages"]


def test_kimi_rejects_responses_protocol(tmp_path, monkeypatch):
    from app.api import model_settings

    service = make_service(tmp_path)
    monkeypatch.setattr(model_settings, "_service", lambda: service)
    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().post("/api/settings/models/connections", json={
        "name": "Kimi", "vendor": "kimi", "protocol": "openai_responses",
        "auth_type": "api_key", "base_url": "https://api.moonshot.cn/v1", "api_key": "secret",
    })

    assert response.status_code == 400
    assert "Kimi 不支持 OpenAI Responses" in response.get_json()["error"]


def test_custom_embedding_connection_derives_capability(tmp_path, monkeypatch):
    from app.api import model_settings

    service = make_service(tmp_path)
    monkeypatch.setattr(model_settings, "_service", lambda: service)
    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().post("/api/settings/models/connections", json={
        "name": "Embedding", "vendor": "custom", "protocol": "openai_embeddings",
        "auth_type": "none", "base_url": "http://embedding.internal/v1", "api_key": "",
    })

    assert response.status_code == 201
    assert response.get_json()["data"]["capability"] == "embedding"
    assert "is_local" not in response.get_json()["data"]


def test_empty_model_assignments_can_be_saved_as_draft_but_not_applied(tmp_path, monkeypatch):
    from app.api import model_settings

    service = make_service(tmp_path)
    monkeypatch.setattr(model_settings, "_service", lambda: service)
    app = create_app()
    app.config.update(TESTING=True)
    empty_draft = {
        "embedding": {"connection_id": "", "model": "", "dimensions": 384, "timeout": 60},
        "high_capability": {"connection_id": "", "model": "", "timeout": 600, "max_concurrency": 2},
        "high_throughput": {
            "connection_id": "", "model": "", "timeout": 600,
            "max_concurrency": 8, "fallback_enabled": True,
        },
    }

    saved = app.test_client().put("/api/settings/models/draft", json=empty_draft)
    applied = app.test_client().post("/api/settings/models/apply")

    assert saved.status_code == 200
    assert saved.get_json()["data"] == empty_draft
    assert applied.status_code == 400
    assert "模型角色配置不完整" in applied.get_json()["error"]


def test_chatgpt_subscription_rejects_custom_auth_or_base_url(tmp_path, monkeypatch):
    from app.api import model_settings

    service = make_service(tmp_path)
    monkeypatch.setattr(model_settings, "_service", lambda: service)
    app = create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post("/api/settings/models/connections", json={
        "name": "ChatGPT",
        "vendor": "chatgpt_subscription",
        "protocol": "openai_responses",
        "auth_type": "api_key",
        "base_url": "https://example.com/v1",
        "api_key": "secret",
    })

    assert response.status_code == 400
    assert "OAuth Gateway 地址和认证方式由系统管理" in response.get_json()["error"]


def test_unsaved_connection_can_be_tested_without_persisting(tmp_path, monkeypatch):
    from app.api import model_settings

    service = make_service(tmp_path)
    captured = {}

    class DraftTester:
        def test(self, data):
            captured.update(data)
            return {"status": "passed", "test_type": "model_list", "latency_ms": 12}

    monkeypatch.setattr(model_settings, "_service", lambda: service)
    monkeypatch.setattr(model_settings, "DraftConnectionTester", DraftTester)
    app = create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post("/api/settings/models/connections/test-draft", json={
        "name": "临时连接",
        "vendor": "custom",
        "protocol": "openai_chat_completions",
        "auth_type": "api_key",
        "base_url": "https://example.com/v1",
        "api_key": "secret",
    })

    assert response.status_code == 200
    assert response.get_json()["data"]["latency_ms"] == 12
    assert captured["api_key"] == "secret"
    assert service.store.list_connections() == []


def test_role_uses_selected_protocol_capability_from_multi_protocol_connection(tmp_path):
    service = make_service(tmp_path)
    item = service.store.create_connection(
        "组合服务", ProviderVendor.CUSTOM, APIProtocol.OPENAI_CHAT_COMPLETIONS,
        AuthType.NONE, ModelCapability.TEXT_GENERATION, "http://model.internal/v1", "",
    )
    service.store.replace_connection_protocols(item.connection_id, [
        {"protocol": "openai_chat_completions", "capability": "text_generation"},
        {"protocol": "openai_embeddings", "capability": "embedding"},
    ])
    assignments = {
        ModelRole.EMBEDDING: {"connection_id": item.connection_id, "protocol": "openai_embeddings", "model": "embed"},
        ModelRole.HIGH_CAPABILITY: {"connection_id": item.connection_id, "protocol": "openai_chat_completions", "model": "text"},
        ModelRole.HIGH_THROUGHPUT: {"connection_id": item.connection_id, "protocol": "openai_chat_completions", "model": "text"},
    }

    normalized = service.validate_draft(assignments)
    assert normalized[ModelRole.EMBEDDING]["protocol"] == "openai_embeddings"

    assignments[ModelRole.EMBEDDING]["protocol"] = "openai_chat_completions"
    with pytest.raises(ValueError, match="Embedding 角色必须使用 Embedding 协议"):
        service.validate_draft(assignments)


def test_create_connection_accepts_multiple_protocol_states(tmp_path, monkeypatch):
    from app.api import model_settings

    service = make_service(tmp_path)
    monkeypatch.setattr(model_settings, "_service", lambda: service)
    app = create_app()
    app.config.update(TESTING=True)

    response = app.test_client().post("/api/settings/models/connections", json={
        "name": "LM Studio", "vendor": "custom", "auth_type": "none",
        "base_url": "http://model.internal/v1", "api_key": "",
        "protocols": [
            {"protocol": "openai_chat_completions", "source": "detected", "verification_status": "passed"},
            {"protocol": "openai_embeddings", "source": "detected", "verification_status": "passed"},
        ],
    })

    assert response.status_code == 201
    assert {item["protocol"] for item in response.get_json()["data"]["protocols"]} == {
        "openai_chat_completions", "openai_embeddings",
    }
