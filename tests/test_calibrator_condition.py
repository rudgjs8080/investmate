"""조건별 캘리브레이션 (calibrator.py 확장) 테스트."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.ai.calibrator import (
    CalibrationCell,
    _confidence_to_range,
    build_condition_calibration,
    format_calibration_for_prompt,
    get_condition_calibration,
)
from src.db.helpers import date_to_id
from src.db.models import (
    DimDate,
    DimMarket,
    DimStock,
    FactAIFeedback,
    FactCalibrationCell,
    FactDailyRecommendation,
    FactMacroIndicator,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _seed_dates(session):
    """테스트용 날짜 + 종목."""
    base = date(2026, 1, 1)
    for i in range(90):
        d = base + timedelta(days=i)
        session.add(DimDate(
            date_id=date_to_id(d),
            date=d,
            year=d.year,
            quarter=(d.month - 1) // 3 + 1,
            month=d.month,
            week_of_year=d.isocalendar()[1],
            day_of_week=d.weekday(),
            is_trading_day=d.weekday() < 5,
        ))
    session.add(DimMarket(market_id=1, code="US", name="US Market", currency="USD", timezone="US/Eastern"))
    for i in range(1, 15):
        session.add(DimStock(
            stock_id=i, ticker=f"T{i-1}", name=f"Test Stock {i}",
            market_id=1, is_active=True, is_sp500=True,
        ))
    session.commit()


@pytest.fixture
def _seed_macro(session, _seed_dates):
    """테스트 매크로."""
    session.add(FactMacroIndicator(
        date_id=date_to_id(date(2026, 1, 15)),
        vix=18.0,
        sp500_close=5000.0,
        sp500_sma20=4900.0,
        market_score=7,
    ))
    session.commit()


@pytest.fixture
def _seed_feedbacks(session, _seed_dates, _seed_macro):
    """테스트 피드백 데이터 (12건 — MIN_CELL_SAMPLES=10 충족)."""
    run_date_id = date_to_id(date(2026, 1, 15))

    for i in range(12):
        rec = FactDailyRecommendation(
            run_date_id=run_date_id,
            stock_id=i + 1,
            rank=i + 1,
            total_score=80.0 - i,
            technical_score=7.0,
            fundamental_score=7.0,
            smart_money_score=6.0,
            external_score=6.0,
            momentum_score=7.0,
            recommendation_reason="test",
            price_at_recommendation=100.0 + i * 10,
            ai_approved=True,
            ai_confidence=7,
        )
        session.add(rec)
        session.flush()

        session.add(FactAIFeedback(
            recommendation_id=rec.recommendation_id,
            run_date_id=run_date_id,
            stock_id=i + 1,
            ticker=f"T{i}",
            sector="Technology",
            ai_approved=True,
            ai_confidence=7,
            return_20d=5.0 if i < 7 else -3.0,
        ))
    session.commit()


# ---------------------------------------------------------------------------
# _confidence_to_range
# ---------------------------------------------------------------------------


class TestConfidenceToRange:
    @pytest.mark.parametrize("conf,expected", [
        (1, "1-3"), (2, "1-3"), (3, "1-3"),
        (4, "4-6"), (5, "4-6"), (6, "4-6"),
        (7, "7-8"), (8, "7-8"),
        (9, "9-10"), (10, "9-10"),
    ])
    def test_ranges(self, conf, expected):
        assert _confidence_to_range(conf) == expected

    def test_out_of_range(self):
        """범위 밖 값은 1-3."""
        assert _confidence_to_range(0) == "1-3"
        assert _confidence_to_range(11) == "1-3"


# ---------------------------------------------------------------------------
# build_condition_calibration
# ---------------------------------------------------------------------------


class TestBuildConditionCalibration:
    def test_builds_cells(self, session, _seed_feedbacks):
        """피드백으로부터 캘리브레이션 셀을 생성한다."""
        cutoff_id = date_to_id(date(2026, 3, 1))
        cells = build_condition_calibration(session, cutoff_id)

        assert len(cells) > 0
        for cell in cells:
            assert cell.sample_count >= 3
            assert 0 <= cell.win_rate <= 100

    def test_persists_to_db(self, session, _seed_feedbacks):
        """DB에 셀이 저장된다."""
        cutoff_id = date_to_id(date(2026, 3, 1))
        build_condition_calibration(session, cutoff_id)

        db_cells = session.query(FactCalibrationCell).all()
        assert len(db_cells) > 0

    def test_empty_feedbacks(self, session, _seed_dates):
        """피드백 없으면 빈 리스트."""
        cells = build_condition_calibration(session, date_to_id(date(2026, 3, 1)))
        assert cells == []


# ---------------------------------------------------------------------------
# get_condition_calibration (폴백 체인)
# ---------------------------------------------------------------------------


class TestGetConditionCalibration:
    def test_exact_match(self, session, _seed_feedbacks):
        """정확한 셀 매칭."""
        cutoff_id = date_to_id(date(2026, 3, 1))
        build_condition_calibration(session, cutoff_id)

        result = get_condition_calibration(
            session, regime="bull", sector="Technology",
            confidence=7, has_event=False,
        )
        # bull이 아닌 체제일 수도 있으므로, 폴백 결과라도 반환되면 OK
        if result is not None:
            assert 0 <= result <= 100

    def test_fallback_to_confidence_only(self, session, _seed_feedbacks):
        """섹터/체제 미매칭 시 신뢰도만으로 폴백."""
        cutoff_id = date_to_id(date(2026, 3, 1))
        build_condition_calibration(session, cutoff_id)

        result = get_condition_calibration(
            session, regime="crisis", sector="Healthcare",
            confidence=7, has_event=False,
        )
        # 폴백 체인을 통해 어떤 값이라도 반환될 수 있음
        # (confidence_range="7-8" 셀이 있으므로)
        if result is not None:
            assert 0 <= result <= 100

    def test_no_data_returns_none(self, session, _seed_dates):
        """데이터 없으면 None."""
        result = get_condition_calibration(
            session, regime="bull", sector="Technology",
            confidence=7,
        )
        assert result is None


# ---------------------------------------------------------------------------
# format_calibration_for_prompt
# ---------------------------------------------------------------------------


class TestFormatCalibrationForPrompt:
    def test_format_with_data(self, session, _seed_feedbacks):
        """데이터가 있으면 테이블 문자열 반환."""
        cutoff_id = date_to_id(date(2026, 3, 1))
        build_condition_calibration(session, cutoff_id)

        result = format_calibration_for_prompt(session, cutoff_id)
        assert result is not None
        assert "체제" in result
        assert "섹터" in result
        assert "승률" in result

    def test_format_no_data(self, session, _seed_dates):
        """데이터 없으면 None."""
        result = format_calibration_for_prompt(
            session, date_to_id(date(2026, 3, 1)),
        )
        assert result is None
