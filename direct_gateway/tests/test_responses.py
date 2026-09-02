import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import httpx
from types import SimpleNamespace

from app.messages import build_responses_payload
from app.schema import normalize_output_schema
from app.responses_client import ProviderResponseError, ResponsesClient, parse_responses_sse


def test_build_payload_preserves_conversation_and_separates_instructions():
    request = {"messages": [{"role": "system", "content": "S"}, {"role": "developer", "content": "D"}, {"role": "user", "content": "U"}, {"role": "assistant", "content": "A"}], "response_format": {"type": "text"}}
    payload = build_responses_payload(request, "gpt-test")
    assert payload["instructions"] == "S\n\nD"
    assert [item["role"] for item in payload["input"]] == ["user", "assistant"]
    assert payload["store"] is False and payload["stream"] is True
    assert "temperature" not in payload


def test_json_object_uses_instruction_instead_of_unsupported_format():
    payload = build_responses_payload(
        {"messages": [{"role": "user", "content": "Return data"}], "response_format": {"type": "json_object"}},
        "gpt-test",
    )
    assert "text" not in payload
    assert "valid JSON object" in payload["instructions"]


def test_schema_is_strict_without_mutating_input():
    schema = {"type": "object", "properties": {"name": {"type": "string", "default": "x"}, "nested": {"type": "object", "properties": {"n": {"type": "integer"}}}}}
    result = normalize_output_schema(schema)
    assert result["additionalProperties"] is False
    assert result["required"] == ["name", "nested"]
    assert result["properties"]["nested"]["required"] == ["n"]
    assert "default" not in result["properties"]["name"]
    assert "default" in schema["properties"]["name"]


def test_sse_collects_text_and_usage_but_not_reasoning():
    lines = [": ping", "data: " + json.dumps({"type": "response.reasoning_text.delta", "delta": "secret"}), "data: " + json.dumps({"type": "response.output_text.delta", "delta": "hel"}), "data: " + json.dumps({"type": "response.output_text.delta", "delta": "lo"}), "data: " + json.dumps({"type": "response.completed", "response": {"model": "gpt", "usage": {"input_tokens": 1}}})]
    result = parse_responses_sse(lines)
    assert result.content == "hello" and result.model == "gpt"
    assert "secret" not in result.content


def test_sse_rejects_failed_or_incomplete_response():
    with pytest.raises(RuntimeError, match="provider_failed"):
        parse_responses_sse(["data: " + json.dumps({"type": "response.failed"})])
    with pytest.raises(RuntimeError, match="incomplete"):
        parse_responses_sse(["data: " + json.dumps({"type": "response.output_text.delta", "delta": "x"})])


def test_client_refreshes_once_after_401():
    calls = []
    completed = "\n".join([
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": "OK"}),
        "data: " + json.dumps({"type": "response.completed", "response": {"model": "gpt", "usage": {}}}),
    ])
    def handler(request):
        calls.append(request.headers["Authorization"])
        if len(calls) == 1:
            return httpx.Response(401, json={"error": "expired"})
        return httpx.Response(200, text=completed)
    class Manager:
        def __init__(self): self.refreshes = 0
        def fresh(self): return SimpleNamespace(access_token="new" if self.refreshes else "old"), {"account_id": "acct", "residency": None}
        def force_refresh(self): self.refreshes += 1
    manager = Manager()
    client = ResponsesClient(endpoint="https://example.test/responses", model="gpt", token_manager=manager, http=httpx.Client(transport=httpx.MockTransport(handler)))
    result = client.complete({"messages": [{"role": "user", "content": "hi"}]})
    assert result.content == "OK"
    assert manager.refreshes == 1
    assert len(calls) == 2


def test_sse_failed_event_preserves_provider_error_details():
    event = {"type": "response.failed", "response": {"error": {"code": "server_error", "message": "temporarily unavailable"}}}
    with pytest.raises(ProviderResponseError) as captured:
        parse_responses_sse(["data: " + json.dumps(event)])
    assert captured.value.code == "server_error"
    assert captured.value.retryable is True
    assert "temporarily unavailable" in str(captured.value)


def test_client_retries_transient_sse_failure_with_backoff():
    calls, sleeps = [], []
    failed = "data: " + json.dumps({"type": "response.failed", "response": {"error": {"code": "server_error", "message": "busy"}}})
    completed = "\n".join([
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": "OK"}),
        "data: " + json.dumps({"type": "response.completed", "response": {"model": "gpt", "usage": {}}}),
    ])

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=failed if len(calls) < 3 else completed)

    class Manager:
        def fresh(self): return SimpleNamespace(access_token="token"), {"account_id": "acct", "residency": None}

    client = ResponsesClient(endpoint="https://example.test/responses", model="gpt", token_manager=Manager(), http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=sleeps.append)
    assert client.complete({"messages": [{"role": "user", "content": "hi"}]}).content == "OK"
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


def test_client_does_not_retry_deterministic_sse_failure():
    calls = []
    failed = "data: " + json.dumps({"type": "response.failed", "response": {"error": {"code": "invalid_request_error", "message": "bad input"}}})

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=failed)

    class Manager:
        def fresh(self): return SimpleNamespace(access_token="token"), {"account_id": "acct", "residency": None}

    client = ResponsesClient(endpoint="https://example.test/responses", model="gpt", token_manager=Manager(), http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
    with pytest.raises(ProviderResponseError) as captured:
        client.complete({"messages": [{"role": "user", "content": "hi"}]})
    assert captured.value.retryable is False
    assert len(calls) == 1


def test_client_retries_incomplete_sse_stream():
    calls = []
    incomplete = "data: " + json.dumps({"type": "response.output_text.delta", "delta": "partial"})
    completed = "\n".join([
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": "OK"}),
        "data: " + json.dumps({"type": "response.completed", "response": {"model": "gpt", "usage": {}}}),
    ])

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=incomplete if len(calls) == 1 else completed)

    class Manager:
        def fresh(self): return SimpleNamespace(access_token="token"), {"account_id": "acct", "residency": None}

    client = ResponsesClient(endpoint="https://example.test/responses", model="gpt", token_manager=Manager(), http=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda _: None)
    assert client.complete({"messages": [{"role": "user", "content": "hi"}]}).content == "OK"
    assert len(calls) == 2


def test_oauth_subscription_requests_respect_configured_limit():
    active = 0
    maximum_active = 0
    lock = threading.Lock()
    completed = "\n".join([
        "data: " + json.dumps({"type": "response.output_text.delta", "delta": "OK"}),
        "data: " + json.dumps({"type": "response.completed", "response": {"model": "gpt", "usage": {}}}),
    ])

    def handler(request):
        nonlocal active, maximum_active
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return httpx.Response(200, text=completed)

    class Manager:
        def fresh(self): return SimpleNamespace(access_token="token"), {"account_id": "acct", "residency": None}

    client = ResponsesClient(endpoint="https://example.test/responses", model="gpt", token_manager=Manager(), http=httpx.Client(transport=httpx.MockTransport(handler)), max_concurrency=1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: client.complete({"messages": [{"role": "user", "content": "hi"}]}), range(2)))

    assert [result.content for result in results] == ["OK", "OK"]
    assert maximum_active == 1


def test_sse_accepts_function_call_as_a_complete_response():
    call = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "create_post",
        "arguments": '{"content":"hello"}',
        "status": "completed",
    }
    lines = ["data: " + json.dumps({
        "type": "response.completed",
        "response": {"model": "gpt", "usage": {}, "output": [call]},
    })]

    result = parse_responses_sse(lines)

    assert result.content == ""
    assert result.output == [call]


def test_sse_collects_streamed_function_call_output_item():
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "create_post", "arguments": '{"content":"hello"}', "status": "completed"}
    lines = [
        "data: " + json.dumps({"type": "response.output_item.done", "item": call}),
        "data: " + json.dumps({"type": "response.completed", "response": {"model": "gpt", "usage": {}}}),
    ]

    result = parse_responses_sse(lines)

    assert result.output == [call]


def test_native_tool_history_round_trips_to_codex_payload():
    payload = build_responses_payload({"messages": [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "create_post", "arguments": '{"content":"hello"}'}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "done"},
    ]}, "gpt-test")

    assert payload["input"] == [
        {"type": "function_call", "call_id": "call_1", "name": "create_post", "arguments": '{"content":"hello"}'},
        {"type": "function_call_output", "call_id": "call_1", "output": "done"},
    ]


def test_chatgpt_codex_payload_does_not_forward_unsupported_truncation():
    payload = build_responses_payload(
        {"messages": [{"role": "user", "content": "hi"}], "truncation": "auto"},
        "gpt-test",
    )
    assert "truncation" not in payload


def test_context_length_exceeded_is_not_retryable():
    error = ProviderResponseError("context_length_exceeded", "too long")
    assert error.retryable is False
    assert error.status_code == 400
