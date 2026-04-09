"""Layer 2: 밸류에이션 컨텍스트."""

from __future__ import annotations

import logging

from sqlalchemy import select, func as sa_func
from sqlalchemy.orm import Session

from src.db.models import FactFinancial, FactValuation
from src.deepdive.layers_utils import round_or_none, sf
from src.deepdive.schemas import ValuationContext

logger = logging.getLogger(__name__)


def compute_layer2_valuation(
    session: Session, stock_id: int, sector_id: int | None,
) -> ValuationContext | None:
    """밸류에이션 컨텍스트: 5년 백분위, 섹터 대비, DCF, PEG, FCF yield."""
    try:
        return _compute(session, stock_id, sector_id)
    except Exception as e:
        logger.warning("Layer 2 계산 실패 (stock_id=%d): %s", stock_id, e)
        return None


def _compute(
    session: Session, stock_id: int, sector_id: int | None,
) -> ValuationContext | None:
    # 최신 밸류에이션
    current_val = session.execute(
        select(FactValuation)
        .where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if current_val is None:
        return None

    cur_per = sf(current_val.per)
    cur_pbr = sf(current_val.pbr)
    cur_ev_ebitda = sf(current_val.ev_ebitda)
    market_cap = sf(current_val.market_cap)

    # 5년 히스토리 (~1260거래일)
    history = list(
        session.execute(
            select(FactValuation)
            .where(FactValuation.stock_id == stock_id)
            .order_by(FactValuation.date_id.desc())
            .limit(1260)
        ).scalars().all()
    )

    per_pct = _percentile(history, "per", cur_per)
    pbr_pct = _percentile(history, "pbr", cur_pbr)
    ev_pct = _percentile(history, "ev_ebitda", cur_ev_ebitda)

    # 섹터 대비
    sector_per_prem, sector_pbr_prem = _sector_premium(
        session, sector_id, stock_id, cur_per, cur_pbr,
    )

    # DCF implied growth
    dcf_growth = _dcf_implied_growth(session, stock_id, market_cap)

    # PEG
    peg = _compute_peg(session, stock_id, cur_per)

    # FCF yield
    fcf_yield = _compute_fcf_yield(session, stock_id, market_cap)

    # 그레이드
    grade = _grade_valuation(per_pct, pbr_pct)

    return ValuationContext(
        valuation_grade=grade,
        per_5y_percentile=round_or_none(per_pct, 1),
        pbr_5y_percentile=round_or_none(pbr_pct, 1),
        ev_ebitda_5y_percentile=round_or_none(ev_pct, 1),
        sector_per_premium=round_or_none(sector_per_prem, 1),
        sector_pbr_premium=round_or_none(sector_pbr_prem, 1),
        dcf_implied_growth=round_or_none(dcf_growth, 1),
        peg_ratio=round_or_none(peg, 2),
        fcf_yield=round_or_none(fcf_yield, 2),
        metrics={
            "history_count": len(history),
            "current_per": round_or_none(cur_per),
            "current_pbr": round_or_none(cur_pbr),
            "market_cap": round_or_none(market_cap, 0),
        },
    )


def _percentile(history: list, attr: str, current: float | None) -> float | None:
    if current is None or not history:
        return None
    values = [sf(getattr(v, attr, None)) for v in history]
    values = [v for v in values if v is not None and v > 0]
    if len(values) < 10:
        return None
    below = sum(1 for v in values if v <= current)
    return below / len(values) * 100


def _sector_premium(
    session: Session, sector_id: int | None, stock_id: int,
    cur_per: float | None, cur_pbr: float | None,
) -> tuple[float | None, float | None]:
    if sector_id is None:
        return None, None

    from src.db.models import DimStock

    peer_ids = list(
        session.execute(
            select(DimStock.stock_id)
            .where(DimStock.sector_id == sector_id, DimStock.stock_id != stock_id, DimStock.is_active.is_(True))
        ).scalars().all()
    )
    if not peer_ids:
        return None, None

    # 피어 최신 PER/PBR 중앙값
    peer_vals = list(
        session.execute(
            select(FactValuation)
            .where(FactValuation.stock_id.in_(peer_ids))
            .order_by(FactValuation.date_id.desc())
        ).scalars().all()
    )
    # 종목별 최신만
    seen = set()
    per_list, pbr_list = [], []
    for v in peer_vals:
        if v.stock_id in seen:
            continue
        seen.add(v.stock_id)
        p = sf(v.per)
        b = sf(v.pbr)
        if p and p > 0:
            per_list.append(p)
        if b and b > 0:
            pbr_list.append(b)

    per_prem = None
    if cur_per and per_list:
        median_per = sorted(per_list)[len(per_list) // 2]
        per_prem = (cur_per / median_per - 1) * 100 if median_per > 0 else None

    pbr_prem = None
    if cur_pbr and pbr_list:
        median_pbr = sorted(pbr_list)[len(pbr_list) // 2]
        pbr_prem = (cur_pbr / median_pbr - 1) * 100 if median_pbr > 0 else None

    return per_prem, pbr_prem


def _dcf_implied_growth(
    session: Session, stock_id: int, market_cap: float | None,
) -> float | None:
    if not market_cap or market_cap <= 0:
        return None
    # FCF TTM = 최근 4분기 operating_cashflow 합
    financials = list(
        session.execute(
            select(FactFinancial)
            .where(FactFinancial.stock_id == stock_id)
            .order_by(FactFinancial.period.desc())
            .limit(4)
        ).scalars().all()
    )
    if len(financials) < 4:
        return None
    fcf_ttm = sum(sf(f.operating_cashflow) or 0 for f in financials)
    if fcf_ttm <= 0:
        return None
    # implied growth = discount_rate - FCF/market_cap
    discount = 0.10
    fcf_yield_dec = fcf_ttm / market_cap
    implied = discount - fcf_yield_dec
    return implied * 100  # %


def _compute_peg(session: Session, stock_id: int, cur_per: float | None) -> float | None:
    if cur_per is None or cur_per <= 0:
        return None
    financials = list(
        session.execute(
            select(FactFinancial)
            .where(FactFinancial.stock_id == stock_id)
            .order_by(FactFinancial.period.desc())
            .limit(8)
        ).scalars().all()
    )
    if len(financials) < 8:
        return None
    # EPS growth: recent 4Q NI vs prev 4Q NI
    recent_ni = sum(sf(f.net_income) or 0 for f in financials[:4])
    prev_ni = sum(sf(f.net_income) or 0 for f in financials[4:8])
    if prev_ni <= 0:
        return None
    growth_rate = (recent_ni / prev_ni - 1) * 100
    if growth_rate <= 0:
        return None
    return cur_per / growth_rate


def _compute_fcf_yield(
    session: Session, stock_id: int, market_cap: float | None,
) -> float | None:
    if not market_cap or market_cap <= 0:
        return None
    financials = list(
        session.execute(
            select(FactFinancial)
            .where(FactFinancial.stock_id == stock_id)
            .order_by(FactFinancial.period.desc())
            .limit(4)
        ).scalars().all()
    )
    if len(financials) < 4:
        return None
    fcf_ttm = sum(sf(f.operating_cashflow) or 0 for f in financials)
    if fcf_ttm <= 0:
        return None
    return fcf_ttm / market_cap * 100


def _grade_valuation(per_pct: float | None, pbr_pct: float | None) -> str:
    if per_pct is None and pbr_pct is None:
        return "Fair"
    scores = [v for v in (per_pct, pbr_pct) if v is not None]
    avg = sum(scores) / len(scores)
    if avg > 95:
        return "Extreme"
    if avg > 75:
        return "Rich"
    if avg < 25:
        return "Cheap"
    return "Fair"
