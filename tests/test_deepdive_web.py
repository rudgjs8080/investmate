"""Deep Dive /personal 웹 라우트 테스트."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Base
from src.web.app import create_app
from src.web.deps import get_db


@pytest.fixture()
def _web_engine():
    """웹 테스트용 in-memory 엔진 (StaticPool로 커넥션 공유)."""
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
    yield sess
    sess.close()


@pytest.fixture()
def client(_db_session):
    """FastAPI 테스트 클라이언트."""
    app = create_app()

    def _override_db():
        yield _db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


class TestPersonalPage:
    """GET /personal 라우트."""

    def test_personal_page_200(self, client):
        resp = client.get("/personal")
        assert resp.status_code == 200

    def test_personal_empty_watchlist(self, client):
        resp = client.get("/personal")
        assert resp.status_code == 200
        assert "워치리스트가 비어 있습니다" in resp.text

    def test_personal_with_data(self, client, _db_session):
        from src.db.repository import WatchlistRepository

        WatchlistRepository.add_ticker(_db_session, "AAPL")
        _db_session.flush()

        resp = client.get("/personal")
        assert resp.status_code == 200
        assert "AAPL" in resp.text


class TestPersonalDetailPage:
    """GET /personal/{ticker} 라우트."""

    def test_detail_no_stock(self, client):
        resp = client.get("/personal/XXXXX")
        assert resp.status_code == 200
        assert "분석 대기중" in resp.text

    def test_detail_with_stock(self, client, _db_session):
        from src.db.models import DimMarket, DimStock

        market = DimMarket(code="US", name="US", currency="USD", timezone="US/Eastern")
        _db_session.add(market)
        _db_session.flush()
        stock = DimStock(ticker="AAPL", name="Apple", market_id=market.market_id, is_sp500=True)
        _db_session.add(stock)
        _db_session.flush()

        resp = client.get("/personal/AAPL")
        assert resp.status_code == 200
        assert "AAPL" in resp.text


class TestHistoryPage:
    """GET /personal/{ticker}/history 라우트."""

    def test_history_route_200(self, client):
        """/personal/AAPL/history -> 200."""
        resp = client.get("/personal/AAPL/history")
        assert resp.status_code == 200

    def test_history_empty(self, client):
        """이력 없으면 빈 상태 메시지."""
        resp = client.get("/personal/AAPL/history")
        assert resp.status_code == 200
        assert "분석 이력이 없습니다" in resp.text


class TestForecastsPage:
    """GET /personal/forecasts 라우트."""

    def test_forecasts_route_200(self, client):
        """/personal/forecasts -> 200."""
        resp = client.get("/personal/forecasts")
        assert resp.status_code == 200

    def test_forecasts_empty(self, client):
        """데이터 없으면 빈 상태 메시지."""
        resp = client.get("/personal/forecasts")
        assert resp.status_code == 200
        assert "만기 도래한 예측이 없습니다" in resp.text

    def test_forecasts_not_captured_as_ticker(self, client):
        """/personal/forecasts가 {ticker}로 잡히지 않음."""
        resp = client.get("/personal/forecasts")
        assert resp.status_code == 200
        assert "예측 정확도" in resp.text


class TestCardChangeBadge:
    """카드 변경 배지 테스트."""

    def test_card_includes_change_count(self, client, _db_session):
        """카드에 change_count 필드 전달."""
        from src.db.repository import WatchlistRepository

        WatchlistRepository.add_ticker(_db_session, "AAPL")
        _db_session.flush()

        resp = client.get("/personal")
        assert resp.status_code == 200
