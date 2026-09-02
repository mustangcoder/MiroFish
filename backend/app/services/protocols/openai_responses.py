"""OpenAI Responses API 适配器。"""

from .base import TextGenerationRequest, TextGenerationResult


class OpenAIResponsesClient:
    def __init__(self, client):
        self.client = client

    def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        kwargs = {"model": request.model, "input": request.messages}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_output_tokens"] = request.max_output_tokens
        if request.response_format:
            kwargs["text"] = {"format": request.response_format}
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "parameters": tool["function"].get("parameters", {}),
                }
                for tool in request.tools
                if tool.get("type") == "function"
            ]
        if request.truncation:
            kwargs["truncation"] = request.truncation
        response = self.client.responses.create(**kwargs)
        return TextGenerationResult(
            text=response.output_text or "",
            finish_reason=getattr(response, "status", None),
            model=getattr(response, "model", None),
            usage=getattr(response, "usage", None),
            request_id=getattr(response, "_request_id", None),
            raw=response,
        )
