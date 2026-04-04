"""시장 레짐 분류 — 시스템 전체에서 이 모듈만 사용.

기존에 calibrator.py, rebalance_trigger.py, retrospective.py 3곳에 중복되던
레짐 분류 로직을 통합한다.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.ai.constants import VIX_CRISIS, VIX_HIGH_VOL, VIX_NORMAL
from src.db.models import FactMacroIndicator

logger = logging.getLogger(__name__)


def classify_regime(
    vix: float | None,
    sp_close: float | None = None,
    sp_sma20: float | None = None,
) -> str:
    """시장 레짐을 분류한다.

    Args:
        vix: VIX 지수.
        sp_close: S&P 500 종가.
        sp_sma20: S&P 500 20일 이동평균.

    Returns:
        "crisis" | "bear" | "bull" | "range"
    """
    if vix is None:
        return "range"
    if vix >= VIX_CRISIS:
        return "crisis"
    if vix >= VIX_HIGH_VOL and sp_close and sp_sma20 and sp_close < sp_sma20:
        return "bear"
    if vix < VIX_NORMAL and sp_close and sp_sma20 and sp_close > sp_sma20:
        return "bull"
    return "range"


def classify_regime_from_macro(session: Session, date_id: int) -> str:
    """DB에서 매크로 데이터를 읽어 레짐을 분류한다.

    가장 가까운 이전 날짜의 매크로 데이터를 사용한다.
    """
    macro = session.scalar(
        select(FactMacroIndicator)
        .where(FactMacroIndicator.date_id <= date_id)
        .order_by(FactMacroIndicator.date_id.desc())
        .limit(1)
    )
    if macro is None:
        return "range"

    vix = float(macro.vix) if macro.vix else None
    sp_close = float(macro.sp500_close) if macro.sp500_close else None
    sp_sma20 = float(macro.sp500_sma20) if macro.sp500_sma20 else None

    return classify_regime(vix, sp_close, sp_sma20)


def classify_regime_from_record(macro: FactMacroIndicator) -> str:
    """FactMacroIndicator 레코드에서 직접 레짐을 분류한다."""
    vix = float(macro.vix) if macro.vix else None
    sp_close = float(macro.sp500_close) if macro.sp500_close else None
    sp_sma20 = float(macro.sp500_sma20) if macro.sp500_sma20 else None

    return classify_regime(vix, sp_close, sp_sma20)
