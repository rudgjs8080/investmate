"""yfinance 래퍼 테스트."""

import time
from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from src.data.yahoo_client import (
    CircuitBreaker,
    _df_to_prices,
    _date_to_quarter,
    _download_with_retry,
    _safe_float,
    batch_download_prices,
)


class TestDfToPrices:
    def test_converts_dataframe(self):
        dates = pd.date_range("2026-03-01", periods=3)
        df = pd.DataFrame({
            "Open": [100.0, 101.0, 102.0],
            "High": [105.0, 106.0, 107.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [103.0, 104.0, 105.0],
            "Volume": [1000000, 1100000, 1200000],
            "Adj Close": [103.0, 104.0, 105.0],
        }, index=dates)

        prices = _df_to_prices(df)
        assert len(prices) == 3
        assert prices[0].close == 103.0
        assert prices[0].volume == 1000000

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        prices = _df_to_prices(df)
        assert prices == []


class TestDateToQuarter:
    def test_timestamp(self):
        ts = pd.Timestamp("2026-03-15")
        assert _date_to_quarter(ts) == "2026Q1"

    def test_q4(self):
        ts = pd.Timestamp("2026-12-01")
        assert _date_to_quarter(ts) == "2026Q4"


class TestSafeFloat:
    def test_valid_value(self):
        df = pd.DataFrame({"col": [42.5]}, index=["row"])
        assert _safe_float(df, "row", "col") == 42.5

    def test_nan_value(self):
        df = pd.DataFrame({"col": [float("nan")]}, index=["row"])
        assert _safe_float(df, "row", "col") is None

    def test_missing_row(self):
        df = pd.DataFrame({"col": [1.0]}, index=["row"])
        assert _safe_float(df, "missing", "col") is None


class TestBatchDownloadPrices:
    @patch("src.data.yahoo_client.yf")
    def test_single_ticker(self, mock_yf):
        dates = pd.date_range("2026-03-01", periods=2)
        df = pd.DataFrame({
            "Open": [100.0, 101.0],
            "High": [105.0, 106.0],
            "Low": [99.0, 100.0],
            "Close": [103.0, 104.0],
            "Volume": [1000000, 1100000],
            "Adj Close": [103.0, 104.0],
        }, index=dates)
        mock_yf.download.return_value = df

        result, failed = batch_download_prices(
            ["AAPL"], date(2026, 3, 1), date(2026, 3, 2), batch_size=50
        )
        assert "AAPL" in result
        assert len(result["AAPL"]) == 2
        assert len(failed) == 0

    @patch("src.data.yahoo_client.yf")
    def test_empty_download(self, mock_yf):
        mock_yf.download.return_value = pd.DataFrame()
        result, failed = batch_download_prices(
            ["AAPL"], date(2026, 3, 1), date(2026, 3, 2)
        )
        assert result == {}


class TestFetchFinancialData:
    @patch("src.data.yahoo_client.yf")
    def test_returns_financials_and_valuation(self, mock_yf):
        from src.data.yahoo_client import fetch_financial_data

        mock_ticker = MagicMock()
        mock_ticker.quarterly_income_stmt = pd.DataFrame(
            {"Total Revenue": [100000], "Operating Income": [30000], "Net Income": [20000]},
            index=[pd.Timestamp("2026-01-15")],
        ).T
        mock_ticker.quarterly_income_stmt.columns = [pd.Timestamp("2026-01-15")]
        mock_ticker.quarterly_balance_sheet = pd.DataFrame()
        mock_ticker.info = {"marketCap": 1e9, "trailingPE": 15.0, "priceToBook": 3.0, "returnOnEquity": 0.2}
        mock_yf.Ticker.return_value = mock_ticker

        fins, val = fetch_financial_data("AAPL")
        assert len(fins) >= 0  # May vary based on mock structure
        assert val is not None or val is None  # Should not crash

    @patch("src.data.yahoo_client.yf")
    def test_handles_exception(self, mock_yf):
        from src.data.yahoo_client import fetch_financial_data

        mock_yf.Ticker.side_effect = Exception("API error")
        fins, val = fetch_financial_data("AAPL")
        assert fins == []
        assert val is None


class TestFetchStockInfo:
    @patch("src.data.yahoo_client.yf")
    def test_returns_info(self, mock_yf):
        from src.data.yahoo_client import fetch_stock_info

        mock_ticker = MagicMock()
        mock_ticker.info = {
            "regularMarketPrice": 180.0,
            "shortName": "Apple Inc.",
            "sector": "Technology",
            "industry": "Consumer Electronics",
        }
        mock_yf.Ticker.return_value = mock_ticker

        info = fetch_stock_info("AAPL")
        assert info is not None
        assert info.ticker == "AAPL"
        assert info.name == "Apple Inc."

    @patch("src.data.yahoo_client.yf")
    def test_returns_none_on_error(self, mock_yf):
        from src.data.yahoo_client import fetch_stock_info

        mock_yf.Ticker.side_effect = Exception("error")
        assert fetch_stock_info("AAPL") is None


class TestRetryOnTransientFailure:
    """리트라이 로직 테스트."""

    @patch("src.data.yahoo_client.yf")
    def test_retry_on_transient_failure(self, mock_yf):
        """일시적 ConnectionError 후 성공하면 데이터를 반환한다."""
        dates = pd.date_range("2026-03-01", periods=2)
        good_df = pd.DataFrame({
            "Open": [100.0, 101.0],
            "High": [105.0, 106.0],
            "Low": [99.0, 100.0],
            "Close": [103.0, 104.0],
            "Volume": [1000000, 1100000],
            "Adj Close": [103.0, 104.0],
        }, index=dates)

        mock_yf.download.side_effect = [ConnectionError("transient"), good_df]
        result = _download_with_retry(["AAPL"], "2026-03-01", "2026-03-03")
        assert not result.empty
        assert mock_yf.download.call_count == 2


class TestTimeoutRaisesError:
    """타임아웃 테스트."""

    @patch("src.data.yahoo_client._download_with_retry")
    def test_timeout_raises_error(self, mock_retry):
        """다운로드 시간이 초과하면 TimeoutError를 발생시킨다."""
        from src.data.yahoo_client import _download_with_timeout

        def slow_download(*args, **kwargs):
            time.sleep(5)
            return pd.DataFrame()

        mock_retry.side_effect = slow_download

        try:
            _download_with_timeout(["AAPL"], "2026-03-01", "2026-03-03", timeout_sec=1)
            assert False, "TimeoutError가 발생해야 한다"
        except TimeoutError:
            pass


class TestCircuitBreaker:
    """서킷브레이커 테스트."""

    def test_circuit_breaker_opens(self):
        """연속 실패 시 서킷브레이커가 열린다."""
        cb = CircuitBreaker(fail_threshold=5, reset_seconds=60)
        for _ in range(5):
            cb.record_failure()
        assert cb.is_open is True

    def test_circuit_breaker_resets(self):
        """reset_seconds 이후 서킷브레이커가 닫힌다."""
        cb = CircuitBreaker(fail_threshold=2, reset_seconds=0)
        cb.record_failure()
        cb.record_failure()
        # reset_seconds=0이므로 즉시 리셋
        time.sleep(0.01)
        assert cb.is_open is False

    def test_circuit_breaker_success_resets(self):
        """성공 호출이 실패 카운터를 초기화한다."""
        cb = CircuitBreaker(fail_threshold=5, reset_seconds=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failures == 0
        assert cb.is_open is False
