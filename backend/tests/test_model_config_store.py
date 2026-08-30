import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.models.model_config import (
    APIProtocol,
    AuthType,
    ModelCapability,
    ModelRole,
    ProtocolSource,
    ProtocolVerificationStatus,
    ProviderVendor,
)
from app.services.credential_cipher import CredentialCipher
from app.services.model_config_store import ModelConfigStore
from app.services.model_config_service import ModelConfigService


def test_cipher_creates_private_key_and_never_returns_plaintext(tmp_path):
    key_path = tmp_path / "model-config" / "master.key"
    cipher = CredentialCipher(key_path)
    encrypted = cipher.encrypt("sk-live-secret-1234")

    assert cipher.decrypt(encrypted) == "sk-live-secret-1234"
    assert cipher.mask("sk-live-secret-1234") == "sk-***1234"
    assert "sk-live-secret-1234" not in encrypted
    assert oct(key_path.parent.stat().st_mode & 0o777) == "0o700"
    assert oct(key_path.stat().st_mode & 0o777) == "0o600"


def test_connection_secret_is_encrypted_and_masked(tmp_path):
    store = ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key"))
    connection = store.create_connection(
        name="线上模型", vendor=ProviderVendor.CUSTOM,
        protocol=APIProtocol.OPENAI_CHAT_COMPLETIONS,
        auth_type=AuthType.API_KEY, capability=ModelCapability.TEXT_GENERATION,
        base_url="https://example.com/v1", api_key="sk-live-secret-1234",
    )

    public = store.get_connection(connection.connection_id)
    assert public.api_key_masked == "sk-***1234"
    assert not hasattr(public, "api_key")
    assert store.get_connection_secret(connection.connection_id) == "sk-live-secret-1234"
    assert b"sk-live-secret-1234" not in (tmp_path / "models.db").read_bytes()


def test_draft_apply_creates_immutable_version_and_project_snapshot(tmp_path):
    store = ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key"))
    text = store.create_connection("文本", ProviderVendor.CUSTOM, APIProtocol.OPENAI_CHAT_COMPLETIONS, AuthType.API_KEY, ModelCapability.TEXT_GENERATION, "https://example.com/v1", "key")
    local = store.create_connection("服务二", ProviderVendor.CUSTOM, APIProtocol.OPENAI_CHAT_COMPLETIONS, AuthType.NONE, ModelCapability.TEXT_GENERATION, "http://127.0.0.1:11434/v1", "")
    embedding = store.create_connection("向量", ProviderVendor.CUSTOM, APIProtocol.OPENAI_EMBEDDINGS, AuthType.NONE, ModelCapability.EMBEDDING, "http://127.0.0.1:8080/v1", "")
    assignments = {
        ModelRole.HIGH_CAPABILITY: {"connection_id": text.connection_id, "model": "strong"},
        ModelRole.HIGH_THROUGHPUT: {"connection_id": local.connection_id, "model": "fast", "fallback_enabled": True},
        ModelRole.EMBEDDING: {"connection_id": embedding.connection_id, "model": "embed", "dimensions": 384},
    }
    store.save_draft(assignments)
    version = store.apply_draft()
    snapshot = store.get_or_create_project_snapshot("proj-1")

    assert version.assignments[ModelRole.HIGH_CAPABILITY]["model"] == "strong"
    assert snapshot.version_id == version.version_id
    store.save_draft({**assignments, ModelRole.HIGH_CAPABILITY: {"connection_id": text.connection_id, "model": "new"}})
    store.apply_draft()
    assert store.get_project_snapshot("proj-1").version_id == version.version_id


def test_connection_in_use_cannot_be_deleted(tmp_path):
    store = ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key"))
    connection = store.create_connection("文本", ProviderVendor.CUSTOM, APIProtocol.OPENAI_CHAT_COMPLETIONS, AuthType.API_KEY, ModelCapability.TEXT_GENERATION, "https://example.com/v1", "key")
    store.save_draft({ModelRole.HIGH_CAPABILITY: {"connection_id": connection.connection_id, "model": "strong"}})

    with pytest.raises(ValueError, match="正在被模型角色使用"):
        store.delete_connection(connection.connection_id)


def test_memory_backend_config_is_persisted_and_secrets_are_encrypted(tmp_path):
    database = tmp_path / "models.db"
    store = ModelConfigStore(database, CredentialCipher(tmp_path / "master.key"))

    store.save_memory_backend_config({
        "backend": "graphiti",
        "zep_api_key": "zep-secret-key",
        "neo4j_uri": "bolt://neo4j.example.com:7687",
        "neo4j_user": "neo4j",
        "neo4j_password": "neo4j-secret-password",
    })

    public = store.get_memory_backend_config()
    secrets = store.get_memory_backend_secrets()
    assert public == {
        "backend": "graphiti",
        "zep_api_key_masked": "zep***-key",
        "neo4j_uri": "bolt://neo4j.example.com:7687",
        "neo4j_user": "neo4j",
        "neo4j_password_masked": "neo***word",
    }
    assert secrets == {
        "zep_api_key": "zep-secret-key",
        "neo4j_password": "neo4j-secret-password",
    }
    database_bytes = database.read_bytes()
    assert b"zep-secret-key" not in database_bytes
    assert b"neo4j-secret-password" not in database_bytes


def test_environment_model_import_is_atomic(tmp_path):
    store = ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key"))
    environment = {
        "LLM_API_KEY": "key",
        "LLM_BASE_URL": "https://example.com/v1",
        "LLM_MODEL_NAME": "text-model",
        "GRAPHITI_LLM_API_KEY": "key",
        "GRAPHITI_LLM_BASE_URL": "https://example.com/v1",
        "GRAPHITI_LLM_MODEL": "text-model",
        "GRAPHITI_EMBEDDING_API_KEY": "key",
        "GRAPHITI_EMBEDDING_BASE_URL": "https://example.com/v1",
        "GRAPHITI_EMBEDDING_MODEL": "embedding-model",
    }

    def initialize():
        ModelConfigService(store=store, environment=environment).initialize_from_environment()

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: initialize(), range(8)))

    assert len(store.list_connections()) == 3


def test_legacy_connections_migrate_idempotently(tmp_path):
    database = tmp_path / "models.db"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE model_connections (
                connection_id TEXT PRIMARY KEY, name TEXT NOT NULL,
                connection_type TEXT NOT NULL, base_url TEXT NOT NULL,
                api_key_encrypted TEXT, api_key_masked TEXT,
                is_local INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)
        connection.executemany(
            "INSERT INTO model_connections VALUES (?, ?, ?, ?, NULL, NULL, ?, 1, 'now', 'now')",
            [
                ("deepseek", "DeepSeek", "openai_compatible", "https://api.deepseek.com", 0),
                ("custom", "服务二", "local_openai", "http://model.internal/v1", 1),
                ("embedding", "向量", "embedding", "http://embedding.internal/v1", 1),
                ("oauth", "ChatGPT", "direct_oauth_gateway", "http://direct-oauth-gateway:8090/v1", 0),
            ],
        )

    store = ModelConfigStore(database, CredentialCipher(tmp_path / "master.key"))
    first = {item.connection_id: item for item in store.list_connections()}
    store.migrate_provider_protocol_schema()
    second = {item.connection_id: item for item in store.list_connections()}

    assert first == second
    assert first["deepseek"].vendor == ProviderVendor.DEEPSEEK
    assert first["deepseek"].protocol == APIProtocol.OPENAI_CHAT_COMPLETIONS
    assert first["custom"].vendor == ProviderVendor.CUSTOM
    assert first["embedding"].protocol == APIProtocol.OPENAI_EMBEDDINGS
    assert first["embedding"].capability == ModelCapability.EMBEDDING
    assert first["oauth"].vendor == ProviderVendor.CHATGPT_SUBSCRIPTION
    assert first["oauth"].auth_type == AuthType.OAUTH_GATEWAY
    assert "is_local" not in first["custom"].__dataclass_fields__


def test_connection_protocol_rows_are_migrated_and_replaceable(tmp_path):
    store = ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key"))
    item = store.create_connection(
        "组合服务", ProviderVendor.CUSTOM, APIProtocol.OPENAI_CHAT_COMPLETIONS,
        AuthType.NONE, ModelCapability.TEXT_GENERATION, "http://model.internal/v1", "",
    )

    assert [row.protocol for row in item.protocols] == [APIProtocol.OPENAI_CHAT_COMPLETIONS]
    store.replace_connection_protocols(item.connection_id, [
        {
            "protocol": APIProtocol.OPENAI_CHAT_COMPLETIONS,
            "capability": ModelCapability.TEXT_GENERATION,
            "source": ProtocolSource.DETECTED,
            "verification_status": ProtocolVerificationStatus.PASSED,
        },
        {
            "protocol": APIProtocol.OPENAI_EMBEDDINGS,
            "capability": ModelCapability.EMBEDDING,
            "source": ProtocolSource.MANUAL,
            "verification_status": ProtocolVerificationStatus.UNTESTED,
        },
    ])

    reloaded = ModelConfigStore(tmp_path / "models.db", CredentialCipher(tmp_path / "master.key")).get_connection(item.connection_id)
    assert {row.protocol for row in reloaded.protocols} == {
        APIProtocol.OPENAI_CHAT_COMPLETIONS,
        APIProtocol.OPENAI_EMBEDDINGS,
    }
    assert reloaded.protocols[1].source == ProtocolSource.MANUAL


def test_legacy_role_assignments_gain_connection_protocol_on_reopen(tmp_path):
    database = tmp_path / "models.db"
    cipher = CredentialCipher(tmp_path / "master.key")
    store = ModelConfigStore(database, cipher)
    item = store.create_connection(
        "文本", ProviderVendor.CUSTOM, APIProtocol.OPENAI_CHAT_COMPLETIONS,
        AuthType.NONE, ModelCapability.TEXT_GENERATION, "http://model.internal/v1", "",
    )
    store.save_draft({
        ModelRole.HIGH_CAPABILITY: {"connection_id": item.connection_id, "model": "text-model"},
    })

    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM model_config_state WHERE state_key='multi_protocol_assignment_version'")
    reopened = ModelConfigStore(database, cipher)

    assert reopened.get_draft()[ModelRole.HIGH_CAPABILITY]["protocol"] == "openai_chat_completions"
