from types import SimpleNamespace

from app.models.model_config import APIProtocol, AuthType, ConnectionProtocol, ModelCapability, ModelRole, ProtocolSource, ProtocolVerificationStatus, ProviderVendor
from app.services.model_discovery import ModelDiscovery


class Models:
    def list(self):
        return SimpleNamespace(data=[
            SimpleNamespace(id="qwen/qwen3.8-27b"),
            SimpleNamespace(id="qwen3-embedding-0.6b"),
            SimpleNamespace(id="text-embedding-nomic-embed-text-v1.5"),
        ])


class Client:
    models = Models()


def connection(protocol, capability):
    return SimpleNamespace(vendor=ProviderVendor.CUSTOM, protocol=protocol, auth_type=AuthType.API_KEY, capability=capability, base_url="http://model.internal/v1")


def test_models_are_classified_by_connection_capability():
    discovery = ModelDiscovery(openai_client_factory=lambda **_: Client())
    chat = discovery.list_models(connection(APIProtocol.OPENAI_CHAT_COMPLETIONS, ModelCapability.TEXT_GENERATION), "key", ModelRole.HIGH_THROUGHPUT)
    embedding = discovery.list_models(connection(APIProtocol.OPENAI_EMBEDDINGS, ModelCapability.EMBEDDING), "key", ModelRole.EMBEDDING)

    assert [item["id"] for item in chat["models"]] == ["qwen/qwen3.8-27b"]
    assert {item["id"] for item in embedding["models"]} == {"qwen3-embedding-0.6b", "text-embedding-nomic-embed-text-v1.5"}
    assert chat["manual_entry"] is False


def test_model_list_failure_falls_back_to_manual_entry():
    class FailingModels:
        def list(self):
            raise RuntimeError("unsupported")

    discovery = ModelDiscovery(openai_client_factory=lambda **_: SimpleNamespace(models=FailingModels()))
    result = discovery.list_models(connection(APIProtocol.OPENAI_RESPONSES, ModelCapability.TEXT_GENERATION), "key", ModelRole.HIGH_CAPABILITY)

    assert result == {"models": [], "manual_entry": True}


def test_multi_protocol_connection_lists_models_for_selected_protocol():
    item = connection(APIProtocol.OPENAI_CHAT_COMPLETIONS, ModelCapability.TEXT_GENERATION)
    item.protocols = (
        ConnectionProtocol(APIProtocol.OPENAI_CHAT_COMPLETIONS, ModelCapability.TEXT_GENERATION, ProtocolSource.DETECTED, ProtocolVerificationStatus.PASSED),
        ConnectionProtocol(APIProtocol.OPENAI_EMBEDDINGS, ModelCapability.EMBEDDING, ProtocolSource.DETECTED, ProtocolVerificationStatus.PASSED),
    )
    discovery = ModelDiscovery(openai_client_factory=lambda **_: Client())

    result = discovery.list_models(item, "key", ModelRole.EMBEDDING, APIProtocol.OPENAI_EMBEDDINGS)

    assert {model["id"] for model in result["models"]} == {
        "qwen3-embedding-0.6b", "text-embedding-nomic-embed-text-v1.5",
    }
