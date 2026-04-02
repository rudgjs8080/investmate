"""에이전트별 예측 정확도 추적 테스트 (Phase 2)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.db.helpers import date_to_id
from src.db.models import (
    Base,
    DimDate,
    DimMarket,
    DimSector,
    DimStock,
    FactAgentAccuracy,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.add(DimMarket(market_id=1, code="US", name="미국", currency="USD", timezone="America/New_York"))
    session.add(DimSector(sector_id=1, sector_name="Technology"))
    session.add(DimStock(stock_id=1, ticker="AAPL", name="Apple", market_id=1, sector_id=1, is_sp500=True))
    d = date(2026, 3, 1)
    did = date_to_id(d)
    session.add(DimDate(date_id=did, date=d, year=2026, quarter=1, month=3,
                        week_of_year=9, day_of_week=0, is_trading_day=True))
    session.flush()
    session.commit()
    return session


class TestFactAgentAccuracy:
    """FactAgentAccuracy 테이블 CRUD 테스트."""

    def test_create_accuracy_record(self):
        """에이전트 정확도 레코드를 생성할 수 있다."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(FactAgentAccuracy(
            run_date_id=did,
            agent_role="bull",
            ticker="AAPL",
            predicted_direction=True,
            actual_return_20d=5.0,
            was_correct=True,
        ))
        session.commit()

        record = session.execute(select(FactAgentAccuracy)).scalar_one()
        assert record.agent_role == "bull"
        assert record.ticker == "AAPL"
        assert record.predicted_direction is True
        assert record.was_correct is True
        session.close()

    def test_multiple_agents_same_ticker(self):
        """같은 종목에 대해 Bull/Bear 모두 기록할 수 있다."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(FactAgentAccuracy(
            run_date_id=did, agent_role="bull", ticker="AAPL",
            predicted_direction=True, actual_return_20d=5.0, was_correct=True,
        ))
        session.add(FactAgentAccuracy(
            run_date_id=did, agent_role="bear", ticker="AAPL",
            predicted_direction=False, actual_return_20d=5.0, was_correct=False,
        ))
        session.commit()

        records = session.execute(select(FactAgentAccuracy)).scalars().all()
        assert len(records) == 2

        bull_rec = next(r for r in records if r.agent_role == "bull")
        bear_rec = next(r for r in records if r.agent_role == "bear")
        assert bull_rec.was_correct is True
        assert bear_rec.was_correct is False
        session.close()

    def test_nullable_fields(self):
        """아직 평가되지 않은 레코드도 저장 가능."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(FactAgentAccuracy(
            run_date_id=did, agent_role="bull", ticker="AAPL",
            predicted_direction=True,
        ))
        session.commit()

        record = session.execute(select(FactAgentAccuracy)).scalar_one()
        assert record.actual_return_20d is None
        assert record.was_correct is None
        session.close()
