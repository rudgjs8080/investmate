"""Phase 12b: 알림 센터 웹 라우트 테스트."""

from __future__ import annotations

from datetime import date as date_type, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import Base, DimMarket, DimStock
from src.db.repository import AlertRepository
from src.deepdive.alert_engine import AlertTrigger
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
    market = DimMarket(
        code="US", name="US", currency="USD", timezone="US/Eastern",
    )
    sess.add(market)
    sess.flush()
    sess.add(DimStock(
        ticker="AAPL", name="Apple",
        market_id=market.market_id, is_sp500=True,
    ))
    sess.add(DimStock(
        ticker="MSFT", name="Microsoft",
        market_id=market.market_id, is_sp500=True,
    ))
    sess.flush()
    ensure_date_ids(sess, [date_type.today() - timedelta(days=i) for i in range(10)])
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


def _seed_alerts(session):
    from sqlalchemy import select

    lookup = {
        r.ticker: r.stock_id
        for r in session.execute(select(DimStock)).scalars().all()
    }
    today_id = date_to_id(date_type.today())
    yesterday_id = date_to_id(date_type.today() - timedelta(days=1))
    AlertRepository.persist_batch(
        session, today_id, lookup,
        [
            AlertTrigger(
                ticker="AAPL", trigger_type="buy_zone_entered",
                severity="info", message="AAPL in zone", current_price=150.0,
            ),
            AlertTrigger(
                ticker="AAPL", trigger_type="stop_proximity",
                severity="critical", message="AAPL near stop", current_price=145.0,
            ),
            AlertTrigger(
                ticker="MSFT", trigger_type="target_1m_hit",
                severity="info", message="MSFT target reached", current_price=305.0,
            ),
        ],
    )
    AlertRepository.persist_batch(
        session, yesterday_id, lookup,
        [
            AlertTrigger(
                ticker="AAPL", trigger_type="review_trigger_hit",
                severity="warning", message="AAPL review", current_price=148.0,
            ),
        ],
    )


class TestAlertsPage:
    def test_empty_page_200(self, client):
        resp = client.get("/personal/alerts")
        assert resp.status_code == 200
        assert "알림이 없습니다" in resp.text

    def test_lists_alerts(self, client, _db_session):
        _seed_alerts(_db_session)
        resp = client.get("/personal/alerts")
        assert resp.status_code == 200
        assert "AAPL" in resp.text
        assert "MSFT" in resp.text
        assert "AAPL near stop" in resp.text

    def test_filter_severity_critical(self, client, _db_session):
        _seed_alerts(_db_session)
        resp = client.get("/personal/alerts?severity=critical")
        assert resp.status_code == 200
        assert "AAPL near stop" in resp.text
        # info 알림은 critical 필터에서 제외
        assert "MSFT target reached" not in resp.text

    def test_filter_by_ticker(self, client, _db_session):
        _seed_alerts(_db_session)
        resp = client.get("/personal/alerts?ticker=MSFT")
        assert resp.status_code == 200
        assert "MSFT target reached" in resp.text
        assert "AAPL near stop" not in resp.text

    def test_shows_unread_count(self, client, _db_session):
        _seed_alerts(_db_session)
        resp = client.get("/personal/alerts")
        assert "미확인 4건" in resp.text


class TestUnreadCountAPI:
    def test_zero_when_empty(self, client):
        resp = client.get("/personal/alerts/unread-count")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["count"] == 0

    def test_counts_unread(self, client, _db_session):
        _seed_alerts(_db_session)
        resp = client.get("/personal/alerts/unread-count")
        assert resp.json()["data"]["count"] == 4


class TestAcknowledge:
    def test_acks_single(self, client, _db_session):
        _seed_alerts(_db_session)
        alerts = AlertRepository.get_recent(_db_session, days=30)
        aid = alerts[0].alert_id

        resp = client.post(f"/personal/alerts/{aid}/ack")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert AlertRepository.count_unread(_db_session) == 3

    def test_nonexistent_404(self, client):
        resp = client.post("/personal/alerts/999/ack")
        assert resp.status_code == 404

    def test_ack_all(self, client, _db_session):
        _seed_alerts(_db_session)
        resp = client.post("/personal/alerts/ack-all", json={})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["data"]["acknowledged_count"] == 4
        assert AlertRepository.count_unread(_db_session) == 0
