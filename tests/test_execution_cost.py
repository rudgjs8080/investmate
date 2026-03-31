"""실행 비용 모델 테스트."""

from __future__ import annotations

import pytest

from src.portfolio.execution_cost import (
    CostBreakdown,
    ExecutionCostConfig,
    PortfolioCostResult,
    estimate_execution_cost,
    estimate_portfolio_cost,
    estimate_strategy_capacity,
)


class TestEstimateExecutionCost:
    """개별 종목 비용 추정 테스트."""

    def test_basic_cost_calculation(self):
        result = estimate_execution_cost(
            ticker="AAPL",
            weight=0.10,
            price=150.0,
            daily_volatility=0.015,
            adtv_shares=50_000_000.0,
            portfolio_value=1_000_000.0,
        )
        assert isinstance(result, CostBreakdown)
        assert result.spread_bps == 2.0  # default
        assert result.impact_bps > 0
        assert result.commission_bps == 1.0  # default
        assert result.total_bps == result.spread_bps + result.impact_bps + result.commission_bps
        assert result.capacity_ok is True

    def test_high_participation_flags_capacity(self):
        result = estimate_execution_cost(
            ticker="SMALL",
            weight=0.50,
            price=10.0,
            daily_volatility=0.03,
            adtv_shares=1000.0,  # 매우 적은 거래량
            portfolio_value=1_000_000.0,
        )
        assert result.capacity_ok is False
        assert result.participation_rate > 0.01

    def test_zero_volume_fallback(self):
        result = estimate_execution_cost(
            ticker="X", weight=0.10, price=100.0,
            daily_volatility=0.02, adtv_shares=0.0,
        )
        assert result.total_bps == 21.0  # fallback 20 + commission 1

    def test_zero_weight_fallback(self):
        result = estimate_execution_cost(
            ticker="X", weight=0.0, price=100.0,
            daily_volatility=0.02, adtv_shares=1_000_000.0,
        )
        assert result.participation_rate == 0.0

    def test_custom_config(self):
        config = ExecutionCostConfig(
            spread_bps=5.0,
            impact_coefficient=2.0,
            commission_bps=3.0,
        )
        result = estimate_execution_cost(
            ticker="A", weight=0.10, price=100.0,
            daily_volatility=0.02, adtv_shares=1_000_000.0,
            config=config,
        )
        assert result.spread_bps == 5.0
        assert result.commission_bps == 3.0

    def test_result_is_frozen(self):
        result = estimate_execution_cost(
            "A", 0.10, 100.0, 0.02, 1_000_000.0,
        )
        with pytest.raises(AttributeError):
            result.total_bps = 0.0  # type: ignore[misc]

    def test_larger_weight_higher_impact(self):
        small = estimate_execution_cost("A", 0.05, 100.0, 0.02, 1_000_000.0)
        large = estimate_execution_cost("A", 0.30, 100.0, 0.02, 1_000_000.0)
        assert large.impact_bps > small.impact_bps


class TestEstimatePortfolioCost:
    """포트폴리오 비용 추정 테스트."""

    def test_basic_portfolio(self):
        result = estimate_portfolio_cost(
            weights={"AAPL": 0.30, "MSFT": 0.30},
            price_map={"AAPL": 150.0, "MSFT": 400.0},
            volatility_map={"AAPL": 0.015, "MSFT": 0.012},
            adtv_map={"AAPL": 50_000_000.0, "MSFT": 30_000_000.0},
        )
        assert isinstance(result, PortfolioCostResult)
        assert len(result.breakdowns) == 2
        assert result.portfolio_avg_cost_bps > 0

    def test_empty_portfolio(self):
        result = estimate_portfolio_cost({}, {}, {}, {})
        assert len(result.breakdowns) == 0
        assert result.portfolio_avg_cost_bps == 0.0

    def test_capacity_limited_tickers(self):
        result = estimate_portfolio_cost(
            weights={"A": 0.50},
            price_map={"A": 10.0},
            volatility_map={"A": 0.03},
            adtv_map={"A": 100.0},  # tiny volume
            portfolio_value=10_000_000.0,
        )
        assert "A" in result.capacity_limited_tickers

    def test_max_aum_estimate(self):
        result = estimate_portfolio_cost(
            weights={"A": 0.50, "B": 0.50},
            price_map={"A": 100.0, "B": 200.0},
            volatility_map={"A": 0.01, "B": 0.01},
            adtv_map={"A": 1_000_000.0, "B": 500_000.0},
        )
        assert result.max_aum_estimate is not None
        assert result.max_aum_estimate > 0

    def test_result_is_frozen(self):
        result = estimate_portfolio_cost({}, {}, {}, {})
        with pytest.raises(AttributeError):
            result.portfolio_avg_cost_bps = 99.0  # type: ignore[misc]


class TestEstimateStrategyCapacity:
    """전략 용량 추정 테스트."""

    def test_basic_capacity(self):
        cap = estimate_strategy_capacity(
            weights={"A": 0.50},
            adtv_map={"A": 1_000_000.0},
            price_map={"A": 100.0},
        )
        # 1% × 1M × $100 / 0.5 = $2M
        assert cap == pytest.approx(2_000_000.0, rel=0.01)

    def test_bottleneck_stock(self):
        cap = estimate_strategy_capacity(
            weights={"A": 0.50, "B": 0.50},
            adtv_map={"A": 10_000_000.0, "B": 100_000.0},
            price_map={"A": 100.0, "B": 100.0},
        )
        # B가 병목: 1% × 100K × $100 / 0.5 = $200K
        assert cap == pytest.approx(200_000.0, rel=0.01)

    def test_empty_returns_none(self):
        cap = estimate_strategy_capacity({}, {}, {})
        assert cap is None

    def test_zero_volume_excluded(self):
        cap = estimate_strategy_capacity(
            weights={"A": 0.50},
            adtv_map={"A": 0.0},
            price_map={"A": 100.0},
        )
        assert cap is None
