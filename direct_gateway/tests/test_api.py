from types import SimpleNamespace
import httpx

from app.api import create_app
from app.responses_client import DirectProviderResult


class Router:
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return DirectProviderResult('{"ok":true}', "gpt-test", {"input_tokens": 1})


def test_api_auth_contract_and_provider_header():
    app = create_app(router=Router(), config=SimpleNamespace(internal_token="inside"), account_reader=lambda: {"authenticated": True, "email": "u***r@example.com"})
    client = app.test_client()
    assert client.post("/v1/chat/completions", json={"messages": []}).status_code == 401
    response = client.post("/v1/chat/completions", headers={"Authorization": "Bearer inside"}, json={"messages": [{"role": "user", "content": "x"}]})
    assert response.status_code == 200
    assert response.headers["X-MiroFish-Provider"] == "chatgpt-direct-oauth"
    assert response.json["choices"][0]["message"]["content"] == '{"ok":true}'
    assert client.post("/v1/chat/completions", headers={"Authorization": "Bearer inside"}, json={"messages": [], "stream": True}).status_code == 400


def test_responses_api_accepts_native_input_and_returns_openai_response_shape():
    router = Router()
    app = create_app(router=router, config=SimpleNamespace(internal_token="inside"))
    client = app.test_client()

    unauthorized = client.post("/v1/responses", json={"input": "hello"})
    response = client.post(
        "/v1/responses",
        headers={"Authorization": "Bearer inside"},
        json={
            "model": "gpt-test",
            "truncation": "auto",
            "input": [
                {"role": "system", "content": "Follow instructions"},
                {"role": "user", "content": "hello"},
            ],
            "text": {"format": {"type": "json_object"}},
        },
    )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert router.requests == [{
        "model": "gpt-test",
        "messages": [
            {"role": "system", "content": "Follow instructions"},
            {"role": "user", "content": "hello"},
        ],
        "response_format": {"type": "json_object"},
        "truncation": "auto",
    }]
    assert response.json["object"] == "response"
    assert response.json["status"] == "completed"
    assert response.json["output"][0]["content"][0]["text"] == '{"ok":true}'
    assert response.headers["X-MiroFish-Provider"] == "chatgpt-direct-oauth"


def test_models_endpoint_lists_supported_chatgpt_subscription_models():
    config = SimpleNamespace(
        internal_token="inside",
        model="gpt-5.6-luna",
        models=("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"),
    )
    app = create_app(router=Router(), config=config)

    response = app.test_client().get(
        "/v1/models", headers={"Authorization": "Bearer inside"},
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.get_json()["data"]] == [
        "gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol",
    ]


def test_responses_api_reports_exhausted_transient_failure_as_503():
    from app.responses_client import ProviderResponseError

    class FailingRouter:
        def complete(self, payload):
            raise ProviderResponseError("server_error", "upstream busy", retryable=True)

    app = create_app(router=FailingRouter(), config=SimpleNamespace(internal_token="inside"))
    response = app.test_client().post(
        "/v1/responses",
        headers={"Authorization": "Bearer inside"},
        json={"model": "gpt-test", "input": "hello"},
    )
    assert response.status_code == 503
    assert response.json["error"]["code"] == "server_error"


def test_responses_api_preserves_function_call_output():
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "create_post", "arguments": '{"content":"hello"}', "status": "completed"}

    class ToolRouter:
        def complete(self, payload):
            return DirectProviderResult("", "gpt-test", {}, output=[call])

    app = create_app(router=ToolRouter(), config=SimpleNamespace(internal_token="inside"))
    response = app.test_client().post("/v1/responses", headers={"Authorization": "Bearer inside"}, json={"model": "gpt-test", "input": "post"})

    assert response.status_code == 200
    assert response.json["output"] == [call]


def test_chat_completions_api_translates_function_call_to_tool_calls():
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "create_post", "arguments": '{"content":"hello"}', "status": "completed"}

    class ToolRouter:
        def complete(self, payload):
            return DirectProviderResult("", "gpt-test", {}, output=[call])

    app = create_app(router=ToolRouter(), config=SimpleNamespace(internal_token="inside"))
    response = app.test_client().post("/v1/chat/completions", headers={"Authorization": "Bearer inside"}, json={"model": "gpt-test", "messages": [{"role": "user", "content": "post"}]})

    choice = response.json["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"] == [{"id": "call_1", "type": "function", "function": {"name": "create_post", "arguments": '{"content":"hello"}'}}]


def test_upstream_bad_request_remains_http_400():
    class BadRequestRouter:
        def complete(self, payload):
            request = httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses")
            response = httpx.Response(400, request=request, json={"detail": "unsupported field"})
            raise httpx.HTTPStatusError("bad request", request=request, response=response)

    app = create_app(router=BadRequestRouter(), config=SimpleNamespace(internal_token="inside"))
    response = app.test_client().post(
        "/v1/responses",
        headers={"Authorization": "Bearer inside"},
        json={"model": "gpt-test", "input": "hello"},
    )

    assert response.status_code == 400
    assert response.json["error"]["code"] == "upstream_invalid_request"
