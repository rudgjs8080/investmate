"""날짜 매핑 캐시 및 prices_to_dataframe 테스트."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine

from src.analysis.technical import load_date_map, prices_to_dataframe
from src.db.engine import create_session_factory
from src.db.helpers import date_to_id
from src.db.models import (
    Base,
    DimDate,
    DimMarket,
    DimStock,
    FactDailyPrice,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()

    # Seed minimal data
    session.add(DimMarket(market_id=1, code="US", name="미국", currency="USD", timezone="America/New_York"))
    session.add(DimStock(stock_id=1, ticker="AAPL", name="Apple", market_id=1, is_sp500=True))

    # 10 days of prices
    for i in range(10):
        d = date(2026, 3, 1) + timedelta(days=i)
        did = date_to_id(d)
        session.add(DimDate(
            date_id=did, date=d, year=2026, quarter=1, month=3,
            week_of_year=d.isocalendar()[1], day_of_week=d.weekday(),
            is_trading_day=True,
        ))
        session.add(FactDailyPrice(
            stock_id=1, date_id=did, open=100 + i, high=102 + i,
            low=99 + i, close=101 + i, adj_close=101 + i, volume=500000,
        ))

    session.flush()
    session.commit()
    return session


class TestLoadDateMap:
    def test_returns_dict(self):
        session = _make_session()
        dm = load_date_map(session)
        assert isinstance(dm, dict)
        assert len(dm) >= 10
        session.close()

    def test_contains_seeded_dates(self):
        session = _make_session()
        dm = load_date_map(session)
        d = date(2026, 3, 1)
        did = date_to_id(d)
        assert did in dm
        assert dm[did] == d
        session.close()


class TestPricesToDataframeWithCache:
    def test_with_date_map(self):
        session = _make_session()
        dm = load_date_map(session)
        df = prices_to_dataframe(session, stock_id=1, date_map=dm)
        assert not df.empty
        assert len(df) == 10
        session.close()

    def test_without_date_map_fallback(self):
        session = _make_session()
        df = prices_to_dataframe(session, stock_id=1, date_map=None)
        assert not df.empty
        assert len(df) == 10
        session.close()

    def test_results_identical(self):
        """캐시 사용/미사용 결과가 동일하다."""
        session = _make_session()
        dm = load_date_map(session)
        df_cached = prices_to_dataframe(session, stock_id=1, date_map=dm)
        df_no_cache = prices_to_dataframe(session, stock_id=1, date_map=None)
        assert len(df_cached) == len(df_no_cache)
        assert list(df_cached.columns) == list(df_no_cache.columns)
        assert df_cached["close"].sum() == df_no_cache["close"].sum()
        session.close()

    def test_empty_stock(self):
        session = _make_session()
        dm = load_date_map(session)
        df = prices_to_dataframe(session, stock_id=999, date_map=dm)
        assert df.empty
        session.close()
