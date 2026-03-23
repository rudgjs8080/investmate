"""웹 API 라우트 테스트 — 헬스체크 및 파라미터 검증."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Base, DimDate, DimMarket, DimStock, FactDailyPrice
from src.web.app import create_app
from src.web.deps import get_db


@pytest.fixture()
def _db_session():
    """테스트용 in-memory DB 세션."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()

    # 최소 시드 데이터
    market = DimMarket(code="US", name="미국", currency="USD", timezone="America/New_York")
    session.add(market)
    session.flush()

    dim_date = DimDate(
        date_id=20260320, date=date(2026, 3, 20),
        year=2026, quarter=1, month=3, week_of_year=12,
        day_of_week=4, is_trading_day=True,
    )
    session.add(dim_date)
    session.flush()

    stock = DimStock(ticker="AAPL", name="Apple Inc.", market_id=market.market_id, is_sp500=True)
    session.add(stock)
    session.flush()

    price = FactDailyPrice(
        stock_id=stock.stock_id, date_id=dim_date.date_id,
        open=150.0, high=155.0, low=149.0, close=153.0,
        adj_close=153.0, volume=1_000_000,
    )
    session.add(price)
    session.commit()

    yield session
    session.close()


@pytest.fixture()
def client(_db_session):
    """TestClient with overridden DB dependency."""
    app = create_app()

    def _override_db():
        yield _db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


class TestHealthCheck:
    def test_health_check_ok(self, client):
        """헬스체크가 200을 반환하고 종목 수를 포함한다."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["stocks"] >= 1

    def test_health_check_fields(self, client):
        """헬스체크 응답에 필수 필드가 포함된다."""
        resp = client.get("/api/health")
        data = resp.json()
        assert "status" in data
        assert "stocks" in data
        assert "latest_data" in data
        assert "timestamp" in data


class TestParameterValidation:
    def test_days_too_large(self, client):
        """days=99999는 422를 반환한다."""
        resp = client.get("/api/equity-curve", params={"days": 99999})
        assert resp.status_code == 422

    def test_days_negative(self, client):
        """days=-1은 422를 반환한다."""
        resp = client.get("/api/equity-curve", params={"days": -1})
        assert resp.status_code == 422
