"""模型配置的迁移、校验和应用服务。"""

import os
from pathlib import Path
from urllib.parse import urlparse

from ..config import Config
from ..models.model_config import (
    APIProtocol,
    AuthType,
    ModelCapability,
    ModelRole,
    ProviderVendor,
)
from .credential_cipher import CredentialCipher
from .model_config_store import ModelConfigStore
from .provider_catalog import infer_vendor
from .provider_catalog import get_provider_spec, protocol_capability
from .model_metadata import input_token_budget, known_context_window
from ..models.database import unified_database_path


class ConnectionProtocolInUseError(ValueError):
    pass


class ModelConfigService:
    def __init__(self, store=None, environment=None):
        root = Path(Config.UPLOAD_FOLDER) / "model-config"
        self.store = store or ModelConfigStore(unified_database_path(), CredentialCipher(root / "master.key"))
        self.environment = environment or os.environ

    def initialize_from_environment(self):
        specs = [
            (ModelRole.HIGH_CAPABILITY, "高能力模型", APIProtocol.OPENAI_CHAT_COMPLETIONS, "LLM"),
            (ModelRole.HIGH_THROUGHPUT, "高吞吐模型", APIProtocol.OPENAI_CHAT_COMPLETIONS, "GRAPHITI_LLM"),
            (ModelRole.EMBEDDING, "Embedding", APIProtocol.OPENAI_EMBEDDINGS, "GRAPHITI_EMBEDDING"),
        ]
        environment_specs = []
        for role, name, protocol, prefix in specs:
            base_url = self.environment.get(f"{prefix}_BASE_URL") or self.environment.get("OPENAI_BASE_URL", "")
            model = self.environment.get(f"{prefix}_MODEL") or self.environment.get(f"{prefix}_MODEL_NAME") or self.environment.get("LLM_MODEL_NAME", "")
            api_key = self.environment.get(f"{prefix}_API_KEY") or self.environment.get("OPENAI_API_KEY", "")
            environment_specs.append({
                "role": role.value,
                "name": name,
                "vendor": infer_vendor(base_url).value,
                "protocol": protocol.value,
                "auth_type": (AuthType.API_KEY if api_key else AuthType.NONE).value,
                "capability": (
                    ModelCapability.EMBEDDING
                    if protocol == APIProtocol.OPENAI_EMBEDDINGS
                    else ModelCapability.TEXT_GENERATION
                ).value,
                "base_url": base_url,
                "api_key": api_key,
                "model": model,
            })
        self.store.import_environment_models(environment_specs)

    def validate_draft(self, assignments):
        normalized = {ModelRole(role): dict(config) for role, config in assignments.items()}
        if set(normalized) != set(ModelRole):
            raise ValueError("三个模型角色必须全部配置")
        for role, config in normalized.items():
            connection_id = config.get("connection_id")
            if not connection_id or not config.get("model"):
                raise ValueError(f"模型角色配置不完整: {role.value}")
            try:
                connection = self.store.get_connection(connection_id)
            except KeyError as error:
                raise ValueError(f"模型角色连接不存在: {role.value}") from error
            if not connection.enabled or not config.get("model"):
                raise ValueError(f"模型角色配置不完整: {role.value}")
            selected_protocol = APIProtocol(config.get("protocol", connection.protocol))
            protocol_state = next(
                (item for item in connection.protocols if item.protocol == selected_protocol),
                None,
            )
            if protocol_state is None:
                raise ValueError(f"连接未启用所选协议: {role.value}")
            config["protocol"] = selected_protocol.value
            parsed = urlparse(connection.base_url)
            if parsed.scheme not in {"http", "https"}:
                raise ValueError("Base URL 必须使用 http 或 https")
            if role == ModelRole.EMBEDDING and protocol_state.capability != ModelCapability.EMBEDDING:
                raise ValueError("Embedding 角色必须使用 Embedding 协议")
            if role != ModelRole.EMBEDDING and protocol_state.capability != ModelCapability.TEXT_GENERATION:
                raise ValueError(f"模型角色必须使用文本生成协议: {role.value}")
            if role != ModelRole.EMBEDDING:
                context_window = config.get("context_window_tokens")
                if context_window is None:
                    context_window = known_context_window(config["model"])
                    if context_window is not None:
                        config["context_window_tokens"] = context_window
                if (
                    isinstance(context_window, bool)
                    or not isinstance(context_window, int)
                    or context_window <= 0
                ):
                    raise ValueError(f"模型角色必须配置有效的最大上下文 Tokens: {role.value}")
                input_token_budget(context_window)
        return normalized

    def validate_connection_data(self, data, require_protocols=False):
        vendor = ProviderVendor(data["vendor"])
        auth_type = AuthType(data["auth_type"])
        spec = get_provider_spec(vendor)
        if auth_type == AuthType.OAUTH_GATEWAY and vendor != ProviderVendor.CHATGPT_SUBSCRIPTION:
            raise ValueError("只有 ChatGPT Subscription 可以使用 OAuth Gateway 认证")
        if vendor == ProviderVendor.CHATGPT_SUBSCRIPTION and (
            auth_type != spec.default_auth_type
            or data.get("base_url") != spec.default_base_url
        ):
            raise ValueError("ChatGPT Subscription 的 OAuth Gateway 地址和认证方式由系统管理")
        raw_protocols = data.get("protocols") or []
        if not raw_protocols and data.get("protocol"):
            raw_protocols = [{
                "protocol": data["protocol"], "source": "manual",
                "verification_status": "untested",
            }]
        if require_protocols and not raw_protocols:
            raise ValueError("连接至少需要启用一个协议")
        normalized_protocols = []
        for item in raw_protocols:
            protocol = APIProtocol(item["protocol"])
            if vendor != ProviderVendor.CUSTOM and protocol not in spec.protocols:
                protocol_labels = {
                    APIProtocol.OPENAI_RESPONSES: "OpenAI Responses",
                    APIProtocol.OPENAI_CHAT_COMPLETIONS: "OpenAI Chat Completions",
                    APIProtocol.ANTHROPIC_MESSAGES: "Anthropic Messages",
                    APIProtocol.OPENAI_EMBEDDINGS: "OpenAI Embeddings",
                }
                raise ValueError(f"{spec.label} 不支持 {protocol_labels[protocol]}")
            normalized_protocols.append({
                "protocol": protocol.value,
                "capability": protocol_capability(protocol).value,
                "source": item.get("source", "manual"),
                "verification_status": item.get("verification_status", "untested"),
                "last_tested_at": item.get("last_tested_at"),
                "error_code": item.get("error_code"),
            })
        protocol = APIProtocol(normalized_protocols[0]["protocol"]) if normalized_protocols else spec.default_protocol
        if vendor != ProviderVendor.CUSTOM and protocol not in spec.protocols:
            protocol_labels = {
                APIProtocol.OPENAI_RESPONSES: "OpenAI Responses",
                APIProtocol.OPENAI_CHAT_COMPLETIONS: "OpenAI Chat Completions",
                APIProtocol.ANTHROPIC_MESSAGES: "Anthropic Messages",
                APIProtocol.OPENAI_EMBEDDINGS: "OpenAI Embeddings",
            }
            raise ValueError(f"{spec.label} 不支持 {protocol_labels[protocol]}")
        parsed = urlparse(data.get("base_url", ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Base URL 必须使用 http 或 https")
        api_key = data.get("api_key", "")
        if auth_type == AuthType.API_KEY and not api_key:
            raise ValueError("API Key 认证需要填写 API Key")
        return vendor, protocol, auth_type, api_key, normalized_protocols

    def create_connection(self, data):
        vendor, protocol, auth_type, api_key, protocols = self.validate_connection_data(data, require_protocols=True)
        item = self.store.create_connection(
            data["name"],
            vendor,
            protocol,
            auth_type,
            protocol_capability(protocol),
            data["base_url"],
            api_key,
        )
        self.store.replace_connection_protocols(item.connection_id, protocols)
        return self.store.get_connection(item.connection_id)

    def update_connection(self, connection_id, data):
        current = self.store.get_connection(connection_id)
        submitted = dict(data)
        submitted.setdefault("vendor", current.vendor.value)
        submitted.setdefault("auth_type", current.auth_type.value)
        submitted.setdefault("base_url", current.base_url)
        submitted.setdefault("name", current.name)
        submitted.setdefault("protocols", [
            {
                "protocol": item.protocol.value,
                "capability": item.capability.value,
                "source": item.source.value,
                "verification_status": item.verification_status.value,
                "last_tested_at": item.last_tested_at,
                "error_code": item.error_code,
            }
            for item in current.protocols
        ])
        submitted_api_key = submitted.get("api_key", "")
        if submitted.get("auth_type") == AuthType.API_KEY.value and not submitted_api_key:
            submitted["api_key"] = self.store.get_connection_secret(connection_id)
        vendor, protocol, auth_type, _, protocols = self.validate_connection_data(
            submitted,
            require_protocols=True,
        )
        selected_protocols = {item["protocol"] for item in protocols}
        current_protocols = {item.protocol.value for item in current.protocols}
        connection_changed = (
            vendor != current.vendor
            or auth_type != current.auth_type
            or submitted["base_url"] != current.base_url
            or selected_protocols != current_protocols
            or bool(submitted_api_key)
        )
        if connection_changed:
            protocols = [
                {
                    **item,
                    "verification_status": "untested",
                    "last_tested_at": None,
                    "error_code": None,
                }
                for item in protocols
            ]
        role_names = {
            ModelRole.EMBEDDING: "Embedding",
            ModelRole.HIGH_CAPABILITY: "高能力模型",
            ModelRole.HIGH_THROUGHPUT: "高吞吐模型",
        }
        blocked_roles = [
            role_names[role]
            for role, assignment in self.store.get_draft().items()
            if assignment.get("connection_id") == connection_id
            and assignment.get("protocol") not in selected_protocols
        ]
        if blocked_roles:
            raise ConnectionProtocolInUseError(
                "不能移除正在被以下角色使用的协议: " + "、".join(blocked_roles)
            )
        return self.store.update_connection(
            connection_id,
            name=str(submitted["name"]).strip(),
            vendor=vendor,
            protocol=protocol,
            auth_type=auth_type,
            capability=protocol_capability(protocol),
            base_url=submitted["base_url"],
            api_key=submitted_api_key,
            protocols=protocols,
        )

    def save_draft(self, assignments):
        normalized = {ModelRole(role): config for role, config in assignments.items()}
        if set(normalized) != set(ModelRole):
            raise ValueError("三个模型角色必须全部配置")
        self.store.save_draft(normalized)
        return normalized

    def apply_draft(self):
        assignments = self.validate_draft(self.store.get_draft())
        untested = []
        for role, config in assignments.items():
            connection = self.store.get_connection(config["connection_id"])
            protocol = APIProtocol(config["protocol"])
            protocol_state = next(item for item in connection.protocols if item.protocol == protocol)
            latest = self.store.latest_test(config["connection_id"], protocol.value)
            verified = protocol_state.verification_status.value == "passed"
            if not verified and (not latest or latest["status"] != "passed"):
                untested.append(role.value)
        if untested:
            raise ValueError("以下模型角色尚未通过连接测试: " + ", ".join(untested))
        self.store.save_draft(assignments)
        return self.store.apply_draft()
