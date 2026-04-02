"""반사실 분석 — AI 의사결정의 대안 시나리오 시뮬레이션 (Phase 6)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import DimStock, FactDailyRecommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CounterfactualResult:
    """반사실 분석 결과."""

    ticker: str
    original_decision: str  # "approved" | "excluded"
    original_return: float | None
    counterfactual_return: float | None
    delta: float | None  # counterfactual - original
    lesson: str


def compute_counterfactuals(
    session: Session,
    run_date_id: int,
    lookback_days: int = 60,
) -> list[CounterfactualResult]:
    """AI가 스크리너를 오버라이드한 과거 케이스에 대해 반대 결정 시뮬레이션.

    Case 1: AI가 높은 점수(>= 7.0) 종목을 거부한 경우 → 실제로 올랐으면 AI가 틀림
    Case 2: AI가 낮은 점수(< 6.0) 종목을 승인한 경우 → 실제로 떨어졌으면 AI가 틀림
    """
    results: list[CounterfactualResult] = []

    cutoff = date.today() - timedelta(days=lookback_days + 35)
    cutoff_id = date_to_id(cutoff)

    recs = (
        session.execute(
            select(FactDailyRecommendation).where(
                FactDailyRecommendation.ai_approved.isnot(None),
                FactDailyRecommendation.return_20d.isnot(None),
                FactDailyRecommendation.run_date_id >= cutoff_id,
                FactDailyRecommendation.run_date_id <= run_date_id,
            )
        )
        .scalars()
        .all()
    )

    for rec in recs:
        total_score = float(rec.total_score) if rec.total_score else 5.0
        ret_20d = float(rec.return_20d) if rec.return_20d is not None else None

        if ret_20d is None:
            continue

        stock = session.scalar(
            select(DimStock.ticker).where(DimStock.stock_id == rec.stock_id)
        )
        ticker = stock if stock else f"ID:{rec.stock_id}"

        # Case 1: High score (>=7) but AI excluded
        if total_score >= 7.0 and rec.ai_approved is False:
            if ret_20d > 0:
                lesson = (
                    f"{ticker}: 고득점({total_score:.1f}) 종목을 거부했으나 "
                    f"+{ret_20d:.1f}% 상승. 향후 유사 조건에서 재검토 필요."
                )
            else:
                lesson = (
                    f"{ticker}: 고득점({total_score:.1f}) 종목 거부가 올바른 판단. "
                    f"{ret_20d:.1f}% 하락."
                )
            results.append(
                CounterfactualResult(
                    ticker=ticker,
                    original_decision="excluded",
                    original_return=ret_20d,
                    counterfactual_return=ret_20d,
                    delta=ret_20d,
                    lesson=lesson,
                )
            )

        # Case 2: Low score (<6) but AI approved
        elif total_score < 6.0 and rec.ai_approved is True:
            if ret_20d < 0:
                lesson = (
                    f"{ticker}: 저득점({total_score:.1f}) 종목을 승인했으나 "
                    f"{ret_20d:.1f}% 하락. 향후 점수 하한 조정 필요."
                )
            else:
                lesson = (
                    f"{ticker}: 저득점({total_score:.1f}) 종목 승인이 올바른 판단. "
                    f"+{ret_20d:.1f}% 상승."
                )
            results.append(
                CounterfactualResult(
                    ticker=ticker,
                    original_decision="approved",
                    original_return=ret_20d,
                    counterfactual_return=0.0,
                    delta=-ret_20d if ret_20d < 0 else 0.0,
                    lesson=lesson,
                )
            )

    # Sort by absolute delta (biggest missed opportunities/avoided losses first)
    results.sort(key=lambda r: abs(r.delta or 0), reverse=True)
    return results[:10]


def format_counterfactuals_for_prompt(
    results: list[CounterfactualResult],
) -> str | None:
    """반사실 분석 결과를 프롬프트에 삽입할 텍스트로 변환."""
    if not results:
        return None

    lines = ["과거 AI 오버라이드 분석 (반사실):"]
    for i, r in enumerate(results[:3], 1):
        lines.append(f"{i}. {r.lesson}")

    return "\n".join(lines)
