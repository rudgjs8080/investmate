"""AI 공용 상수 및 레짐 분류 테스트."""

from __future__ import annotations

from src.ai.constants import (
    MAX_RECS_BY_REGIME,
    NON_TICKERS,
    VIX_CRISIS,
    VIX_HIGH_VOL,
    VIX_NORMAL,
)
from src.ai.regime import classify_regime


class TestConstants:
    def test_vix_thresholds_ordered(self):
        assert VIX_NORMAL < VIX_HIGH_VOL < VIX_CRISIS

    def test_non_tickers_contains_common(self):
        assert "BUY" in NON_TICKERS
        assert "SELL" in NON_TICKERS
        assert "VIX" in NON_TICKERS
        assert "RSI" in NON_TICKERS

    def test_non_tickers_excludes_real_tickers(self):
        assert "AAPL" not in NON_TICKERS
        assert "MSFT" not in NON_TICKERS
        assert "NVDA" not in NON_TICKERS

    def test_max_recs_by_regime(self):
        assert MAX_RECS_BY_REGIME["crisis"] < MAX_RECS_BY_REGIME["bear"]
        assert MAX_RECS_BY_REGIME["bear"] < MAX_RECS_BY_REGIME["range"]
        assert MAX_RECS_BY_REGIME["range"] < MAX_RECS_BY_REGIME["bull"]


class TestClassifyRegime:
    def test_crisis(self):
        assert classify_regime(35.0) == "crisis"

    def test_bear(self):
        assert classify_regime(27.0, sp_close=4000, sp_sma20=4200) == "bear"

    def test_bull(self):
        assert classify_regime(15.0, sp_close=5200, sp_sma20=5000) == "bull"

    def test_range_default(self):
        assert classify_regime(22.0, sp_close=5000, sp_sma20=5000) == "range"

    def test_none_vix(self):
        assert classify_regime(None) == "range"

    def test_crisis_overrides_sp(self):
        """VIX >= 30이면 S&P 위치와 무관하게 crisis."""
        assert classify_regime(32.0, sp_close=5500, sp_sma20=5000) == "crisis"

    def test_high_vix_no_sp_data(self):
        """S&P 데이터 없으면 bear 조건 미충족 → range."""
        assert classify_regime(27.0) == "range"

    def test_low_vix_no_sp_data(self):
        """S&P 데이터 없으면 bull 조건 미충족 → range."""
        assert classify_regime(15.0) == "range"
