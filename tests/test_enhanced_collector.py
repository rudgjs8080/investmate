"""강화 데이터 수집 테스트."""

from unittest.mock import MagicMock, patch, PropertyMock

import pandas as pd

import pytest

from src.data.enhanced_collector import (
    collect_all_enhanced,
    collect_analyst_consensus,
    collect_earnings_surprises,
    collect_insider_trades,
    collect_institutional_holdings,
    collect_short_interest,
    _safe_float,
)


class TestCollectInsiderTrades:
    @patch("src.data.enhanced_collector.yf")
    def test_parses_trades(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.insider_transactions = pd.DataFrame({
            "Start Date": [pd.Timestamp("2026-03-01")],
            "Insider": ["John Doe"],
            "Position": ["CEO"],
            "Transaction": ["Purchase"],
            "Shares": [1000],
            "Value": [50000.0],
        })
        mock_yf.Ticker.return_value = mock_ticker

        trades = collect_insider_trades("AAPL")
        assert len(trades) == 1
        assert trades[0]["insider_name"] == "John Doe"
        assert trades[0]["transaction_type"] == "Purchase"

    @patch("src.data.enhanced_collector.yf")
    def test_empty_trades(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.insider_transactions = pd.DataFrame()
        mock_yf.Ticker.return_value = mock_ticker

        assert collect_insider_trades("AAPL") == []

    @patch("src.data.enhanced_collector.yf")
    def test_handles_exception(self, mock_yf):
        mock_yf.Ticker.side_effect = Exception("API error")
        assert collect_insider_trades("AAPL") == []


class TestCollectAnalystConsensus:
    @patch("src.data.enhanced_collector.yf")
    def test_parses_consensus(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.recommendations = pd.DataFrame({
            "strongBuy": [10],
            "buy": [15],
            "hold": [5],
            "sell": [2],
            "strongSell": [1],
        })
        mock_ticker.analyst_price_targets = {
            "mean": 200.0, "high": 250.0, "low": 150.0, "median": 195.0,
        }
        mock_yf.Ticker.return_value = mock_ticker

        result = collect_analyst_consensus("AAPL")
        assert result is not None
        assert result["strong_buy"] == 10
        assert result["buy"] == 15
        assert result["target_mean"] == 200.0

    @patch("src.data.enhanced_collector.yf")
    def test_empty_recommendations(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.recommendations = pd.DataFrame()
        mock_yf.Ticker.return_value = mock_ticker

        assert collect_analyst_consensus("AAPL") is None


class TestCollectEarningsSurprises:
    @patch("src.data.enhanced_collector.yf")
    def test_parses_surprises(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.earnings_history = pd.DataFrame({
            "reportDate": [pd.Timestamp("2026-01-15")],
            "epsEstimate": [2.0],
            "epsActual": [2.2],
            "surprisePercent": [10.0],
            "quarter": [None],
        })
        mock_yf.Ticker.return_value = mock_ticker

        results = collect_earnings_surprises("AAPL")
        assert len(results) == 1
        assert results[0]["surprise_pct"] == 10.0

    @patch("src.data.enhanced_collector.yf")
    def test_handles_exception(self, mock_yf):
        mock_yf.Ticker.side_effect = Exception("API error")
        assert collect_earnings_surprises("AAPL") == []


class TestCollectShortInterest:
    @patch("src.data.enhanced_collector.yf")
    def test_parses_short_data(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.info = {"shortRatio": 3.5, "shortPercentOfFloat": 5.2}
        mock_yf.Ticker.return_value = mock_ticker

        result = collect_short_interest("AAPL")
        assert result["short_ratio"] == 3.5
        assert result["short_pct_of_float"] == 5.2

    @patch("src.data.enhanced_collector.yf")
    def test_handles_exception(self, mock_yf):
        mock_yf.Ticker.side_effect = Exception("API error")
        result = collect_short_interest("AAPL")
        assert result == {}


class TestSafeFloat:
    def test_valid_float(self):
        assert _safe_float(42.5) == 42.5

    def test_none(self):
        assert _safe_float(None) is None

    def test_nan(self):
        assert _safe_float(float("nan")) is None

    def test_string_number(self):
        assert _safe_float("3.14") == 3.14

    def test_invalid_string(self):
        assert _safe_float("abc") is None


class TestCollectAllEnhanced:
    @patch("src.data.enhanced_collector.collect_short_interest", return_value={})
    @patch("src.data.enhanced_collector.collect_earnings_surprises", return_value=[])
    @patch("src.data.enhanced_collector.collect_analyst_consensus", return_value=None)
    @patch("src.data.enhanced_collector.collect_institutional_holdings", return_value=[])
    @patch("src.data.enhanced_collector.collect_insider_trades", return_value=[])
    def test_empty_results(self, m1, m2, m3, m4, m5, seeded_session):
        from datetime import date as dt_date
        from src.db.repository import StockRepository
        from src.db.helpers import ensure_date_ids

        ensure_date_ids(seeded_session, [dt_date(2026, 3, 19)])
        stocks = StockRepository.get_sp500_active(seeded_session)
        if not stocks:
            stocks = [StockRepository.add(seeded_session, "TEST", "Test", 1, is_sp500=True)]
            seeded_session.flush()

        result = collect_all_enhanced(seeded_session, stocks[:1], dt_date(2026, 3, 19))
        assert isinstance(result, dict)
        assert "insider" in result
        assert "analyst" in result
