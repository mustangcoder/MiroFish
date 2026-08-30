"""Test unsaved provider form values without persisting credentials."""

from types import SimpleNamespace
import time

from openai import OpenAI

from ..models.model_config import APIProtocol, AuthType
from .model_connection_tester import ModelConnectionTester
from .protocol_detector import ProtocolDetector


class DraftConnectionTester:
    def __init__(self, openai_factory=OpenAI, anthropic_factory=None, detector=None):
        self.openai_factory = openai_factory
        self.anthropic_factory = anthropic_factory
        self.detector = detector or ProtocolDetector()

    def test(self, data):
        auth_type = AuthType(data["auth_type"])
        if auth_type == AuthType.OAUTH_GATEWAY:
            raise ValueError("OAuth Gateway 请通过登录状态验证")
        if "protocol" not in data:
            protocols = self.detector.detect(data)
            passed = any(item["verification_status"] == "passed" for item in protocols)
            return {
                "status": "passed" if passed else "failed",
                "test_type": "protocol_detection",
                "latency_ms": 0,
                "protocols": protocols,
            }
        protocol = APIProtocol(data["protocol"])
        api_key = data.get("api_key") or "local"
        started = time.monotonic()
        try:
            if protocol == APIProtocol.ANTHROPIC_MESSAGES:
                factory = self.anthropic_factory
                if factory is None:
                    from anthropic import Anthropic
                    factory = Anthropic
                factory(api_key=api_key, base_url=data["base_url"]).models.list(limit=1)
            else:
                self.openai_factory(api_key=api_key, base_url=data["base_url"]).models.list()
            return {
                "status": "passed",
                "test_type": "model_list",
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        except Exception as error:
            return {
                "status": "failed",
                "test_type": "model_list",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error_code": ModelConnectionTester._error_code(error),
            }
