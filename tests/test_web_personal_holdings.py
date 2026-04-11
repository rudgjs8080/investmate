"""Phase 12a: 보유/워치리스트 웹 CRUD 라우트 테스트."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Base, DimMarket, DimStock
from src.db.repository import WatchlistRepository
from src.web.app import create_app
from src.web.deps import get_db


@pytest.fixture()
def _web_engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def _db_session(_web_engine) -> Session:
    factory = sessionmaker(bind=_web_engine, expire_on_commit=False)
    sess = factory()
    # 기본 US 마켓 + 기존 종목 하나 준비 (자동등록 우회용)
    market = DimMarket(
        code="US", name="US Stock Market", currency="USD", timezone="US/Eastern",
    )
    sess.add(market)
    sess.flush()
    stock = DimStock(
        ticker="AAPL", name="Apple Inc.",
        market_id=market.market_id, is_sp500=True,
    )
    sess.add(stock)
    sess.flush()
    yield sess
    sess.close()


@pytest.fixture()
def client(_db_session):
    app = create_app()

    def _override_db():
        yield _db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


# ────────────────────────────────────────────────────────────
# POST /personal/watchlist
# ────────────────────────────────────────────────────────────


class TestPostWatchlist:
    def test_adds_existing_stock(self, client, _db_session):
        resp = client.post("/personal/watchlist", json={"ticker": "AAPL"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["ticker"] == "AAPL"

        # DB 상태 검증
        watch = WatchlistRepository.get_active(_db_session)
        assert any(w.ticker == "AAPL" for w in watch)

    def test_lowercase_is_normalized(self, client, _db_session):
        resp = client.post("/personal/watchlist", json={"ticker": "aapl"})
        assert resp.status_code == 201
        assert resp.json()["data"]["ticker"] == "AAPL"

    def test_idempotent(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        resp = client.post("/personal/watchlist", json={"ticker": "AAPL"})
        assert resp.status_code == 201  # 동일 상태로 재활성화
        watch = WatchlistRepository.get_active(_db_session)
        assert sum(1 for w in watch if w.ticker == "AAPL") == 1

    def test_invalid_ticker_rejected(self, client):
        resp = client.post("/personal/watchlist", json={"ticker": "TOO_LONG_TICKER"})
        assert resp.status_code == 400
        assert resp.json()["success"] is False

    def test_empty_ticker_rejected(self, client):
        resp = client.post("/personal/watchlist", json={"ticker": ""})
        assert resp.status_code == 400

    def test_special_chars_rejected(self, client):
        resp = client.post("/personal/watchlist", json={"ticker": "AAPL;DROP"})
        assert resp.status_code == 400

    def test_non_existing_stock_auto_registers(self, client, _db_session):
        """알려지지 않은 종목도 yfinance .info mock으로 자동 등록."""
        with patch(
            "src.deepdive.watchlist_manager._fetch_stock_info",
            return_value={"name": "Microsoft Corp.", "sector": "Technology", "industry": "Software"},
        ):
            resp = client.post("/personal/watchlist", json={"ticker": "MSFT"})
        assert resp.status_code == 201
        assert resp.json()["data"]["name"] == "Microsoft Corp."


# ────────────────────────────────────────────────────────────
# DELETE /personal/watchlist/{ticker}
# ────────────────────────────────────────────────────────────


class TestDeleteWatchlist:
    def test_removes_existing(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        resp = client.delete("/personal/watchlist/AAPL")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert WatchlistRepository.get_active(_db_session) == []

    def test_cascades_to_holding(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": 100, "avg_cost": 150.0},
        )
        resp = client.delete("/personal/watchlist/AAPL")
        assert resp.status_code == 200
        assert WatchlistRepository.get_holding(_db_session, "AAPL") is None

    def test_nonexistent_returns_404(self, client):
        resp = client.delete("/personal/watchlist/UNKNOWN")
        assert resp.status_code == 404


# ────────────────────────────────────────────────────────────
# POST /personal/holdings
# ────────────────────────────────────────────────────────────


class TestUpsertHolding:
    def test_upserts_new(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        resp = client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": 100, "avg_cost": 150.5},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["shares"] == 100
        assert data["avg_cost"] == 150.5

    def test_updates_existing(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": 100, "avg_cost": 150.0},
        )
        resp = client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": 200, "avg_cost": 145.0},
        )
        assert resp.status_code == 200
        holding = WatchlistRepository.get_holding(_db_session, "AAPL")
        assert holding.shares == 200
        assert float(holding.avg_cost) == 145.0

    def test_rejects_unwatched_ticker(self, client):
        # 워치리스트에 없는 종목은 거부
        resp = client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": 100, "avg_cost": 150.0},
        )
        assert resp.status_code == 404

    def test_rejects_negative_shares(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        resp = client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": -5, "avg_cost": 150.0},
        )
        assert resp.status_code == 400

    def test_rejects_zero_shares(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        resp = client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": 0, "avg_cost": 150.0},
        )
        assert resp.status_code == 400

    def test_rejects_negative_avg_cost(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        resp = client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": 100, "avg_cost": -1.0},
        )
        assert resp.status_code == 400

    def test_accepts_opened_at(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        resp = client.post(
            "/personal/holdings",
            json={
                "ticker": "AAPL", "shares": 100, "avg_cost": 150.0,
                "opened_at": "2024-01-15",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["opened_at"] == "2024-01-15"

    def test_rejects_bad_opened_at(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        resp = client.post(
            "/personal/holdings",
            json={
                "ticker": "AAPL", "shares": 100, "avg_cost": 150.0,
                "opened_at": "01-15-2024",
            },
        )
        assert resp.status_code == 400


# ────────────────────────────────────────────────────────────
# DELETE /personal/holdings/{ticker}
# ────────────────────────────────────────────────────────────


class TestDeleteHolding:
    def test_deletes_existing(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": 100, "avg_cost": 150.0},
        )
        resp = client.delete("/personal/holdings/AAPL")
        assert resp.status_code == 200
        assert WatchlistRepository.get_holding(_db_session, "AAPL") is None

    def test_watchlist_remains_after_holding_delete(self, client, _db_session):
        client.post("/personal/watchlist", json={"ticker": "AAPL"})
        client.post(
            "/personal/holdings",
            json={"ticker": "AAPL", "shares": 100, "avg_cost": 150.0},
        )
        client.delete("/personal/holdings/AAPL")
        watch = WatchlistRepository.get_active(_db_session)
        assert any(w.ticker == "AAPL" for w in watch)

    def test_nonexistent_returns_404(self, client):
        resp = client.delete("/personal/holdings/AAPL")
        assert resp.status_code == 404


# ────────────────────────────────────────────────────────────
# GET /personal/holdings/csv-template
# ────────────────────────────────────────────────────────────


class TestCsvTemplate:
    def test_returns_csv(self, client):
        resp = client.get("/personal/holdings/csv-template")
        assert resp.status_code == 200
        assert "text/csv" in resp.headers.get("content-type", "")
        text = resp.text
        assert "ticker" in text
        assert "shares" in text
        assert "avg_cost" in text
        assert "opened_at" in text

    def test_content_disposition_attachment(self, client):
        resp = client.get("/personal/holdings/csv-template")
        assert "attachment" in resp.headers.get("content-disposition", "")


# ────────────────────────────────────────────────────────────
# POST /personal/holdings/import
# ────────────────────────────────────────────────────────────


def _mock_fetch_info(ticker: str) -> dict:
    return {"name": f"{ticker} Inc.", "sector": "Technology", "industry": None}


class TestCsvImport:
    def test_imports_valid_rows(self, client, _db_session):
        csv = b"ticker,shares,avg_cost,opened_at\nAAPL,100,150.0,2024-01-15\n"
        with patch(
            "src.deepdive.watchlist_manager._fetch_stock_info",
            side_effect=_mock_fetch_info,
        ):
            resp = client.post(
                "/personal/holdings/import",
                files={"file": ("holdings.csv", csv, "text/csv")},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["imported_count"] == 1
        assert data["error_count"] == 0

    def test_imports_multiple_rows(self, client, _db_session):
        csv = (
            b"ticker,shares,avg_cost,opened_at\n"
            b"AAPL,100,150.0,\n"
            b"MSFT,50,300.0,2024-02-01\n"
            b"NVDA,10,900.0,\n"
        )
        with patch(
            "src.deepdive.watchlist_manager._fetch_stock_info",
            side_effect=_mock_fetch_info,
        ):
            resp = client.post(
                "/personal/holdings/import",
                files={"file": ("h.csv", csv, "text/csv")},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["imported_count"] == 3

    def test_reports_invalid_rows(self, client, _db_session):
        csv = (
            b"ticker,shares,avg_cost,opened_at\n"
            b"AAPL,100,150.0,\n"
            b"BAD,abc,150.0,\n"              # shares 변환 실패
            b"TSLA,-10,200.0,\n"             # shares 범위
            b",100,150.0,\n"                 # ticker 비어있음
        )
        with patch(
            "src.deepdive.watchlist_manager._fetch_stock_info",
            side_effect=_mock_fetch_info,
        ):
            resp = client.post(
                "/personal/holdings/import",
                files={"file": ("h.csv", csv, "text/csv")},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["imported_count"] == 1
        assert data["error_count"] == 3

    def test_rejects_missing_header(self, client):
        csv = b"ticker,shares\nAAPL,100\n"
        resp = client.post(
            "/personal/holdings/import",
            files={"file": ("h.csv", csv, "text/csv")},
        )
        assert resp.status_code == 400

    def test_rejects_empty_file(self, client):
        resp = client.post(
            "/personal/holdings/import",
            files={"file": ("h.csv", b"", "text/csv")},
        )
        assert resp.status_code == 400

    def test_handles_utf8_bom(self, client, _db_session):
        csv = "\ufeffticker,shares,avg_cost,opened_at\nAAPL,100,150.0,\n".encode("utf-8")
        with patch(
            "src.deepdive.watchlist_manager._fetch_stock_info",
            side_effect=_mock_fetch_info,
        ):
            resp = client.post(
                "/personal/holdings/import",
                files={"file": ("h.csv", csv, "text/csv")},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["imported_count"] == 1
