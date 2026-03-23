"""AI 프롬프트 성과 평가 모듈."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import FactAIFeedback, FactDailyRecommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluationResult:
    """프롬프트 평가 결과."""

    version: str
    period: str
    total_recommendations: int
    direction_accuracy: float | None
    avg_return_20d: float | None
    win_rate_20d: float | None
    avg_target_error: float | None
    ece: float | None


def evaluate_ai_performance(
    session: Session,
    start_date: date | None = None,
    end_date: date | None = None,
) -> EvaluationResult:
    """AI 분석 성과를 평가한다."""
    stmt = select(FactAIFeedback).where(FactAIFeedback.return_20d.isnot(None))

    feedbacks = list(session.execute(stmt).scalars().all())

    if not feedbacks:
        return EvaluationResult(
            version="current", period="all",
            total_recommendations=0, direction_accuracy=None,
            avg_return_20d=None, win_rate_20d=None,
            avg_target_error=None, ece=None,
        )

    # Direction accuracy
    dir_items = [f for f in feedbacks if f.direction_correct is not None]
    correct = sum(1 for f in dir_items if f.direction_correct)
    dir_acc = round(correct / len(dir_items) * 100, 1) if dir_items else None

    # Returns
    returns = [float(f.return_20d) for f in feedbacks if f.return_20d is not None]
    avg_ret = round(sum(returns) / len(returns), 2) if returns else None
    wins = sum(1 for r in returns if r > 0)
    win_rate = round(wins / len(returns) * 100, 1) if returns else None

    # Target error
    errors = [float(f.target_error_pct) for f in feedbacks if f.target_error_pct is not None]
    avg_err = round(sum(errors) / len(errors), 2) if errors else None

    # ECE
    from src.ai.feedback import compute_calibration_curve, compute_ece
    curve = compute_calibration_curve(session)
    ece = compute_ece(curve)

    period = f"{start_date or 'all'}~{end_date or 'now'}"
    return EvaluationResult(
        version="current", period=period,
        total_recommendations=len(feedbacks),
        direction_accuracy=dir_acc, avg_return_20d=avg_ret,
        win_rate_20d=win_rate, avg_target_error=avg_err, ece=ece,
    )
