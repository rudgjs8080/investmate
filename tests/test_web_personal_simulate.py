"""Phase 12c: /personal/simulate 라우트 테스트."""

from __future__ import annotations

import json
from datetime import date as date_type, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import (
    Base,
    DimMarket,
    DimSector,
    DimStock,
    FactDailyPrice,
    FactDeepDiveReport,
)
from src.db.repository import WatchlistRepository
from src.web.app import create_app
from src.web.deps import get_db


@pytest.fixture()
def _engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _r):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def _db_session(_engine) -> Session:
    factory = sessionmaker(bind=_engine, expire_on_commit=False)
    sess = factory()

    # 기본 세팅: 2 종목 (AAPL Tech, JNJ Healthcare) + 최신 가격 + 보유 + 보고서
    market = DimMarket(
        code="US", name="US", currency="USD", timezone="US/Eastern",
    )
    sess.add(market)
    sess.flush()

    tech = DimSector(sector_name="Technology")
    health = DimSector(sector_name="Healthcare")
    sess.add(tech)
    sess.add(health)
    sess.flush()

    aapl = DimStock(
        ticker="AAPL", name="Apple Inc.",
        market_id=market.market_id, sector_id=tech.sector_id, is_sp500=True,
    )
    jnj = DimStock(
        ticker="JNJ", name="Johnson & Johnson",
        market_id=market.market_id, sector_id=health.sector_id, is_sp500=True,
    )
    nvda = DimStock(
        ticker="NVDA", name="NVIDIA",
        market_id=market.market_id, sector_id=tech.sector_id, is_sp500=True,
    )
    sess.add_all([aapl, jnj, nvda])
    sess.flush()

    # 워치리스트
    WatchlistRepository.add_ticker(sess, "AAPL")
    WatchlistRepository.add_ticker(sess, "JNJ")
    WatchlistRepository.add_ticker(sess, "NVDA")

    # 보유: AAPL 100주, JNJ 30주
    WatchlistRepository.set_holding(sess, "AAPL", shares=100, avg_cost=150.0)
    WatchlistRepository.set_holding(sess, "JNJ", shares=30, avg_cost=160.0)

    # 날짜 축 + 최신 가격
    today = date_type.today()
    ensure_date_ids(sess, [today])
    today_id = date_to_id(today)
    sess.add(FactDailyPrice(
        stock_id=aapl.stock_id, date_id=today_id,
        open=180, high=182, low=179, close=180, adj_close=180, volume=1000000,
    ))
    sess.add(FactDailyPrice(
        stock_id=jnj.stock_id, date_id=today_id,
        open=155, high=156, low=154, close=155, adj_close=155, volume=500000,
    ))
    sess.add(FactDailyPrice(
        stock_id=nvda.stock_id, date_id=today_id,
        open=900, high=910, low=895, close=900, adj_close=900, volume=800000,
    ))
    sess.flush()

    # Deep Dive 보고서 (실행 가이드 포함)
    def _make_report(stock_id, ticker, suggested_pct, ev_3m):
        return FactDeepDiveReport(
            date_id=today_id,
            stock_id=stock_id,
            ticker=ticker,
            action_grade="HOLD",
            conviction=6,
            uncertainty="medium",
            report_json=json.dumps({
                "execution_guide": {
                    "suggested_position_pct": suggested_pct,
                    "expected_value_pct": {"3M": ev_3m},
                    "risk_reward_ratio": 2.0,
                    "portfolio_fit_warnings": [],
                }
            }),
        )

    sess.add(_make_report(aapl.stock_id, "AAPL", 8.0, 12.0))
    sess.add(_make_report(jnj.stock_id, "JNJ", 5.0, 3.0))
    sess.add(_make_report(nvda.stock_id, "NVDA", 7.0, 15.0))
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


class TestSimulateRoute:
    def test_returns_before_and_after(self, client):
        resp = client.post(
            "/personal/simulate",
            json={
                "modifications": [
                    {"ticker": "AAPL", "shares_delta": 50},
                ],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        body = data["data"]
        assert "before" in body
        assert "after" in body
        assert "AAPL" in body["modified_tickers"]

    def test_full_sell_removes_from_plan(self, client):
        resp = client.post(
            "/personal/simulate",
            json={"modifications": [{"ticker": "JNJ", "shares": 0}]},
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert body["after_total_value"] < body["before_total_value"]
        # 섹터 분포에서 Healthcare 사라짐
        after_sectors = {s["sector"]: s["pct"] for s in body["after_sector_weights"]}
        assert "Healthcare" not in after_sectors

    def test_add_new_ticker_from_watchlist(self, client):
        resp = client.post(
            "/personal/simulate",
            json={"modifications": [{"ticker": "NVDA", "shares": 10}]},
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        assert "NVDA" in body["modified_tickers"]
        assert body["after_total_value"] > body["before_total_value"]

    def test_rejects_empty_modifications(self, client):
        resp = client.post("/personal/simulate", json={"modifications": []})
        assert resp.status_code == 400

    def test_rejects_both_shares_and_delta(self, client):
        resp = client.post(
            "/personal/simulate",
            json={
                "modifications": [
                    {"ticker": "AAPL", "shares": 100, "shares_delta": 10},
                ],
            },
        )
        assert resp.status_code == 400

    def test_rejects_invalid_ticker(self, client):
        resp = client.post(
            "/personal/simulate",
            json={"modifications": [{"ticker": "!!!", "shares": 100}]},
        )
        assert resp.status_code == 400

    def test_sector_violation_reported(self, client):
        """Tech 섹터를 30% 이상으로 만드는 시뮬레이션."""
        resp = client.post(
            "/personal/simulate",
            json={
                "modifications": [
                    {"ticker": "NVDA", "shares": 100},  # 90,000 tech
                    {"ticker": "JNJ", "shares": 0},     # Healthcare 제거
                ],
            },
        )
        assert resp.status_code == 200
        body = resp.json()["data"]
        # violations에 Technology 섹터 관련 메시지 있어야 함
        assert any("Technology" in v for v in body["violations"])
