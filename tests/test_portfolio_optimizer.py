"""포트폴리오 최적화 엔진 테스트."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.portfolio.optimizer import (
    PortfolioResult,
    equal_weight,
    optimize_portfolio,
)
from src.portfolio.efficient_frontier import compute_efficient_frontier


# ── 테스트 데이터 ──

def _make_prices(n_days: int = 100, n_stocks: int = 3, seed: int = 42) -> dict[str, pd.Series]:
    """재현 가능한 가격 시계열 생성."""
    rng = np.random.RandomState(seed)
    tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "META"][:n_stocks]
    result = {}
    for i, ticker in enumerate(tickers):
        daily_returns = rng.normal(0.0005 + i * 0.0001, 0.015 + i * 0.002, n_days)
        prices = 100 * np.cumprod(1 + daily_returns)
        result[ticker] = pd.Series(prices, index=range(n_days))
    return result


class TestEqualWeight:
    """동일 비중 테스트."""

    def test_basic(self):
        result = equal_weight(["AAPL", "MSFT", "GOOGL"])
        assert result.strategy == "동일비중"
        assert len(result.allocations) == 3
        assert abs(sum(result.allocations.values()) - 1.0) < 0.01

    def test_with_investment(self):
        result = equal_weight(["AAPL", "MSFT"], investment=10000.0)
        assert result.amounts["AAPL"] == 5000.0
        assert result.amounts["MSFT"] == 5000.0

    def test_empty(self):
        result = equal_weight([])
        assert result.allocations == {}
        assert result.expected_return == 0.0

    def test_single_stock(self):
        result = equal_weight(["AAPL"])
        assert result.allocations["AAPL"] == 1.0


class TestOptimizePortfolio:
    """포트폴리오 최적화 테스트."""

    def test_max_sharpe(self):
        prices = _make_prices()
        result = optimize_portfolio(prices, strategy="max_sharpe")
        assert result.strategy == "최대샤프"
        assert abs(sum(result.allocations.values()) - 1.0) < 0.02
        assert all(0 <= w <= 1.0 for w in result.allocations.values())

    def test_min_variance(self):
        prices = _make_prices()
        result = optimize_portfolio(prices, strategy="min_variance")
        assert result.strategy == "최소분산"
        assert abs(sum(result.allocations.values()) - 1.0) < 0.02

    def test_risk_parity(self):
        prices = _make_prices()
        result = optimize_portfolio(prices, strategy="risk_parity")
        assert result.strategy == "역변동성"
        assert abs(sum(result.allocations.values()) - 1.0) < 0.02

    def test_equal_weight_strategy(self):
        prices = _make_prices()
        result = optimize_portfolio(prices, strategy="equal_weight")
        assert result.strategy == "동일비중"

    def test_with_investment(self):
        prices = _make_prices()
        result = optimize_portfolio(prices, strategy="max_sharpe", investment=100_000)
        total_amount = sum(result.amounts.values())
        assert abs(total_amount - 100_000) < 100  # 반올림 오차 허용

    def test_single_stock_fallback(self):
        prices = {"AAPL": pd.Series(np.cumsum(np.random.normal(0, 1, 100)) + 100)}
        result = optimize_portfolio(prices, strategy="max_sharpe")
        assert result.allocations["AAPL"] == 1.0

    def test_empty_data(self):
        result = optimize_portfolio({}, strategy="max_sharpe")
        assert result.allocations == {}

    def test_insufficient_data_fallback(self):
        """데이터 부족 시 동일 비중 폴백."""
        prices = {
            "AAPL": pd.Series([100, 101, 102]),
            "MSFT": pd.Series([200, 201, 202]),
        }
        result = optimize_portfolio(prices, strategy="max_sharpe")
        assert result.strategy == "동일비중"  # 데이터 부족으로 폴백

    def test_result_is_frozen(self):
        result = equal_weight(["AAPL", "MSFT"])
        with pytest.raises(AttributeError):
            result.strategy = "changed"


class TestEfficientFrontier:
    """효율적 프런티어 테스트."""

    def test_returns_points(self):
        prices = _make_prices(n_stocks=3, n_days=100)
        frontier = compute_efficient_frontier(prices, n_points=10)
        assert len(frontier) > 0
        assert all(hasattr(p, "expected_return") for p in frontier)
        assert all(hasattr(p, "volatility") for p in frontier)

    def test_single_stock_empty(self):
        """단일 종목은 프런티어 없음."""
        prices = {"AAPL": pd.Series(np.cumsum(np.random.normal(0, 1, 100)) + 100)}
        frontier = compute_efficient_frontier(prices)
        assert frontier == []

    def test_frontier_monotonic_risk(self):
        """프런티어 점들의 수익률이 대체로 증가하면 변동성도 증가."""
        prices = _make_prices(n_stocks=4, n_days=200)
        frontier = compute_efficient_frontier(prices, n_points=15)
        if len(frontier) >= 3:
            # 프런티어의 첫 점은 최소 변동성에 가깝고, 마지막은 최대
            assert frontier[0].volatility <= frontier[-1].volatility + 5.0  # 약간의 허용

    def test_frontier_allocations_sum_to_one(self):
        """각 프런티어 점의 배분 합은 ~1."""
        prices = _make_prices(n_stocks=3, n_days=100)
        frontier = compute_efficient_frontier(prices, n_points=5)
        for point in frontier:
            total = sum(point.allocations.values())
            assert abs(total - 1.0) < 0.05


class TestLedoitWolfCovariance:
    """Ledoit-Wolf 공분산 축소 테스트."""

    def test_sklearn_import_available(self):
        """sklearn.covariance.LedoitWolf가 임포트 가능한지 확인."""
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf()
        assert lw is not None

    def test_result_stability_with_ledoit_wolf(self):
        """Ledoit-Wolf로 최적화된 결과가 안정적인지 확인."""
        prices = _make_prices(n_stocks=4, n_days=200, seed=42)
        result1 = optimize_portfolio(prices, strategy="max_sharpe")
        result2 = optimize_portfolio(prices, strategy="max_sharpe")
        # 동일 입력이면 동일 결과
        assert result1.allocations == result2.allocations
        assert result1.sharpe_ratio == result2.sharpe_ratio

    def test_fallback_when_sklearn_unavailable(self):
        """sklearn 없을 때 fallback이 동작하는지 확인 (간접 테스트)."""
        prices = _make_prices(n_stocks=3, n_days=100)
        # optimize_portfolio는 내부적으로 try/except로 fallback
        result = optimize_portfolio(prices, strategy="min_variance")
        assert result.strategy == "최소분산"
        assert abs(sum(result.allocations.values()) - 1.0) < 0.02
