"""실행 비용 모델 — 슬리피지, 시장충격, 용량 제약."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecutionCostConfig:
    """실행 비용 모델 설정."""

    enabled: bool = False
    spread_bps: float = 2.0  # S&P 500 평균 스프레드/2
    impact_coefficient: float = 1.0  # Almgren-Chriss 계수
    max_participation_rate: float = 0.01  # ADTV의 최대 1%
    commission_bps: float = 1.0  # 수수료 (왕복)
    fallback_cost_bps: float = 20.0  # 데이터 없을 때 fallback


@dataclass(frozen=True)
class CostBreakdown:
    """개별 종목의 비용 분해."""

    ticker: str
    spread_bps: float
    impact_bps: float
    commission_bps: float
    total_bps: float
    participation_rate: float
    capacity_ok: bool


@dataclass(frozen=True)
class PortfolioCostResult:
    """포트폴리오 전체 비용 결과."""

    breakdowns: tuple[CostBreakdown, ...]
    portfolio_avg_cost_bps: float
    capacity_limited_tickers: tuple[str, ...]
    max_aum_estimate: float | None


def estimate_execution_cost(
    ticker: str,
    weight: float,
    price: float,
    daily_volatility: float,
    adtv_shares: float,
    portfolio_value: float = 1_000_000.0,
    config: ExecutionCostConfig | None = None,
) -> CostBreakdown:
    """개별 종목의 실행 비용을 추정한다.

    Args:
        ticker: 종목 코드
        weight: 포트폴리오 비중 (0~1)
        price: 현재가
        daily_volatility: 일간 변동성 (소수)
        adtv_shares: 20일 평균 일거래량 (주)
        portfolio_value: 포트폴리오 총 가치 ($)
        config: 비용 모델 설정

    Returns:
        CostBreakdown
    """
    if config is None:
        config = ExecutionCostConfig()

    # 데이터 부족 시 fallback
    if price <= 0 or adtv_shares <= 0 or weight <= 0:
        return CostBreakdown(
            ticker=ticker,
            spread_bps=config.fallback_cost_bps,
            impact_bps=0.0,
            commission_bps=config.commission_bps,
            total_bps=config.fallback_cost_bps + config.commission_bps,
            participation_rate=0.0,
            capacity_ok=True,
        )

    # 주문 주수
    order_dollars = weight * portfolio_value
    order_shares = order_dollars / price

    # 참여율
    participation_rate = order_shares / adtv_shares

    # 스프레드 비용 (고정)
    spread = config.spread_bps

    # 시장충격: coefficient × σ_daily × sqrt(participation_rate)
    # σ_daily를 bps로 변환 (× 10000)
    impact = (
        config.impact_coefficient
        * daily_volatility
        * math.sqrt(min(participation_rate, 1.0))
        * 10000  # 소수 → bps
    )

    # 수수료
    commission = config.commission_bps

    total = round(spread + impact + commission, 2)
    capacity_ok = participation_rate <= config.max_participation_rate

    return CostBreakdown(
        ticker=ticker,
        spread_bps=round(spread, 2),
        impact_bps=round(impact, 2),
        commission_bps=round(commission, 2),
        total_bps=total,
        participation_rate=round(participation_rate, 6),
        capacity_ok=capacity_ok,
    )


def estimate_portfolio_cost(
    weights: dict[str, float],
    price_map: dict[str, float],
    volatility_map: dict[str, float],
    adtv_map: dict[str, float],
    portfolio_value: float = 1_000_000.0,
    config: ExecutionCostConfig | None = None,
) -> PortfolioCostResult:
    """포트폴리오 전체의 실행 비용을 추정한다.

    Args:
        weights: ticker -> weight
        price_map: ticker -> 현재가
        volatility_map: ticker -> 일간 변동성
        adtv_map: ticker -> 20일 평균 거래량 (주)
        portfolio_value: 포트폴리오 총 가치
        config: 비용 모델 설정

    Returns:
        PortfolioCostResult
    """
    if config is None:
        config = ExecutionCostConfig()

    breakdowns: list[CostBreakdown] = []
    capacity_limited: list[str] = []

    for ticker, weight in weights.items():
        if weight <= 0:
            continue
        cb = estimate_execution_cost(
            ticker=ticker,
            weight=weight,
            price=price_map.get(ticker, 0.0),
            daily_volatility=volatility_map.get(ticker, 0.01),
            adtv_shares=adtv_map.get(ticker, 0.0),
            portfolio_value=portfolio_value,
            config=config,
        )
        breakdowns.append(cb)
        if not cb.capacity_ok:
            capacity_limited.append(ticker)

    if breakdowns:
        # 비중 가중 평균 비용
        total_weight = sum(weights.get(cb.ticker, 0.0) for cb in breakdowns)
        if total_weight > 0:
            avg_cost = sum(
                cb.total_bps * weights.get(cb.ticker, 0.0)
                for cb in breakdowns
            ) / total_weight
        else:
            avg_cost = 0.0
    else:
        avg_cost = 0.0

    # 용량 추정
    max_aum = estimate_strategy_capacity(weights, adtv_map, price_map, config)

    return PortfolioCostResult(
        breakdowns=tuple(breakdowns),
        portfolio_avg_cost_bps=round(avg_cost, 2),
        capacity_limited_tickers=tuple(capacity_limited),
        max_aum_estimate=max_aum,
    )


def estimate_strategy_capacity(
    weights: dict[str, float],
    adtv_map: dict[str, float],
    price_map: dict[str, float],
    config: ExecutionCostConfig | None = None,
) -> float | None:
    """전략의 최대 운용 가능 자산(AUM)을 추정한다.

    각 종목에서 max_participation_rate 이내로 거래할 수 있는
    최대 포트폴리오 크기의 최솟값.

    max_aum = min_i(max_participation × adtv_i × price_i / weight_i)
    """
    if config is None:
        config = ExecutionCostConfig()

    capacities: list[float] = []

    for ticker, weight in weights.items():
        if weight <= 0:
            continue
        adtv = adtv_map.get(ticker, 0.0)
        price = price_map.get(ticker, 0.0)
        if adtv <= 0 or price <= 0:
            continue

        # 이 종목에서 참여율 한도 내 최대 주문 금액
        max_order_dollars = config.max_participation_rate * adtv * price
        # 포트폴리오 크기 = 주문 금액 / 비중
        max_portfolio = max_order_dollars / weight
        capacities.append(max_portfolio)

    if not capacities:
        return None

    return round(min(capacities), 0)
