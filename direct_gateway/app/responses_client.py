from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

import httpx

from .messages import build_responses_payload

logger = logging.getLogger(__name__)


class ProviderResponseError(RuntimeError):
    """Structured failure reported inside an otherwise successful SSE response."""

    _NON_RETRYABLE_CODES = {
        "invalid_request_error",
        "invalid_prompt",
        "context_length_exceeded",
        "model_not_found",
        "permission_denied",
        "unsupported_value",
    }

    def __init__(self, code: str, message: str, *, retryable: bool | None = None) -> None:
        self.code = code or "provider_failed"
        self.retryable = (
            self.code not in self._NON_RETRYABLE_CODES
            if retryable is None
            else retryable
        )
        self.status_code = 503 if self.retryable else 400
        super().__init__(message or self.code)


@dataclass(frozen=True)
class DirectProviderResult:
    content: str
    model: str
    usage: dict | None
    provider: str = "chatgpt-direct-oauth"
    output: list[dict] | None = None


def parse_responses_sse(lines: Iterable[str]) -> DirectProviderResult:
    parts = []
    streamed_output = []
    completed = None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith(":") or not line.startswith("data:"):
            continue
        data_text = line[5:].strip()
        if data_text == "[DONE]":
            continue
        try:
            event = json.loads(data_text)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "response.output_text.delta":
            parts.append(str(event.get("delta", "")))
        elif kind == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "function_call":
                streamed_output.append(item)
        elif kind == "response.failed":
            response_error = (event.get("response") or {}).get("error") or event.get("error") or {}
            if not isinstance(response_error, dict):
                response_error = {"message": str(response_error)}
            raise ProviderResponseError(
                str(response_error.get("code") or response_error.get("type") or "provider_failed"),
                str(response_error.get("message") or "provider_failed"),
            )
        elif kind == "response.completed":
            completed = event.get("response") or {}
    if completed is None:
        raise ProviderResponseError("incomplete_response", "incomplete_response", retryable=True)
    content = "".join(parts)
    completed_output = [
        item for item in (completed.get("output") or [])
        if isinstance(item, dict) and item.get("type") in {"function_call"}
    ]
    output = []
    seen = set()
    for item in (*streamed_output, *completed_output):
        identity = item.get("call_id") or item.get("id") or json.dumps(item, sort_keys=True)
        if identity not in seen:
            seen.add(identity)
            output.append(item)
    if not content.strip() and not output:
        raise ProviderResponseError("empty_response", "empty_response", retryable=True)
    return DirectProviderResult(content, completed.get("model", "unknown"), completed.get("usage"), output=output)


class ResponsesClient:
    def __init__(self, *, endpoint: str, model: str, token_manager: Any, http: httpx.Client | None = None, timeout: int = 600, max_concurrency: int = 8, sleep=time.sleep) -> None:
        self.endpoint = endpoint
        self.model = model
        self.token_manager = token_manager
        self.http = http or httpx.Client(timeout=httpx.Timeout(timeout, connect=30))
        self._slots = threading.BoundedSemaphore(max(1, max_concurrency))
        self._sleep = sleep

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        if isinstance(error, ProviderResponseError):
            return error.retryable
        if isinstance(error, (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError, httpx.TimeoutException)):
            return True
        status = getattr(error, "status_code", None)
        if status is None:
            status = getattr(getattr(error, "response", None), "status_code", None)
        return status in {408, 409, 425, 429} or bool(status and status >= 500)

    @staticmethod
    def _retry_delay(error: Exception, attempt: int) -> float:
        response = getattr(error, "response", None)
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            try:
                return min(30.0, max(0.0, float(retry_after)))
            except ValueError:
                pass
        return 0.5 * (2 ** attempt)

    def complete(self, request: dict) -> DirectProviderResult:
        with self._slots:
            refreshed = False
            for attempt in range(4):
                tokens, metadata = self.token_manager.fresh()
                headers = {"Authorization": f"Bearer {tokens.access_token}", "ChatGPT-Account-Id": metadata["account_id"], "Accept": "text/event-stream", "Content-Type": "application/json", "originator": "mirofish-direct-oauth", "User-Agent": "mirofish-direct-oauth/0.1.0"}
                if metadata.get("residency"):
                    headers["x-openai-internal-codex-residency"] = metadata["residency"]
                try:
                    with self.http.stream("POST", self.endpoint, headers=headers, json=build_responses_payload(request, request.get("model") or self.model)) as response:
                        if response.status_code == 401 and not refreshed:
                            self.token_manager.force_refresh()
                            refreshed = True
                            continue
                        response.raise_for_status()
                        return parse_responses_sse(response.iter_lines())
                except Exception as error:
                    if attempt >= 3 or not self._is_retryable(error):
                        raise
                    delay = self._retry_delay(error, attempt)
                    logger.warning(
                        "retrying direct provider attempt=%s delay=%.1f error_type=%s status_code=%s provider_code=%s",
                        attempt + 2,
                        delay,
                        type(error).__name__,
                        getattr(error, "status_code", None),
                        getattr(error, "code", None),
                    )
                    self._sleep(delay)
            raise RuntimeError("direct provider attempts exhausted")
