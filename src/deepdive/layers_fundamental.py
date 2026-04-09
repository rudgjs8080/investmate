"""Layer 1: 펀더멘털 헬스체크."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FactEarningsSurprise, FactFinancial, FactValuation
from src.deepdive.layers_utils import calc_ratio, round_or_none, sf
from src.deepdive.schemas import FundamentalHealth

logger = logging.getLogger(__name__)


def compute_layer1_fundamental(
    session: Session, stock_id: int,
) -> FundamentalHealth | None:
    """펀더멘털 헬스체크: F-Score, Z-Score, 마진 추세, ROE, 실적 beat."""
    try:
        return _compute(session, stock_id)
    except Exception as e:
        logger.warning("Layer 1 계산 실패 (stock_id=%d): %s", stock_id, e)
        return None


def _compute(session: Session, stock_id: int) -> FundamentalHealth | None:
    financials = list(
        session.execute(
            select(FactFinancial)
            .where(FactFinancial.stock_id == stock_id)
            .order_by(FactFinancial.period.desc())
            .limit(8)
        ).scalars().all()
    )
    if len(financials) < 2:
        return None

    cur, prev = financials[0], financials[1]

    from src.analysis.quality import calculate_altman_z, calculate_piotroski

    valuation = session.execute(
        select(FactValuation)
        .where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    f_result = calculate_piotroski(cur, prev, valuation)
    f_score = f_result.score

    market_cap = float(valuation.market_cap) if valuation and valuation.market_cap else None
    z_result = calculate_altman_z(cur, market_cap)
    z_score = z_result.z_score if z_result else None

    def _margin(fin, kind: str) -> float | None:
        rev = sf(fin.revenue)
        if not rev or rev == 0:
            return None
        if kind == "gross":
            oi = sf(fin.operating_income)
            return (oi / rev * 100) if oi is not None else None
        if kind == "net":
            ni = sf(fin.net_income)
            return (ni / rev * 100) if ni is not None else None
        return None

    gross_margin = _margin(cur, "gross")
    net_margin = _margin(cur, "net")
    operating_margin = gross_margin

    margin_trend = _detect_margin_trend(financials[:4])
    roe = calc_ratio(sf(cur.net_income), sf(cur.total_equity))
    debt_ratio = calc_ratio(sf(cur.total_liabilities), sf(cur.total_assets))

    surprises = list(
        session.execute(
            select(FactEarningsSurprise)
            .where(FactEarningsSurprise.stock_id == stock_id)
            .order_by(FactEarningsSurprise.date_id.desc())
            .limit(4)
        ).scalars().all()
    )
    beat_streak = _count_beat_streak(surprises)
    grade = _grade_fundamental(f_score, z_score)

    return FundamentalHealth(
        health_grade=grade,
        f_score=f_score,
        z_score=round(z_score, 2) if z_score is not None else None,
        margin_trend=margin_trend,
        gross_margin=round_or_none(gross_margin),
        operating_margin=round_or_none(operating_margin),
        net_margin=round_or_none(net_margin),
        roe=round_or_none(roe),
        debt_ratio=round_or_none(debt_ratio),
        earnings_beat_streak=beat_streak,
        metrics={
            "f_score_details": f_result.details,
            "z_zone": z_result.zone if z_result else None,
            "quarters_available": len(financials),
        },
    )


def _grade_fundamental(f_score: int, z_score: float | None) -> str:
    z_safe = z_score is not None and z_score >= 3.0
    z_danger = z_score is not None and z_score < 1.8
    if f_score >= 7 and (z_safe or z_score is None):
        return "A"
    if f_score >= 5:
        return "B"
    if f_score >= 3 and not z_danger:
        return "C"
    if z_danger or f_score < 3:
        return "D" if not (f_score < 3 and z_danger) else "F"
    return "C"


def _detect_margin_trend(financials: list) -> str:
    margins = []
    for f in financials:
        rev = sf(f.revenue)
        oi = sf(f.operating_income)
        if rev and rev > 0 and oi is not None:
            margins.append(oi / rev)
    if len(margins) < 2:
        return "stable"
    improving = sum(1 for i in range(len(margins) - 1) if margins[i] > margins[i + 1])
    if improving >= len(margins) - 1:
        return "improving"
    declining = sum(1 for i in range(len(margins) - 1) if margins[i] < margins[i + 1])
    if declining >= len(margins) - 1:
        return "declining"
    return "stable"


def _count_beat_streak(surprises: list) -> int:
    streak = 0
    for s in surprises:
        sp = sf(getattr(s, "surprise_pct", None))
        if sp is not None and sp > 0:
            streak += 1
        else:
            break
    return streak
