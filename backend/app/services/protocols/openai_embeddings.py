"""OpenAI-compatible Embeddings API 适配器。"""

from .base import EmbeddingResult


class OpenAIEmbeddingsClient:
    def __init__(self, client):
        self.client = client

    def embed(self, inputs: list[str], model: str, dimensions: int | None = None) -> EmbeddingResult:
        kwargs = {"model": model, "input": inputs}
        if dimensions is not None:
            kwargs["dimensions"] = dimensions
        response = self.client.embeddings.create(**kwargs)
        vectors = [list(item.embedding) for item in response.data]
        if len(vectors) != len(inputs) or any(not vector for vector in vectors):
            raise ValueError("Embedding 返回向量为空或数量不匹配")
        return EmbeddingResult(
            vectors=vectors,
            model=getattr(response, "model", None),
            usage=getattr(response, "usage", None),
            request_id=getattr(response, "_request_id", None),
            raw=response,
        )
