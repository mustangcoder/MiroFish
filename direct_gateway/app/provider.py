from __future__ import annotations

import threading
import time
from typing import Any


class CircuitOpenError(RuntimeError):
    pass


class ProviderRouter:
    def __init__(self, direct: Any, fallback: Any | None = None, *, cooldown_seconds: float = 30, clock=time.monotonic) -> None:
        self.direct = direct
        self.fallback = fallback
        self._circuit_reason = None
        self._circuit_opened_at = None
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()

    def complete(self, request: dict):
        with self._lock:
            if self._circuit_reason:
                elapsed = self._clock() - self._circuit_opened_at
                if elapsed < self._cooldown_seconds:
                    raise CircuitOpenError(self._circuit_reason)
                self._circuit_reason = None
                self._circuit_opened_at = None
        try:
            result = self.direct.complete(request)
            with self._lock:
                self._circuit_reason = None
                self._circuit_opened_at = None
            return result
        except Exception as error:
            response = getattr(error, "response", None)
            status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
            with self._lock:
                if status in {401, 403}:
                    self._circuit_reason = "authentication_unavailable"
                    self._circuit_opened_at = self._clock()
                elif status == 429:
                    self._circuit_reason = "rate_limited"
                    self._circuit_opened_at = self._clock()
            if self.fallback is None:
                raise
            return self.fallback.complete(request)
