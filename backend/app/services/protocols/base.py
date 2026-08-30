"""协议中立的模型请求与结果。"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextGenerationRequest:
    model: str
    messages: list[dict[str, Any]]
    temperature: float | None = None
    max_output_tokens: int | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class TextGenerationResult:
    text: str
    finish_reason: str | None = None
    model: str | None = None
    usage: Any = None
    request_id: str | None = None
    raw: Any = None


@dataclass(frozen=True)
class EmbeddingResult:
    vectors: list[list[float]]
    model: str | None = None
    usage: Any = None
    request_id: str | None = None
    raw: Any = None
