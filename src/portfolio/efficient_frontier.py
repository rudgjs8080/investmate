"""효율적 프런티어 계산 모듈."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FrontierPoint:
    """효율적 프런티어 상의 한 점."""

    expected_return: float  # %
    volatility: float  # %
    sharpe_ratio: float
    allocations: dict[str, float]


def compute_efficient_frontier(
    price_data: dict[str, pd.Series],
    n_points: int = 30,
    risk_free_rate: float = 0.04,
) -> list[FrontierPoint]:
    """효율적 프런티어를 계산한다.

    Args:
        price_data: {ticker: 가격 시계열} — 최소 30일
        n_points: 프런티어 상의 점 수
        risk_free_rate: 무위험 이자율

    Returns:
        (return, volatility) 쌍의 리스트
    """
    tickers = list(price_data.keys())
    n = len(tickers)

    if n < 2:
        return []

    df = pd.DataFrame(price_data)
    returns = df.pct_change().dropna()
    if len(returns) < 10:
        return []

    mean_returns = returns.mean().values
    try:
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf()
        cov_matrix = lw.fit(returns.values).covariance_
    except Exception:
        cov_matrix = returns.cov().values  # fallback
    trading_days = 252

    # 수익률 범위 설정
    individual_returns = mean_returns * trading_days
    min_ret = max(individual_returns.min(), -0.5)
    max_ret = min(individual_returns.max(), 2.0)

    if min_ret >= max_ret:
        return []

    target_returns = np.linspace(min_ret, max_ret, n_points)
    bounds = tuple((0.0, 1.0) for _ in range(n))
    init_weights = np.array([1.0 / n] * n)

    frontier = []
    for target in target_returns:
        constraints = [
            {"type": "eq", "fun": lambda w: np.sum(w) - 1.0},
            {"type": "eq", "fun": lambda w, t=target: np.dot(w, mean_returns) * trading_days - t},
        ]
        try:
            result = minimize(
                lambda w: np.sqrt(np.dot(w, np.dot(cov_matrix * trading_days, w))),
                init_weights,
                method="SLSQP",
                bounds=bounds,
                constraints=constraints,
            )
            if result.success:
                weights = result.x
                port_ret = np.dot(weights, mean_returns) * trading_days
                port_vol = np.sqrt(np.dot(weights, np.dot(cov_matrix * trading_days, weights)))
                sharpe = (port_ret - risk_free_rate) / port_vol if port_vol > 0 else 0

                allocs = {
                    tickers[i]: round(float(weights[i]), 4)
                    for i in range(n) if weights[i] > 0.001
                }

                frontier.append(FrontierPoint(
                    expected_return=round(float(port_ret) * 100, 2),
                    volatility=round(float(port_vol) * 100, 2),
                    sharpe_ratio=round(float(sharpe), 3),
                    allocations=allocs,
                ))
        except Exception as exc:
            logger.debug("프런티어 점 계산 실패 (target=%.4f): %s", target, exc)
            continue

    return frontier
