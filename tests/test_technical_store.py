"""기술적 분석 store/load 테스트."""

from datetime import date

import numpy as np
import pandas as pd

import pytest

from src.analysis.technical import calculate_indicators, prices_to_dataframe, store_indicators, INDICATOR_COLUMNS
from src.db.helpers import ensure_date_ids
from src.db.repository import DailyPriceRepository, IndicatorValueRepository
from datetime import date


class TestCalculateIndicators:
    def test_returns_all_columns(self):
        dates = pd.date_range("2026-01-01", periods=150, freq="B")
        np.random.seed(42)
        prices = 100 + np.cumsum(np.random.randn(150) * 0.5)
        df = pd.DataFrame({
            "close": prices,
            "high": prices + 1,
            "low": prices - 1,
            "open": prices,
            "volume": np.random.randint(100000, 1000000, 150),
        }, index=dates)

        result = calculate_indicators(df)
        for col in INDICATOR_COLUMNS:
            assert col in result.columns, f"Missing column: {col}"

    def test_short_data_still_works(self):
        dates = pd.date_range("2026-01-01", periods=30, freq="B")
        df = pd.DataFrame({
            "close": range(100, 130),
            "high": range(101, 131),
            "low": range(99, 129),
            "open": range(100, 130),
            "volume": [500000] * 30,
        }, index=dates)

        result = calculate_indicators(df)
        assert not result.empty
        # RSI should have values (14-period)
        assert result["rsi_14"].notna().any()

class TestPricesToDataframe:
    def test_empty_stock(self, seeded_session, sample_stock):
        df = prices_to_dataframe(seeded_session, sample_stock["id"])
        assert df.empty

    def test_with_prices(self, seeded_session, sample_stock):
        prices = [
            {"date": date(2026, 1, i + 1), "open": 100.0 + i, "high": 102.0 + i,
             "low": 99.0 + i, "close": 101.0 + i, "volume": 500000, "adj_close": 101.0 + i}
            for i in range(20)
        ]
        DailyPriceRepository.upsert_prices_batch(seeded_session, sample_stock["id"], prices)
        seeded_session.flush()

        df = prices_to_dataframe(seeded_session, sample_stock["id"])
        assert len(df) == 20
        assert "close" in df.columns


class TestStoreIndicators:
    def test_stores_to_db(self, seeded_session, sample_stock):
        dates = pd.date_range("2026-01-02", periods=30, freq="B")
        indicators_df = pd.DataFrame({
            "close": range(100, 130),
            "rsi_14": [50.0] * 30,
            "sma_20": [110.0] * 30,
            "macd": [1.0] * 30,
        }, index=[d.date() for d in dates])

        for d in dates:
            ensure_date_ids(seeded_session, [d.date()])

        count = store_indicators(seeded_session, sample_stock["id"], indicators_df)
        assert count > 0

        # 저장 확인
        result = IndicatorValueRepository.get_latest_for_stock(
            seeded_session, sample_stock["id"], 20260213
        )
        assert "RSI_14" in result or len(result) > 0


    def test_immutable_input(self):
        dates = pd.date_range("2026-01-01", periods=50, freq="B")
        df = pd.DataFrame({
            "close": range(100, 150),
            "high": range(101, 151),
            "low": range(99, 149),
            "open": range(100, 150),
            "volume": [500000] * 50,
        }, index=dates)

        original_close = df["close"].copy()
        calculate_indicators(df)
        pd.testing.assert_series_equal(df["close"], original_close)
