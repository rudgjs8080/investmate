"""스크리너 테스트."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.analysis.screener import (
    _passes_filter,
    _score_external,
    _score_momentum,
    _score_technical,
    _generate_reason,
)


def _make_latest(
    close: float = 100.0, volume: int = 500_000,
    rsi_14: float = 50.0, sma_120: float = 90.0,
) -> pd.Series:
    """테스트용 latest Series."""
    return pd.Series({
        "close": close, "volume": volume,
        "rsi_14": rsi_14, "sma_120": sma_120,
    })


def _make_df(days: int = 30, base: float = 100.0) -> pd.DataFrame:
    """테스트용 DataFrame."""
    dates = [date.today() - timedelta(days=days - i) for i in range(days)]
    np.random.seed(42)
    prices = base + np.cumsum(np.random.randn(days))
    return pd.DataFrame({
        "close": prices,
        "volume": [500_000] * days,
        "sma_5": prices,
        "sma_20": prices - 1,
        "sma_60": prices - 2,
    }, index=dates)


class TestFilter:
    def test_passes_normal(self):
        latest = _make_latest()
        df = _make_df()
        assert _passes_filter(latest, df) is True

    def test_low_volume_rejected(self):
        latest = _make_latest(volume=50_000)
        df = _make_df()
        assert _passes_filter(latest, df) is False

    def test_high_rsi_rejected(self):
        latest = _make_latest(rsi_14=75.0)
        df = _make_df()
        assert _passes_filter(latest, df) is False

    def test_below_sma120_rejected(self):
        latest = _make_latest(close=80.0, sma_120=100.0)
        df = _make_df()
        assert _passes_filter(latest, df) is False


class TestMomentum:
    def test_positive_momentum(self):
        df = _make_df(days=30, base=100.0)
        # 상승 추세 데이터
        df["close"] = range(100, 130)
        latest = pd.Series({
            "close": 129.0,
            "sma_5": 127.0, "sma_20": 120.0, "sma_60": 110.0,
            "volume": 500_000, "volume_sma_20": 400_000,
        })
        score = _score_momentum(df, latest)
        assert score >= 5.0

    def test_neutral_momentum(self):
        df = _make_df(days=30, base=100.0)
        latest = pd.Series({
            "close": 100.0,
            "sma_5": 100.0, "sma_20": 100.0, "sma_60": 100.0,
            "volume": 500_000,
        })
        score = _score_momentum(df, latest)
        assert 3.0 <= score <= 7.0

    def test_crisis_momentum_deep_drop(self):
        """VIX=35, ret=-20% → 평균회귀로 양수 점수."""
        df = _make_df(days=30, base=125.0)
        # iloc[-20]이 125.0이 되도록: 앞 11개 125, 뒤 19개 100
        df["close"] = [125.0] * 11 + [100.0] * 19
        latest = pd.Series({
            "close": 100.0,
            "sma_5": 100.0, "sma_20": 105.0, "sma_60": 115.0,
            "volume": 500_000,
        })
        score = _score_momentum(df, latest, vix=35.0)
        # ret_20d = (100-125)/125*100 = -20% → -ret/5 = +4.0 clamped to +2.0
        assert score > 5.0

    def test_crisis_momentum_bounce(self):
        """VIX=35, ret=+8% → 약한 양수 (데드캣 바운스 감쇄)."""
        df = _make_df(days=30, base=92.6)
        # iloc[-20]이 92.6이 되도록: 앞 11개 92.6, 뒤 19개 100
        df["close"] = [92.6] * 11 + [100.0] * 19
        latest = pd.Series({
            "close": 100.0,
            "sma_5": 100.0, "sma_20": 98.0, "sma_60": 95.0,
            "volume": 500_000,
        })
        score = _score_momentum(df, latest, vix=35.0)
        # ret_20d ≈ +8% → 8/10 = +0.8 (dampened) + SMA 정배열 +1.5
        assert 5.0 < score < 8.0

    def test_crisis_momentum_flat(self):
        """VIX=35, ret~0% → 모멘텀 기여 없음."""
        df = _make_df(days=30, base=100.0)
        df["close"] = [100.0] * 30
        latest = pd.Series({
            "close": 100.0,
            "sma_5": 100.0, "sma_20": 100.0, "sma_60": 100.0,
            "volume": 500_000,
        })
        score = _score_momentum(df, latest, vix=35.0)
        # 0% return → 횡보 → no contribution → base 5.0
        assert 4.5 <= score <= 5.5

    def test_crisis_momentum_moderate_drop(self):
        """VIX=35, ret=-5% → 충분히 깊지 않아 반전 없음."""
        df = _make_df(days=30, base=105.3)
        # iloc[-20]이 105.3이 되도록: 앞 11개 105.3, 뒤 19개 100
        df["close"] = [105.3] * 11 + [100.0] * 19
        latest = pd.Series({
            "close": 100.0,
            "sma_5": 100.0, "sma_20": 101.0, "sma_60": 103.0,
            "volume": 500_000,
        })
        score = _score_momentum(df, latest, vix=35.0)
        # ret_20d ≈ -5%, between -15 and +5 → 횡보 → no momentum contribution
        assert 4.0 <= score <= 6.0


class TestExternal:
    def test_base_score_from_market(self):
        score = _score_external(7, 0.0, None, None)
        assert score == 7.0

    def test_news_sentiment_positive(self):
        score = _score_external(5, 0.5, None, None)
        assert score == 6.0

    def test_news_sentiment_negative(self):
        score = _score_external(5, -0.5, None, None)
        assert score == 4.0

    def test_sector_momentum_boost(self):
        class MockSector:
            sector_name = "Energy"
        momentum = {"Energy": 8.0, "Technology": 3.0}
        score = _score_external(5, 0.0, momentum, MockSector())
        assert score > 5.0

    def test_sector_momentum_penalty(self):
        class MockSector:
            sector_name = "Technology"
        momentum = {"Energy": 8.0, "Technology": 2.0}
        score = _score_external(5, 0.0, momentum, MockSector())
        assert score < 5.0

    def test_clamp_to_range(self):
        score = _score_external(1, -1.0, None, None)
        assert score >= 1.0
        score2 = _score_external(10, 1.0, None, None)
        assert score2 <= 10.0


class TestGenerateReason:
    def test_high_tech_score(self):
        latest = pd.Series({"rsi_14": 30.0, "sma_5": 100, "sma_20": 95, "sma_60": 90, "macd_hist": 1.0, "close": 100})
        reason = _generate_reason("AAPL", 8.0, 5.0, 5.0, 5.0, latest)
        assert "AAPL" in reason
        assert "과매도" in reason or "RSI" in reason

    def test_includes_ticker(self):
        latest = pd.Series({"rsi_14": 50.0, "sma_5": 100, "sma_20": 95, "sma_60": 90, "macd_hist": 1.0, "close": 100})
        reason = _generate_reason("MSFT", 5.0, 5.0, 5.0, 5.0, latest)
        assert reason.startswith("MSFT:")

    def test_strong_momentum_includes_pct(self):
        latest = pd.Series({"rsi_14": 55.0, "sma_5": 110, "sma_20": 100, "sma_60": 95, "macd_hist": 0.5, "close": 110})
        reason = _generate_reason("TEST", 5.0, 5.0, 5.0, 9.0, latest)
        assert "%" in reason or "모멘텀" in reason


class TestDollarVolumeFilter:
    def test_low_dollar_volume_rejected(self):
        """달러 거래량 $500K 미만이면 필터링된다."""
        # close=5.0, volume=50_000 → $250K < $500K
        latest = _make_latest(close=5.0, volume=50_000)
        df = _make_df()
        assert _passes_filter(latest, df) is False

    def test_high_dollar_volume_passes(self):
        """달러 거래량 $500K 이상이면 통과한다."""
        # close=100.0, volume=500_000 → $50M > $500K
        latest = _make_latest(close=100.0, volume=500_000)
        df = _make_df()
        assert _passes_filter(latest, df) is True

    def test_borderline_dollar_volume_passes(self):
        """달러 거래량이 정확히 $500K이면 통과한다."""
        # close=5.0, volume=100_000 → $500K == $500K (not < 500K)
        # volume >= MIN_VOLUME(100K), sma_120=4.5 so close > sma_120
        latest = _make_latest(close=5.0, volume=100_000, sma_120=4.5)
        df = _make_df()
        assert _passes_filter(latest, df) is True
