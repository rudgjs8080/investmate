"""포지션 사이징 엔진 테스트."""

from __future__ import annotations

import numpy as np
import pytest

from src.portfolio.position_sizer import (
    PositionSizingInput,
    SizingResult,
    _apply_confidence_tilt,
    _equal_risk_contribution,
    _half_kelly,
    _inverse_volatility_fallback,
    _volatility_targeting,
    size_positions,
)


def _make_input(ticker: str, vol: float = 0.20, confidence: int | None = 5,
                sector: str | None = "Tech") -> PositionSizingInput:
    return PositionSizingInput(
        ticker=ticker, stock_id=hash(ticker) % 1000,
        volatility=vol, ai_confidence=confidence,
        sector=sector, price=100.0, daily_volume=1_000_000.0,
    )


def _make_cov(n: int, base_vol: float = 0.01, corr: float = 0.3) -> np.ndarray:
    """n x n 공분산 행렬 생성 (일간 기준)."""
    vols = np.full(n, base_vol)
    cov = np.full((n, n), corr * base_vol * base_vol)
    np.fill_diagonal(cov, base_vol ** 2)
    return cov


class TestEqualRiskContribution:
    """ERC 전략 테스트."""

    def test_two_stocks_equal_vol(self):
        inputs = [_make_input("A", vol=0.20), _make_input("B", vol=0.20)]
        cov = _make_cov(2)
        result = _equal_risk_contribution(inputs, cov)
        assert len(result) == 2
        assert result["A"] == pytest.approx(result["B"], abs=0.05)

    def test_different_vol_inverse_weight(self):
        inputs = [_make_input("A", vol=0.10), _make_input("B", vol=0.40)]
        result = _inverse_volatility_fallback(inputs)
        assert result["A"] > result["B"]

    def test_single_stock(self):
        inputs = [_make_input("A")]
        cov = _make_cov(1)
        result = _equal_risk_contribution(inputs, cov)
        assert result["A"] == pytest.approx(1.0, abs=0.01)

    def test_fallback_when_no_cov(self):
        inputs = [_make_input("A"), _make_input("B")]
        result = _equal_risk_contribution(inputs, None)
        assert sum(result.values()) == pytest.approx(1.0, abs=0.01)

    def test_weights_sum_to_one(self):
        inputs = [_make_input(f"S{i}", vol=0.15 + i * 0.05) for i in range(5)]
        cov = _make_cov(5)
        result = _equal_risk_contribution(inputs, cov)
        assert sum(result.values()) == pytest.approx(1.0, abs=0.01)


class TestVolatilityTargeting:
    """Vol Target 전략 테스트."""

    def test_target_vol_scaling(self):
        inputs = [_make_input("A"), _make_input("B")]
        cov = _make_cov(2, base_vol=0.02)
        result = _volatility_targeting(inputs, cov, target_vol=0.15)
        total = sum(result.values())
        assert total <= 1.0 + 1e-6

    def test_high_vol_caps_exposure(self):
        inputs = [_make_input("A", vol=0.50)]
        cov = _make_cov(1, base_vol=0.05)
        result = _volatility_targeting(inputs, cov, target_vol=0.10)
        assert sum(result.values()) <= 1.0 + 1e-6

    def test_no_cov_returns_base(self):
        inputs = [_make_input("A"), _make_input("B")]
        result = _volatility_targeting(inputs, None, target_vol=0.15)
        assert len(result) == 2


class TestHalfKelly:
    """Half-Kelly 전략 테스트."""

    def test_positive_edge(self):
        inputs = [_make_input("A"), _make_input("B")]
        cov = _make_cov(2, base_vol=0.01)
        expected_returns = {"A": 0.10, "B": 0.08}  # 양의 초과수익
        result = _half_kelly(inputs, expected_returns, cov, 0.04)
        assert all(v >= 0 for v in result.values())

    def test_no_expected_returns_fallback(self):
        inputs = [_make_input("A")]
        cov = _make_cov(1)
        result = _half_kelly(inputs, None, cov, 0.04)
        assert len(result) == 1

    def test_weights_nonnegative(self):
        inputs = [_make_input("A"), _make_input("B")]
        cov = _make_cov(2)
        expected_returns = {"A": 0.01, "B": -0.05}  # B는 음의 초과수익
        result = _half_kelly(inputs, expected_returns, cov, 0.04)
        assert all(v >= 0 for v in result.values())


class TestConfidenceTilt:
    """AI 신뢰도 틸트 테스트."""

    def test_high_confidence_increases(self):
        weights = {"A": 0.5, "B": 0.5}
        inputs = [
            _make_input("A", confidence=9),
            _make_input("B", confidence=5),
        ]
        result = _apply_confidence_tilt(weights, inputs)
        assert result["A"] > result["B"]

    def test_low_confidence_decreases(self):
        weights = {"A": 0.5, "B": 0.5}
        inputs = [
            _make_input("A", confidence=2),
            _make_input("B", confidence=5),
        ]
        result = _apply_confidence_tilt(weights, inputs)
        assert result["A"] < result["B"]

    def test_neutral_confidence_unchanged(self):
        weights = {"A": 0.5, "B": 0.5}
        inputs = [
            _make_input("A", confidence=5),
            _make_input("B", confidence=5),
        ]
        result = _apply_confidence_tilt(weights, inputs)
        assert result["A"] == pytest.approx(0.5, abs=0.01)

    def test_none_confidence_treated_as_neutral(self):
        weights = {"A": 0.5, "B": 0.5}
        inputs = [
            _make_input("A", confidence=None),
            _make_input("B", confidence=5),
        ]
        result = _apply_confidence_tilt(weights, inputs)
        assert result["A"] == pytest.approx(result["B"], abs=0.01)

    def test_preserves_total_exposure(self):
        weights = {"A": 0.3, "B": 0.3, "C": 0.4}
        inputs = [
            _make_input("A", confidence=8),
            _make_input("B", confidence=3),
            _make_input("C", confidence=5),
        ]
        result = _apply_confidence_tilt(weights, inputs)
        assert sum(result.values()) == pytest.approx(1.0, abs=0.01)


class TestSizePositions:
    """size_positions 통합 테스트."""

    def test_empty_input(self):
        result = size_positions([], None)
        assert result.weights == {}
        assert result.cash_weight == 1.0
        assert result.total_exposure == 0.0

    def test_single_stock(self):
        inputs = [_make_input("AAPL")]
        result = size_positions(inputs, None)
        assert "AAPL" in result.weights
        assert result.total_exposure > 0

    def test_result_is_frozen(self):
        result = size_positions([], None)
        with pytest.raises(AttributeError):
            result.strategy = "other"  # type: ignore[misc]

    def test_cash_weight_nonnegative(self):
        inputs = [_make_input(f"S{i}") for i in range(5)]
        cov = _make_cov(5)
        result = size_positions(inputs, cov, strategy="vol_target")
        assert result.cash_weight >= 0.0

    def test_weights_sum_le_one(self):
        inputs = [_make_input(f"S{i}") for i in range(5)]
        cov = _make_cov(5)
        result = size_positions(inputs, cov, strategy="erc")
        assert result.total_exposure <= 1.0 + 1e-6

    def test_erc_strategy(self):
        inputs = [_make_input("A"), _make_input("B"), _make_input("C")]
        cov = _make_cov(3)
        result = size_positions(inputs, cov, strategy="erc")
        assert result.strategy == "erc"
        assert len(result.weights) == 3

    def test_vol_target_strategy(self):
        inputs = [_make_input("A"), _make_input("B")]
        cov = _make_cov(2)
        result = size_positions(inputs, cov, strategy="vol_target", target_vol=0.15)
        assert result.strategy == "vol_target"

    def test_raw_weights_preserved(self):
        inputs = [_make_input("A", confidence=9), _make_input("B", confidence=1)]
        cov = _make_cov(2)
        result = size_positions(inputs, cov, strategy="erc")
        # raw_weights should be pre-tilt
        assert len(result.raw_weights) == 2
