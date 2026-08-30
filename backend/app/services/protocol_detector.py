"""Advisory protocol detection for unsaved provider connections."""

from __future__ import annotations

import httpx

from ..models.model_config import APIProtocol, ModelCapability, ProviderVendor
from .provider_catalog import get_provider_spec, protocol_capability


class ProtocolDetector:
    def __init__(self, request=None, timeout=8):
        if request is None:
            client = httpx.Client(timeout=timeout, trust_env=False)
            request = client.request
        self.request = request

    @staticmethod
    def _model_ids(response):
        try:
            body = response.json()
        except Exception:
            return []
        return [str(item.get("id", "")) for item in body.get("data", []) if isinstance(item, dict)]

    @staticmethod
    def _is_embedding(model_id):
        value = model_id.lower()
        return "embed" in value or "embedding" in value

    @staticmethod
    def _endpoint_exists(response):
        text = (getattr(response, "text", "") or "").lower()
        return response.status_code not in {404, 405} and "unexpected endpoint" not in text

    def detect(self, data):
        vendor = ProviderVendor(data["vendor"])
        candidates = get_provider_spec(vendor).protocols if vendor != ProviderVendor.CUSTOM else tuple(APIProtocol)
        base_url = data["base_url"].rstrip("/")
        api_key = data.get("api_key", "")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["x-api-key"] = api_key
        try:
            models_response = self.request("GET", f"{base_url}/models", headers=headers)
            model_ids = self._model_ids(models_response) if models_response.status_code < 400 else []
        except Exception:
            model_ids = []
        has_embedding = any(self._is_embedding(item) for item in model_ids)
        has_text = any(not self._is_embedding(item) for item in model_ids)
        results = []
        for protocol in candidates:
            passed = False
            error_code = None
            try:
                if protocol == APIProtocol.OPENAI_EMBEDDINGS and has_embedding:
                    passed = True
                elif protocol == APIProtocol.OPENAI_CHAT_COMPLETIONS and has_text:
                    passed = True
                else:
                    path = {
                        APIProtocol.OPENAI_RESPONSES: "/responses",
                        APIProtocol.OPENAI_CHAT_COMPLETIONS: "/chat/completions",
                        APIProtocol.ANTHROPIC_MESSAGES: "/messages",
                        APIProtocol.OPENAI_EMBEDDINGS: "/embeddings",
                    }[protocol]
                    payload = {"model": "__mirofish_protocol_probe__"}
                    if protocol == APIProtocol.OPENAI_RESPONSES:
                        payload["input"] = "probe"
                    elif protocol == APIProtocol.OPENAI_CHAT_COMPLETIONS:
                        payload["messages"] = [{"role": "user", "content": "probe"}]
                    elif protocol == APIProtocol.ANTHROPIC_MESSAGES:
                        payload.update({"messages": [{"role": "user", "content": "probe"}], "max_tokens": 1})
                    else:
                        payload["input"] = ["probe"]
                    response = self.request("POST", f"{base_url}{path}", headers=headers, json=payload)
                    passed = self._endpoint_exists(response)
                    if not passed:
                        error_code = "endpoint_not_found"
            except Exception as error:
                error_code = type(error).__name__
            results.append({
                "protocol": protocol.value,
                "capability": protocol_capability(protocol).value,
                "source": "detected",
                "verification_status": "passed" if passed else "failed",
                "error_code": error_code,
            })
        return results
