"""턴오버 관리 모듈 테스트."""

from __future__ import annotations

import pytest

from src.portfolio.turnover import (
    TurnoverConfig,
    TurnoverStats,
    apply_hold_rules,
    calculate_turnover,
)


class TestCalculateTurnover:
    """턴오버 계산 테스트."""

    def test_no_change_zero_turnover(self):
        weights = {"A": 0.30, "B": 0.30}
        result = calculate_turnover(weights, weights)
        assert result.daily_turnover == 0.0
        assert result.trade_count == 0

    def test_full_rotation(self):
        old = {"A": 0.50, "B": 0.50}
        new = {"C": 0.50, "D": 0.50}
        result = calculate_turnover(new, old)
        # |0-0.5|+|0-0.5|+|0.5-0|+|0.5-0| = 2.0, / 2 = 1.0
        assert result.daily_turnover == pytest.approx(1.0, abs=0.01)
        assert len(result.buys) == 2
        assert len(result.sells) == 2

    def test_partial_rebalance(self):
        old = {"A": 0.40, "B": 0.30, "C": 0.30}
        new = {"A": 0.50, "B": 0.30, "C": 0.20}
        result = calculate_turnover(new, old)
        # |0.5-0.4| + |0.3-0.3| + |0.2-0.3| = 0.2, / 2 = 0.1
        assert result.daily_turnover == pytest.approx(0.1, abs=0.01)

    def test_annualized_turnover(self):
        old = {"A": 0.50}
        new = {"A": 0.60}
        result = calculate_turnover(new, old)
        assert result.annualized_turnover == pytest.approx(
            result.daily_turnover * 252, abs=0.1
        )

    def test_excessive_turnover_warning(self):
        config = TurnoverConfig(annualized_warn_threshold=0.1)
        old = {"A": 0.50}
        new = {"B": 0.50}
        result = calculate_turnover(new, old, config)
        assert result.is_excessive is True
        assert result.warning_message is not None

    def test_empty_old_weights(self):
        new = {"A": 0.50}
        result = calculate_turnover(new, {})
        assert result.daily_turnover == pytest.approx(0.25, abs=0.01)
        assert "A" in result.buys

    def test_result_is_frozen(self):
        result = calculate_turnover({}, {})
        with pytest.raises(AttributeError):
            result.daily_turnover = 1.0  # type: ignore[misc]


class TestApplyHoldRules:
    """홀드 룰 테스트."""

    def test_hold_when_no_stop_and_good_score(self):
        old = {"A": 0.30, "B": 0.20, "C": 0.20}
        new = {"B": 0.50}  # A, C dropped
        scores = {"A": 7.0, "B": 8.0, "C": 6.0}  # C is bottom
        stop = {"A": False, "B": False, "C": False}
        result = apply_hold_rules(new, old, scores, stop)
        assert "A" in result  # held (good score, no stop)
        assert result["A"] == 0.30

    def test_sell_when_stop_triggered(self):
        old = {"A": 0.30}
        new = {}  # A dropped
        scores = {"A": 7.0}
        stop = {"A": True}  # stop triggered
        result = apply_hold_rules(new, old, scores, stop)
        assert result.get("A", 0.0) < 0.005  # sold

    def test_sell_when_score_bottom(self):
        old = {"A": 0.30, "B": 0.30, "C": 0.30}
        new = {}  # all dropped
        scores = {"A": 2.0, "B": 5.0, "C": 8.0}  # A is bottom
        stop = {"A": False, "B": False, "C": False}
        result = apply_hold_rules(new, old, scores, stop)
        assert result.get("A", 0.0) < 0.005  # bottom → sell allowed
        assert "B" in result  # not bottom → held
        assert "C" in result  # not bottom → held

    def test_empty_old_returns_new(self):
        new = {"A": 0.50}
        result = apply_hold_rules(new, {}, {}, {})
        assert result == new

    def test_new_buys_preserved(self):
        old = {"A": 0.30}
        new = {"A": 0.30, "B": 0.20}
        scores = {"A": 7.0, "B": 8.0}
        stop = {}
        result = apply_hold_rules(new, old, scores, stop)
        assert result["B"] == 0.20  # new buy preserved

    def test_hysteresis_small_weight_ignored(self):
        old = {"A": 0.003}  # below sell_threshold
        new = {}
        scores = {"A": 9.0}
        stop = {"A": False}
        result = apply_hold_rules(new, old, scores, stop)
        # old weight too small to hold
        assert result.get("A", 0.0) < 0.005
