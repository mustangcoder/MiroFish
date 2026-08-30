import asyncio

from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.prompts.models import Message

from app.services.graphiti_protocol_client import GraphitiProtocolClient
from app.services.protocols.base import TextGenerationResult


class FakeTextClient:
    def generate(self, request):
        assert request.messages == [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ]
        return TextGenerationResult(text='{"nodes": []}', finish_reason="stop")


def test_graphiti_bridge_returns_parsed_json():
    client = GraphitiProtocolClient(LLMConfig(model="model", max_tokens=128), FakeTextClient())

    result = asyncio.run(client._generate_response([
        Message(role="system", content="system"),
        Message(role="user", content="user"),
    ]))

    assert result == {"nodes": []}
