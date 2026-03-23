"""재무 품질 필터 — Piotroski F-Score, Altman Z-Score, Earnings Quality."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FactFinancial, FactValuation
from src.db.repository import FinancialRepository

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# Piotroski F-Score (9-point)
# ──────────────────────────────────────────


@dataclass(frozen=True)
class PiotroskiScore:
    """Piotroski F-Score 결과 (0-9)."""

    score: int
    details: dict[str, bool]


def calculate_piotroski(
    current: FactFinancial,
    previous: FactFinancial | None,
    valuation: FactValuation | None,
) -> PiotroskiScore:
    """Piotroski F-Score를 계산한다 (0-9).

    9 criteria (each True = +1):
    1. net_income > 0 (수익성)
    2. operating_cashflow > 0 (영업현금흐름)
    3. ROA 증가 (current NI/TA > prev NI/TA)
    4. operating_cashflow > net_income (이익 품질)
    5. 장기 부채 감소 (current TL/TA < prev TL/TA)
    6. 유동비율 증가 (current TA/TL > prev TA/TL) — simplified proxy
    7. 주식 희석 없음 — 데이터 미보유, True 고정
    8. 매출총이익률 증가 (OI/Revenue proxy)
    9. 자산회전율 증가 (Revenue/TA)
    """
    details: dict[str, bool] = {}

    cur_ni = _safe_float(current.net_income)
    cur_ocf = _safe_float(current.operating_cashflow)
    cur_ta = _safe_float(current.total_assets)
    cur_tl = _safe_float(current.total_liabilities)
    cur_rev = _safe_float(current.revenue)
    cur_oi = _safe_float(current.operating_income)

    # 1. net_income > 0
    details["positive_net_income"] = cur_ni is not None and cur_ni > 0

    # 2. operating_cashflow > 0
    details["positive_cashflow"] = cur_ocf is not None and cur_ocf > 0

    # 3. ROA 증가
    if previous is not None and cur_ta and cur_ta > 0:
        prev_ni = _safe_float(previous.net_income)
        prev_ta = _safe_float(previous.total_assets)
        if prev_ni is not None and prev_ta and prev_ta > 0 and cur_ni is not None:
            details["roa_increased"] = (cur_ni / cur_ta) > (prev_ni / prev_ta)
        else:
            details["roa_increased"] = False
    else:
        details["roa_increased"] = False

    # 4. operating_cashflow > net_income (이익 품질)
    if cur_ocf is not None and cur_ni is not None:
        details["earnings_quality"] = cur_ocf > cur_ni
    else:
        details["earnings_quality"] = False

    # 5. 장기 부채 감소 (TL/TA 감소)
    if previous is not None and cur_ta and cur_ta > 0 and cur_tl is not None:
        prev_tl = _safe_float(previous.total_liabilities)
        prev_ta = _safe_float(previous.total_assets)
        if prev_tl is not None and prev_ta and prev_ta > 0:
            details["leverage_decreased"] = (cur_tl / cur_ta) < (prev_tl / prev_ta)
        else:
            details["leverage_decreased"] = False
    else:
        details["leverage_decreased"] = False

    # 6. 유동비율 증가 (TA/TL proxy)
    if previous is not None and cur_tl and cur_tl > 0 and cur_ta is not None:
        prev_tl = _safe_float(previous.total_liabilities)
        prev_ta = _safe_float(previous.total_assets)
        if prev_tl and prev_tl > 0 and prev_ta is not None:
            details["current_ratio_increased"] = (cur_ta / cur_tl) > (prev_ta / prev_tl)
        else:
            details["current_ratio_increased"] = False
    else:
        details["current_ratio_increased"] = False

    # 7. 주식 희석 없음 — 데이터 미보유, 보수적으로 False 처리
    details["no_dilution"] = False

    # 8. 매출총이익률 증가 (OI/Revenue proxy)
    if previous is not None and cur_rev and cur_rev > 0 and cur_oi is not None:
        prev_oi = _safe_float(previous.operating_income)
        prev_rev = _safe_float(previous.revenue)
        if prev_oi is not None and prev_rev and prev_rev > 0:
            details["gross_margin_increased"] = (cur_oi / cur_rev) > (prev_oi / prev_rev)
        else:
            details["gross_margin_increased"] = False
    else:
        details["gross_margin_increased"] = False

    # 9. 자산회전율 증가 (Revenue/TA)
    if previous is not None and cur_ta and cur_ta > 0 and cur_rev is not None:
        prev_rev = _safe_float(previous.revenue)
        prev_ta = _safe_float(previous.total_assets)
        if prev_rev is not None and prev_ta and prev_ta > 0:
            details["asset_turnover_increased"] = (cur_rev / cur_ta) > (prev_rev / prev_ta)
        else:
            details["asset_turnover_increased"] = False
    else:
        details["asset_turnover_increased"] = False

    total = sum(1 for v in details.values() if v)
    return PiotroskiScore(score=total, details=details)


# ──────────────────────────────────────────
# Altman Z-Score
# ──────────────────────────────────────────


@dataclass(frozen=True)
class AltmanResult:
    """Altman Z-Score 결과."""

    z_score: float
    zone: str  # "safe" (>3.0), "gray" (1.8-3.0), "distress" (<1.8)


def calculate_altman_z(
    financial: FactFinancial,
    market_cap: float | None,
) -> AltmanResult:
    """Altman Z-Score를 계산한다.

    Z = 1.2*(WC/TA) + 1.4*(RE/TA) + 3.3*(EBIT/TA) + 0.6*(ME/TL) + 1.0*(Sales/TA)

    Proxies:
    - WC = equity - (non-current assets ~50% of TA) (working capital proxy)
    - RE = annualized NI + 30% of equity (retained earnings proxy)
    - EBIT = operating_income
    - ME = market_cap
    - TL = total_liabilities
    - Sales = revenue
    - TA = total_assets
    """
    ta = _safe_float(financial.total_assets)
    tl = _safe_float(financial.total_liabilities)
    te = _safe_float(financial.total_equity)
    oi = _safe_float(financial.operating_income)
    rev = _safe_float(financial.revenue)

    if not ta or ta <= 0:
        return AltmanResult(z_score=0.0, zone="distress")

    ni = _safe_float(financial.net_income)

    # WC/TA (proxy: equity - non-current portion of assets, ~50%)
    wc = (te - ta * 0.5) if te is not None and ta else 0
    wc_ta = wc / ta if ta and ta > 0 else 0.0

    # RE/TA (proxy: annualized NI + ~30% of equity as accumulated earnings)
    annual_ni = (ni * 4) if ni is not None else 0  # Annualize quarterly NI
    re = annual_ni + (te * 0.3 if te is not None else 0)
    re_ta = re / ta if ta and ta > 0 else 0.0

    # EBIT/TA
    ebit_ta = (oi / ta) if oi is not None else 0.0

    # ME/TL
    if market_cap is not None and tl is not None and tl > 0:
        me_tl = market_cap / tl
    else:
        me_tl = 0.0

    # Sales/TA
    sales_ta = (rev / ta) if rev is not None else 0.0

    z = 1.2 * wc_ta + 1.4 * re_ta + 3.3 * ebit_ta + 0.6 * me_tl + 1.0 * sales_ta

    if z > 3.0:
        zone = "safe"
    elif z >= 1.8:
        zone = "gray"
    else:
        zone = "distress"

    return AltmanResult(z_score=round(z, 4), zone=zone)


# ──────────────────────────────────────────
# Earnings Quality
# ──────────────────────────────────────────


@dataclass(frozen=True)
class EarningsQuality:
    """이익 품질 결과."""

    accrual_ratio: float | None
    quality: str  # "high", "medium", "low"


def calculate_earnings_quality(financial: FactFinancial) -> EarningsQuality:
    """이익 품질을 계산한다.

    accrual_ratio = (net_income - operating_cashflow) / total_assets
    - < 0.05: "high" (현금 기반 이익)
    - 0.05-0.10: "medium"
    - > 0.10: "low" (의심스러운 발생액)
    """
    ni = _safe_float(financial.net_income)
    ocf = _safe_float(financial.operating_cashflow)
    ta = _safe_float(financial.total_assets)

    if ni is None or ocf is None or ta is None or ta <= 0:
        return EarningsQuality(accrual_ratio=None, quality="medium")

    accrual_ratio = (ni - ocf) / ta

    if accrual_ratio < 0.05:
        quality = "high"
    elif accrual_ratio <= 0.10:
        quality = "medium"
    else:
        quality = "low"

    return EarningsQuality(accrual_ratio=round(accrual_ratio, 6), quality=quality)


# ──────────────────────────────────────────
# Integration function
# ──────────────────────────────────────────


def assess_quality(
    session: Session,
    stock_id: int,
) -> tuple[PiotroskiScore, AltmanResult, EarningsQuality]:
    """재무 품질을 종합 평가한다.

    DB에서 최신 2개 분기 재무 데이터 + 밸류에이션을 조회하여
    Piotroski F-Score, Altman Z-Score, Earnings Quality를 모두 계산한다.

    Returns:
        (PiotroskiScore, AltmanResult, EarningsQuality) 튜플.

    Raises:
        ValueError: 재무 데이터가 없을 경우.
    """
    fins = FinancialRepository.get_by_stock(session, stock_id)
    if not fins:
        raise ValueError(f"재무 데이터 없음: stock_id={stock_id}")

    current = fins[0]  # 최신 분기 (period desc 정렬)
    previous = fins[1] if len(fins) >= 2 else None

    # 최신 밸류에이션 (market_cap 조회용)
    val_row = session.execute(
        select(FactValuation)
        .where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    market_cap = float(val_row.market_cap) if val_row and val_row.market_cap else None

    piotroski = calculate_piotroski(current, previous, val_row)
    altman = calculate_altman_z(current, market_cap)
    earnings_q = calculate_earnings_quality(current)

    logger.debug(
        "재무 품질 [stock_id=%d]: Piotroski=%d, Z=%.2f(%s), EQ=%s",
        stock_id, piotroski.score, altman.z_score, altman.zone, earnings_q.quality,
    )

    return piotroski, altman, earnings_q


# ──────────────────────────────────────────
# Utility
# ──────────────────────────────────────────


def _safe_float(value: object) -> float | None:
    """None-safe float 변환."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
