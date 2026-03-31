"""드로다운 관리 — 포트폴리오 트레일링 스톱 + 개별 종목 ATR 스톱."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FactDailyRecommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DrawdownConfig:
    """드로다운 관리 설정."""

    portfolio_trailing_stop_pct: float = 0.10
    portfolio_reduction_factor: float = 0.50
    atr_stop_multiplier: float = 2.0
    atr_period: int = 14


@dataclass(frozen=True)
class StopLossResult:
    """개별 종목 스톱로스 결과."""

    ticker: str
    stop_type: str  # "ai" | "atr"
    stop_price: float
    atr_value: float | None = None


@dataclass(frozen=True)
class DrawdownState:
    """포트폴리오 드로다운 상태."""

    peak_value: float
    current_value: float
    drawdown_pct: float
    is_triggered: bool
    exposure_multiplier: float  # 1.0 정상, 0.5 트리거 시


def calculate_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float | None:
    """Average True Range를 계산한다.

    Args:
        highs: 고가 리스트
        lows: 저가 리스트
        closes: 종가 리스트
        period: ATR 기간 (기본 14)

    Returns:
        ATR 값 또는 데이터 부족 시 None
    """
    n = len(highs)
    if n < period + 1 or len(lows) != n or len(closes) != n:
        return None

    true_ranges: list[float] = []
    for i in range(1, n):
        high_low = highs[i] - lows[i]
        high_prev_close = abs(highs[i] - closes[i - 1])
        low_prev_close = abs(lows[i] - closes[i - 1])
        true_ranges.append(max(high_low, high_prev_close, low_prev_close))

    if len(true_ranges) < period:
        return None

    return float(np.mean(true_ranges[-period:]))


def compute_stop_loss(
    ticker: str,
    entry_price: float,
    ai_stop_loss: float | None,
    highs: list[float],
    lows: list[float],
    closes: list[float],
    config: DrawdownConfig | None = None,
) -> StopLossResult:
    """종목별 손절가를 결정한다.

    AI 손절가가 합리적 범위(진입가 대비 3-15% 이내)이면 채택,
    그렇지 않으면 ATR 기반 손절가를 사용한다.

    Args:
        ticker: 종목 코드
        entry_price: 진입 가격
        ai_stop_loss: AI가 제시한 손절가 (없으면 None)
        highs: 고가 리스트
        lows: 저가 리스트
        closes: 종가 리스트
        config: 드로다운 설정

    Returns:
        StopLossResult
    """
    if config is None:
        config = DrawdownConfig()

    # AI 손절가 합리성 검사: 진입가 대비 3-15% 이내
    if ai_stop_loss is not None and entry_price > 0:
        drop_pct = (entry_price - ai_stop_loss) / entry_price
        if 0.03 <= drop_pct <= 0.15:
            atr = calculate_atr(highs, lows, closes, config.atr_period)
            return StopLossResult(
                ticker=ticker,
                stop_type="ai",
                stop_price=round(ai_stop_loss, 2),
                atr_value=round(atr, 4) if atr is not None else None,
            )

    # ATR 기반 손절가
    atr = calculate_atr(highs, lows, closes, config.atr_period)
    if atr is not None and atr > 0 and entry_price > 0:
        stop_price = entry_price - config.atr_stop_multiplier * atr
        stop_price = max(stop_price, entry_price * 0.85)  # 최소 85%
        return StopLossResult(
            ticker=ticker,
            stop_type="atr",
            stop_price=round(stop_price, 2),
            atr_value=round(atr, 4),
        )

    # fallback: 진입가의 90%
    return StopLossResult(
        ticker=ticker,
        stop_type="atr",
        stop_price=round(entry_price * 0.90, 2),
        atr_value=None,
    )


def check_portfolio_drawdown(
    session: Session,
    run_date_id: int,
    config: DrawdownConfig | None = None,
) -> DrawdownState:
    """포트폴리오 레벨 드로다운 상태를 확인한다.

    최근 60거래일의 추천 포트폴리오 수익률을 추적하여
    고점 대비 하락률을 계산한다.

    Args:
        session: DB 세션
        run_date_id: 현재 실행 날짜 ID
        config: 드로다운 설정

    Returns:
        DrawdownState
    """
    if config is None:
        config = DrawdownConfig()

    # 최근 60거래일 추천의 1일 수익률 로드 (겹치지 않는 일간 수익률)
    recs = session.execute(
        select(
            FactDailyRecommendation.run_date_id,
            FactDailyRecommendation.return_1d,
        )
        .where(
            FactDailyRecommendation.run_date_id <= run_date_id,
            FactDailyRecommendation.return_1d.isnot(None),
        )
        .order_by(FactDailyRecommendation.run_date_id.desc())
        .limit(600)  # 약 60일 x 10종목
    ).all()

    if not recs:
        return DrawdownState(
            peak_value=1.0,
            current_value=1.0,
            drawdown_pct=0.0,
            is_triggered=False,
            exposure_multiplier=1.0,
        )

    # 날짜별 평균 1일 수익률 계산
    date_returns: dict[int, list[float]] = {}
    for row in recs:
        date_id = row[0]
        ret = float(row[1]) if row[1] is not None else 0.0
        if date_id not in date_returns:
            date_returns[date_id] = []
        date_returns[date_id].append(ret)

    sorted_dates = sorted(date_returns.keys())
    daily_avg_returns = [
        np.mean(date_returns[d]) / 100.0 for d in sorted_dates
    ]

    # 누적 가치 계산
    cumulative = 1.0
    peak = 1.0
    for r in daily_avg_returns:
        cumulative *= (1.0 + r)
        peak = max(peak, cumulative)

    drawdown_pct = (peak - cumulative) / peak if peak > 0 else 0.0
    is_triggered = drawdown_pct >= config.portfolio_trailing_stop_pct
    multiplier = config.portfolio_reduction_factor if is_triggered else 1.0

    return DrawdownState(
        peak_value=round(peak, 6),
        current_value=round(cumulative, 6),
        drawdown_pct=round(drawdown_pct, 4),
        is_triggered=is_triggered,
        exposure_multiplier=multiplier,
    )


def apply_drawdown_reduction(
    weights: dict[str, float],
    drawdown_state: DrawdownState,
) -> dict[str, float]:
    """드로다운 트리거 시 전체 비중을 축소한다.

    Args:
        weights: ticker -> weight 딕셔너리
        drawdown_state: 현재 드로다운 상태

    Returns:
        조정된 비중 딕셔너리 (새 객체)
    """
    if not drawdown_state.is_triggered:
        return dict(weights)

    multiplier = drawdown_state.exposure_multiplier
    return {
        ticker: round(w * multiplier, 6)
        for ticker, w in weights.items()
    }
