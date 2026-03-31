"""수익률 분해 모듈 테스트."""

from __future__ import annotations

import pytest

from src.portfolio.execution_cost import CostBreakdown, PortfolioCostResult
from src.portfolio.return_decomposer import (
    PortfolioReturnDecomposition,
    ReturnDecomposition,
    decompose_portfolio_returns,
    decompose_returns,
)


def _make_cost(ticker: str = "AAPL", spread: float = 2.0,
               impact: float = 3.0, commission: float = 1.0) -> CostBreakdown:
    return CostBreakdown(
        ticker=ticker,
        spread_bps=spread,
        impact_bps=impact,
        commission_bps=commission,
        total_bps=spread + impact + commission,
        participation_rate=0.005,
        capacity_ok=True,
    )


class TestDecomposeReturns:
    """개별 종목 수익률 분해 테스트."""

    def test_basic_decomposition(self):
        cost = _make_cost(spread=2.0, impact=3.0, commission=1.0)
        result = decompose_returns(5.0, cost)
        assert result.gross_return_pct == 5.0
        assert result.spread_cost_pct == pytest.approx(0.02)  # 2bps = 0.02%
        assert result.impact_cost_pct == pytest.approx(0.03)
        assert result.commission_pct == pytest.approx(0.01)
        assert result.net_return_pct == pytest.approx(5.0 - 0.06, abs=0.001)

    def test_negative_gross_return(self):
        cost = _make_cost()
        result = decompose_returns(-3.0, cost)
        assert result.net_return_pct < -3.0

    def test_zero_cost(self):
        cost = _make_cost(spread=0.0, impact=0.0, commission=0.0)
        result = decompose_returns(5.0, cost)
        assert result.net_return_pct == pytest.approx(5.0)

    def test_result_is_frozen(self):
        cost = _make_cost()
        result = decompose_returns(5.0, cost)
        with pytest.raises(AttributeError):
            result.net_return_pct = 0.0  # type: ignore[misc]


class TestDecomposePortfolioReturns:
    """포트폴리오 수익률 분해 테스트."""

    def test_basic_portfolio(self):
        returns = {"AAPL": 5.0, "MSFT": 3.0}
        cost_result = PortfolioCostResult(
            breakdowns=(
                _make_cost("AAPL"),
                _make_cost("MSFT"),
            ),
            portfolio_avg_cost_bps=6.0,
            capacity_limited_tickers=(),
            max_aum_estimate=None,
        )
        result = decompose_portfolio_returns(returns, cost_result)
        assert isinstance(result, PortfolioReturnDecomposition)
        assert result.gross_avg_return_pct == pytest.approx(4.0)
        assert result.net_avg_return_pct < result.gross_avg_return_pct
        assert result.total_cost_pct > 0
        assert len(result.by_stock) == 2

    def test_empty_portfolio(self):
        result = decompose_portfolio_returns(
            {},
            PortfolioCostResult((), 0.0, (), None),
        )
        assert result.gross_avg_return_pct == 0.0
        assert result.net_avg_return_pct == 0.0

    def test_ticker_without_cost(self):
        returns = {"AAPL": 5.0, "GOOG": 3.0}
        cost_result = PortfolioCostResult(
            breakdowns=(_make_cost("AAPL"),),
            portfolio_avg_cost_bps=6.0,
            capacity_limited_tickers=(),
            max_aum_estimate=None,
        )
        result = decompose_portfolio_returns(returns, cost_result)
        # GOOG has no cost → net = gross
        goog = next(d for d in result.by_stock if d.ticker == "GOOG")
        assert goog.net_return_pct == goog.gross_return_pct

    def test_result_is_frozen(self):
        result = decompose_portfolio_returns(
            {},
            PortfolioCostResult((), 0.0, (), None),
        )
        with pytest.raises(AttributeError):
            result.total_cost_pct = 99.0  # type: ignore[misc]
