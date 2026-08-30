"""Resolve transport credentials without mixing user API keys and internal OAuth tokens."""

import os

from ..models.model_config import AuthType


def resolve_connection_credential(connection, stored_secret: str, environment=None) -> str:
    environment = environment or os.environ
    if AuthType(getattr(connection, "auth_type", AuthType.API_KEY)) == AuthType.OAUTH_GATEWAY:
        token = environment.get("DIRECT_GATEWAY_TOKEN", "")
        if not token:
            raise ValueError("OAuth Gateway 内部令牌未配置")
        return token
    return stored_secret
