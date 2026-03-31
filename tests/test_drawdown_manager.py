"""드로다운 관리 모듈 테스트."""

from __future__ import annotations

import pytest

from src.portfolio.drawdown_manager import (
    DrawdownConfig,
    DrawdownState,
    StopLossResult,
    apply_drawdown_reduction,
    calculate_atr,
    compute_stop_loss,
)


class TestCalculateATR:
    """ATR 계산 테스트."""

    def test_basic_atr(self):
        highs = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
                 21, 22, 23, 24, 25, 26]
        lows = [9, 10, 11, 12, 13, 14, 15, 16, 17, 18,
                19, 20, 21, 22, 23, 24]
        closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19,
                  20, 21, 22, 23, 24, 25]
        result = calculate_atr(highs, lows, closes, period=14)
        assert result is not None
        assert result > 0

    def test_insufficient_data_returns_none(self):
        result = calculate_atr([10, 11], [9, 10], [10, 11], period=14)
        assert result is None

    def test_flat_market(self):
        n = 20
        highs = [100.5] * n
        lows = [99.5] * n
        closes = [100.0] * n
        result = calculate_atr(highs, lows, closes, period=14)
        assert result is not None
        assert result == pytest.approx(1.0, abs=0.01)

    def test_mismatched_lengths_returns_none(self):
        result = calculate_atr([10, 11, 12], [9, 10], [10, 11, 12])
        assert result is None


class TestComputeStopLoss:
    """손절가 계산 테스트."""

    def _make_ohlc(self, n=20, base=100.0):
        highs = [base + 1 + i * 0.1 for i in range(n)]
        lows = [base - 1 + i * 0.1 for i in range(n)]
        closes = [base + i * 0.1 for i in range(n)]
        return highs, lows, closes

    def test_ai_stop_preferred_when_reasonable(self):
        highs, lows, closes = self._make_ohlc()
        result = compute_stop_loss(
            ticker="AAPL",
            entry_price=100.0,
            ai_stop_loss=92.0,  # 8% below → reasonable
            highs=highs, lows=lows, closes=closes,
        )
        assert result.stop_type == "ai"
        assert result.stop_price == 92.0

    def test_atr_fallback_when_no_ai(self):
        highs, lows, closes = self._make_ohlc()
        result = compute_stop_loss(
            ticker="MSFT",
            entry_price=100.0,
            ai_stop_loss=None,
            highs=highs, lows=lows, closes=closes,
        )
        assert result.stop_type == "atr"
        assert result.stop_price < 100.0
        assert result.atr_value is not None

    def test_unreasonable_ai_stop_uses_atr(self):
        highs, lows, closes = self._make_ohlc()
        # 50% below → unreasonable
        result = compute_stop_loss(
            ticker="GOOG",
            entry_price=100.0,
            ai_stop_loss=50.0,
            highs=highs, lows=lows, closes=closes,
        )
        assert result.stop_type == "atr"

    def test_ai_stop_too_close_uses_atr(self):
        highs, lows, closes = self._make_ohlc()
        # 1% below → too close
        result = compute_stop_loss(
            ticker="AMZN",
            entry_price=100.0,
            ai_stop_loss=99.0,
            highs=highs, lows=lows, closes=closes,
        )
        assert result.stop_type == "atr"

    def test_result_is_frozen(self):
        highs, lows, closes = self._make_ohlc()
        result = compute_stop_loss(
            "AAPL", 100.0, None, highs, lows, closes,
        )
        with pytest.raises(AttributeError):
            result.stop_price = 50.0  # type: ignore[misc]

    def test_insufficient_data_fallback(self):
        result = compute_stop_loss(
            "AAPL", 100.0, None,
            highs=[101], lows=[99], closes=[100],
        )
        assert result.stop_price == pytest.approx(90.0, abs=0.1)


class TestApplyDrawdownReduction:
    """드로다운 축소 테스트."""

    def test_no_trigger_no_change(self):
        weights = {"AAPL": 0.3, "MSFT": 0.2}
        state = DrawdownState(
            peak_value=1.0, current_value=0.95,
            drawdown_pct=0.05, is_triggered=False,
            exposure_multiplier=1.0,
        )
        result = apply_drawdown_reduction(weights, state)
        assert result["AAPL"] == pytest.approx(0.3)

    def test_triggered_halves_weights(self):
        weights = {"AAPL": 0.4, "MSFT": 0.3}
        state = DrawdownState(
            peak_value=1.0, current_value=0.88,
            drawdown_pct=0.12, is_triggered=True,
            exposure_multiplier=0.5,
        )
        result = apply_drawdown_reduction(weights, state)
        assert result["AAPL"] == pytest.approx(0.2, abs=1e-4)
        assert result["MSFT"] == pytest.approx(0.15, abs=1e-4)

    def test_returns_new_dict(self):
        weights = {"AAPL": 0.5}
        state = DrawdownState(1.0, 1.0, 0.0, False, 1.0)
        result = apply_drawdown_reduction(weights, state)
        assert result is not weights


class TestDrawdownConfig:
    """DrawdownConfig frozen 검증."""

    def test_frozen(self):
        config = DrawdownConfig()
        with pytest.raises(AttributeError):
            config.atr_period = 20  # type: ignore[misc]

    def test_defaults(self):
        config = DrawdownConfig()
        assert config.portfolio_trailing_stop_pct == 0.10
        assert config.portfolio_reduction_factor == 0.50
        assert config.atr_stop_multiplier == 2.0
        assert config.atr_period == 14
