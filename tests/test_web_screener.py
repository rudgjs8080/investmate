"""인터랙티브 스크리너 API 테스트."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import (
    Base,
    DimDate,
    DimIndicatorType,
    DimMarket,
    DimSector,
    DimSignalType,
    DimStock,
    FactDailyPrice,
    FactIndicatorValue,
    FactSignal,
    FactValuation,
)


@pytest.fixture()
def screener_session():
    """스크리너 테스트용 세션 (충분한 시드 데이터)."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()

    # 디멘션
    market = DimMarket(code="US", name="미국", currency="USD", timezone="America/New_York")
    session.add(market)
    session.flush()

    tech_sector = DimSector(sector_name="Information Technology")
    energy_sector = DimSector(sector_name="Energy")
    session.add_all([tech_sector, energy_sector])
    session.flush()

    # 종목
    aapl = DimStock(ticker="AAPL", name="Apple Inc.", market_id=market.market_id, sector_id=tech_sector.sector_id, is_sp500=True, is_active=True)
    msft = DimStock(ticker="MSFT", name="Microsoft Corp.", market_id=market.market_id, sector_id=tech_sector.sector_id, is_sp500=True, is_active=True)
    xom = DimStock(ticker="XOM", name="Exxon Mobil", market_id=market.market_id, sector_id=energy_sector.sector_id, is_sp500=True, is_active=True)
    session.add_all([aapl, msft, xom])
    session.flush()

    # 날짜
    session.add(DimDate(date_id=20260320, date=date(2026, 3, 20), year=2026, quarter=1, month=3, week_of_year=12, day_of_week=4, is_trading_day=True))
    session.flush()

    # 밸류에이션
    session.add(FactValuation(stock_id=aapl.stock_id, date_id=20260320, per=28.5, pbr=12.0, roe=0.35, market_cap=3.5e12, dividend_yield=0.005))
    session.add(FactValuation(stock_id=msft.stock_id, date_id=20260320, per=32.0, pbr=14.0, roe=0.40, market_cap=3.0e12, dividend_yield=0.008))
    session.add(FactValuation(stock_id=xom.stock_id, date_id=20260320, per=12.0, pbr=1.8, roe=0.15, market_cap=5.0e11, dividend_yield=0.035))
    session.flush()

    # 가격
    session.add(FactDailyPrice(stock_id=aapl.stock_id, date_id=20260320, open=180, high=185, low=179, close=183.5, adj_close=183.5, volume=50_000_000))
    session.add(FactDailyPrice(stock_id=msft.stock_id, date_id=20260320, open=420, high=425, low=418, close=422.0, adj_close=422.0, volume=30_000_000))
    session.add(FactDailyPrice(stock_id=xom.stock_id, date_id=20260320, open=110, high=112, low=109, close=111.0, adj_close=111.0, volume=20_000_000))
    session.flush()

    # RSI 지표
    rsi_type = DimIndicatorType(code="RSI_14", name="RSI", category="momentum")
    session.add(rsi_type)
    session.flush()

    session.add(FactIndicatorValue(stock_id=aapl.stock_id, date_id=20260320, indicator_type_id=rsi_type.indicator_type_id, value=45.0))
    session.add(FactIndicatorValue(stock_id=msft.stock_id, date_id=20260320, indicator_type_id=rsi_type.indicator_type_id, value=28.0))
    session.add(FactIndicatorValue(stock_id=xom.stock_id, date_id=20260320, indicator_type_id=rsi_type.indicator_type_id, value=72.0))
    session.flush()

    # 시그널
    sig_type = DimSignalType(code="rsi_oversold", name="RSI 과매도", direction="BUY", default_weight=0.6)
    session.add(sig_type)
    session.flush()
    session.add(FactSignal(stock_id=msft.stock_id, date_id=20260320, signal_type_id=sig_type.signal_type_id, strength=7))
    session.flush()

    session.commit()
    yield session
    session.close()


class TestScreenerFiltering:
    """스크리너 필터링 로직 테스트."""

    def test_no_filters_returns_all(self, screener_session):
        """필터 없이 전체 종목 반환."""
        from src.web.routes.screener import screener_data
        # screener_data를 직접 호출 (FastAPI 의존성 주입 시뮬레이션)
        result = screener_data(db=screener_session)
        assert result["total"] == 3

    def test_sector_filter(self, screener_session):
        """섹터 필터 동작."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session, sector="Energy")
        assert result["total"] == 1
        assert result["results"][0]["ticker"] == "XOM"

    def test_rsi_max_filter(self, screener_session):
        """RSI 상한 필터 (과매도 종목)."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session, rsi_max=30.0)
        assert result["total"] == 1
        assert result["results"][0]["ticker"] == "MSFT"

    def test_rsi_min_filter(self, screener_session):
        """RSI 하한 필터 (과매수 종목)."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session, rsi_min=70.0)
        assert result["total"] == 1
        assert result["results"][0]["ticker"] == "XOM"

    def test_per_range_filter(self, screener_session):
        """PER 범위 필터."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session, per_min=0.0, per_max=15.0)
        assert result["total"] == 1
        assert result["results"][0]["ticker"] == "XOM"

    def test_dividend_filter(self, screener_session):
        """배당수익률 필터."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session, div_min=0.02)
        assert result["total"] == 1
        assert result["results"][0]["ticker"] == "XOM"

    def test_sort_by_per_asc(self, screener_session):
        """PER 오름차순 정렬."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session, sort_by="per", sort_dir="asc")
        tickers = [r["ticker"] for r in result["results"]]
        assert tickers[0] == "XOM"  # PER 12.0

    def test_sort_by_rsi_desc(self, screener_session):
        """RSI 내림차순 정렬."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session, sort_by="rsi", sort_dir="desc")
        assert result["results"][0]["ticker"] == "XOM"  # RSI 72.0

    def test_combined_filters(self, screener_session):
        """복합 필터 (섹터 + PER)."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session, sector="Information Technology", per_max=30.0)
        assert result["total"] == 1
        assert result["results"][0]["ticker"] == "AAPL"  # PER 28.5

    def test_no_matches(self, screener_session):
        """조건에 맞는 종목 없음."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session, per_max=5.0)
        assert result["total"] == 0
        assert result["results"] == []

    def test_result_fields(self, screener_session):
        """결과 필드 완전성."""
        from src.web.routes.screener import screener_data
        result = screener_data(db=screener_session)
        item = next(r for r in result["results"] if r["ticker"] == "AAPL")
        assert item["name"] is not None
        assert item["sector"] == "Information Technology"
        assert item["price"] == 183.5
        assert item["rsi"] == 45.0
        assert item["per"] == 28.5
        assert item["pbr"] == 12.0
        assert item["market_cap"] == 3.5e12
