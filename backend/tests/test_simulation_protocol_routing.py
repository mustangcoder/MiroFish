import pytest

from scripts.protocol_model_backend import describe_backend


@pytest.mark.parametrize(
    "protocol,backend",
    [
        ("openai_chat_completions", "openai-compatible-model"),
        ("anthropic_messages", "anthropic"),
        ("openai_responses", "protocol-bridge"),
    ],
)
def test_simulation_backend_matches_protocol(protocol, backend):
    assert describe_backend(protocol) == backend


def test_embedding_protocol_cannot_run_simulation():
    with pytest.raises(ValueError, match="文本生成"):
        describe_backend("openai_embeddings")
