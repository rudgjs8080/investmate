"""S&P 500 종목 관리 테스트."""

from unittest.mock import MagicMock, patch

import pandas as pd

from src.data.sp500 import fetch_sp500_list, sync_sp500
from src.db.repository import StockRepository


class TestFetchSp500List:
    @patch("requests.get")
    @patch("pandas.read_html")
    def test_parses_wikipedia_table(self, mock_read_html, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "<html></html>"
        mock_get.return_value = mock_resp

        mock_read_html.return_value = [
            pd.DataFrame({
                "Symbol": ["AAPL", "MSFT"],
                "Security": ["Apple Inc.", "Microsoft Corp."],
                "GICS Sector": ["Information Technology", "Information Technology"],
                "GICS Sub-Industry": ["Consumer Electronics", "Software"],
            })
        ]

        result = fetch_sp500_list()
        assert len(result) == 2
        assert result[0]["ticker"] == "AAPL"
        assert result[0]["name"] == "Apple Inc."
        assert result[0]["sector"] == "Information Technology"

    @patch("requests.get", side_effect=Exception("Network error"))
    def test_handles_network_error(self, mock_get):
        result = fetch_sp500_list()
        assert result == []

    @patch("requests.get")
    @patch("pandas.read_html")
    def test_dot_to_dash_conversion(self, mock_read_html, mock_get):
        mock_resp = MagicMock()
        mock_resp.text = "<html></html>"
        mock_get.return_value = mock_resp

        mock_read_html.return_value = [
            pd.DataFrame({
                "Symbol": ["BRK.B"],
                "Security": ["Berkshire Hathaway"],
                "GICS Sector": ["Financials"],
                "GICS Sub-Industry": ["Diversified"],
            })
        ]

        result = fetch_sp500_list()
        assert result[0]["ticker"] == "BRK-B"


class TestSyncSp500:
    @patch("src.data.sp500.fetch_sp500_list")
    def test_adds_new_stocks(self, mock_fetch, seeded_session):
        mock_fetch.return_value = [
            {"ticker": "NEWSTOCK", "name": "New Stock Inc.", "sector": "Technology", "industry": "Software"},
        ]

        # Get market_id
        market_id = StockRepository.resolve_market_id(seeded_session, "US")
        result = sync_sp500(seeded_session, market_id)
        assert result["added"] >= 1

    @patch("src.data.sp500.fetch_sp500_list")
    def test_empty_list_skips(self, mock_fetch, seeded_session):
        mock_fetch.return_value = []
        result = sync_sp500(seeded_session, 1)
        assert result == {"added": 0, "removed": 0, "total": 0}
