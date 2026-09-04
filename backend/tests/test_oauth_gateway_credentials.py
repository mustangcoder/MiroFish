from types import SimpleNamespace

from app.models.model_config import APIProtocol, AuthType, ModelCapability, ModelRole
from app.services.model_connection_tester import ModelConnectionTester
from app.services.model_router import ModelRouter
from app.services.protocols.base import TextGenerationResult


class TesterStore:
    connection = SimpleNamespace(
        auth_type=AuthType.OAUTH_GATEWAY,
        capability=ModelCapability.TEXT_GENERATION,
        protocol=APIProtocol.OPENAI_RESPONSES,
    )

    def get_connection(self, _connection_id):
        return self.connection

    def get_connection_secret(self, _connection_id):
        return ""

    def get_draft(self):
        return {}

    def record_test(self, *_args):
        pass


def test_connection_tester_uses_internal_gateway_token(monkeypatch):
    monkeypatch.setenv("DIRECT_GATEWAY_TOKEN", "internal-token")
    monkeypatch.setenv("DIRECT_CODEX_MODEL", "gpt-test")
    captured = {}

    class Client:
        def generate(self, request):
            captured["model"] = request.model
            return TextGenerationResult(text="OK")

    def factory(_connection, api_key):
        captured["api_key"] = api_key
        return Client()

    result = ModelConnectionTester(TesterStore(), text_client_factory=factory).test("oauth")

    assert result["status"] == "passed"
    assert captured["api_key"] == "internal-token"
    assert captured["model"] == "gpt-test"


def test_model_router_exposes_internal_gateway_token(monkeypatch):
    monkeypatch.setenv("DIRECT_GATEWAY_TOKEN", "internal-token")
    connection = SimpleNamespace(
        connection_id="oauth",
        vendor=SimpleNamespace(value="chatgpt_subscription"),
        protocol=SimpleNamespace(value="openai_responses"),
        auth_type=AuthType.OAUTH_GATEWAY,
        capability=SimpleNamespace(value="text_generation"),
        base_url="http://chatgpt-oauth-gateway:8090/v1",
    )
    version = SimpleNamespace(assignments={
        ModelRole.HIGH_CAPABILITY: {"connection_id": "oauth", "model": "gateway-default"},
    })
    store = SimpleNamespace(
        get_active_version=lambda: version,
        get_connection=lambda _connection_id: connection,
        get_connection_secret=lambda _connection_id: "",
    )

    resolved = ModelRouter(store=store).resolve(ModelRole.HIGH_CAPABILITY)

    assert resolved["api_key"] == "internal-token"
