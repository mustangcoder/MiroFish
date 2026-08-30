from types import SimpleNamespace

from app.services.protocols.base import TextGenerationResult
from app.utils.llm_client import LLMClient


class FakeRouter:
    def resolve(self, role, project_id=None):
        return {
            "api_key": "key",
            "base_url": "https://api.anthropic.com",
            "model": "claude-test",
            "protocol": "anthropic_messages",
            "vendor": "anthropic",
            "auth_type": "api_key",
            "capability": "text_generation",
        }


class FakeTextClient:
    def __init__(self):
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return TextGenerationResult(text='{"ok": true}', finish_reason="stop")


def test_llm_client_routes_through_selected_protocol():
    protocol_client = FakeTextClient()
    factory_calls = []

    def factory(connection, api_key):
        factory_calls.append((connection.protocol, api_key))
        return protocol_client

    client = LLMClient(router=FakeRouter(), text_client_factory=factory)

    assert client.chat([{"role": "user", "content": "hello"}]) == '{"ok": true}'
    assert client.chat_json([{"role": "user", "content": "json"}]) == {"ok": True}
    assert factory_calls == [("anthropic_messages", "key")]
    assert protocol_client.requests[0].model == "claude-test"
