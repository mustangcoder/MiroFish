import pytest

from app.provider import CircuitOpenError, ProviderRouter


def test_transient_provider_failures_do_not_permanently_open_circuit():
    class Direct:
        def __init__(self):
            self.calls = 0

        def complete(self, request):
            self.calls += 1
            if self.calls <= 3:
                raise RuntimeError("provider_failed")
            return "ok"

    direct = Direct()
    router = ProviderRouter(direct)

    for _ in range(3):
        with pytest.raises(RuntimeError, match="provider_failed"):
            router.complete({})

    assert router.complete({}) == "ok"


def test_rate_limit_circuit_recovers_after_cooldown():
    now = [100.0]

    class RateLimitError(RuntimeError):
        status_code = 429

    class Direct:
        def __init__(self):
            self.calls = 0

        def complete(self, request):
            self.calls += 1
            if self.calls == 1:
                raise RateLimitError("limited")
            return "ok"

    router = ProviderRouter(Direct(), cooldown_seconds=30, clock=lambda: now[0])

    with pytest.raises(RateLimitError):
        router.complete({})
    with pytest.raises(CircuitOpenError):
        router.complete({})

    now[0] += 31
    assert router.complete({}) == "ok"
