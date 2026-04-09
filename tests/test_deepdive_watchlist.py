"""워치리스트 CRUD + Manager + Seed 테스트."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.db.repository import WatchlistRepository


class TestWatchlistRepository:
    """WatchlistRepository CRUD."""

    def test_add_ticker(self, seeded_session):
        item = WatchlistRepository.add_ticker(seeded_session, "AAPL")
        assert item.ticker == "AAPL"
        assert item.active is True

    def test_add_ticker_idempotent(self, seeded_session):
        WatchlistRepository.add_ticker(seeded_session, "AAPL")
        WatchlistRepository.remove_ticker(seeded_session, "AAPL")
        item = WatchlistRepository.add_ticker(seeded_session, "AAPL")
        assert item.active is True

    def test_remove_ticker(self, seeded_session):
        WatchlistRepository.add_ticker(seeded_session, "MSFT")
        result = WatchlistRepository.remove_ticker(seeded_session, "MSFT")
        assert result is True
        active = WatchlistRepository.get_active(seeded_session)
        assert all(w.ticker != "MSFT" for w in active)

    def test_remove_nonexistent(self, seeded_session):
        result = WatchlistRepository.remove_ticker(seeded_session, "ZZZZ")
        assert result is False

    def test_get_active_only(self, seeded_session):
        WatchlistRepository.add_ticker(seeded_session, "AAPL")
        WatchlistRepository.add_ticker(seeded_session, "GOOG")
        WatchlistRepository.remove_ticker(seeded_session, "GOOG")
        active = WatchlistRepository.get_active(seeded_session)
        tickers = [w.ticker for w in active]
        assert "AAPL" in tickers
        assert "GOOG" not in tickers

    def test_set_holding_upsert(self, seeded_session):
        WatchlistRepository.add_ticker(seeded_session, "NVDA")
        h1 = WatchlistRepository.set_holding(seeded_session, "NVDA", 100, 130.50)
        assert h1.shares == 100
        h2 = WatchlistRepository.set_holding(seeded_session, "NVDA", 200, 140.00)
        assert h2.shares == 200
        assert float(h2.avg_cost) == 140.00

    def test_get_all_holdings(self, seeded_session):
        WatchlistRepository.add_ticker(seeded_session, "AAPL")
        WatchlistRepository.set_holding(seeded_session, "AAPL", 50, 170.0)
        holdings = WatchlistRepository.get_all_holdings(seeded_session)
        assert "AAPL" in holdings


class TestWatchlistManager:
    """워치리스트 매니저 load + auto-register."""

    @patch("src.deepdive.watchlist_manager._fetch_stock_info")
    def test_auto_register_non_sp500(self, mock_fetch, seeded_session, us_market):
        mock_fetch.return_value = {
            "name": "NuScale Power",
            "sector": "Industrials",
            "industry": "Nuclear",
        }
        from src.deepdive.watchlist_manager import ensure_stock_registered

        stock = ensure_stock_registered(seeded_session, "SMR")
        assert stock.ticker == "SMR"
        assert stock.is_sp500 is False

    @patch("src.deepdive.watchlist_manager._fetch_stock_info")
    def test_auto_register_existing(self, mock_fetch, seeded_session, us_market):
        from src.db.repository import StockRepository
        from src.deepdive.watchlist_manager import ensure_stock_registered

        StockRepository.add(seeded_session, "AAPL", "Apple", us_market, is_sp500=True)
        stock = ensure_stock_registered(seeded_session, "AAPL")
        assert stock.is_sp500 is True
        mock_fetch.assert_not_called()

    @patch("src.deepdive.watchlist_manager._fetch_stock_info")
    def test_load_watchlist(self, mock_fetch, seeded_session, us_market):
        from src.db.repository import StockRepository
        from src.deepdive.watchlist_manager import load_watchlist

        mock_fetch.return_value = {"name": "Test", "sector": "Tech", "industry": None}
        StockRepository.add(seeded_session, "AAPL", "Apple", us_market, is_sp500=True)
        WatchlistRepository.add_ticker(seeded_session, "AAPL")
        WatchlistRepository.set_holding(seeded_session, "AAPL", 100, 150.0)

        entries = load_watchlist(seeded_session)
        assert len(entries) == 1
        assert entries[0].ticker == "AAPL"
        assert entries[0].holding is not None
        assert entries[0].holding.shares == 100
