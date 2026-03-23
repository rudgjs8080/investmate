"""시그널 감지 테스트."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from src.analysis.signals import (
    calculate_composite_strength,
    detect_signals,
)
from src.data.schemas import SignalData


def _make_indicator_df(**overrides) -> pd.DataFrame:
    """2행짜리 지표 DataFrame을 생성한다 (어제, 오늘)."""
    yesterday = date.today() - timedelta(days=1)
    today = date.today()

    base = {
        "close": [100.0, 102.0],
        "sma_20": [98.0, 101.0],
        "sma_60": [100.0, 100.0],
        "rsi_14": [50.0, 50.0],
        "macd": [0.5, 0.5],
        "macd_signal": [0.3, 0.3],
        "bb_upper": [110.0, 110.0],
        "bb_lower": [90.0, 90.0],
    }
    base.update(overrides)

    df = pd.DataFrame(base, index=[yesterday, today])
    return df


class TestGoldenCross:
    """골든크로스 시그널 테스트."""

    def test_golden_cross_detected(self):
        """SMA20이 SMA60을 상향 돌파하면 골든크로스."""
        df = _make_indicator_df(
            sma_20=[99.0, 101.0],
            sma_60=[100.0, 100.0],
        )
        signals = detect_signals(df, stock_id=1)
        types = [s.signal_type for s in signals]
        assert "golden_cross" in types

    def test_no_golden_cross_when_already_above(self):
        """이미 SMA20 > SMA60이면 골든크로스 아님."""
        df = _make_indicator_df(
            sma_20=[101.0, 102.0],
            sma_60=[100.0, 100.0],
        )
        signals = detect_signals(df, stock_id=1)
        types = [s.signal_type for s in signals]
        assert "golden_cross" not in types


class TestDeathCross:
    """데드크로스 시그널 테스트."""

    def test_death_cross_detected(self):
        """SMA20이 SMA60을 하향 돌파하면 데드크로스."""
        df = _make_indicator_df(
            sma_20=[101.0, 99.0],
            sma_60=[100.0, 100.0],
        )
        signals = detect_signals(df, stock_id=1)
        types = [s.signal_type for s in signals]
        assert "death_cross" in types


class TestRSI:
    """RSI 시그널 테스트."""

    def test_rsi_oversold(self):
        """RSI < 30이면 과매도."""
        df = _make_indicator_df(rsi_14=[40.0, 25.0])
        signals = detect_signals(df, stock_id=1)
        types = [s.signal_type for s in signals]
        assert "rsi_oversold" in types

        oversold = [s for s in signals if s.signal_type == "rsi_oversold"][0]
        assert oversold.direction == "BUY"

    def test_rsi_overbought(self):
        """RSI > 70이면 과매수."""
        df = _make_indicator_df(rsi_14=[60.0, 75.0])
        signals = detect_signals(df, stock_id=1)
        types = [s.signal_type for s in signals]
        assert "rsi_overbought" in types

        overbought = [s for s in signals if s.signal_type == "rsi_overbought"][0]
        assert overbought.direction == "SELL"

    def test_rsi_normal_no_signal(self):
        """RSI가 30-70 사이면 시그널 없음."""
        df = _make_indicator_df(rsi_14=[50.0, 55.0])
        signals = detect_signals(df, stock_id=1)
        rsi_signals = [s for s in signals if "rsi" in s.signal_type]
        assert len(rsi_signals) == 0


class TestMACD:
    """MACD 시그널 테스트."""

    def test_macd_bullish(self):
        """MACD가 시그널선 상향 돌파."""
        df = _make_indicator_df(
            macd=[-0.1, 0.5],
            macd_signal=[0.0, 0.0],
        )
        signals = detect_signals(df, stock_id=1)
        types = [s.signal_type for s in signals]
        assert "macd_bullish" in types

    def test_macd_bearish(self):
        """MACD가 시그널선 하향 돌파."""
        df = _make_indicator_df(
            macd=[0.1, -0.5],
            macd_signal=[0.0, 0.0],
        )
        signals = detect_signals(df, stock_id=1)
        types = [s.signal_type for s in signals]
        assert "macd_bearish" in types


class TestBollingerBand:
    """볼린저 밴드 시그널 테스트."""

    def test_bb_lower_break(self):
        """종가가 하단 이탈."""
        df = _make_indicator_df(
            close=[95.0, 88.0],
            bb_lower=[90.0, 90.0],
        )
        signals = detect_signals(df, stock_id=1)
        types = [s.signal_type for s in signals]
        assert "bb_lower_break" in types

    def test_bb_upper_break(self):
        """종가가 상단 이탈."""
        df = _make_indicator_df(
            close=[105.0, 112.0],
            bb_upper=[110.0, 110.0],
        )
        signals = detect_signals(df, stock_id=1)
        types = [s.signal_type for s in signals]
        assert "bb_upper_break" in types


class TestCompositeStrength:
    """복합 강도 계산 테스트."""

    def test_empty_signals(self):
        assert calculate_composite_strength([]) == 0

    def test_single_signal(self):
        signals = [
            SignalData(
                date=date.today(), signal_type="golden_cross",
                direction="BUY", strength=8, description="test",
            ),
        ]
        result = calculate_composite_strength(signals)
        assert 1 <= result <= 10

    def test_multiple_signals(self):
        signals = [
            SignalData(
                date=date.today(), signal_type="golden_cross",
                direction="BUY", strength=8, description="test",
            ),
            SignalData(
                date=date.today(), signal_type="rsi_oversold",
                direction="BUY", strength=6, description="test",
            ),
        ]
        result = calculate_composite_strength(signals)
        assert 1 <= result <= 10


class TestRSIOversoldStrength:
    """RSI 과매도 strength 공식 테스트 (수정된 공식: (30-rsi)/3+5)."""

    def test_rsi_30_strength_is_5(self):
        """RSI=30 직전(29.9)에서 strength는 5."""
        df = _make_indicator_df(rsi_14=[40.0, 29.0])
        signals = detect_signals(df, stock_id=1)
        oversold = [s for s in signals if s.signal_type == "rsi_oversold"]
        assert len(oversold) == 1
        # RSI=29: (30-29)/3+5 = 5.33 → int → 5
        assert oversold[0].strength == 5

    def test_rsi_20_strength_is_8(self):
        """RSI=20에서 strength는 8."""
        df = _make_indicator_df(rsi_14=[40.0, 20.0])
        signals = detect_signals(df, stock_id=1)
        oversold = [s for s in signals if s.signal_type == "rsi_oversold"]
        assert len(oversold) == 1
        # RSI=20: (30-20)/3+5 = 8.33 → int → 8
        assert oversold[0].strength == 8

    def test_rsi_10_strength_is_10(self):
        """RSI=10에서 strength는 10 (최대)."""
        df = _make_indicator_df(rsi_14=[40.0, 10.0])
        signals = detect_signals(df, stock_id=1)
        oversold = [s for s in signals if s.signal_type == "rsi_oversold"]
        assert len(oversold) == 1
        # RSI=10: (30-10)/3+5 = 11.67 → int → 11 → clamped to 10
        assert oversold[0].strength == 10

    def test_strength_always_at_least_1(self):
        """strength는 항상 1 이상."""
        df = _make_indicator_df(rsi_14=[40.0, 28.0])
        signals = detect_signals(df, stock_id=1)
        oversold = [s for s in signals if s.signal_type == "rsi_oversold"]
        assert len(oversold) == 1
        assert oversold[0].strength >= 1


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_insufficient_data(self):
        """데이터가 2행 미만이면 시그널 없음."""
        df = pd.DataFrame(
            {"close": [100.0], "sma_20": [98.0], "sma_60": [100.0]},
            index=[date.today()],
        )
        signals = detect_signals(df, stock_id=1)
        assert len(signals) == 0

    def test_nan_values_handled(self):
        """NaN 값이 있어도 에러 없이 동작."""
        df = _make_indicator_df(
            sma_20=[float("nan"), float("nan")],
            sma_60=[float("nan"), float("nan")],
        )
        signals = detect_signals(df, stock_id=1)
        cross_signals = [
            s for s in signals
            if s.signal_type in ("golden_cross", "death_cross")
        ]
        assert len(cross_signals) == 0
