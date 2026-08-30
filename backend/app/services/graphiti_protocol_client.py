"""将项目协议适配器桥接到 Graphiti LLMClient。"""

import asyncio
import json
from typing import Any

from graphiti_core.llm_client.client import LLMClient as GraphitiBaseLLMClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.client import ModelSize
from graphiti_core.prompts.models import Message
from pydantic import BaseModel

from .protocols.base import TextGenerationRequest


class GraphitiProtocolClient(GraphitiBaseLLMClient):
    def __init__(self, config: LLMConfig, text_client):
        super().__init__(config)
        self.text_client = text_client

    async def _generate_response(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int = 8192,
        model_size: ModelSize = ModelSize.medium,
    ) -> dict[str, Any]:
        normalized = [
            {"role": message.role, "content": message.content}
            for message in messages
            if message.role in {"system", "user", "assistant"}
        ]
        response_format = {"type": "json_object"}
        if response_model is not None:
            response_format = {
                "type": "json_schema",
                "name": response_model.__name__,
                "schema": response_model.model_json_schema(),
            }
        result = await asyncio.to_thread(
            self.text_client.generate,
            TextGenerationRequest(
                model=self.model,
                messages=normalized,
                temperature=self.temperature,
                max_output_tokens=max_tokens,
                response_format=response_format,
            ),
        )
        return json.loads(result.text)
