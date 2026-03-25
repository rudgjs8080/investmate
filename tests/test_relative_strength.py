"""상대 강도 (Relative Strength) 테스트."""

from __future__ import annotations

from src.analysis.relative_strength import (
    _cumulative_return,
    _to_percentiles,
)


class TestCumulativeReturn:
    def test_positive_return(self):
        prices = [100.0, 105.0, 110.0, 115.0, 120.0]
        ret = _cumulative_return(prices, 4)
        assert ret is not None
        # prices[-4] = 105.0, prices[-1] = 120.0 → (120/105-1)*100 ≈ 14.3%
        assert ret > 10.0

    def test_negative_return(self):
        prices = [100.0, 95.0, 90.0, 85.0, 80.0]
        ret = _cumulative_return(prices, 4)
        assert ret is not None
        assert ret < 0

    def test_insufficient_data(self):
        prices = [100.0, 105.0]
        assert _cumulative_return(prices, 10) is None

    def test_zero_start_price(self):
        # prices[-3] = 100.0 (not 0), so result is valid
        # Test with zero at the lookback position
        prices = [0, 100.0, 105.0]
        assert _cumulative_return(prices, 3) is None

    def test_flat_return(self):
        prices = [100.0] * 10
        ret = _cumulative_return(prices, 5)
        assert ret == 0.0


class TestToPercentiles:
    def test_basic_ranking(self):
        values = {1: 10.0, 2: 20.0, 3: 30.0}
        pcts = _to_percentiles(values)
        assert pcts[1] < pcts[2] < pcts[3]
        assert pcts[1] == 0.0  # 최하위
        assert pcts[3] == 100.0  # 최상위

    def test_empty_input(self):
        assert _to_percentiles({}) == {}

    def test_single_stock(self):
        pcts = _to_percentiles({1: 5.0})
        assert pcts[1] == 0.0

    def test_all_equal(self):
        """동일 값이면 정렬 순서에 따라 0-100."""
        pcts = _to_percentiles({1: 5.0, 2: 5.0, 3: 5.0})
        assert len(pcts) == 3
        # 값이 동일하므로 모든 백분위가 0-100 범위
        assert all(0 <= v <= 100 for v in pcts.values())

    def test_five_stocks_percentile_spread(self):
        values = {10: -5.0, 20: 0.0, 30: 5.0, 40: 10.0, 50: 15.0}
        pcts = _to_percentiles(values)
        assert pcts[10] == 0.0
        assert pcts[20] == 25.0
        assert pcts[30] == 50.0
        assert pcts[40] == 75.0
        assert pcts[50] == 100.0


class TestRSInMomentumScore:
    """RS가 _score_momentum에 반영되는지 테스트."""

    def test_high_rs_boost(self):
        import numpy as np
        import pandas as pd

        from src.analysis.screener import _score_momentum

        dates = pd.date_range("2026-01-01", periods=30, freq="B")
        prices = 100 + np.cumsum(np.random.default_rng(42).standard_normal(30) * 0.5)
        df = pd.DataFrame({
            "close": prices,
            "volume": [500000] * 30,
            "sma_5": prices, "sma_20": prices - 2, "sma_60": prices - 5,
            "volume_sma_20": [400000] * 30,
        }, index=dates)
        latest = df.iloc[-1]

        score_no_rs = _score_momentum(df, latest)
        score_high_rs = _score_momentum(df, latest, rs_percentile=85.0)
        score_low_rs = _score_momentum(df, latest, rs_percentile=10.0)

        assert score_high_rs > score_no_rs
        assert score_low_rs < score_no_rs

    def test_rs_none_no_effect(self):
        import numpy as np
        import pandas as pd

        from src.analysis.screener import _score_momentum

        dates = pd.date_range("2026-01-01", periods=30, freq="B")
        prices = 100 + np.cumsum(np.random.default_rng(42).standard_normal(30) * 0.5)
        df = pd.DataFrame({
            "close": prices,
            "volume": [500000] * 30,
            "sma_5": prices, "sma_20": prices - 2, "sma_60": prices - 5,
            "volume_sma_20": [400000] * 30,
        }, index=dates)
        latest = df.iloc[-1]

        assert _score_momentum(df, latest) == _score_momentum(df, latest, rs_percentile=None)
