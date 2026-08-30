from app.models.model_config import (
    APIProtocol,
    AuthType,
    ModelCapability,
    ModelConnection,
    ProviderVendor,
)
from app.services.provider_catalog import (
    get_provider_spec,
    infer_vendor,
    protocol_capability,
)


def test_deepseek_supports_two_text_protocols():
    spec = get_provider_spec(ProviderVendor.DEEPSEEK)

    assert spec.protocols == (
        APIProtocol.OPENAI_CHAT_COMPLETIONS,
        APIProtocol.ANTHROPIC_MESSAGES,
    )
    assert spec.default_base_url == "https://api.deepseek.com"


def test_vendor_inference_only_matches_known_api_hosts():
    assert infer_vendor("https://api.openai.com/v1") == ProviderVendor.OPENAI
    assert infer_vendor("https://api.moonshot.cn/v1") == ProviderVendor.KIMI
    assert infer_vendor("https://api.kimi.com/coding/v1") == ProviderVendor.KIMI
    assert infer_vendor("https://openai.example.com/v1") == ProviderVendor.CUSTOM
    assert infer_vendor("http://model.internal/v1") == ProviderVendor.CUSTOM


def test_protocol_determines_capability():
    assert protocol_capability(APIProtocol.OPENAI_RESPONSES) == ModelCapability.TEXT_GENERATION
    assert protocol_capability(APIProtocol.OPENAI_CHAT_COMPLETIONS) == ModelCapability.TEXT_GENERATION
    assert protocol_capability(APIProtocol.ANTHROPIC_MESSAGES) == ModelCapability.TEXT_GENERATION
    assert protocol_capability(APIProtocol.OPENAI_EMBEDDINGS) == ModelCapability.EMBEDDING


def test_deployment_location_is_not_a_domain_type():
    assert "LOCAL" not in ProviderVendor.__members__
    assert "is_local" not in ModelConnection.__dataclass_fields__


def test_chatgpt_subscription_uses_oauth_gateway():
    spec = get_provider_spec(ProviderVendor.CHATGPT_SUBSCRIPTION)

    assert spec.default_auth_type == AuthType.OAUTH_GATEWAY
    assert spec.default_protocol == APIProtocol.OPENAI_RESPONSES
    assert spec.protocols == (
        APIProtocol.OPENAI_RESPONSES,
        APIProtocol.OPENAI_CHAT_COMPLETIONS,
    )
