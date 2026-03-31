"""백테스트 이중 차감 버그 수정 검증."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import DimDate, DimStock, FactDailyRecommendation


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


class TestNoDoubleDeduction:
    """백테스트가 이미 차감된 수익률을 재차감하지 않는지 검증."""

    def test_backtest_uses_stored_returns_as_is(self, seeded_session, us_market):
        session = seeded_session
        run_date = date(2026, 3, 10)
        date_id = _seed_date(session, run_date)

        sid = _seed_stock(session, "AAPL", us_market)

        # 수익률 5.0% 저장 (performance.py에서 이미 tx_cost 차감 완료)
        session.add(FactDailyRecommendation(
            run_date_id=date_id, stock_id=sid, rank=1,
            total_score=8.0, technical_score=7.0, fundamental_score=7.0,
            external_score=6.0, momentum_score=7.0, smart_money_score=5.0,
            recommendation_reason="Test", price_at_recommendation=100.0,
            return_1d=1.0, return_5d=3.0, return_10d=4.0, return_20d=5.0,
        ))
        session.commit()

        from src.backtest.engine import BacktestConfig, BacktestEngine

        config = BacktestConfig(
            start_date=run_date,
            end_date=run_date,
        )
        engine = BacktestEngine()
        result = engine.run(session, config)

        # 수익률이 그대로 사용되어야 함 (재차감 없음)
        assert result.avg_return_20d == pytest.approx(5.0, abs=0.1)
        assert result.avg_return_1d == pytest.approx(1.0, abs=0.1)
