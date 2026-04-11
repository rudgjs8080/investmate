"""Phase 9: 과거 hit_rate 기반 EV 디스카운트 테스트."""

from __future__ import annotations

from src.deepdive.forecast_evaluator import apply_hit_rate_discount


class TestApplyHitRateDiscount:
    def test_no_hit_rates_returns_original(self):
        ev = {"1M": 3.5, "3M": 7.2, "6M": 12.0}
        result = apply_hit_rate_discount(ev, {})
        assert result == ev

    def test_discount_applied(self):
        ev = {"1M": 10.0, "3M": 20.0}
        hit_rates = {"1M": 0.5, "3M": 0.8}
        result = apply_hit_rate_discount(ev, hit_rates)
        assert result["1M"] == 5.0  # 10 × 0.5
        assert result["3M"] == 16.0  # 20 × 0.8

    def test_floor_applied_to_low_hit_rate(self):
        ev = {"1M": 10.0}
        hit_rates = {"1M": 0.05}  # 매우 낮음
        result = apply_hit_rate_discount(ev, hit_rates, floor=0.30)
        assert result["1M"] == 3.0  # 10 × 0.30 (floor)

    def test_missing_horizon_untouched(self):
        ev = {"1M": 5.0, "3M": 10.0, "6M": 15.0}
        hit_rates = {"1M": 0.5}  # 3M/6M 없음
        result = apply_hit_rate_discount(ev, hit_rates)
        assert result["1M"] == 2.5
        assert result["3M"] == 10.0  # 원본 유지
        assert result["6M"] == 15.0

    def test_none_ev_preserved(self):
        ev = {"1M": None, "3M": 10.0}
        hit_rates = {"1M": 0.5, "3M": 0.6}
        result = apply_hit_rate_discount(ev, hit_rates)
        assert result["1M"] is None
        assert result["3M"] == 6.0

    def test_negative_ev_discounted(self):
        """음수 EV도 가중치 곱해짐 (손실이 작아짐)."""
        ev = {"1M": -10.0}
        hit_rates = {"1M": 0.5}
        result = apply_hit_rate_discount(ev, hit_rates)
        assert result["1M"] == -5.0
