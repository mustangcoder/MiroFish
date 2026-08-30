"""使用连接所选协议执行最小安全探测。"""

import os
import time

from ..models.model_config import AuthType, ModelCapability, ModelRole
from .protocols.base import TextGenerationRequest
from .protocols.factory import create_embedding_client, create_text_client
from .provider_credentials import resolve_connection_credential


class ModelConnectionTester:
    def __init__(self, store, text_client_factory=create_text_client, embedding_client_factory=create_embedding_client):
        self.store = store
        self.text_client_factory = text_client_factory
        self.embedding_client_factory = embedding_client_factory

    @staticmethod
    def _error_code(error):
        status_code = getattr(error, "status_code", None)
        if status_code in {401, 403}:
            return "authentication_failed"
        if status_code == 404:
            return "endpoint_or_model_not_found"
        if status_code == 429:
            return "rate_limited"
        if isinstance(error, TimeoutError):
            return "timeout"
        if isinstance(error, ValueError):
            return "model_required"
        return "connection_failed"

    def test(self, connection_id):
        connection = self.store.get_connection(connection_id)
        api_key = resolve_connection_credential(
            connection,
            self.store.get_connection_secret(connection_id),
        )
        started = time.monotonic()
        test_type = connection.protocol.value
        try:
            draft = self.store.get_draft()
            roles = (
                (ModelRole.EMBEDDING,)
                if connection.capability == ModelCapability.EMBEDDING
                else (ModelRole.HIGH_CAPABILITY, ModelRole.HIGH_THROUGHPUT)
            )
            assignment = next(
                (draft[role] for role in roles if draft.get(role, {}).get("connection_id") == connection_id),
                None,
            )
            if connection.auth_type == AuthType.OAUTH_GATEWAY and assignment is None:
                assignment = {"model": os.environ.get("DIRECT_CODEX_MODEL", "gpt-5.6-luna")}
            if not assignment or not assignment.get("model"):
                raise ValueError("请先在角色配置中选择模型后再测试")
            model = assignment["model"]
            if connection.capability == ModelCapability.EMBEDDING:
                self.embedding_client_factory(connection, api_key).embed(["MiroFish connection test"], model)
            else:
                result = self.text_client_factory(connection, api_key).generate(TextGenerationRequest(
                    model=model,
                    messages=[{"role": "user", "content": "Return OK."}],
                    max_output_tokens=8,
                ))
                if not result.text.strip():
                    raise RuntimeError("empty_response")
            latency = int((time.monotonic() - started) * 1000)
            self.store.record_test(connection_id, test_type, "passed", latency)
            return {"status": "passed", "test_type": test_type, "latency_ms": latency}
        except Exception as error:
            latency = int((time.monotonic() - started) * 1000)
            code = self._error_code(error)
            self.store.record_test(connection_id, test_type, "failed", latency, code)
            return {"status": "failed", "test_type": test_type, "latency_ms": latency, "error_code": code}
