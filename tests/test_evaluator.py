"""AI 프롬프트 성과 평가 모듈 테스트."""

from dataclasses import FrozenInstanceError
from datetime import date

import pytest
from sqlalchemy import create_engine

from src.ai.evaluator import EvaluationResult, evaluate_ai_performance
from src.db.engine import create_session_factory
from src.db.helpers import date_to_id
from src.db.models import (
    Base, DimDate, DimMarket, DimSector, DimStock, FactAIFeedback,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    session.add(DimMarket(market_id=1, code="US", name="US", currency="USD", timezone="America/New_York"))
    session.add(DimSector(sector_id=1, sector_name="Technology"))
    session.add(DimStock(stock_id=1, ticker="AAPL", name="Apple", market_id=1, sector_id=1, is_sp500=True))
    d = date(2026, 3, 1)
    did = date_to_id(d)
    session.add(DimDate(date_id=did, date=d, year=2026, quarter=1, month=3,
                        week_of_year=9, day_of_week=0, is_trading_day=True))
    session.flush()
    session.commit()
    return session


class TestEvaluateEmptyFeedbacks:
    def test_evaluate_empty_feedbacks(self):
        session = _make_session()
        result = evaluate_ai_performance(session)
        assert result.total_recommendations == 0
        assert result.direction_accuracy is None
        assert result.avg_return_20d is None
        assert result.ece is None
        session.close()


class TestEvaluationResultFrozen:
    def test_evaluation_result_frozen(self):
        result = EvaluationResult(
            version="test", period="all",
            total_recommendations=0, direction_accuracy=None,
            avg_return_20d=None, win_rate_20d=None,
            avg_target_error=None, ece=None,
        )
        with pytest.raises(FrozenInstanceError):
            result.version = "modified"
