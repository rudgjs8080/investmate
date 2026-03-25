"""히트맵 API 테스트."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import (
    Base,
    DimDate,
    DimMarket,
    DimSector,
    DimStock,
    FactDailyPrice,
    FactDailyRecommendation,
    FactValuation,
)
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

    # 시드 데이터
    market = DimMarket(code="US", name="미국", currency="USD", timezone="America/New_York")
    session.add(market)
    session.flush()

    sector = DimSector(sector_name="Technology")
    session.add(sector)
    session.flush()

    for d_id, d_date in [
        (20260320, date(2026, 3, 20)),
        (20260321, date(2026, 3, 21)),
        (20260322, date(2026, 3, 22)),
    ]:
        session.add(DimDate(
            date_id=d_id, date=d_date,
            year=2026, quarter=1, month=3, week_of_year=12,
            day_of_week=4, is_trading_day=True,
        ))
    session.flush()

    stock = DimStock(
        ticker="AAPL", name="Apple Inc.",
        market_id=market.market_id, sector_id=sector.sector_id,
        is_active=True, is_sp500=True,
    )
    session.add(stock)
    session.flush()

    # 가격 데이터 (2일)
    session.add(FactDailyPrice(
        stock_id=stock.stock_id, date_id=20260320,
        open=100, high=105, low=99, close=100, adj_close=100, volume=1000000,
    ))
    session.add(FactDailyPrice(
        stock_id=stock.stock_id, date_id=20260321,
        open=100, high=108, low=100, close=105, adj_close=105, volume=1200000,
    ))

    # 밸류에이션
    session.add(FactValuation(
        stock_id=stock.stock_id, date_id=20260321,
        market_cap=3000000000000, per=28.5,
    ))

    # 추천
    session.add(FactDailyRecommendation(
        run_date_id=20260321, stock_id=stock.stock_id,
        rank=1, total_score=8.5,
        technical_score=7.0, fundamental_score=8.0,
        smart_money_score=7.5, external_score=6.0, momentum_score=7.5,
        recommendation_reason="테스트",
        price_at_recommendation=105,
    ))

    session.commit()
    yield session
    session.close()


@pytest.fixture()
def client(_db_session):
    """테스트 HTTP 클라이언트."""
    app = create_app()

    def _override_db():
        yield _db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app)


class TestHeatmapAPI:
    def test_heatmap_1d(self, client):
        """1일 기간 히트맵 데이터."""
        resp = client.get("/api/heatmap?period=1d")
        assert resp.status_code == 200
        data = resp.json()
        assert "sectors" in data
        assert "summary" in data
        assert "recommended_tickers" in data
        assert data["period"] == "1d"

    def test_heatmap_has_sectors(self, client):
        """섹터 데이터 포함."""
        data = client.get("/api/heatmap?period=1d").json()
        assert len(data["sectors"]) > 0
        sector = data["sectors"][0]
        assert "name" in sector
        assert "children" in sector
        assert "return_pct" in sector

    def test_stock_data_fields(self, client):
        """종목 데이터 필드 완전성."""
        data = client.get("/api/heatmap?period=1d").json()
        stock = data["sectors"][0]["children"][0]
        assert stock["ticker"] == "AAPL"
        assert "return_pct" in stock
        assert "price" in stock
        assert "stock_name" in stock
        assert "is_recommended" in stock
        assert "rsi" in stock
        assert "per" in stock

    def test_return_calculation(self, client):
        """수익률 계산 정확성 (100→105 = +5%)."""
        data = client.get("/api/heatmap?period=1d").json()
        stock = data["sectors"][0]["children"][0]
        assert stock["return_pct"] == 5.0

    def test_recommended_ticker_included(self, client):
        """추천 종목 목록에 AAPL 포함."""
        data = client.get("/api/heatmap?period=1d").json()
        assert "AAPL" in data["recommended_tickers"]

    def test_recommended_highlight(self, client):
        """추천 종목에 is_recommended=True, rec_rank 설정."""
        data = client.get("/api/heatmap?period=1d").json()
        stock = data["sectors"][0]["children"][0]
        assert stock["is_recommended"] is True
        assert stock["rec_rank"] == 1

    def test_summary_present(self, client):
        """시장 요약 통계 포함."""
        data = client.get("/api/heatmap?period=1d").json()
        summary = data["summary"]
        assert summary is not None
        assert summary["total_stocks"] == 1
        assert summary["up_count"] == 1
        assert summary["down_count"] == 0
        assert summary["market_breadth"] == 100.0
        assert summary["avg_return"] == 5.0

    def test_empty_db(self):
        """빈 DB → 빈 응답."""
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        empty_session = factory()

        app = create_app()

        def _override():
            yield empty_session

        app.dependency_overrides[get_db] = _override
        c = TestClient(app)

        data = c.get("/api/heatmap").json()
        assert data["sectors"] == []
        assert data["summary"] is None
        empty_session.close()

    def test_period_5d(self, client):
        """5일 기간 요청."""
        data = client.get("/api/heatmap?period=5d").json()
        assert data["period"] == "5d"
        # 데이터가 2일치밖에 없어도 fallback
        assert "sectors" in data

    def test_invalid_period_defaults(self, client):
        """잘못된 기간 → 기본값 1d."""
        data = client.get("/api/heatmap?period=xyz").json()
        assert data["period"] == "xyz"
        # lookback=2 (기본값) 적용


class TestHeatmapHelpers:
    def test_build_summary_empty(self):
        """빈 데이터 → None."""
        from src.web.routes.heatmap import _build_summary
        assert _build_summary([], {}) is None

    def test_build_summary_basic(self):
        """기본 통계 계산."""
        from src.web.routes.heatmap import _build_summary
        returns = [2.0, -1.0, 0.5, -0.3, 1.5]
        sector_data = {
            "Tech": [{"return_pct": 2.0}, {"return_pct": 0.5}],
            "Health": [{"return_pct": -1.0}, {"return_pct": -0.3}],
        }
        summary = _build_summary(returns, sector_data)
        assert summary["total_stocks"] == 5
        assert summary["up_count"] == 3
        assert summary["down_count"] == 2
        assert summary["best_sector"] == "Tech"
        assert summary["worst_sector"] == "Health"

    def test_build_summary_all_positive(self):
        """전부 양수 → breadth 100%."""
        from src.web.routes.heatmap import _build_summary
        returns = [1.0, 2.0, 3.0]
        summary = _build_summary(returns, {"A": [{"return_pct": r} for r in returns]})
        assert summary["market_breadth"] == 100.0
        assert summary["down_count"] == 0
