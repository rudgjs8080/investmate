"""ML 모델 성능 평가 — Walk-Forward Validation."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluationResult:
    """모델 평가 결과."""

    data_days: int
    total_predictions: int
    accuracy: float  # 방향 정확도 (%)
    precision_at_10: float  # 상위 10개 중 실제 양수 수익 비율
    avg_return_positive: float  # 양수 예측의 평균 수익률
    avg_return_negative: float  # 음수 예측의 평균 수익률
    status: str


def evaluate_model(session: Session) -> dict:
    """ML 모델 예측 성과를 실제 수익률 대비 평가한다.

    fact_daily_recommendations에 저장된 과거 추천의 return_20d를 기반으로
    모델의 예측 정확도를 측정한다.
    """
    from sqlalchemy import select

    from src.db.models import FactDailyRecommendation

    # return_20d가 채워진 추천 이력 조회
    stmt = (
        select(FactDailyRecommendation)
        .where(FactDailyRecommendation.return_20d.isnot(None))
        .order_by(FactDailyRecommendation.run_date_id.desc())
    )
    recs = session.execute(stmt).scalars().all()

    if not recs:
        logger.info("평가 데이터 없음: return_20d가 채워진 추천 없음")
        return {"status": "데이터 부족", "message": "return_20d가 채워진 추천 데이터 필요"}

    # 기본 통계
    returns = [float(r.return_20d) for r in recs]
    positive_count = sum(1 for r in returns if r > 0)
    total = len(returns)

    accuracy = positive_count / total * 100 if total > 0 else 0

    # Precision@10: 가장 높은 점수의 종목 10개 중 양수 수익 비율
    # 날짜별로 그룹핑하여 상위 10개의 양수 수익 비율 계산
    date_groups: dict[int, list] = {}
    for r in recs:
        date_groups.setdefault(r.run_date_id, []).append(r)

    precision_scores = []
    for date_id, group in date_groups.items():
        top10 = sorted(group, key=lambda x: float(x.total_score), reverse=True)[:10]
        if top10:
            hits = sum(1 for r in top10 if float(r.return_20d) > 0)
            precision_scores.append(hits / len(top10) * 100)

    avg_precision = sum(precision_scores) / len(precision_scores) if precision_scores else 0

    # 양수/음수 예측의 평균 수익률
    positive_returns = [r for r in returns if r > 0]
    negative_returns = [r for r in returns if r <= 0]

    avg_pos = sum(positive_returns) / len(positive_returns) if positive_returns else 0
    avg_neg = sum(negative_returns) / len(negative_returns) if negative_returns else 0

    result = EvaluationResult(
        data_days=len(date_groups),
        total_predictions=total,
        accuracy=round(accuracy, 1),
        precision_at_10=round(avg_precision, 1),
        avg_return_positive=round(avg_pos, 2),
        avg_return_negative=round(avg_neg, 2),
        status="평가 완료",
    )

    logger.info(
        "ML 평가: %d일 %d건 | 정확도 %.1f%% | P@10 %.1f%% | 양수평균 %.2f%% | 음수평균 %.2f%%",
        result.data_days, result.total_predictions, result.accuracy,
        result.precision_at_10, result.avg_return_positive, result.avg_return_negative,
    )

    return {
        "status": result.status,
        "data_days": result.data_days,
        "total_predictions": result.total_predictions,
        "accuracy": result.accuracy,
        "precision_at_10": result.precision_at_10,
        "avg_return_positive": result.avg_return_positive,
        "avg_return_negative": result.avg_return_negative,
    }
