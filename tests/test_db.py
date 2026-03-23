"""Star Schema Repository 테스트."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from src.db.helpers import date_to_id, ensure_date_ids, id_to_date
from src.db.repository import (
    CollectionLogRepository,
    DailyPriceRepository,
    FinancialRepository,
    IndicatorValueRepository,
    MacroRepository,
    NewsRepository,
    RecommendationRepository,
    SignalRepository,
    StockRepository,
)


class TestHelpers:
    """date_to_id / id_to_date 테스트."""

    def test_date_to_id(self):
        assert date_to_id(date(2025, 3, 16)) == 20250316

    def test_id_to_date(self):
        assert id_to_date(20250316) == date(2025, 3, 16)

    def test_roundtrip(self):
        d = date(2024, 12, 1)
        assert id_to_date(date_to_id(d)) == d

    def test_ensure_date_ids(self, seeded_session):
        dates = [date(2025, 1, 15), date(2025, 1, 16)]
        result = ensure_date_ids(seeded_session, dates)
        assert result[date(2025, 1, 15)] == 20250115
        assert result[date(2025, 1, 16)] == 20250116


class TestStockRepository:
    """StockRepository 테스트."""

    def test_add_and_get(self, seeded_session, us_market):
        stock = StockRepository.add(
            seeded_session, "MSFT", "Microsoft", us_market, is_sp500=True
        )
        assert stock.stock_id is not None

        found = StockRepository.get_by_ticker(seeded_session, "MSFT")
        assert found is not None
        assert found.name == "Microsoft"
        assert found.is_sp500 is True

    def test_get_sp500_active(self, seeded_session, us_market):
        StockRepository.add(seeded_session, "AAPL", "Apple", us_market, is_sp500=True)
        StockRepository.add(seeded_session, "GOOG", "Google", us_market, is_sp500=True)
        StockRepository.add(seeded_session, "TSLA", "Tesla", us_market, is_sp500=False)
        seeded_session.commit()

        sp500 = StockRepository.get_sp500_active(seeded_session)
        tickers = [s.ticker for s in sp500]
        assert "AAPL" in tickers
        assert "GOOG" in tickers
        assert "TSLA" not in tickers

    def test_resolve_sector_id(self, seeded_session):
        sid = StockRepository.resolve_sector_id(
            seeded_session, "Technology", "Software & Services"
        )
        assert sid is not None

        # 같은 섹터 재조회
        sid2 = StockRepository.resolve_sector_id(
            seeded_session, "Technology", "Software & Services"
        )
        assert sid == sid2


class TestDailyPriceRepository:
    """DailyPriceRepository 테스트."""

    def test_upsert_prices(self, seeded_session, sample_stock):
        prices = [
            {
                "date": date(2024, 1, 15),
                "open": 100.0, "high": 105.0, "low": 99.0,
                "close": 103.0, "volume": 1000000, "adj_close": 103.0,
            },
            {
                "date": date(2024, 1, 16),
                "open": 103.0, "high": 107.0, "low": 102.0,
                "close": 106.0, "volume": 1200000, "adj_close": 106.0,
            },
        ]
        count = DailyPriceRepository.upsert_prices_batch(
            seeded_session, sample_stock["id"], prices
        )
        assert count == 2

    def test_upsert_updates_existing(self, seeded_session, sample_stock):
        prices = [
            {
                "date": date(2024, 1, 15),
                "open": 100.0, "high": 105.0, "low": 99.0,
                "close": 103.0, "volume": 1000000, "adj_close": 103.0,
            },
        ]
        DailyPriceRepository.upsert_prices_batch(
            seeded_session, sample_stock["id"], prices
        )
        seeded_session.commit()

        updated = [
            {
                "date": date(2024, 1, 15),
                "open": 101.0, "high": 110.0, "low": 98.0,
                "close": 108.0, "volume": 1500000, "adj_close": 108.0,
            },
        ]
        DailyPriceRepository.upsert_prices_batch(
            seeded_session, sample_stock["id"], updated
        )
        seeded_session.commit()

        result = DailyPriceRepository.get_prices(seeded_session, sample_stock["id"])
        assert len(result) == 1
        assert float(result[0].close) == 108.0

    def test_get_last_date(self, seeded_session, sample_stock):
        assert DailyPriceRepository.get_last_date(
            seeded_session, sample_stock["id"]
        ) is None

        prices = [
            {
                "date": date(2024, 1, 15),
                "open": 100.0, "high": 105.0, "low": 99.0,
                "close": 103.0, "volume": 1000000, "adj_close": 103.0,
            },
            {
                "date": date(2024, 1, 16),
                "open": 103.0, "high": 107.0, "low": 102.0,
                "close": 106.0, "volume": 1200000, "adj_close": 106.0,
            },
        ]
        DailyPriceRepository.upsert_prices_batch(
            seeded_session, sample_stock["id"], prices
        )
        seeded_session.commit()

        last = DailyPriceRepository.get_last_date(seeded_session, sample_stock["id"])
        assert last == date(2024, 1, 16)


class TestIndicatorValueRepository:
    """IndicatorValueRepository (EAV) 테스트."""

    def test_upsert_values(self, seeded_session, sample_stock):
        type_map = IndicatorValueRepository.get_indicator_type_map(seeded_session)
        assert "RSI_14" in type_map

        records = [
            {
                "date_id": 20240115,
                "indicator_type_id": type_map["RSI_14"],
                "value": 55.3,
            },
            {
                "date_id": 20240115,
                "indicator_type_id": type_map["SMA_20"],
                "value": 102.5,
            },
        ]
        # date_id가 dim_date에 있는지 확인
        ensure_date_ids(seeded_session, [date(2024, 1, 15)])

        count = IndicatorValueRepository.upsert_values(
            seeded_session, sample_stock["id"], records
        )
        assert count == 2

    def test_upsert_updates_value(self, seeded_session, sample_stock):
        type_map = IndicatorValueRepository.get_indicator_type_map(seeded_session)
        ensure_date_ids(seeded_session, [date(2024, 1, 15)])

        records = [
            {"date_id": 20240115, "indicator_type_id": type_map["RSI_14"], "value": 55.0},
        ]
        IndicatorValueRepository.upsert_values(seeded_session, sample_stock["id"], records)
        seeded_session.commit()

        updated = [
            {"date_id": 20240115, "indicator_type_id": type_map["RSI_14"], "value": 60.0},
        ]
        IndicatorValueRepository.upsert_values(seeded_session, sample_stock["id"], updated)
        seeded_session.commit()

    def test_get_latest_falls_back_to_earlier_date(self, seeded_session, sample_stock):
        """run_date_id와 실제 거래일이 다를 때 이전 날짜의 지표를 반환한다."""
        type_map = IndicatorValueRepository.get_indicator_type_map(seeded_session)
        ensure_date_ids(seeded_session, [date(2024, 1, 15)])

        records = [
            {"date_id": 20240115, "indicator_type_id": type_map["RSI_14"], "value": 42.5},
            {"date_id": 20240115, "indicator_type_id": type_map["SMA_20"], "value": 150.0},
        ]
        IndicatorValueRepository.upsert_values(seeded_session, sample_stock["id"], records)
        seeded_session.commit()

        # 20240116 (다음 날)으로 조회 → 20240115 데이터 반환
        result = IndicatorValueRepository.get_latest_for_stock(
            seeded_session, sample_stock["id"], 20240116,
        )
        assert "RSI_14" in result
        assert result["RSI_14"] == 42.5
        assert "SMA_20" in result

    def test_get_latest_returns_empty_for_future_stock(self, seeded_session, sample_stock):
        """데이터가 없는 미래 날짜로 조회하면 빈 dict 반환."""
        result = IndicatorValueRepository.get_latest_for_stock(
            seeded_session, sample_stock["id"], 20200101,
        )
        assert result == {}


class TestSignalFallback:
    """시그널 범위 조회 테스트."""

    def test_get_by_date_falls_back(self, seeded_session, sample_stock):
        """run_date_id와 다른 날짜의 시그널도 반환한다."""
        type_map = SignalRepository.get_signal_type_map(seeded_session)
        ensure_date_ids(seeded_session, [date(2024, 3, 1)])

        signals = [
            {"signal_type_id": type_map["golden_cross"], "strength": 8, "description": "test"},
        ]
        SignalRepository.create_signals_batch(
            seeded_session, sample_stock["id"], 20240301, signals
        )
        seeded_session.commit()

        # 20240303 (주말 이후)로 조회 → 20240301 시그널 반환
        result = SignalRepository.get_by_date(seeded_session, 20240303)
        assert len(result) >= 1
        assert result[0].strength == 8

    def test_get_by_stock_and_date_falls_back(self, seeded_session, sample_stock):
        type_map = SignalRepository.get_signal_type_map(seeded_session)
        ensure_date_ids(seeded_session, [date(2024, 3, 1)])

        signals = [
            {"signal_type_id": type_map["rsi_oversold"], "strength": 6, "description": "test"},
        ]
        SignalRepository.create_signals_batch(
            seeded_session, sample_stock["id"], 20240301, signals
        )
        seeded_session.commit()

        result = SignalRepository.get_by_stock_and_date(
            seeded_session, sample_stock["id"], 20240305,
        )
        assert len(result) >= 1


class TestFinancialRepository:
    """FinancialRepository 테스트."""

    def test_upsert(self, seeded_session, sample_stock):
        records = [
            {"period": "2024Q1", "revenue": 90000.0, "net_income": 20000.0},
        ]
        count = FinancialRepository.upsert(
            seeded_session, sample_stock["id"], records
        )
        assert count == 1

    def test_get_by_stock(self, seeded_session, sample_stock):
        records = [
            {"period": "2024Q1", "revenue": 90000.0},
            {"period": "2024Q2", "revenue": 95000.0},
        ]
        FinancialRepository.upsert(seeded_session, sample_stock["id"], records)
        seeded_session.commit()

        result = FinancialRepository.get_by_stock(seeded_session, sample_stock["id"])
        assert len(result) == 2


class TestSignalRepository:
    """SignalRepository 테스트."""

    def test_create_and_get(self, seeded_session, sample_stock):
        type_map = SignalRepository.get_signal_type_map(seeded_session)
        assert "golden_cross" in type_map

        ensure_date_ids(seeded_session, [date(2024, 3, 1)])

        signals = [
            {
                "signal_type_id": type_map["golden_cross"],
                "strength": 8,
                "description": "골든크로스",
            },
        ]
        count = SignalRepository.create_signals_batch(
            seeded_session, sample_stock["id"], 20240301, signals
        )
        assert count == 1

        result = SignalRepository.get_by_stock(seeded_session, sample_stock["id"])
        assert len(result) == 1
        assert result[0].strength == 8


class TestMacroRepository:
    """MacroRepository 테스트."""

    def test_upsert_and_get(self, seeded_session):
        ensure_date_ids(seeded_session, [date(2024, 3, 1)])

        MacroRepository.upsert(seeded_session, 20240301, {
            "vix": 15.5, "us_10y_yield": 4.2, "sp500_close": 5100.0,
            "market_score": 7,
        })
        seeded_session.commit()

        latest = MacroRepository.get_latest(seeded_session)
        assert latest is not None
        assert float(latest.vix) == 15.5
        assert latest.market_score == 7


class TestNewsRepository:
    """NewsRepository + Bridge 테스트."""

    def test_upsert_and_link(self, seeded_session, sample_stock):
        articles = [
            {
                "title": "Apple 실적 발표",
                "url": "https://example.com/1",
                "source": "Reuters",
                "published_at": datetime(2024, 3, 1, 10, 0),
            },
        ]
        count = NewsRepository.upsert_by_url(seeded_session, articles)
        assert count == 1

        # news_id 조회
        from sqlalchemy import select
        from src.db.models import FactNews
        news = seeded_session.execute(
            select(FactNews).where(FactNews.url == "https://example.com/1")
        ).scalar_one()

        NewsRepository.link_to_stocks(
            seeded_session, news.news_id, [sample_stock["id"]]
        )
        seeded_session.commit()

        result = NewsRepository.get_by_stock(seeded_session, sample_stock["id"])
        assert len(result) == 1
        assert result[0].title == "Apple 실적 발표"


class TestCollectionLogRepository:
    """CollectionLogRepository 테스트."""

    def test_log_step(self, seeded_session):
        ensure_date_ids(seeded_session, [date(2024, 3, 1)])

        log = CollectionLogRepository.log_step(
            seeded_session, 20240301, "step1_collect", "success",
            started_at=datetime(2024, 3, 1, 6, 30),
            finished_at=datetime(2024, 3, 1, 6, 35),
            records_count=500,
        )
        assert log.log_id is not None

        logs = CollectionLogRepository.get_by_run_date(seeded_session, 20240301)
        assert len(logs) == 1


class TestRecommendationRepository:
    """RecommendationRepository 테스트."""

    def test_create_batch(self, seeded_session, sample_stock):
        ensure_date_ids(seeded_session, [date(2024, 3, 1)])

        recs = [
            {
                "stock_id": sample_stock["id"],
                "rank": 1,
                "total_score": 8.5,
                "technical_score": 8.0,
                "fundamental_score": 9.0,
                "external_score": 7.5,
                "momentum_score": 8.0,
                "recommendation_reason": "강한 상승 모멘텀",
                "price_at_recommendation": 175.0,
            },
        ]
        count = RecommendationRepository.create_batch(seeded_session, 20240301, recs)
        assert count == 1

        result = RecommendationRepository.get_by_date(seeded_session, 20240301)
        assert len(result) == 1
        assert result[0].rank == 1
