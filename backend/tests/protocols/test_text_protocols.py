from types import SimpleNamespace

import pytest

from app.models.model_config import APIProtocol
from app.services.protocols.anthropic_messages import AnthropicMessagesClient
from app.services.protocols.base import TextGenerationRequest
from app.services.protocols.factory import create_text_client
from app.services.protocols.openai_chat import OpenAIChatCompletionsClient
from app.services.protocols.openai_responses import OpenAIResponsesClient


def request():
    return TextGenerationRequest(
        model="model-1",
        messages=[
            {"role": "system", "content": "System rule"},
            {"role": "user", "content": "Hello"},
        ],
        temperature=0.2,
        max_output_tokens=128,
    )


def test_responses_adapter_uses_output_text_and_request_id():
    calls = []
    response = SimpleNamespace(
        output_text="response text",
        model="gpt-test",
        status="completed",
        usage=SimpleNamespace(input_tokens=3, output_tokens=2),
        _request_id="req_responses",
    )
    sdk = SimpleNamespace(responses=SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or response))

    result = OpenAIResponsesClient(sdk).generate(request())

    assert result.text == "response text"
    assert result.request_id == "req_responses"
    assert calls[0]["input"] == request().messages
    assert calls[0]["max_output_tokens"] == 128


def test_chat_adapter_extracts_message_text():
    calls = []
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="chat text"), finish_reason="stop")],
        model="chat-model",
        usage=None,
        _request_id="req_chat",
    )
    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or response)))

    result = OpenAIChatCompletionsClient(sdk).generate(request())

    assert result.text == "chat text"
    assert result.finish_reason == "stop"
    assert calls[0]["messages"] == request().messages
    assert calls[0]["max_tokens"] == 128


def test_anthropic_adapter_moves_system_messages_and_extracts_text_blocks():
    calls = []
    response = SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking="hidden"), SimpleNamespace(type="text", text="anthropic text")],
        model="claude-test",
        stop_reason="end_turn",
        usage=None,
        _request_id="req_anthropic",
    )
    sdk = SimpleNamespace(messages=SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or response))

    result = AnthropicMessagesClient(sdk).generate(request())

    assert result.text == "anthropic text"
    assert calls[0]["system"] == "System rule"
    assert calls[0]["messages"] == [{"role": "user", "content": "Hello"}]
    assert calls[0]["max_tokens"] == 128


def test_text_factory_rejects_embedding_protocol():
    connection = SimpleNamespace(protocol=APIProtocol.OPENAI_EMBEDDINGS, base_url="http://example.test")

    with pytest.raises(ValueError, match="不是文本生成协议"):
        create_text_client(connection, "key")
