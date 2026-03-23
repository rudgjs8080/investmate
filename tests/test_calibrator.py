"""AI 캘리브레이션 테스트."""

import pytest

from src.ai.calibrator import CalibrationResult, apply_calibration, calculate_calibration
from src.db.models import FactAIFeedback, FactDailyRecommendation


class TestApplyCalibration:
    def test_no_calibration_when_insufficient_data(self):
        parsed = [{"ticker": "AAPL", "ai_approved": True, "ai_target_price": 200.0}]
        cal = CalibrationResult(sample_size=2)  # 5 미만
        result = apply_calibration(parsed, cal)
        assert result[0]["ai_target_price"] == 200.0  # 보정 없음

    def test_optimistic_correction(self):
        parsed = [{"ticker": "AAPL", "ai_approved": True, "ai_target_price": 200.0}]
        cal = CalibrationResult(
            target_adjustment=0.9, is_optimistic=True, sample_size=10,
        )
        apply_calibration(parsed, cal)
        assert parsed[0]["ai_target_price"] == 180.0  # 10% 하향

    def test_stop_loss_correction(self):
        parsed = [{"ticker": "AAPL", "ai_approved": True, "ai_stop_loss": 100.0}]
        cal = CalibrationResult(
            stop_adjustment=0.95, sample_size=10,
        )
        apply_calibration(parsed, cal)
        assert parsed[0]["ai_stop_loss"] == 95.0  # 5% 넓힘

    def test_excluded_not_calibrated(self):
        parsed = [{"ticker": "AAPL", "ai_approved": False, "ai_target_price": 200.0}]
        cal = CalibrationResult(target_adjustment=0.8, sample_size=10)
        apply_calibration(parsed, cal)
        assert parsed[0]["ai_target_price"] == 200.0  # 제외 종목은 보정 안 함


class TestCalibrationResult:
    def test_defaults(self):
        cal = CalibrationResult()
        assert cal.target_adjustment == 1.0
        assert cal.stop_adjustment == 1.0
        assert cal.is_optimistic is False
        assert cal.sample_size == 0


def _make_rec(session, stock_id: int, run_date_id: int) -> FactDailyRecommendation:
    """헬퍼: 추천 레코드 생성."""
    rec = FactDailyRecommendation(
        run_date_id=run_date_id,
        stock_id=stock_id,
        rank=1,
        total_score=7.0,
        technical_score=7.0,
        fundamental_score=6.0,
        external_score=5.0,
        momentum_score=6.0,
        smart_money_score=5.0,
        recommendation_reason="test",
        price_at_recommendation=100.0,
    )
    session.add(rec)
    session.flush()
    return rec


def _make_feedback(
    session, rec: FactDailyRecommendation, *, target_error_pct: float = 5.0,
) -> FactAIFeedback:
    """헬퍼: AI 피드백 레코드 생성."""
    fb = FactAIFeedback(
        recommendation_id=rec.recommendation_id,
        run_date_id=rec.run_date_id,
        stock_id=rec.stock_id,
        ticker="AAPL",
        ai_approved=True,
        target_error_pct=target_error_pct,
    )
    session.add(fb)
    session.flush()
    return fb


class TestCalibrationCutoff:
    """cutoff_date_id에 의한 look-ahead bias 방지 테스트."""

    def test_calibration_with_cutoff(self, sample_stock, seeded_session):
        """cutoff 이전 피드백만 사용한다."""
        sid = sample_stock["id"]
        # 오래된 추천 (date_id=20260101) — cutoff 이전
        for i in range(6):
            rec = _make_rec(seeded_session, sid, 20260101)
            _make_feedback(seeded_session, rec, target_error_pct=5.0)
        # 최근 추천 (date_id=20260320) — cutoff 이후
        for i in range(6):
            rec = _make_rec(seeded_session, sid, 20260320)
            _make_feedback(seeded_session, rec, target_error_pct=-10.0)
        seeded_session.commit()

        # cutoff=20260201 → 20260101 피드백(+5.0)만 사용
        result = calculate_calibration(seeded_session, cutoff_date_id=20260201)
        assert result.sample_size == 6
        assert result.avg_target_error_pct == 5.0
        assert result.is_optimistic is True

    def test_calibration_without_cutoff(self, sample_stock, seeded_session):
        """cutoff 없으면 전체 피드백 사용 (하위 호환)."""
        sid = sample_stock["id"]
        for i in range(6):
            rec = _make_rec(seeded_session, sid, 20260101)
            _make_feedback(seeded_session, rec, target_error_pct=5.0)
        for i in range(6):
            rec = _make_rec(seeded_session, sid, 20260320)
            _make_feedback(seeded_session, rec, target_error_pct=-5.0)
        seeded_session.commit()

        result = calculate_calibration(seeded_session)  # no cutoff
        assert result.sample_size == 12
        # 평균 = (6*5 + 6*(-5)) / 12 = 0.0
        assert abs(result.avg_target_error_pct) < 0.1

    def test_calibration_cutoff_excludes_recent(self, sample_stock, seeded_session):
        """최근 피드백이 제외되는지 확인."""
        sid = sample_stock["id"]
        # 모든 피드백이 최근 날짜
        for i in range(6):
            rec = _make_rec(seeded_session, sid, 20260320)
            _make_feedback(seeded_session, rec, target_error_pct=8.0)
        seeded_session.commit()

        # cutoff=20260101 → 모든 피드백 제외 → sample_size < 5
        result = calculate_calibration(seeded_session, cutoff_date_id=20260101)
        assert result.sample_size == 0
        assert result.target_adjustment == 1.0  # 보정 없음
