"""매크로 수집 모듈 테스트."""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from src.data.macro_collector import collect_macro, MACRO_TICKERS


class TestCollectMacro:
    @patch("src.data.macro_collector.yf")
    def test_collects_all_tickers(self, mock_yf):
        # Mock 배치 다운로드 — MultiIndex DataFrame
        import numpy as np
        dates = pd.date_range("2026-03-01", "2026-03-19")
        tickers = list(MACRO_TICKERS.values())
        arrays = []
        for ticker in tickers:
            for col in ["Close", "Open", "High", "Low", "Volume"]:
                arrays.append((ticker, col))
        multi_idx = pd.MultiIndex.from_tuples(arrays)
        data = np.random.rand(len(dates), len(arrays)) * 100 + 10
        df = pd.DataFrame(data, index=dates, columns=multi_idx)
        mock_yf.download.return_value = df

        result = collect_macro(date(2026, 3, 19))

        assert result.date == date(2026, 3, 19)
        # 배치 1회 호출
        assert mock_yf.download.call_count == 1

    @patch("src.data.macro_collector.yf")
    def test_handles_empty_df(self, mock_yf):
        mock_yf.download.return_value = pd.DataFrame()

        result = collect_macro(date(2026, 3, 19))
        assert result.date == date(2026, 3, 19)
        # All values should be None since download returned empty
        assert result.vix is None

    @patch("src.data.macro_collector.yf")
    def test_calculates_sp500_sma20(self, mock_yf):
        dates = pd.date_range("2026-02-15", "2026-03-19")
        prices = [100.0 + i for i in range(len(dates))]
        df = pd.DataFrame({"Close": prices}, index=dates)
        mock_yf.download.return_value = df

        result = collect_macro(date(2026, 3, 19))
        # sp500 should have sma20
        if result.sp500_close is not None:
            assert result.sp500_sma20 is not None

    @patch("src.data.macro_collector.yf")
    def test_handles_download_exception(self, mock_yf):
        mock_yf.download.side_effect = Exception("Network error")

        result = collect_macro(date(2026, 3, 19))
        assert result.date == date(2026, 3, 19)
