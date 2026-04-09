"""Layer 4: 수급/포지셔닝."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import (
    FactAnalystConsensus,
    FactInsiderTrade,
    FactInstitutionalHolding,
    FactValuation,
)
from src.deepdive.layers_utils import round_or_none, sf
from src.deepdive.schemas import FlowProfile

logger = logging.getLogger(__name__)


def compute_layer4_flow(
    session: Session, stock_id: int,
) -> FlowProfile | None:
    """수급/포지셔닝: 내부자, 공매도, 애널리스트, 기관."""
    try:
        return _compute(session, stock_id)
    except Exception as e:
        logger.warning("Layer 4 계산 실패 (stock_id=%d): %s", stock_id, e)
        return None


def _compute(session: Session, stock_id: int) -> FlowProfile | None:
    cutoff = date_to_id(date.today() - timedelta(days=90))

    insiders = list(
        session.execute(
            select(FactInsiderTrade)
            .where(FactInsiderTrade.stock_id == stock_id, FactInsiderTrade.date_id >= cutoff)
        ).scalars().all()
    )
    insider_net, insider_signal = _analyze_insiders(insiders)

    val = session.execute(
        select(FactValuation).where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc()).limit(1)
    ).scalar_one_or_none()
    short_ratio = float(val.short_ratio) if val and val.short_ratio else None
    short_pct = float(val.short_pct_of_float) if val and val.short_pct_of_float else None

    analyst = session.execute(
        select(FactAnalystConsensus).where(FactAnalystConsensus.stock_id == stock_id)
        .order_by(FactAnalystConsensus.date_id.desc()).limit(1)
    ).scalar_one_or_none()
    buy_pct, target_upside = _analyze_analyst(analyst)

    inst = session.execute(
        select(FactInstitutionalHolding).where(FactInstitutionalHolding.stock_id == stock_id)
        .order_by(FactInstitutionalHolding.date_id.desc()).limit(1)
    ).scalar_one_or_none()
    inst_change = _analyze_institutional(inst)

    grade = _grade_flow(insider_signal, short_pct, buy_pct)

    return FlowProfile(
        flow_grade=grade,
        insider_net_90d=round(insider_net, 2),
        insider_signal=insider_signal,
        short_ratio=round_or_none(short_ratio),
        short_pct_float=round_or_none(short_pct),
        analyst_buy_pct=round_or_none(buy_pct),
        analyst_target_upside=round_or_none(target_upside),
        institutional_change=inst_change,
        metrics={"insider_trade_count": len(insiders)},
    )


def _analyze_insiders(insiders: list) -> tuple[float, str]:
    if not insiders:
        return 0.0, "neutral"
    net = 0.0
    for t in insiders:
        amount = sf(getattr(t, "value", None)) or 0.0
        tx_type = getattr(t, "transaction_type", "") or ""
        weight = 2.0 if _is_csuite(getattr(t, "insider_title", "")) else 1.0
        if "buy" in tx_type.lower() or "purchase" in tx_type.lower():
            net += amount * weight
        elif "sale" in tx_type.lower() or "sell" in tx_type.lower():
            net -= amount * weight
    if net > 10000:
        return net, "net_buy"
    if net < -10000:
        return net, "net_sell"
    return net, "neutral"


def _is_csuite(title: str) -> bool:
    title_lower = (title or "").lower()
    return any(kw in title_lower for kw in ("ceo", "cfo", "coo", "cto", "president", "chief"))


def _analyze_analyst(analyst) -> tuple[float | None, float | None]:
    if analyst is None:
        return None, None
    total = sum(sf(getattr(analyst, f, None)) or 0 for f in ("strong_buy", "buy", "hold", "sell", "strong_sell"))
    if total == 0:
        return None, None
    buy_count = (sf(getattr(analyst, "strong_buy", None)) or 0) + (sf(getattr(analyst, "buy", None)) or 0)
    buy_pct = buy_count / total * 100
    target = sf(getattr(analyst, "target_mean_price", None))
    current = sf(getattr(analyst, "current_price", None))
    upside = ((target / current - 1) * 100) if target and current and current > 0 else None
    return round(buy_pct, 1), round_or_none(upside)


def _analyze_institutional(inst) -> str | None:
    if inst is None:
        return None
    change = sf(getattr(inst, "shares_change_pct", None))
    if change is None:
        return None
    if change > 1.0:
        return "increasing"
    if change < -1.0:
        return "decreasing"
    return "stable"


def _grade_flow(insider_signal: str, short_pct: float | None, buy_pct: float | None) -> str:
    score = 0
    if insider_signal == "net_buy":
        score += 2
    elif insider_signal == "net_sell":
        score -= 2
    if short_pct is not None:
        score += 1 if short_pct < 3 else (-1 if short_pct > 10 else 0)
    if buy_pct is not None:
        score += 1 if buy_pct > 70 else (-1 if buy_pct < 30 else 0)
    if score >= 2:
        return "Accumulation"
    if score <= -2:
        return "Distribution"
    return "Neutral"
