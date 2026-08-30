"""模型厂商预设与协议能力目录。"""

from dataclasses import dataclass
from urllib.parse import urlparse

from ..models.model_config import (
    APIProtocol,
    AuthType,
    ModelCapability,
    ProviderVendor,
)


@dataclass(frozen=True)
class ProviderSpec:
    vendor: ProviderVendor
    label: str
    default_base_url: str
    protocols: tuple[APIProtocol, ...]
    default_protocol: APIProtocol
    default_auth_type: AuthType


_TEXT_PROTOCOLS = (
    APIProtocol.OPENAI_RESPONSES,
    APIProtocol.OPENAI_CHAT_COMPLETIONS,
    APIProtocol.ANTHROPIC_MESSAGES,
)


_PROVIDERS = {
    ProviderVendor.OPENAI: ProviderSpec(
        ProviderVendor.OPENAI,
        "OpenAI API",
        "https://api.openai.com/v1",
        (
            APIProtocol.OPENAI_RESPONSES,
            APIProtocol.OPENAI_CHAT_COMPLETIONS,
            APIProtocol.OPENAI_EMBEDDINGS,
        ),
        APIProtocol.OPENAI_RESPONSES,
        AuthType.API_KEY,
    ),
    ProviderVendor.ANTHROPIC: ProviderSpec(
        ProviderVendor.ANTHROPIC,
        "Anthropic API",
        "https://api.anthropic.com",
        (APIProtocol.ANTHROPIC_MESSAGES,),
        APIProtocol.ANTHROPIC_MESSAGES,
        AuthType.API_KEY,
    ),
    ProviderVendor.DEEPSEEK: ProviderSpec(
        ProviderVendor.DEEPSEEK,
        "DeepSeek",
        "https://api.deepseek.com",
        (
            APIProtocol.OPENAI_CHAT_COMPLETIONS,
            APIProtocol.ANTHROPIC_MESSAGES,
        ),
        APIProtocol.OPENAI_CHAT_COMPLETIONS,
        AuthType.API_KEY,
    ),
    ProviderVendor.KIMI: ProviderSpec(
        ProviderVendor.KIMI,
        "Kimi",
        "https://api.moonshot.cn/v1",
        (APIProtocol.OPENAI_CHAT_COMPLETIONS,),
        APIProtocol.OPENAI_CHAT_COMPLETIONS,
        AuthType.API_KEY,
    ),
    ProviderVendor.CHATGPT_SUBSCRIPTION: ProviderSpec(
        ProviderVendor.CHATGPT_SUBSCRIPTION,
        "ChatGPT Subscription",
        "http://direct-oauth-gateway:8090/v1",
        (
            APIProtocol.OPENAI_RESPONSES,
            APIProtocol.OPENAI_CHAT_COMPLETIONS,
        ),
        APIProtocol.OPENAI_RESPONSES,
        AuthType.OAUTH_GATEWAY,
    ),
    ProviderVendor.CUSTOM: ProviderSpec(
        ProviderVendor.CUSTOM,
        "自定义",
        "",
        (*_TEXT_PROTOCOLS, APIProtocol.OPENAI_EMBEDDINGS),
        APIProtocol.OPENAI_CHAT_COMPLETIONS,
        AuthType.API_KEY,
    ),
}


_HOST_VENDORS = {
    "api.openai.com": ProviderVendor.OPENAI,
    "api.anthropic.com": ProviderVendor.ANTHROPIC,
    "api.deepseek.com": ProviderVendor.DEEPSEEK,
    "api.moonshot.cn": ProviderVendor.KIMI,
    "api.moonshot.ai": ProviderVendor.KIMI,
    "api.kimi.com": ProviderVendor.KIMI,
}


def get_provider_spec(vendor: ProviderVendor | str) -> ProviderSpec:
    return _PROVIDERS[ProviderVendor(vendor)]


def list_provider_specs() -> tuple[ProviderSpec, ...]:
    return tuple(_PROVIDERS.values())


def infer_vendor(base_url: str) -> ProviderVendor:
    hostname = (urlparse(base_url).hostname or "").lower()
    return _HOST_VENDORS.get(hostname, ProviderVendor.CUSTOM)


def protocol_capability(protocol: APIProtocol | str) -> ModelCapability:
    protocol = APIProtocol(protocol)
    if protocol == APIProtocol.OPENAI_EMBEDDINGS:
        return ModelCapability.EMBEDDING
    return ModelCapability.TEXT_GENERATION
