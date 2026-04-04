"""범용 서킷브레이커 — 연속 실패 시 호출을 차단하여 시스템을 보호한다."""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)


class CircuitBreaker:
    """간이 서킷브레이커 — 연속 실패 시 호출 차단.

    fail_threshold회 연속 실패하면 OPEN 상태가 되어
    reset_seconds 동안 호출을 차단한다.
    """

    def __init__(self, fail_threshold: int = 5, reset_seconds: int = 60):
        self._failures = 0
        self._threshold = fail_threshold
        self._reset_at = 0.0
        self._reset_seconds = reset_seconds

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._reset_at = time.time() + self._reset_seconds
            logger.warning("서킷브레이커 OPEN: %d회 연속 실패", self._failures)

    def record_success(self) -> None:
        if self._failures > 0:
            self._failures = 0

    @property
    def is_open(self) -> bool:
        if self._failures < self._threshold:
            return False
        if time.time() >= self._reset_at:
            self._failures = 0
            return False
        return True

    def status(self) -> dict:
        """현재 서킷브레이커 상태를 반환한다."""
        return {
            "is_open": self.is_open,
            "failures": self._failures,
            "threshold": self._threshold,
            "resets_at": self._reset_at,
        }
