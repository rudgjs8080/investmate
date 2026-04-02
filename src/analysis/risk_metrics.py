"""리스크 조정 수익률 지표 — Sharpe, Sortino, MDD, Calmar, Omega.

backtest/engine.py와 analysis/performance.py 양쪽에서 공유한다.
입력은 기간별 수익률(%) 리스트이며, 연환산은 period_days 매개변수로 조정한다.
"""

from __future__ import annotations

import math
import statistics


def calculate_sharpe(
    returns: list[float], risk_free: float = 0.0, period_days: int = 20,
) -> float | None:
    """연환산 샤프 비율.

    Args:
        returns: 기간별 수익률(%) 리스트
        risk_free: 동일 기간 무위험 수익률(%)
        period_days: 수익률 측정 기간 (거래일). 1d→1, 20d→20
    """
    if len(returns) < 2:
        return None
    mean_r = statistics.mean(returns)
    std_r = statistics.stdev(returns)
    if std_r == 0:
        return None
    raw = (mean_r - risk_free) / std_r
    annualization = math.sqrt(252 / period_days)
    return round(raw * annualization, 3)


def calculate_sortino(
    returns: list[float], risk_free: float = 0.0, period_days: int = 20,
) -> float | None:
    """소르티노 비율 — 하방 리스크만 고려."""
    if len(returns) < 2:
        return None
    mean_r = statistics.mean(returns)
    downside = [r for r in returns if r < 0]
    if len(downside) < 2:
        return None
    downside_std = statistics.stdev(downside)
    if downside_std == 0:
        return None
    raw = (mean_r - risk_free) / downside_std
    annualization = math.sqrt(252 / period_days)
    return round(raw * annualization, 3)


def calculate_max_drawdown(returns: list[float]) -> float | None:
    """누적 수익 기준 최대 낙폭(%)."""
    if not returns:
        return None
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in returns:
        cumulative += r
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return max_dd


def calculate_calmar(
    returns: list[float], max_dd: float | None, period_days: int = 20,
) -> float | None:
    """칼마 비율 — 연환산 수익 / 최대 낙폭."""
    if not returns or max_dd is None or max_dd == 0:
        return None
    mean_r = statistics.mean(returns)
    annual_return = mean_r * (252 / period_days)
    return round(annual_return / max_dd, 3)


def calculate_omega(
    returns: list[float], threshold: float = 0.0,
) -> float | None:
    """오메가 비율 — sum(gains) / sum(|losses|). >1이면 양호."""
    if not returns:
        return None
    gains = sum(r - threshold for r in returns if r > threshold)
    losses = sum(threshold - r for r in returns if r < threshold)
    if losses == 0:
        return None
    return round(gains / losses, 3)


def calculate_max_drawdown_days(returns: list[float]) -> int | None:
    """MDD 회복 기간 — 최대 낙폭 시작부터 회복까지의 기간 수."""
    if not returns:
        return None

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    dd_start_idx = 0
    max_dd_start = 0
    max_dd_end = 0

    for i, r in enumerate(returns):
        cumulative += r
        if cumulative > peak:
            peak = cumulative
            dd_start_idx = i + 1
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
            max_dd_start = dd_start_idx
            max_dd_end = i

    if max_dd == 0:
        return 0

    for i in range(max_dd_end + 1, len(returns)):
        cumulative_at_i = sum(returns[: i + 1])
        if cumulative_at_i >= peak:
            return i - max_dd_start
    return len(returns) - max_dd_start
