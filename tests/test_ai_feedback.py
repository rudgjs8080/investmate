"""AI 피드백 시스템 테스트."""

from datetime import date

from sqlalchemy import create_engine

from src.ai.feedback import (
    AIPerformanceSummary, calculate_ai_performance, collect_ai_feedback,
    compute_calibration_curve, compute_ece,
)
from src.db.engine import create_session_factory
from src.db.helpers import date_to_id
from src.db.models import (
    Base, DimDate, DimMarket, DimSector, DimStock,
    FactAIFeedback, FactDailyRecommendation,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    session.add(DimMarket(market_id=1, code="US", name="미국", currency="USD", timezone="America/New_York"))
    session.add(DimSector(sector_id=1, sector_name="Technology"))
    session.add(DimStock(stock_id=1, ticker="AAPL", name="Apple", market_id=1, sector_id=1, is_sp500=True))
    session.add(DimStock(stock_id=2, ticker="MSFT", name="Microsoft", market_id=1, sector_id=1, is_sp500=True))
    d = date(2026, 3, 1)
    did = date_to_id(d)
    session.add(DimDate(date_id=did, date=d, year=2026, quarter=1, month=3,
                        week_of_year=9, day_of_week=0, is_trading_day=True))
    session.flush()
    session.commit()
    return session


class TestCollectAIFeedback:
    def test_collects_feedback_from_recommendations(self):
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        # 추천 데이터 + AI 분석 + 사후 수익률
        session.add(FactDailyRecommendation(
            run_date_id=did, stock_id=1, rank=1,
            total_score=7.0, technical_score=7.0, fundamental_score=7.0,
            smart_money_score=6.0, external_score=6.0, momentum_score=7.0,
            recommendation_reason="test", price_at_recommendation=150.0,
            ai_approved=True, ai_confidence=8, ai_target_price=170.0, ai_stop_loss=140.0,
            return_1d=1.0, return_5d=3.0, return_20d=5.0,
        ))
        session.commit()

        count = collect_ai_feedback(session)
        assert count == 1

        fb = session.query(FactAIFeedback).first()
        assert fb.ticker == "AAPL"
        assert fb.direction_correct is True  # 추천 + 양수 수익
        assert fb.return_20d == 5.0
        session.close()

    def test_skips_already_collected(self):
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(FactDailyRecommendation(
            run_date_id=did, stock_id=1, rank=1,
            total_score=7.0, technical_score=7.0, fundamental_score=7.0,
            smart_money_score=6.0, external_score=6.0, momentum_score=7.0,
            recommendation_reason="test", price_at_recommendation=150.0,
            ai_approved=True, return_20d=5.0,
        ))
        session.commit()

        # 첫 번째 수집
        count1 = collect_ai_feedback(session)
        assert count1 == 1

        # 두 번째 수집 — 중복 안 됨
        count2 = collect_ai_feedback(session)
        assert count2 == 0
        session.close()


class TestCalculateAIPerformance:
    def test_empty_feedback(self):
        session = _make_session()
        result = calculate_ai_performance(session)
        assert result.total_predictions == 0
        session.close()

    def test_with_feedback_data(self):
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        # 직접 피드백 데이터 삽입
        session.add(FactAIFeedback(
            recommendation_id=1, run_date_id=did, stock_id=1,
            ticker="AAPL", sector="Technology",
            ai_approved=True, ai_confidence=8,
            ai_target_price=170.0, ai_stop_loss=140.0,
            price_at_rec=150.0, actual_price_20d=160.0,
            return_20d=6.67, direction_correct=True,
            target_hit=False, target_error_pct=6.25,
        ))
        session.add(FactAIFeedback(
            recommendation_id=2, run_date_id=did, stock_id=2,
            ticker="MSFT", sector="Technology",
            ai_approved=True, ai_confidence=6,
            price_at_rec=300.0, actual_price_20d=290.0,
            return_20d=-3.33, direction_correct=False,
            target_error_pct=-5.0,
        ))
        session.commit()

        result = calculate_ai_performance(session)
        assert result.total_predictions == 2
        assert result.ai_approved_count == 2
        assert result.win_rate_approved == 50.0
        assert result.direction_accuracy == 50.0
        assert result.sector_accuracy is not None
        assert "Technology" in result.sector_accuracy
        session.close()


class TestComputeCalibrationCurve:
    def test_compute_calibration_curve_basic(self):
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        # 신뢰도 8: 2건 중 1건 양수 -> actual=0.5
        session.add(FactAIFeedback(
            recommendation_id=10, run_date_id=did, stock_id=1,
            ticker="AAPL", ai_confidence=8, return_20d=5.0,
            direction_correct=True,
        ))
        session.add(FactAIFeedback(
            recommendation_id=11, run_date_id=did, stock_id=2,
            ticker="MSFT", ai_confidence=8, return_20d=-3.0,
            direction_correct=False,
        ))
        session.commit()

        curve = compute_calibration_curve(session)
        assert 8 in curve
        assert curve[8]["predicted"] == 0.8
        assert curve[8]["actual"] == 0.5
        assert curve[8]["count"] == 2
        assert curve[8]["gap"] == -0.3
        session.close()

    def test_compute_calibration_curve_empty(self):
        session = _make_session()
        curve = compute_calibration_curve(session)
        assert curve == {}
        session.close()


class TestComputeECE:
    def test_compute_ece_perfect_calibration(self):
        """예측과 실제가 완벽히 일치하면 ECE=0."""
        curve = {
            5: {"predicted": 0.5, "actual": 0.5, "count": 10, "gap": 0.0},
            8: {"predicted": 0.8, "actual": 0.8, "count": 10, "gap": 0.0},
        }
        assert compute_ece(curve) == 0.0

    def test_compute_ece_poor_calibration(self):
        """gap이 크면 ECE > 0."""
        curve = {
            8: {"predicted": 0.8, "actual": 0.3, "count": 10, "gap": -0.5},
        }
        ece = compute_ece(curve)
        assert ece == 0.5
