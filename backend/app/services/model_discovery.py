"""按厂商能力发现模型，失败时允许手工输入。"""

from openai import OpenAI

from ..models.model_config import APIProtocol, ModelCapability, ModelRole
from .provider_credentials import resolve_connection_credential


class ModelDiscovery:
    def __init__(self, openai_client_factory=OpenAI):
        self.openai_client_factory = openai_client_factory

    @staticmethod
    def _capability(model_id):
        value = model_id.lower()
        return ModelCapability.EMBEDDING if "embed" in value or "embedding" in value else ModelCapability.TEXT_GENERATION

    def list_models(self, connection, api_key, role, protocol=None):
        api_key = resolve_connection_credential(connection, api_key)
        role = ModelRole(role)
        protocol = APIProtocol(protocol or connection.protocol)
        if getattr(connection, "protocols", ()) and protocol not in {item.protocol for item in connection.protocols}:
            raise ValueError("连接未启用所选协议")
        try:
            if protocol == APIProtocol.ANTHROPIC_MESSAGES:
                from anthropic import Anthropic

                response = Anthropic(api_key=api_key or "local", base_url=connection.base_url).models.list()
            else:
                client = self.openai_client_factory(api_key=api_key or "local", base_url=connection.base_url, timeout=20)
                response = client.models.list()
        except Exception:
            return {"models": [], "manual_entry": True}

        expected = ModelCapability.EMBEDDING if role == ModelRole.EMBEDDING else ModelCapability.TEXT_GENERATION
        values = []
        for model in response.data:
            model_id = str(model.id)
            capability = self._capability(model_id)
            if capability != expected:
                continue
            values.append({"id": model_id, "capability": capability.value, "available": True})
        return {"models": sorted(values, key=lambda item: item["id"].lower()), "manual_entry": False}
