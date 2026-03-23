"""AI 캘리브레이션 통합 테스트."""

from datetime import date

from sqlalchemy import create_engine

from src.ai.calibrator import CalibrationResult, apply_calibration, calculate_calibration
from src.db.engine import create_session_factory
from src.db.helpers import date_to_id
from src.db.models import Base, DimDate, DimMarket, DimSector, DimStock, FactAIFeedback


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
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


class TestCalculateCalibration:
    def test_insufficient_data(self):
        session = _make_session()
        cal = calculate_calibration(session)
        assert cal.sample_size < 5
        assert cal.target_adjustment == 1.0
        session.close()

    def test_optimistic_bias_detected(self):
        session = _make_session()
        did = date_to_id(date(2026, 3, 1))
        # 10개 피드백: 모두 목표가를 10% 과대추정
        for i in range(10):
            session.add(FactAIFeedback(
                recommendation_id=i + 1, run_date_id=did, stock_id=1,
                ticker="AAPL", ai_approved=True,
                ai_target_price=110.0, price_at_rec=100.0,
                actual_price_20d=100.0,  # 실제로 안 올랐음
                return_20d=0.0, target_error_pct=10.0,  # 10% 과대
            ))
        session.commit()

        cal = calculate_calibration(session)
        assert cal.sample_size == 10
        assert cal.is_optimistic is True
        assert cal.target_adjustment < 1.0  # 하향 보정
        session.close()

    def test_accurate_no_correction(self):
        session = _make_session()
        did = date_to_id(date(2026, 3, 1))
        # 10개 피드백: 목표가 오차 ±2% 이내
        for i in range(10):
            error = (i % 3 - 1) * 1.5  # -1.5, 0, 1.5 반복
            session.add(FactAIFeedback(
                recommendation_id=i + 1, run_date_id=did, stock_id=1,
                ticker="AAPL", ai_approved=True,
                target_error_pct=error,
            ))
        session.commit()

        cal = calculate_calibration(session)
        assert cal.is_optimistic is False
        assert cal.is_pessimistic is False
        assert 0.95 <= cal.target_adjustment <= 1.05
        session.close()
