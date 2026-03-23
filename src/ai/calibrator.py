"""AI 목표가/손절가 캘리브레이션 — 과거 편향 기반 자동 보정."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FactAIFeedback, FactDailyRecommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalibrationResult:
    """캘리브레이션 결과."""

    target_adjustment: float = 1.0  # 목표가 보정 계수 (1.0 = 보정 없음)
    stop_adjustment: float = 1.0  # 손절가 보정 계수
    is_optimistic: bool = False  # 목표가 과대추정 성향
    is_pessimistic: bool = False  # 목표가 과소추정 성향
    sample_size: int = 0
    avg_target_error_pct: float = 0.0


def calculate_calibration(
    session: Session, cutoff_date_id: int | None = None,
) -> CalibrationResult:
    """과거 AI 예측의 편향을 분석하여 보정 계수를 계산한다.

    Args:
        session: DB 세션.
        cutoff_date_id: 이 날짜 이전 추천의 피드백만 사용 (look-ahead bias 방지).
            None이면 전체 피드백 사용 (하위 호환).

    Returns:
        CalibrationResult with adjustment factors.
    """
    stmt = (
        select(FactAIFeedback)
        .where(FactAIFeedback.ai_approved == True)  # noqa: E712
        .where(FactAIFeedback.target_error_pct.isnot(None))
    )
    if cutoff_date_id is not None:
        # look-ahead bias 방지: cutoff 이전 추천에 대한 피드백만 사용
        stmt = stmt.where(
            FactAIFeedback.recommendation_id.in_(
                select(FactDailyRecommendation.recommendation_id)
                .where(FactDailyRecommendation.run_date_id <= cutoff_date_id)
            )
        )
    feedbacks = session.execute(stmt).scalars().all()

    if len(feedbacks) < 5:
        return CalibrationResult(sample_size=len(feedbacks))

    errors = [float(f.target_error_pct) for f in feedbacks]
    avg_error = sum(errors) / len(errors)

    # 양수 에러 = 과대추정 (목표가 > 실제), 음수 = 과소추정
    is_optimistic = avg_error > 3.0  # 평균 3% 이상 과대추정
    is_pessimistic = avg_error < -3.0

    # 보정 계수: 과대추정 시 목표가를 낮춤
    if is_optimistic:
        target_adj = max(0.85, 1.0 - avg_error / 100)  # 최대 15% 하향
    elif is_pessimistic:
        target_adj = min(1.15, 1.0 - avg_error / 100)  # 최대 15% 상향
    else:
        target_adj = 1.0

    # 손절가: 실제로 손절 타격률 기반 보정
    stop_hits = [f for f in feedbacks if f.stop_hit is True]
    if len(stop_hits) > len(feedbacks) * 0.3:
        # 30% 이상 손절 타격 → 손절가가 너무 가까움 → 넓힘
        stop_adj = 0.95  # 5% 더 넓게
    else:
        stop_adj = 1.0

    return CalibrationResult(
        target_adjustment=round(target_adj, 3),
        stop_adjustment=round(stop_adj, 3),
        is_optimistic=is_optimistic,
        is_pessimistic=is_pessimistic,
        sample_size=len(feedbacks),
        avg_target_error_pct=round(avg_error, 2),
    )


def apply_calibration(parsed: list[dict], calibration: CalibrationResult) -> list[dict]:
    """AI 응답에 캘리브레이션을 적용한다.

    Args:
        parsed: parse_ai_response 결과.
        calibration: 캘리브레이션 결과.

    Returns:
        보정된 parsed 리스트 (원본 수정).
    """
    if calibration.sample_size < 5:
        return parsed  # 데이터 부족 → 보정 안 함

    for p in parsed:
        if not p.get("ai_approved"):
            continue

        if p.get("ai_target_price") and calibration.target_adjustment != 1.0:
            original = p["ai_target_price"]
            p["ai_target_price"] = round(original * calibration.target_adjustment, 2)
            logger.debug(
                "%s 목표가 보정: $%.0f → $%.0f (계수 %.3f)",
                p.get("ticker"), original, p["ai_target_price"], calibration.target_adjustment,
            )

        if p.get("ai_stop_loss") and calibration.stop_adjustment != 1.0:
            original = p["ai_stop_loss"]
            p["ai_stop_loss"] = round(original * calibration.stop_adjustment, 2)

    return parsed
