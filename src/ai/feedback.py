"""AI 예측 피드백 시스템 — 과거 예측 vs 실제 결과를 추적하고 분석한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import (
    DimStock,
    FactAIFeedback,
    FactDailyPrice,
    FactDailyRecommendation,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AIPerformanceSummary:
    """AI 성과 요약."""

    total_predictions: int = 0
    ai_approved_count: int = 0
    ai_excluded_count: int = 0
    win_rate_approved: float | None = None  # AI 추천 종목의 승률
    win_rate_excluded: float | None = None  # AI 제외 종목의 승률 (낮을수록 좋음)
    avg_return_approved: float | None = None
    avg_return_excluded: float | None = None
    avg_target_error_pct: float | None = None  # 목표가 오차 평균
    direction_accuracy: float | None = None  # 방향 예측 정확도
    sector_accuracy: dict[str, float] | None = None  # 섹터별 승률
    confidence_calibration: dict[int, float] | None = None  # 신뢰도별 실제 승률
    overestimate_rate: float | None = None  # 목표가 과대추정 비율


def collect_ai_feedback(session: Session, days_back: int = 90) -> int:
    """과거 AI 예측의 실제 결과를 수집하여 fact_ai_feedback에 저장한다.

    Args:
        days_back: 몇 일 전까지의 추천을 평가할지.

    Returns:
        업데이트된 피드백 수.
    """
    from src.db.helpers import id_to_date

    cutoff_date = date.today()
    cutoff_id = date_to_id(cutoff_date)

    # AI 분석이 완료된 추천 중 아직 피드백이 없는 것들
    existing_feedback = set(
        session.execute(select(FactAIFeedback.recommendation_id)).scalars().all()
    )

    recs = session.execute(
        select(FactDailyRecommendation)
        .where(FactDailyRecommendation.ai_approved.isnot(None))
        .where(FactDailyRecommendation.return_20d.isnot(None))
    ).scalars().all()

    count = 0
    for rec in recs:
        if rec.recommendation_id in existing_feedback:
            continue

        stock = session.execute(
            select(DimStock).where(DimStock.stock_id == rec.stock_id)
        ).scalar_one_or_none()
        if not stock:
            continue

        # 실제 가격 (20일 후)
        price_at = float(rec.price_at_recommendation) if rec.price_at_recommendation else None
        return_20d = float(rec.return_20d) if rec.return_20d is not None else None
        actual_20d = price_at * (1 + return_20d / 100) if price_at and return_20d is not None else None

        # 방향 예측 정확도
        direction_correct = None
        if rec.ai_approved is not None and return_20d is not None:
            if rec.ai_approved:
                direction_correct = return_20d > 0  # 추천했고 실제로 올랐으면 True
            else:
                direction_correct = return_20d <= 0  # 제외했고 실제로 안 올랐으면 True

        # 목표가 도달 여부
        target_hit = None
        target_error = None
        if rec.ai_target_price and actual_20d and price_at:
            target_hit = actual_20d >= float(rec.ai_target_price)
            target_error = round(
                (float(rec.ai_target_price) - actual_20d) / price_at * 100, 2
            )

        # 손절 도달 여부
        stop_hit = None
        if rec.ai_stop_loss and return_20d is not None and price_at:
            min_price_approx = price_at * (1 + min(0, return_20d) / 100)
            stop_hit = min_price_approx <= float(rec.ai_stop_loss)

        feedback = FactAIFeedback(
            recommendation_id=rec.recommendation_id,
            run_date_id=rec.run_date_id,
            stock_id=rec.stock_id,
            ticker=stock.ticker,
            sector=stock.sector.sector_name if stock.sector else None,
            ai_approved=rec.ai_approved,
            ai_confidence=int(rec.ai_confidence) if rec.ai_confidence is not None else None,
            ai_target_price=float(rec.ai_target_price) if rec.ai_target_price else None,
            ai_stop_loss=float(rec.ai_stop_loss) if rec.ai_stop_loss else None,
            price_at_rec=price_at,
            actual_price_20d=actual_20d,
            return_20d=return_20d,
            direction_correct=direction_correct,
            target_hit=target_hit,
            stop_hit=stop_hit,
            target_error_pct=target_error,
        )
        session.add(feedback)
        count += 1

    if count:
        session.flush()
    logger.info("AI 피드백 수집: %d건", count)
    return count


def calculate_ai_performance(session: Session, days_back: int = 90) -> AIPerformanceSummary:
    """AI 성과를 분석한다.

    Returns:
        AIPerformanceSummary with detailed metrics.
    """
    feedbacks = session.execute(select(FactAIFeedback)).scalars().all()
    if not feedbacks:
        return AIPerformanceSummary()

    approved = [f for f in feedbacks if f.ai_approved is True]
    excluded = [f for f in feedbacks if f.ai_approved is False]

    # 승률
    def _win_rate(items: list) -> float | None:
        with_returns = [f for f in items if f.return_20d is not None]
        if not with_returns:
            return None
        wins = sum(1 for f in with_returns if float(f.return_20d) > 0)
        return round(wins / len(with_returns) * 100, 1)

    def _avg_return(items: list) -> float | None:
        with_returns = [f for f in items if f.return_20d is not None]
        if not with_returns:
            return None
        return round(sum(float(f.return_20d) for f in with_returns) / len(with_returns), 2)

    # 방향 정확도
    dir_items = [f for f in feedbacks if f.direction_correct is not None]
    dir_accuracy = round(sum(1 for f in dir_items if f.direction_correct) / len(dir_items) * 100, 1) if dir_items else None

    # 목표가 오차
    target_errors = [float(f.target_error_pct) for f in feedbacks if f.target_error_pct is not None]
    avg_target_error = round(sum(target_errors) / len(target_errors), 2) if target_errors else None
    overestimate = round(sum(1 for e in target_errors if e > 0) / len(target_errors) * 100, 1) if target_errors else None

    # 섹터별 승률
    sector_data: dict[str, list[float]] = {}
    for f in approved:
        if f.sector and f.return_20d is not None:
            sector_data.setdefault(f.sector, []).append(float(f.return_20d))
    sector_accuracy = {
        s: round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1)
        for s, rets in sector_data.items() if rets
    } or None

    # 신뢰도별 승률
    conf_data: dict[int, list[float]] = {}
    for f in approved:
        if f.ai_confidence is not None and f.return_20d is not None:
            conf_data.setdefault(int(f.ai_confidence), []).append(float(f.return_20d))
    conf_calibration = {
        c: round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1)
        for c, rets in conf_data.items() if rets
    } or None

    return AIPerformanceSummary(
        total_predictions=len(feedbacks),
        ai_approved_count=len(approved),
        ai_excluded_count=len(excluded),
        win_rate_approved=_win_rate(approved),
        win_rate_excluded=_win_rate(excluded),
        avg_return_approved=_avg_return(approved),
        avg_return_excluded=_avg_return(excluded),
        avg_target_error_pct=avg_target_error,
        direction_accuracy=dir_accuracy,
        sector_accuracy=sector_accuracy,
        confidence_calibration=conf_calibration,
        overestimate_rate=overestimate,
    )


def compute_calibration_curve(session: Session) -> dict[int, dict]:
    """AI 신뢰도별 실제 정확도를 계산한다 (캘리브레이션 커브).

    Returns:
        {1: {"predicted": 0.1, "actual": 0.05, "count": 3, "gap": -0.05}, ...}
    """
    feedbacks = list(session.execute(
        select(FactAIFeedback)
        .where(FactAIFeedback.ai_confidence.isnot(None))
        .where(FactAIFeedback.return_20d.isnot(None))
    ).scalars().all())

    if not feedbacks:
        return {}

    curve: dict[int, dict] = {}
    for level in range(1, 11):
        entries = [f for f in feedbacks if f.ai_confidence == level]
        if not entries:
            continue
        predicted = level / 10.0
        wins = sum(1 for f in entries if f.return_20d is not None and float(f.return_20d) > 0)
        actual = wins / len(entries) if entries else 0
        curve[level] = {
            "predicted": predicted,
            "actual": round(actual, 3),
            "count": len(entries),
            "gap": round(actual - predicted, 3),
        }
    return curve


def compute_ece(calibration_curve: dict[int, dict]) -> float:
    """Expected Calibration Error를 계산한다."""
    if not calibration_curve:
        return 0.0
    total = sum(entry["count"] for entry in calibration_curve.values())
    if total == 0:
        return 0.0
    ece = sum(
        abs(entry["gap"]) * entry["count"] / total
        for entry in calibration_curve.values()
    )
    return round(ece, 4)
