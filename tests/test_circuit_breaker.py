"""서킷브레이커 테스트."""

from __future__ import annotations

from src.data.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_opens_after_threshold(self):
        cb = CircuitBreaker(fail_threshold=3, reset_seconds=60)
        for _ in range(3):
            cb.record_failure()
        assert cb.is_open

    def test_closed_below_threshold(self):
        cb = CircuitBreaker(fail_threshold=5, reset_seconds=60)
        cb.record_failure()
        cb.record_failure()
        assert not cb.is_open

    def test_resets_after_timeout(self):
        cb = CircuitBreaker(fail_threshold=2, reset_seconds=0)
        cb.record_failure()
        cb.record_failure()
        # reset_seconds=0이므로 즉시 리셋
        assert not cb.is_open

    def test_success_resets_counter(self):
        cb = CircuitBreaker(fail_threshold=5, reset_seconds=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failures == 0

    def test_status(self):
        cb = CircuitBreaker(fail_threshold=3, reset_seconds=60)
        status = cb.status()
        assert status["is_open"] is False
        assert status["failures"] == 0
        assert status["threshold"] == 3

    def test_status_after_failures(self):
        cb = CircuitBreaker(fail_threshold=2, reset_seconds=60)
        cb.record_failure()
        cb.record_failure()
        status = cb.status()
        assert status["failures"] == 2
