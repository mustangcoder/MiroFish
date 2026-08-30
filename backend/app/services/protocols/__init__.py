"""模型 HTTP 协议适配器。"""

from .base import EmbeddingResult, TextGenerationRequest, TextGenerationResult
from .factory import create_embedding_client, create_text_client

__all__ = [
    "EmbeddingResult",
    "TextGenerationRequest",
    "TextGenerationResult",
    "create_embedding_client",
    "create_text_client",
]
