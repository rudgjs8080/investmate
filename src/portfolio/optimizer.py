"""포트폴리오 최적화 엔진 — 4가지 배분 전략."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PortfolioResult:
    """포트폴리오 최적화 결과."""

    strategy: str
    allocations: dict[str, float]  # ticker -> weight (0~1)
    expected_return: float  # 연환산 기대수익률
    volatility: float  # 연환산 변동성
    sharpe_ratio: float  # 샤프 비율
    amounts: dict[str, float] = field(default_factory=dict)  # ticker -> 투자금액


def _annualized_stats(
    weights: np.ndarray,
    mean_returns: np.ndarray,
    cov_matrix: np.ndarray,
    trading_days: int = 252,
) -> tuple[float, float]:
    """연환산 기대수익률과 변동성을 계산한다."""
    port_return = np.dot(weights, mean_returns) * trading_days
    port_vol = np.sqrt(np.dot(weights, np.dot(cov_matrix * trading_days, weights)))
    return float(port_return), float(port_vol)


def _negative_sharpe(
    weights: np.ndarray,
    mean_returns: np.ndarray,
    cov_matrix: np.ndarray,
    risk_free_rate: float = 0.04,
) -> float:
    """최대 샤프를 위한 목적 함수 (음수 반환)."""
    ret, vol = _annualized_stats(weights, mean_returns, cov_matrix)
    if vol == 0:
        return 0.0
    return -(ret - risk_free_rate) / vol


def _portfolio_volatility(
    weights: np.ndarray,
    mean_returns: np.ndarray,
    cov_matrix: np.ndarray,
) -> float:
    """포트폴리오 변동성 (최소분산용)."""
    _, vol = _annualized_stats(weights, mean_returns, cov_matrix)
    return vol


def _build_return_matrix(
    price_data: dict[str, pd.Series],
) -> pd.DataFrame:
    """가격 시계열 → 일간 수익률 DataFrame."""
    df = pd.DataFrame(price_data)
    returns = df.pct_change().dropna()
    return returns


def equal_weight(tickers: list[str], investment: float = 0.0) -> PortfolioResult:
    """동일 비중 배분."""
    n = len(tickers)
    if n == 0:
        return PortfolioResult(
            strategy="동일비중", allocations={}, expected_return=0.0,
            volatility=0.0, sharpe_ratio=0.0,
        )

    weight = round(1.0 / n, 4)
    allocs = {t: weight for t in tickers}
    amounts = {t: round(investment * weight, 2) for t in tickers} if investment > 0 else {}

    return PortfolioResult(
        strategy="동일비중",
        allocations=allocs,
        expected_return=0.0,
        volatility=0.0,
        sharpe_ratio=0.0,
        amounts=amounts,
    )


def optimize_portfolio(
    price_data: dict[str, pd.Series],
    strategy: str = "max_sharpe",
    investment: float = 0.0,
    risk_free_rate: float = 0.04,
) -> PortfolioResult:
    """포트폴리오를 최적화한다.

    Args:
        price_data: {ticker: 가격 시계열 Series} — 최소 30일 필요
        strategy: "max_sharpe" | "min_variance" | "risk_parity" | "equal_weight"
        investment: 총 투자금액 (0이면 배분 비율만)
        risk_free_rate: 무위험 이자율 (기본 4%)

    Returns:
        PortfolioResult
    """
    tickers = list(price_data.keys())
    n = len(tickers)

    if n == 0:
        return PortfolioResult(
            strategy=_strategy_name(strategy), allocations={},
            expected_return=0.0, volatility=0.0, sharpe_ratio=0.0,
        )

    if strategy == "equal_weight":
        return equal_weight(tickers, investment)

    if n == 1:
        # 단일 종목
        allocs = {tickers[0]: 1.0}
        amounts = {tickers[0]: investment} if investment > 0 else {}
        return PortfolioResult(
            strategy=_strategy_name(strategy), allocations=allocs,
            expected_return=0.0, volatility=0.0, sharpe_ratio=0.0,
            amounts=amounts,
        )

    returns = _build_return_matrix(price_data)
    if returns.empty or len(returns) < 10:
        return equal_weight(tickers, investment)

    mean_returns = returns.mean().values
    try:
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf()
        cov_matrix = lw.fit(returns.values).covariance_
    except Exception:
        cov_matrix = returns.cov().values  # fallback

    # 제약 조건: 비중 합 = 1, 각 비중 0~1
    bounds = tuple((0.0, 1.0) for _ in range(n))
    constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
    init_weights = np.array([1.0 / n] * n)

    try:
        if strategy == "max_sharpe":
            result = minimize(
                _negative_sharpe,
                init_weights,
                args=(mean_returns, cov_matrix, risk_free_rate),
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
            )
        elif strategy == "min_variance":
            result = minimize(
                _portfolio_volatility,
                init_weights,
                args=(mean_returns, cov_matrix),
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
            )
        elif strategy == "risk_parity":
            result = _risk_parity(cov_matrix, n)
        else:
            return equal_weight(tickers, investment)

        if strategy == "risk_parity":
            weights = result
        else:
            if not result.success:
                logger.warning("최적화 수렴 실패, 동일비중 폴백: %s", result.message)
                return equal_weight(tickers, investment)
            weights = result.x

    except Exception as e:
        logger.warning("포트폴리오 최적화 실패: %s", e)
        return equal_weight(tickers, investment)

    # 결과 조립
    ret, vol = _annualized_stats(weights, mean_returns, cov_matrix)
    sharpe = (ret - risk_free_rate) / vol if vol > 0 else 0.0

    allocs = {tickers[i]: round(float(weights[i]), 4) for i in range(n) if weights[i] > 0.001}
    amounts = {t: round(investment * w, 2) for t, w in allocs.items()} if investment > 0 else {}

    return PortfolioResult(
        strategy=_strategy_name(strategy),
        allocations=allocs,
        expected_return=round(ret * 100, 2),
        volatility=round(vol * 100, 2),
        sharpe_ratio=round(sharpe, 3),
        amounts=amounts,
    )


def _risk_parity(cov_matrix: np.ndarray, n: int) -> np.ndarray:
    """역변동성 가중 — 변동성이 낮은 자산에 더 많이 배분."""
    inv_vol = 1.0 / np.sqrt(np.diag(cov_matrix))
    weights = inv_vol / inv_vol.sum()
    return weights


def estimate_market_impact(position_size: float, daily_volume: float, price: float) -> float:
    """시장 충격을 추정한다 (%).

    impact = 0.025% * sqrt(position_size / (daily_volume * price))
    """
    if daily_volume <= 0 or price <= 0:
        return 0.5  # default 50bps
    dollar_volume = daily_volume * price
    if dollar_volume == 0:
        return 0.5
    participation = position_size / dollar_volume
    return 0.025 * (participation ** 0.5) * 100  # convert to percentage


def _strategy_name(strategy: str) -> str:
    """전략 코드 → 한글명."""
    names = {
        "max_sharpe": "최대샤프",
        "min_variance": "최소분산",
        "risk_parity": "역변동성",
        "equal_weight": "동일비중",
    }
    return names.get(strategy, strategy)
