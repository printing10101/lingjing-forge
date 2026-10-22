"""Synchronous callers must share the half-open probe limit with async callers."""

from datetime import datetime, timedelta

import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState
from app.core.exceptions import CircuitBreakerHalfOpenException

pytestmark = pytest.mark.unit


def half_open_breaker(*, fallback=None):
    breaker = CircuitBreaker(
        "sync-probe",
        CircuitBreakerConfig(failure_threshold=1, half_open_max_calls=1, success_threshold=2),
        fallback=fallback,
    )
    breaker.record_failure(RuntimeError("offline"))
    breaker._opened_at = datetime.now() - timedelta(seconds=60)
    assert breaker.state == CircuitState.HALF_OPEN
    return breaker


def test_sync_execute_rejects_when_probe_slot_is_occupied():
    breaker = half_open_breaker()
    breaker.try_acquire_half_open()
    called = []
    try:
        with pytest.raises(CircuitBreakerHalfOpenException):
            breaker.execute(lambda: called.append(True))
        assert called == []
        assert breaker._half_open_in_flight == 1
    finally:
        breaker.release_half_open()


def test_sync_execute_reserves_and_releases_its_probe_slot():
    breaker = half_open_breaker()

    def service():
        with pytest.raises(CircuitBreakerHalfOpenException):
            breaker.try_acquire_half_open()
        return "healthy"

    assert breaker.execute(service) == "healthy"
    assert breaker._half_open_in_flight == 0
    assert breaker.state == CircuitState.HALF_OPEN
    assert breaker.execute(lambda: "healthy") == "healthy"
    assert breaker.state == CircuitState.CLOSED


def test_sync_execute_releases_probe_on_cancellation():
    breaker = half_open_breaker()

    def cancelled():
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        breaker.execute(cancelled)
    assert breaker._half_open_in_flight == 0
    assert breaker.state == CircuitState.HALF_OPEN


def test_sync_fallback_preserves_failure_and_releases_probe():
    breaker = half_open_breaker(fallback=lambda exc: str(exc))

    def offline():
        raise RuntimeError("still offline")

    assert breaker.execute(offline) == "still offline"
    assert breaker._half_open_in_flight == 0
    assert breaker.state == CircuitState.OPEN
