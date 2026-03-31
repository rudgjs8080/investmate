"""턴오버 관리 — 일일 턴오버 계산, 홀드 룰, 히스테리시스."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import DimStock, FactDailyRecommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TurnoverConfig:
    """턴오버 관리 설정."""

    annualized_warn_threshold: float = 12.0  # 1200%
    hold_score_floor_pct: float = 0.30  # 하위 30%
    buy_threshold: float = 0.01  # 매수 최소 비중
    sell_threshold: float = 0.005  # 매도 최소 비중 (히스테리시스)


@dataclass(frozen=True)
class TurnoverStats:
    """턴오버 통계."""

    daily_turnover: float  # Σ|w_new - w_old| / 2
    annualized_turnover: float  # × 252
    trade_count: int  # 비중 변화가 있는 종목 수
    buys: tuple[str, ...]  # 신규 매수
    sells: tuple[str, ...]  # 전량 매도
    is_excessive: bool  # 연환산 > threshold
    warning_message: str | None


def calculate_turnover(
    new_weights: dict[str, float],
    old_weights: dict[str, float],
    config: TurnoverConfig | None = None,
) -> TurnoverStats:
    """일일 턴오버를 계산한다.

    turnover = Σ|w_new - w_old| / 2

    Args:
        new_weights: 당일 비중 {ticker: weight}
        old_weights: 전일 비중 {ticker: weight}
        config: 턴오버 설정

    Returns:
        TurnoverStats
    """
    if config is None:
        config = TurnoverConfig()

    all_tickers = set(new_weights.keys()) | set(old_weights.keys())

    total_abs_change = 0.0
    trade_count = 0
    buys: list[str] = []
    sells: list[str] = []

    for ticker in all_tickers:
        w_new = new_weights.get(ticker, 0.0)
        w_old = old_weights.get(ticker, 0.0)
        change = abs(w_new - w_old)

        if change > 0.0001:
            total_abs_change += change
            trade_count += 1

            if w_old < 0.0001 and w_new >= config.buy_threshold:
                buys.append(ticker)
            elif w_new < 0.0001 and w_old >= config.sell_threshold:
                sells.append(ticker)

    daily_turnover = total_abs_change / 2.0
    annualized = daily_turnover * 252

    is_excessive = annualized > config.annualized_warn_threshold
    warning = None
    if is_excessive:
        warning = (
            f"연환산 턴오버 {annualized:.0%} > "
            f"임계값 {config.annualized_warn_threshold:.0%}"
        )

    return TurnoverStats(
        daily_turnover=round(daily_turnover, 6),
        annualized_turnover=round(annualized, 4),
        trade_count=trade_count,
        buys=tuple(sorted(buys)),
        sells=tuple(sorted(sells)),
        is_excessive=is_excessive,
        warning_message=warning,
    )


def apply_hold_rules(
    new_weights: dict[str, float],
    old_weights: dict[str, float],
    scores: dict[str, float],
    stop_triggered: dict[str, bool],
    config: TurnoverConfig | None = None,
) -> dict[str, float]:
    """홀드 룰을 적용하여 불필요한 매도를 억제한다.

    유지 조건 (매도하지 않음):
    1. 손절가 미트리거 (stop_triggered[ticker] is False)
    2. 점수가 하위 score_floor_pct에 해당하지 않음

    히스테리시스: buy_threshold > sell_threshold로
    매수 임계값과 매도 임계값에 갭을 두어 불필요한 회전 방지.

    Args:
        new_weights: 당일 사이징 결과 비중
        old_weights: 전일 비중
        scores: ticker -> total_score
        stop_triggered: ticker -> 손절 트리거 여부
        config: 턴오버 설정

    Returns:
        조정된 비중 (새 딕셔너리)
    """
    if config is None:
        config = TurnoverConfig()

    if not old_weights:
        return dict(new_weights)

    # 점수 하한 계산
    all_scores = list(scores.values())
    if all_scores:
        all_scores.sort()
        floor_idx = max(0, int(len(all_scores) * config.hold_score_floor_pct) - 1)
        score_floor = all_scores[floor_idx]
    else:
        score_floor = 0.0

    result = dict(new_weights)

    for ticker, old_w in old_weights.items():
        if old_w < config.sell_threshold:
            continue  # 이전 비중도 무시할 수준

        new_w = new_weights.get(ticker, 0.0)

        # 신규 비중이 0 (= 추천 목록에서 빠짐) → 홀드 룰 적용 검토
        if new_w < config.sell_threshold:
            is_stopped = stop_triggered.get(ticker, False)
            ticker_score = scores.get(ticker, 0.0)
            is_bottom = ticker_score <= score_floor

            if not is_stopped and not is_bottom:
                # 유지: 기존 비중 그대로
                result[ticker] = old_w
                logger.debug(
                    "홀드 룰: %s 유지 (score=%.1f, stop=%s)",
                    ticker, ticker_score, is_stopped,
                )

    return result


def get_previous_weights(
    session: Session,
    run_date_id: int,
) -> dict[str, float]:
    """직전 거래일의 포지션 비중을 조회한다.

    Args:
        session: DB 세션
        run_date_id: 현재 실행 날짜 ID

    Returns:
        {ticker: position_weight} 딕셔너리
    """
    # 직전 거래일 찾기
    prev_date = session.execute(
        select(FactDailyRecommendation.run_date_id)
        .where(FactDailyRecommendation.run_date_id < run_date_id)
        .where(FactDailyRecommendation.position_weight.isnot(None))
        .order_by(FactDailyRecommendation.run_date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if prev_date is None:
        return {}

    # 해당 날짜의 비중 조회 (배치 로드)
    rows = session.execute(
        select(
            DimStock.ticker,
            FactDailyRecommendation.position_weight,
        )
        .join(DimStock, FactDailyRecommendation.stock_id == DimStock.stock_id)
        .where(FactDailyRecommendation.run_date_id == prev_date)
        .where(FactDailyRecommendation.position_weight.isnot(None))
    ).all()

    return {
        ticker: float(weight) for ticker, weight in rows if weight is not None
    }
