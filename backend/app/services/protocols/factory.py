"""按连接协议创建模型客户端。"""

from openai import OpenAI

from ...models.model_config import APIProtocol
from ..provider_credentials import resolve_connection_credential
from .anthropic_messages import AnthropicMessagesClient
from .openai_chat import OpenAIChatCompletionsClient
from .openai_embeddings import OpenAIEmbeddingsClient
from .openai_responses import OpenAIResponsesClient


def create_text_client(connection, api_key: str):
    protocol = APIProtocol(connection.protocol)
    if protocol == APIProtocol.OPENAI_EMBEDDINGS:
        raise ValueError("连接协议不是文本生成协议")
    api_key = resolve_connection_credential(connection, api_key)
    if protocol == APIProtocol.ANTHROPIC_MESSAGES:
        from anthropic import Anthropic

        client = Anthropic(api_key=api_key or "local", base_url=connection.base_url)
        return AnthropicMessagesClient(client)
    client = OpenAI(api_key=api_key or "local", base_url=connection.base_url)
    if protocol == APIProtocol.OPENAI_RESPONSES:
        return OpenAIResponsesClient(client)
    return OpenAIChatCompletionsClient(client)


def create_embedding_client(connection, api_key: str):
    protocol = APIProtocol(connection.protocol)
    if protocol != APIProtocol.OPENAI_EMBEDDINGS:
        raise ValueError("连接协议不是 Embedding 协议")
    client = OpenAI(api_key=api_key or "local", base_url=connection.base_url)
    return OpenAIEmbeddingsClient(client)
