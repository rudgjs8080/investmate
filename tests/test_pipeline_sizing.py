"""파이프라인 포지션 사이징 통합 테스트."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    DimDate,
    DimStock,
    FactDailyPrice,
    FactDailyRecommendation,
)


def _seed_date(session: Session, d: date) -> int:
    date_id = int(d.strftime("%Y%m%d"))
    existing = session.execute(
        select(DimDate).where(DimDate.date_id == date_id)
    ).scalar_one_or_none()
    if not existing:
        session.add(DimDate(
            date_id=date_id, date=d,
            year=d.year, quarter=(d.month - 1) // 3 + 1,
            month=d.month, week_of_year=1,
            day_of_week=d.weekday(), is_trading_day=True,
            fiscal_quarter=f"Q{(d.month - 1) // 3 + 1}",
        ))
        session.flush()
    return date_id


def _seed_stock(session: Session, ticker: str, market_id: int) -> int:
    from src.db.repository import StockRepository
    stock = StockRepository.add(session, ticker, f"{ticker} Inc.", market_id, is_sp500=True)
    session.flush()
    return stock.stock_id


def _seed_prices(session: Session, stock_id: int, run_date: date, n_days: int = 60):
    """n_days 분의 가격 데이터를 시딩한다."""
    from datetime import timedelta
    for i in range(n_days):
        d = run_date - timedelta(days=n_days - i)
        date_id = _seed_date(session, d)
        base = 100.0 + i * 0.5
        existing = session.execute(
            select(FactDailyPrice).where(
                FactDailyPrice.stock_id == stock_id,
                FactDailyPrice.date_id == date_id,
            )
        ).scalar_one_or_none()
        if not existing:
            session.add(FactDailyPrice(
                stock_id=stock_id, date_id=date_id,
                open=base - 0.5, high=base + 1.0,
                low=base - 1.0, close=base,
                adj_close=base, volume=1_000_000,
            ))
    session.flush()


def _seed_recommendation(
    session: Session, stock_id: int, date_id: int, rank: int,
    ai_approved: bool | None = True, price: float = 100.0,
):
    session.add(FactDailyRecommendation(
        run_date_id=date_id, stock_id=stock_id, rank=rank,
        total_score=8.0, technical_score=7.0, fundamental_score=7.0,
        external_score=6.0, momentum_score=7.0, smart_money_score=5.0,
        recommendation_reason="Test", price_at_recommendation=price,
        ai_approved=ai_approved, ai_confidence=7,
    ))
    session.flush()


class TestStep4_6:
    """step4_6_position_sizing 통합 테스트."""

    def test_sizing_populates_weights(self, seeded_session, us_market):
        """승인 종목에 position_weight가 채워지는지 확인."""
        session = seeded_session
        run_date = date(2026, 3, 15)
        date_id = _seed_date(session, run_date)

        # 3개 종목 시딩
        for ticker in ["AAPL", "MSFT", "GOOG"]:
            sid = _seed_stock(session, ticker, us_market)
            _seed_prices(session, sid, run_date)
            _seed_recommendation(session, sid, date_id, rank=1, ai_approved=True)
        session.commit()

        # step4_6 실행
        from src.pipeline import DailyPipeline

        with patch("src.pipeline.get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.sizing_enabled = True
            settings.sizing_strategy = "vol_target"
            settings.target_volatility_pct = 15.0
            settings.max_single_stock_pct = 0.10
            settings.max_sector_weight_pct = 0.30
            settings.daily_var_limit_pct = 2.0
            settings.portfolio_trailing_stop_pct = 10.0
            settings.atr_stop_multiplier = 2.0
            settings.risk_free_rate_pct = 4.0
            settings.execution_cost_enabled = False
            settings.turnover_warn_threshold = 12.0
            settings.turnover_hold_floor_pct = 0.30

            pipeline = DailyPipeline.__new__(DailyPipeline)
            pipeline.engine = session.get_bind()
            pipeline.target_date = run_date
            pipeline.run_date_id = date_id
            pipeline.top_n = 10
            pipeline._interrupted = False

            result = pipeline.step4_6_position_sizing()

        assert result > 0

        # DB 확인
        recs = session.execute(
            select(FactDailyRecommendation).where(
                FactDailyRecommendation.run_date_id == date_id
            )
        ).scalars().all()

        for rec in recs:
            if rec.ai_approved is not False:
                assert rec.position_weight is not None
                assert rec.sizing_strategy == "vol_target"

    def test_rejected_stocks_zero_weight(self, seeded_session, us_market):
        """AI 거부 종목은 비중 0."""
        session = seeded_session
        run_date = date(2026, 3, 16)
        date_id = _seed_date(session, run_date)

        # 승인 종목 1개 + 거부 종목 1개
        sid1 = _seed_stock(session, "AAPL", us_market)
        _seed_prices(session, sid1, run_date)
        _seed_recommendation(session, sid1, date_id, rank=1, ai_approved=True)

        sid2 = _seed_stock(session, "TSLA", us_market)
        _seed_prices(session, sid2, run_date)
        _seed_recommendation(session, sid2, date_id, rank=2, ai_approved=False)
        session.commit()

        from src.pipeline import DailyPipeline

        with patch("src.pipeline.get_settings") as mock_settings:
            settings = mock_settings.return_value
            settings.sizing_enabled = True
            settings.sizing_strategy = "erc"
            settings.target_volatility_pct = 15.0
            settings.max_single_stock_pct = 0.10
            settings.max_sector_weight_pct = 0.30
            settings.daily_var_limit_pct = 2.0
            settings.portfolio_trailing_stop_pct = 10.0
            settings.atr_stop_multiplier = 2.0
            settings.risk_free_rate_pct = 4.0
            settings.execution_cost_enabled = False
            settings.turnover_warn_threshold = 12.0
            settings.turnover_hold_floor_pct = 0.30

            pipeline = DailyPipeline.__new__(DailyPipeline)
            pipeline.engine = session.get_bind()
            pipeline.target_date = run_date
            pipeline.run_date_id = date_id
            pipeline.top_n = 10
            pipeline._interrupted = False

            pipeline.step4_6_position_sizing()

        rejected = session.execute(
            select(FactDailyRecommendation).where(
                FactDailyRecommendation.run_date_id == date_id,
                FactDailyRecommendation.stock_id == sid2,
            )
        ).scalar_one()
        assert rejected.position_weight == 0.0

    def test_sizing_disabled_skips(self, seeded_session, us_market):
        """sizing_enabled=False이면 0 반환."""
        session = seeded_session
        run_date = date(2026, 3, 17)
        date_id = _seed_date(session, run_date)

        from src.pipeline import DailyPipeline

        with patch("src.pipeline.get_settings") as mock_settings:
            mock_settings.return_value.sizing_enabled = False

            pipeline = DailyPipeline.__new__(DailyPipeline)
            pipeline.engine = session.get_bind()
            pipeline.target_date = run_date
            pipeline.run_date_id = date_id
            pipeline.top_n = 10
            pipeline._interrupted = False

            result = pipeline.step4_6_position_sizing()

        assert result == 0
