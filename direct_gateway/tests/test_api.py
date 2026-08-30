from types import SimpleNamespace

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
