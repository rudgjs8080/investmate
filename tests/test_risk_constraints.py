"""리스크 제약 엔진 테스트."""

from __future__ import annotations

import numpy as np
import pytest

from src.portfolio.risk_constraints import (
    ConstraintCheckResult,
    ConstraintSeverity,
    RiskConstraints,
    _check_correlation_warning,
    _enforce_leverage_limit,
    _enforce_sector_limit,
    _enforce_single_stock_limit,
    _enforce_var_limit,
    check_and_adjust,
)


class TestSingleStockLimit:
    """단일 종목 비중 제약 테스트."""

    def test_excess_weight_reduced(self):
        weights = {"AAPL": 0.15, "MSFT": 0.08}
        adjusted, violations = _enforce_single_stock_limit(weights, 0.10)
        assert adjusted["AAPL"] == 0.10
        assert adjusted["MSFT"] == 0.08
        assert len(violations) == 1
        assert violations[0].ticker == "AAPL"

    def test_within_limit_unchanged(self):
        weights = {"AAPL": 0.08, "MSFT": 0.07}
        adjusted, violations = _enforce_single_stock_limit(weights, 0.10)
        assert adjusted == weights
        assert len(violations) == 0

    def test_violation_recorded(self):
        weights = {"A": 0.20, "B": 0.30}
        _, violations = _enforce_single_stock_limit(weights, 0.10)
        assert len(violations) == 2
        assert all(v.severity == ConstraintSeverity.HARD for v in violations)


class TestSectorLimit:
    """섹터 비중 제약 테스트."""

    def test_sector_over_30pct_reduced(self):
        weights = {"AAPL": 0.20, "MSFT": 0.20, "GOOG": 0.10}
        sector_map = {"AAPL": "Tech", "MSFT": "Tech", "GOOG": "Comm"}
        adjusted, violations = _enforce_sector_limit(weights, sector_map, 0.30)
        tech_total = adjusted["AAPL"] + adjusted["MSFT"]
        assert tech_total == pytest.approx(0.30, abs=0.01)
        assert len(violations) == 1

    def test_mixed_sectors_pass(self):
        weights = {"AAPL": 0.15, "JPM": 0.15, "GOOG": 0.15}
        sector_map = {"AAPL": "Tech", "JPM": "Finance", "GOOG": "Comm"}
        adjusted, violations = _enforce_sector_limit(weights, sector_map, 0.30)
        assert adjusted == weights
        assert len(violations) == 0

    def test_null_sector_excluded(self):
        weights = {"AAPL": 0.50, "XXX": 0.30}
        sector_map = {"AAPL": "Tech", "XXX": None}
        adjusted, violations = _enforce_sector_limit(weights, sector_map, 0.30)
        # XXX has no sector, not counted
        assert len(violations) == 1  # Tech over 30%


class TestVaRLimit:
    """VaR 제약 테스트."""

    def test_var_within_limit(self):
        # 매우 낮은 변동성 → VaR 안전
        cov = np.eye(2) * 0.0001
        weights = {"A": 0.5, "B": 0.5}
        tickers = ["A", "B"]
        adjusted, violations, var = _enforce_var_limit(weights, cov, tickers, 0.02)
        assert len(violations) == 0
        assert var < 0.02

    def test_var_exceeded_scales_down(self):
        # 높은 변동성 → VaR 초과
        cov = np.eye(2) * 0.01  # 일간 vol ~10%
        weights = {"A": 0.5, "B": 0.5}
        tickers = ["A", "B"]
        adjusted, violations, var = _enforce_var_limit(weights, cov, tickers, 0.02)
        assert len(violations) == 1
        # 조정 후 비중 감소
        assert adjusted["A"] < 0.5
        assert adjusted["B"] < 0.5


class TestLeverageLimit:
    """레버리지 제약 테스트."""

    def test_within_limit(self):
        weights = {"A": 0.5, "B": 0.3}
        adjusted, violations = _enforce_leverage_limit(weights, 1.0)
        assert adjusted == weights
        assert len(violations) == 0

    def test_exceeded_scales_down(self):
        weights = {"A": 0.6, "B": 0.6}
        adjusted, violations = _enforce_leverage_limit(weights, 1.0)
        total = sum(adjusted.values())
        assert total == pytest.approx(1.0, abs=0.01)
        assert len(violations) == 1


class TestCorrelationWarning:
    """상관관계 경고 테스트."""

    def test_high_correlation_warns(self):
        # 상관 0.8
        cov = np.array([[0.01, 0.008], [0.008, 0.01]])
        weights = {"A": 0.5, "B": 0.5}
        warnings = _check_correlation_warning(weights, cov, ["A", "B"], 0.50)
        assert len(warnings) == 1
        assert warnings[0].severity == ConstraintSeverity.SOFT

    def test_low_correlation_no_warning(self):
        cov = np.array([[0.01, 0.001], [0.001, 0.01]])
        weights = {"A": 0.5, "B": 0.5}
        warnings = _check_correlation_warning(weights, cov, ["A", "B"], 0.50)
        assert len(warnings) == 0


class TestCheckAndAdjust:
    """전체 제약 파이프라인 테스트."""

    def test_happy_path(self):
        weights = {"A": 0.08, "B": 0.07, "C": 0.05}
        sector_map = {"A": "Tech", "B": "Finance", "C": "Health"}
        cov = np.eye(3) * 0.0001
        result = check_and_adjust(
            weights, sector_map, cov, ["A", "B", "C"],
        )
        assert isinstance(result, ConstraintCheckResult)
        assert len(result.violations) == 0
        assert result.cash_weight > 0

    def test_cascading_constraints(self):
        weights = {"A": 0.20, "B": 0.20, "C": 0.20, "D": 0.20, "E": 0.20}
        sector_map = {"A": "Tech", "B": "Tech", "C": "Tech",
                      "D": "Finance", "E": "Health"}
        cov = np.eye(5) * 0.001
        result = check_and_adjust(
            weights, sector_map, cov, ["A", "B", "C", "D", "E"],
        )
        # Tech sector (A+B+C=60%) should be capped to 30%
        tech_total = sum(
            result.adjusted_weights.get(t, 0) for t in ["A", "B", "C"]
        )
        assert tech_total <= 0.30 + 0.01
        assert len(result.violations) > 0

    def test_result_is_frozen(self):
        result = check_and_adjust({}, {})
        with pytest.raises(AttributeError):
            result.cash_weight = 0.5  # type: ignore[misc]

    def test_no_cov_skips_var(self):
        weights = {"A": 0.5}
        result = check_and_adjust(weights, {"A": "Tech"})
        assert result.portfolio_var_95 is None
