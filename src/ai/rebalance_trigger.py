"""AI 리밸런싱 트리거 시스템."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RebalanceSuggestion:
    """리밸런싱 제안."""

    tickers_to_review: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    urgency: str = "low"  # "low" | "medium" | "high"


def check_stop_loss_triggers(session: Session, run_date_id: int) -> list[str]:
    """활성 추천 중 손절가에 도달한 종목을 감지한다.

    최근 20 거래일(~300 date_id) 이내의 추천에서 AI 손절가가 설정된 종목의
    최신 종가를 확인하여, 손절가 이하로 하락한 종목을 반환한다.

    Args:
        session: SQLAlchemy 세션.
        run_date_id: 기준 날짜 ID (YYYYMMDD).

    Returns:
        손절가 도달 종목 티커 리스트.
    """
    from sqlalchemy import select

    from src.db.models import DimStock, FactDailyPrice, FactDailyRecommendation

    # 최근 추천 중 stop_loss 설정된 건 조회 (~20 거래일)
    recs = (
        session.execute(
            select(FactDailyRecommendation)
            .where(
                FactDailyRecommendation.ai_stop_loss.isnot(None),
                FactDailyRecommendation.run_date_id <= run_date_id,
                FactDailyRecommendation.run_date_id > run_date_id - 300,
            )
            .order_by(FactDailyRecommendation.run_date_id.desc())
            .limit(50)
        )
        .scalars()
        .all()
    )

    triggered: list[str] = []
    for rec in recs:
        # 최신 종가 조회
        latest = session.scalar(
            select(FactDailyPrice.close)
            .where(
                FactDailyPrice.stock_id == rec.stock_id,
                FactDailyPrice.date_id <= run_date_id,
            )
            .order_by(FactDailyPrice.date_id.desc())
            .limit(1)
        )
        if latest is None or rec.ai_stop_loss is None:
            continue

        if float(latest) <= float(rec.ai_stop_loss):
            stock_ticker = session.scalar(
                select(DimStock.ticker).where(
                    DimStock.stock_id == rec.stock_id
                )
            )
            if stock_ticker:
                triggered.append(stock_ticker)

    return triggered


def _classify_regime(
    vix: float, sp_close: float, sp_sma20: float
) -> str:
    """매크로 지표로 시장 체제를 분류한다."""
    from src.ai.regime import classify_regime
    return classify_regime(vix, sp_close, sp_sma20)


def check_regime_change_trigger(
    session: Session, run_date_id: int
) -> str | None:
    """최근 레짐 변경을 감지한다.

    최신 2일의 매크로 지표를 비교하여 레짐이 변경되었으면
    "이전레짐->현재레짐" 문자열을, 변경 없으면 None을 반환한다.

    Args:
        session: SQLAlchemy 세션.
        run_date_id: 기준 날짜 ID (YYYYMMDD).

    Returns:
        레짐 변경 문자열 또는 None.
    """
    from sqlalchemy import select

    from src.db.models import FactMacroIndicator

    macros = (
        session.execute(
            select(FactMacroIndicator)
            .where(FactMacroIndicator.date_id <= run_date_id)
            .order_by(FactMacroIndicator.date_id.desc())
            .limit(5)
        )
        .scalars()
        .all()
    )

    if len(macros) < 2:
        return None

    latest = macros[0]
    prev = macros[1]

    r_now = _classify_regime(
        float(latest.vix or 20),
        float(latest.sp500_close or 0),
        float(latest.sp500_sma20 or 0),
    )
    r_prev = _classify_regime(
        float(prev.vix or 20),
        float(prev.sp500_close or 0),
        float(prev.sp500_sma20 or 0),
    )

    if r_now != r_prev:
        return f"{r_prev}\u2192{r_now}"
    return None


def generate_rebalance_alerts(
    session: Session, run_date_id: int
) -> RebalanceSuggestion:
    """리밸런싱 트리거를 종합하여 제안을 생성한다.

    손절 트리거와 레짐 변경을 확인하고, 우선순위를 매겨
    통합 리밸런싱 제안을 반환한다.

    Args:
        session: SQLAlchemy 세션.
        run_date_id: 기준 날짜 ID (YYYYMMDD).

    Returns:
        RebalanceSuggestion (frozen dataclass).
    """
    tickers: list[str] = []
    reasons: list[str] = []
    urgency = "low"

    # 1. 손절 트리거
    stop_triggered = check_stop_loss_triggers(session, run_date_id)
    if stop_triggered:
        tickers.extend(stop_triggered)
        reasons.append(f"손절가 도달: {', '.join(stop_triggered)}")
        urgency = "high"

    # 2. 레짐 변경
    regime_change = check_regime_change_trigger(session, run_date_id)
    if regime_change:
        reasons.append(f"시장 체제 변경: {regime_change}")
        if urgency != "high":
            urgency = "medium"

    return RebalanceSuggestion(
        tickers_to_review=tuple(set(tickers)),
        reasons=tuple(reasons),
        urgency=urgency,
    )
