"""Anthropic Messages API 适配器。"""

from .base import TextGenerationRequest, TextGenerationResult


class AnthropicMessagesClient:
    def __init__(self, client):
        self.client = client

    def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        system_parts = []
        messages = []
        for message in request.messages:
            if message.get("role") in {"system", "developer"}:
                system_parts.append(str(message.get("content", "")))
            else:
                messages.append(message)
        kwargs = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_output_tokens or 4096,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.tools:
            kwargs["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"].get("parameters", {}),
                }
                for tool in request.tools
                if tool.get("type") == "function"
            ]
        response = self.client.messages.create(**kwargs)
        text = "".join(
            str(block.text)
            for block in response.content
            if getattr(block, "type", None) == "text"
        )
        return TextGenerationResult(
            text=text,
            finish_reason=getattr(response, "stop_reason", None),
            model=getattr(response, "model", None),
            usage=getattr(response, "usage", None),
            request_id=getattr(response, "_request_id", None),
            raw=response,
        )
