"""模型配置中心的数据类型。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ModelRole(str, Enum):
    EMBEDDING = "embedding"
    HIGH_CAPABILITY = "high_capability"
    HIGH_THROUGHPUT = "high_throughput"


class ProviderVendor(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    DEEPSEEK = "deepseek"
    KIMI = "kimi"
    CHATGPT_SUBSCRIPTION = "chatgpt_subscription"
    CUSTOM = "custom"


class APIProtocol(str, Enum):
    OPENAI_RESPONSES = "openai_responses"
    OPENAI_CHAT_COMPLETIONS = "openai_chat_completions"
    ANTHROPIC_MESSAGES = "anthropic_messages"
    OPENAI_EMBEDDINGS = "openai_embeddings"


class AuthType(str, Enum):
    API_KEY = "api_key"
    OAUTH_GATEWAY = "oauth_gateway"
    NONE = "none"


class ModelCapability(str, Enum):
    TEXT_GENERATION = "text_generation"
    EMBEDDING = "embedding"


class ProtocolSource(str, Enum):
    DETECTED = "detected"
    MANUAL = "manual"


class ProtocolVerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNTESTED = "untested"


@dataclass(frozen=True)
class ConnectionProtocol:
    protocol: APIProtocol
    capability: ModelCapability
    source: ProtocolSource
    verification_status: ProtocolVerificationStatus
    last_tested_at: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ModelConnection:
    connection_id: str
    name: str
    vendor: ProviderVendor
    protocol: APIProtocol
    auth_type: AuthType
    capability: ModelCapability
    base_url: str
    api_key_masked: str | None
    enabled: bool
    created_at: str
    updated_at: str
    protocols: tuple[ConnectionProtocol, ...] = ()


@dataclass(frozen=True)
class ConfigVersion:
    version_id: str
    assignments: dict[ModelRole, dict[str, Any]]
    created_at: str


@dataclass(frozen=True)
class ProjectModelSnapshot:
    project_id: str
    version_id: str
    assignments: dict[ModelRole, dict[str, Any]]
    created_at: str
