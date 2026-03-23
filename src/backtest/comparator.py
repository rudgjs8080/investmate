"""가중치 비교 — 다른 가중치 세트로 추천 재랭킹 후 실제 수익률 비교.

저장된 dimension별 점수(technical_score 등)를 활용하여
대안 가중치로 상위 N개를 재선정하고, 실제 수익률과 비교한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import DimStock, FactDailyRecommendation

logger = logging.getLogger(__name__)

# 기본 가중치 (screener.py와 동일)
DEFAULT_WEIGHTS = {
    "technical": 0.25,
    "fundamental": 0.25,
    "smart_money": 0.15,
    "external": 0.15,
    "momentum": 0.20,
}


@dataclass(frozen=True)
class WeightComparisonResult:
    """가중치 비교 결과."""

    weights: dict[str, float]
    label: str
    total_picks: int
    avg_return_20d: float | None
    win_rate_20d: float | None


def compare_weights(
    session: Session,
    start_date: date,
    end_date: date,
    weight_sets: list[tuple[str, dict[str, float]]],
    top_n: int = 10,
) -> list[WeightComparisonResult]:
    """여러 가중치 세트로 재랭킹하여 성과를 비교한다.

    Args:
        weight_sets: [(라벨, 가중치딕셔너리), ...]
        top_n: 상위 N개만 비교

    Returns:
        각 가중치 세트의 비교 결과 리스트.
    """
    start_id = date_to_id(start_date)
    end_id = date_to_id(end_date)

    stmt = (
        select(FactDailyRecommendation, DimStock.ticker)
        .join(DimStock, FactDailyRecommendation.stock_id == DimStock.stock_id)
        .where(FactDailyRecommendation.run_date_id >= start_id)
        .where(FactDailyRecommendation.run_date_id <= end_id)
        .order_by(FactDailyRecommendation.run_date_id)
    )
    rows = session.execute(stmt).all()

    if not rows:
        return []

    # 날짜별 그룹핑
    by_date: dict[int, list[tuple]] = {}
    for rec, ticker in rows:
        by_date.setdefault(rec.run_date_id, []).append((rec, ticker))

    results = []
    for label, weights in weight_sets:
        all_returns: list[float] = []

        for date_id, recs_tickers in by_date.items():
            # 새 가중치로 재스코어링
            scored = []
            for rec, ticker in recs_tickers:
                new_total = (
                    float(rec.technical_score) * weights.get("technical", 0)
                    + float(rec.fundamental_score) * weights.get("fundamental", 0)
                    + float(rec.smart_money_score) * weights.get("smart_money", 0)
                    + float(rec.external_score) * weights.get("external", 0)
                    + float(rec.momentum_score) * weights.get("momentum", 0)
                )
                scored.append((new_total, rec, ticker))

            # 새 점수 기준 정렬 → 상위 N개
            scored.sort(key=lambda x: x[0], reverse=True)
            top_picks = scored[:top_n]

            for _, rec, _ in top_picks:
                if rec.return_20d is not None:
                    all_returns.append(float(rec.return_20d))

        avg_r = sum(all_returns) / len(all_returns) if all_returns else None
        wins = sum(1 for r in all_returns if r > 0)
        wr = (wins / len(all_returns) * 100) if all_returns else None

        results.append(WeightComparisonResult(
            weights=weights,
            label=label,
            total_picks=len(all_returns),
            avg_return_20d=avg_r,
            win_rate_20d=wr,
        ))

    return results
