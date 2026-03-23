"""기술적 분석 테스트."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.analysis.technical import INDICATOR_COLUMNS, calculate_indicators


def _make_ohlcv_df(days: int = 150, base_price: float = 100.0) -> pd.DataFrame:
    """테스트용 OHLCV DataFrame 생성."""
    dates = [date.today() - timedelta(days=days - i) for i in range(days)]
    np.random.seed(42)
    prices = base_price + np.cumsum(np.random.randn(days) * 2)

    data = {
        "open": prices - np.random.rand(days),
        "high": prices + np.abs(np.random.randn(days) * 2),
        "low": prices - np.abs(np.random.randn(days) * 2),
        "close": prices,
        "volume": np.random.randint(100000, 1000000, days),
        "adjusted_close": prices,
    }
    df = pd.DataFrame(data, index=dates)
    df.index.name = "date"
    return df


class TestCalculateIndicators:
    """calculate_indicators 테스트."""

    def test_returns_new_dataframe(self):
        """입력 DataFrame을 변경하지 않는다."""
        df = _make_ohlcv_df()
        original_cols = set(df.columns)
        result = calculate_indicators(df)

        assert set(df.columns) == original_cols  # 원본 불변
        assert len(result.columns) > len(df.columns)

    def test_all_indicator_columns_present(self):
        """모든 지표 컬럼이 존재한다."""
        df = _make_ohlcv_df()
        result = calculate_indicators(df)

        for col in INDICATOR_COLUMNS:
            assert col in result.columns, f"{col} 누락"

    def test_sma_values(self):
        """SMA 값이 올바르게 계산된다."""
        df = _make_ohlcv_df()
        result = calculate_indicators(df)

        # SMA5는 5일 이후부터 값이 있어야 함
        assert pd.notna(result["sma_5"].iloc[-1])
        # SMA120은 120일 이후부터 값이 있어야 함
        assert pd.notna(result["sma_120"].iloc[-1])
        # SMA5 초기값은 NaN
        assert pd.isna(result["sma_5"].iloc[0])

    def test_rsi_range(self):
        """RSI는 0-100 범위여야 한다."""
        df = _make_ohlcv_df()
        result = calculate_indicators(df)

        rsi_values = result["rsi_14"].dropna()
        assert all(0 <= v <= 100 for v in rsi_values)

    def test_bollinger_bands_order(self):
        """볼린저 밴드는 lower < middle < upper 순서여야 한다."""
        df = _make_ohlcv_df()
        result = calculate_indicators(df)

        valid = result[["bb_lower", "bb_middle", "bb_upper"]].dropna()
        assert all(valid["bb_lower"] <= valid["bb_middle"])
        assert all(valid["bb_middle"] <= valid["bb_upper"])

    def test_short_dataframe(self):
        """데이터가 짧아도 에러 없이 동작한다."""
        df = _make_ohlcv_df(days=10)
        result = calculate_indicators(df)
        assert len(result) == 10
