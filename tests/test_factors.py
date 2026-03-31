"""팩터 투자 프레임워크 테스트."""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.analysis.factors import (
    DEFAULT_CATEGORY_WEIGHTS,
    FACTOR_REGIME_WEIGHTS,
    CompositeFactorScore,
    FactorValue,
    normalize_cross_section,
)


class TestNormalizeCrossSection:
    """z-score 정규화 테스트."""

    def test_basic_normalization(self):
        raw = {1: 10.0, 2: 20.0, 3: 30.0, 4: 40.0, 5: 50.0,
               6: 15.0, 7: 25.0, 8: 35.0, 9: 45.0, 10: 55.0,
               11: 12.0, 12: 22.0, 13: 32.0, 14: 42.0, 15: 52.0,
               16: 14.0, 17: 24.0, 18: 34.0, 19: 44.0, 20: 54.0,
               21: 11.0, 22: 21.0, 23: 31.0, 24: 41.0, 25: 51.0,
               26: 13.0, 27: 23.0, 28: 33.0, 29: 43.0, 30: 53.0}
        result = normalize_cross_section(raw)
        # z-score의 평균은 ~0
        values = list(result.values())
        assert abs(np.mean(values)) < 0.1

    def test_insufficient_data_returns_zeros(self):
        raw = {1: 10.0, 2: 20.0}
        result = normalize_cross_section(raw)
        assert all(v == 0.0 for v in result.values())

    def test_winsorization(self):
        raw = {i: float(i) for i in range(1, 101)}
        raw[999] = 10000.0  # extreme outlier
        result = normalize_cross_section(raw, winsorize_sigma=3.0)
        assert result[999] <= 3.0

    def test_constant_values_return_zeros(self):
        raw = {i: 5.0 for i in range(1, 50)}
        result = normalize_cross_section(raw)
        assert all(v == 0.0 for v in result.values())

    def test_nan_values_get_zero(self):
        raw = {i: float(i) for i in range(1, 40)}
        raw[999] = float("nan")
        result = normalize_cross_section(raw)
        assert result[999] == 0.0

    def test_higher_raw_gets_positive_z(self):
        raw = {i: float(i) for i in range(1, 50)}
        result = normalize_cross_section(raw)
        assert result[49] > 0
        assert result[1] < 0


class TestCompositeFactorScore:
    """CompositeFactorScore frozen 검증."""

    def test_frozen(self):
        score = CompositeFactorScore(
            stock_id=1, ticker="AAPL",
            value_z=0.5, momentum_z=0.3, quality_z=0.2,
            low_vol_z=0.1, size_z=-0.1, composite=0.3,
            category_details={},
        )
        with pytest.raises(AttributeError):
            score.composite = 1.0  # type: ignore[misc]

    def test_fields(self):
        score = CompositeFactorScore(
            stock_id=1, ticker="AAPL",
            value_z=0.5, momentum_z=0.3, quality_z=0.2,
            low_vol_z=0.1, size_z=-0.1, composite=0.3,
            category_details={"value": {"earnings_yield": 0.5}},
        )
        assert score.value_z == 0.5
        assert "earnings_yield" in score.category_details["value"]


class TestFactorValue:
    """FactorValue frozen 검증."""

    def test_frozen(self):
        fv = FactorValue(
            stock_id=1, ticker="AAPL",
            factor_name="earnings_yield", category="value",
            raw_value=0.05, z_score=1.2,
        )
        with pytest.raises(AttributeError):
            fv.z_score = 0.0  # type: ignore[misc]


class TestDefaultWeights:
    """기본 가중치 검증."""

    def test_default_weights_sum_to_one(self):
        total = sum(DEFAULT_CATEGORY_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=0.01)

    def test_regime_weights_sum_to_one(self):
        for regime, weights in FACTOR_REGIME_WEIGHTS.items():
            total = sum(weights.values())
            assert total == pytest.approx(1.0, abs=0.01), f"{regime} 가중치 합 {total}"

    def test_all_regimes_present(self):
        assert set(FACTOR_REGIME_WEIGHTS.keys()) == {"bull", "bear", "range", "crisis"}


class TestComputeCompositeScores:
    """compute_composite_scores 통합 테스트 (DB 필요)."""

    def test_empty_stock_ids(self, session):
        from src.analysis.factors import compute_composite_scores
        result = compute_composite_scores(session, [], date(2026, 3, 15))
        assert result == {}


# 날짜 import
from datetime import date
