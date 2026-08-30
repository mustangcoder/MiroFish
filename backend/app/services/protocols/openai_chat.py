"""OpenAI Chat Completions API 适配器。"""

from .base import TextGenerationRequest, TextGenerationResult


class OpenAIChatCompletionsClient:
    def __init__(self, client):
        self.client = client

    def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        kwargs = {"model": request.model, "messages": request.messages}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            kwargs["max_tokens"] = request.max_output_tokens
        if request.response_format:
            kwargs["response_format"] = request.response_format
        if request.tools:
            kwargs["tools"] = request.tools
        response = self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        return TextGenerationResult(
            text=choice.message.content or "",
            finish_reason=getattr(choice, "finish_reason", None),
            model=getattr(response, "model", None),
            usage=getattr(response, "usage", None),
            request_id=getattr(response, "_request_id", None),
            raw=response,
        )
