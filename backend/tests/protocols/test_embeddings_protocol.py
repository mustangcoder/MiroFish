from types import SimpleNamespace

import pytest

from app.services.protocols.openai_embeddings import OpenAIEmbeddingsClient


def test_embeddings_adapter_returns_vectors_and_model():
    calls = []
    response = SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.1, 0.2]), SimpleNamespace(embedding=[0.3, 0.4])],
        model="embed-model",
        usage=None,
        _request_id="req_embed",
    )
    sdk = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or response))

    result = OpenAIEmbeddingsClient(sdk).embed(["one", "two"], "embed-model")

    assert result.vectors == [[0.1, 0.2], [0.3, 0.4]]
    assert result.model == "embed-model"
    assert calls == [{"model": "embed-model", "input": ["one", "two"]}]


def test_embeddings_adapter_rejects_empty_vectors():
    response = SimpleNamespace(data=[SimpleNamespace(embedding=[])], model="embed-model")
    sdk = SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: response))

    with pytest.raises(ValueError, match="向量为空"):
        OpenAIEmbeddingsClient(sdk).embed(["one"], "embed-model")
