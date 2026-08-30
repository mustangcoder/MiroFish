"""按配置协议创建 CAMEL/OASIS 模型后端。"""

import asyncio
import time
from types import SimpleNamespace

from camel.models import BaseModelBackend, ModelFactory
from camel.types import ModelPlatformType
from camel.utils import OpenAITokenCounter
from openai.types.chat import ChatCompletion

from app.services.protocols.base import TextGenerationRequest
from app.services.protocols.factory import create_text_client


def describe_backend(protocol: str) -> str:
    values = {
        "openai_chat_completions": "openai-compatible-model",
        "anthropic_messages": "anthropic",
        "openai_responses": "protocol-bridge",
    }
    if protocol not in values:
        raise ValueError("模拟只支持文本生成协议")
    return values[protocol]


class ResponsesModelBackend(BaseModelBackend):
    def __init__(self, model_type, api_key, url, timeout=None):
        super().__init__(model_type=model_type, api_key=api_key, url=url, timeout=timeout)
        self._client = create_text_client(
            SimpleNamespace(protocol="openai_responses", base_url=url),
            api_key,
        )
        self._token_counter_instance = OpenAITokenCounter(self.model_type)

    @property
    def token_counter(self):
        return self._token_counter_instance

    @staticmethod
    def _responses_input(messages):
        values = []
        for message in messages:
            if message.get("role") == "tool":
                values.append({
                    "type": "function_call_output",
                    "call_id": message.get("tool_call_id"),
                    "output": message.get("content", ""),
                })
                continue
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                for tool_call in tool_calls:
                    values.append({
                        "type": "function_call",
                        "call_id": tool_call.get("id"),
                        "name": tool_call["function"]["name"],
                        "arguments": tool_call["function"].get("arguments", "{}"),
                    })
                continue
            values.append({"role": message.get("role"), "content": message.get("content", "")})
        return values

    def _run(self, messages, response_format=None, tools=None):
        result = self._client.generate(TextGenerationRequest(
            model=str(self.model_type),
            messages=self._responses_input(self.preprocess_messages(messages)),
            max_output_tokens=self.model_config_dict.get("max_tokens"),
            tools=tools,
        ))
        response = result.raw
        tool_calls = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) == "function_call":
                tool_calls.append({
                    "id": getattr(item, "call_id", None) or getattr(item, "id", ""),
                    "type": "function",
                    "function": {
                        "name": item.name,
                        "arguments": item.arguments,
                    },
                })
        usage = getattr(response, "usage", None)
        return ChatCompletion.model_validate({
            "id": getattr(response, "id", "response"),
            "created": int(time.time()),
            "model": getattr(response, "model", str(self.model_type)),
            "object": "chat.completion",
            "choices": [{
                "index": 0,
                "finish_reason": "tool_calls" if tool_calls else "stop",
                "message": {
                    "role": "assistant",
                    "content": result.text or None,
                    "tool_calls": tool_calls or None,
                },
            }],
            "usage": {
                "prompt_tokens": getattr(usage, "input_tokens", 0),
                "completion_tokens": getattr(usage, "output_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
            },
        })

    async def _arun(self, messages, response_format=None, tools=None):
        return await asyncio.to_thread(self._run, messages, response_format, tools)


def create_simulation_model(api_key: str, base_url: str, model: str, protocol: str, timeout=None):
    backend = describe_backend(protocol)
    if backend == "protocol-bridge":
        return ResponsesModelBackend(model, api_key, base_url, timeout)
    platform = (
        ModelPlatformType.ANTHROPIC
        if backend == "anthropic"
        else ModelPlatformType.OPENAI_COMPATIBLE_MODEL
    )
    return ModelFactory.create(
        model_platform=platform,
        model_type=model,
        api_key=api_key,
        url=base_url or None,
        timeout=timeout,
    )
