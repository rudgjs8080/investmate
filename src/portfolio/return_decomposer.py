"""수익률 분해 — gross return에서 비용 항목별 차감."""

from __future__ import annotations

from dataclasses import dataclass

from src.portfolio.execution_cost import CostBreakdown, PortfolioCostResult


@dataclass(frozen=True)
class ReturnDecomposition:
    """개별 종목의 수익률 분해."""

    ticker: str
    gross_return_pct: float
    spread_cost_pct: float
    impact_cost_pct: float
    commission_pct: float
    net_return_pct: float


@dataclass(frozen=True)
class PortfolioReturnDecomposition:
    """포트폴리오 수익률 분해."""

    gross_avg_return_pct: float
    total_cost_pct: float
    net_avg_return_pct: float
    by_stock: tuple[ReturnDecomposition, ...]


def decompose_returns(
    gross_return_pct: float,
    cost_breakdown: CostBreakdown,
) -> ReturnDecomposition:
    """개별 종목의 수익률을 비용 항목별로 분해한다.

    Args:
        gross_return_pct: 거래비용 차감 전 수익률 (%)
        cost_breakdown: 비용 분해 결과

    Returns:
        ReturnDecomposition
    """
    spread_pct = cost_breakdown.spread_bps / 100.0
    impact_pct = cost_breakdown.impact_bps / 100.0
    commission_pct = cost_breakdown.commission_bps / 100.0
    net = gross_return_pct - spread_pct - impact_pct - commission_pct

    return ReturnDecomposition(
        ticker=cost_breakdown.ticker,
        gross_return_pct=round(gross_return_pct, 4),
        spread_cost_pct=round(spread_pct, 4),
        impact_cost_pct=round(impact_pct, 4),
        commission_pct=round(commission_pct, 4),
        net_return_pct=round(net, 4),
    )


def decompose_portfolio_returns(
    returns_by_ticker: dict[str, float],
    cost_result: PortfolioCostResult,
) -> PortfolioReturnDecomposition:
    """포트폴리오 수익률을 비용 항목별로 분해한다.

    Args:
        returns_by_ticker: {ticker: gross return %}
        cost_result: 포트폴리오 비용 결과

    Returns:
        PortfolioReturnDecomposition
    """
    cost_map = {cb.ticker: cb for cb in cost_result.breakdowns}
    decompositions: list[ReturnDecomposition] = []

    for ticker, gross_ret in returns_by_ticker.items():
        cb = cost_map.get(ticker)
        if cb is None:
            decompositions.append(ReturnDecomposition(
                ticker=ticker,
                gross_return_pct=round(gross_ret, 4),
                spread_cost_pct=0.0,
                impact_cost_pct=0.0,
                commission_pct=0.0,
                net_return_pct=round(gross_ret, 4),
            ))
        else:
            decompositions.append(decompose_returns(gross_ret, cb))

    if decompositions:
        gross_avg = sum(d.gross_return_pct for d in decompositions) / len(decompositions)
        net_avg = sum(d.net_return_pct for d in decompositions) / len(decompositions)
        total_cost = gross_avg - net_avg
    else:
        gross_avg = 0.0
        net_avg = 0.0
        total_cost = 0.0

    return PortfolioReturnDecomposition(
        gross_avg_return_pct=round(gross_avg, 4),
        total_cost_pct=round(total_cost, 4),
        net_avg_return_pct=round(net_avg, 4),
        by_stock=tuple(decompositions),
    )
