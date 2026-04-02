"""비선형 포지션 사이징 테스트 — sigmoid_tilt + 틸트 모드."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.portfolio.position_sizer import (
    PositionSizingInput,
    _apply_confidence_tilt,
    _get_tilt_factor,
    sigmoid_tilt,
    size_positions,
)


def _make_input(
    ticker: str,
    vol: float = 0.20,
    confidence: int | None = 5,
    sector: str | None = "Tech",
) -> PositionSizingInput:
    return PositionSizingInput(
        ticker=ticker,
        stock_id=hash(ticker) % 1000,
        volatility=vol,
        ai_confidence=confidence,
        sector=sector,
        price=100.0,
        daily_volume=1_000_000.0,
    )


# ──────────────────────────────────────────
# sigmoid_tilt 단위 테스트
# ──────────────────────────────────────────


class TestSigmoidTilt:
    """S-커브 비선형 틸트 함수 테스트."""

    def test_low_confidence_near_0_3(self):
        """confidence=1 이면 ~0.3 근처."""
        result = sigmoid_tilt(1)
        assert 0.25 <= result <= 0.55

    def test_neutral_confidence_near_1_0(self):
        """confidence=5 또는 6이면 ~1.0 근처 (5.5 중심)."""
        r5 = sigmoid_tilt(5)
        r6 = sigmoid_tilt(6)
        # 5와 6의 평균이 1.05 부근이어야 함
        avg = (r5 + r6) / 2
        assert 0.9 <= avg <= 1.2

    def test_high_confidence_near_1_8(self):
        """confidence=10 이면 ~1.8 근처."""
        result = sigmoid_tilt(10)
        assert 1.5 <= result <= 1.85

    def test_monotonically_increasing(self):
        """1부터 10까지 단조 증가."""
        values = [sigmoid_tilt(c) for c in range(1, 11)]
        for i in range(len(values) - 1):
            assert values[i] < values[i + 1], (
                f"sigmoid_tilt({i + 1})={values[i]} >= "
                f"sigmoid_tilt({i + 2})={values[i + 1]}"
            )

    def test_output_within_bounds(self):
        """모든 값이 [0.3, 1.8] 범위 안에 있어야 한다."""
        for c in range(1, 11):
            result = sigmoid_tilt(c)
            assert 0.3 <= result <= 1.8, f"sigmoid_tilt({c})={result} out of range"

    def test_center_gives_approximately_1_05(self):
        """confidence=5.5 중심에서 0.3 + 0.5*1.5 = 1.05 이어야 한다."""
        # 정수만 사용하므로 5와 6 평균으로 검증
        r5 = sigmoid_tilt(5)
        r6 = sigmoid_tilt(6)
        avg = (r5 + r6) / 2
        assert avg == pytest.approx(1.05, abs=0.05)


# ──────────────────────────────────────────
# _get_tilt_factor 단위 테스트
# ──────────────────────────────────────────


class TestGetTiltFactor:
    """틸트 모드별 배율 함수 테스트."""

    def test_linear_mode_preserves_existing(self):
        """linear 모드는 기존 confidence/5.0 동작을 유지해야 한다."""
        assert _get_tilt_factor(5, "linear") == pytest.approx(1.0)
        assert _get_tilt_factor(10, "linear") == pytest.approx(2.0)
        assert _get_tilt_factor(1, "linear") == pytest.approx(0.2)

    def test_sigmoid_mode_uses_sigmoid_tilt(self):
        """sigmoid 모드는 sigmoid_tilt() 값을 반환해야 한다."""
        for c in range(1, 11):
            assert _get_tilt_factor(c, "sigmoid") == sigmoid_tilt(c)

    def test_calibrated_mode_uses_win_rate(self):
        """calibrated 모드는 win_rate/50.0 을 사용해야 한다."""
        cal = {7: 70.0, 8: 80.0, 5: 50.0}
        assert _get_tilt_factor(7, "calibrated", cal) == pytest.approx(1.4)
        assert _get_tilt_factor(8, "calibrated", cal) == pytest.approx(1.6)
        assert _get_tilt_factor(5, "calibrated", cal) == pytest.approx(1.0)

    def test_calibrated_mode_fallback_to_sigmoid(self):
        """calibrated 모드에서 데이터 없는 신뢰도는 sigmoid fallback."""
        cal = {7: 70.0}  # confidence=3 데이터 없음
        result = _get_tilt_factor(3, "calibrated", cal)
        assert result == sigmoid_tilt(3)

    def test_calibrated_mode_no_data_fallback(self):
        """calibration_win_rates가 None이면 sigmoid fallback."""
        result = _get_tilt_factor(5, "calibrated", None)
        assert result == sigmoid_tilt(5)

    def test_unknown_mode_defaults_to_sigmoid(self):
        """알 수 없는 모드는 sigmoid 기본값."""
        result = _get_tilt_factor(5, "unknown_mode")
        assert result == sigmoid_tilt(5)


# ──────────────────────────────────────────
# _apply_confidence_tilt 모드별 통합 테스트
# ──────────────────────────────────────────


class TestApplyConfidenceTiltModes:
    """_apply_confidence_tilt의 틸트 모드별 동작 테스트."""

    def test_linear_mode_backward_compatible(self):
        """linear 모드는 기존 동작(confidence/5.0 + 정규화)과 동일해야 한다."""
        weights = {"A": 0.5, "B": 0.5}
        inputs = [_make_input("A", confidence=9), _make_input("B", confidence=5)]

        result = _apply_confidence_tilt(weights, inputs, tilt_mode="linear")
        # A: 9/5=1.8, B: 5/5=1.0 → 정규화 후 A > B
        assert result["A"] > result["B"]
        assert sum(result.values()) == pytest.approx(1.0, abs=0.01)

    def test_sigmoid_mode_high_confidence_boost(self):
        """sigmoid 모드에서 높은 신뢰도는 비중 상향."""
        weights = {"A": 0.5, "B": 0.5}
        inputs = [_make_input("A", confidence=9), _make_input("B", confidence=3)]

        result = _apply_confidence_tilt(weights, inputs, tilt_mode="sigmoid")
        assert result["A"] > result["B"]

    def test_sigmoid_mode_preserves_total(self):
        """sigmoid 모드에서 총 노출도 보존."""
        weights = {"A": 0.3, "B": 0.3, "C": 0.4}
        inputs = [
            _make_input("A", confidence=8),
            _make_input("B", confidence=3),
            _make_input("C", confidence=5),
        ]
        result = _apply_confidence_tilt(weights, inputs, tilt_mode="sigmoid")
        assert sum(result.values()) == pytest.approx(1.0, abs=0.01)

    def test_calibrated_mode_with_data(self):
        """calibrated 모드에서 win_rate 데이터 사용."""
        weights = {"A": 0.5, "B": 0.5}
        inputs = [_make_input("A", confidence=8), _make_input("B", confidence=5)]
        cal = {8: 80.0, 5: 40.0}  # A: 80/50=1.6, B: 40/50=0.8

        result = _apply_confidence_tilt(
            weights, inputs, tilt_mode="calibrated", calibration_win_rates=cal,
        )
        assert result["A"] > result["B"]
        assert sum(result.values()) == pytest.approx(1.0, abs=0.01)

    def test_none_tilt_mode_reads_settings(self):
        """tilt_mode=None 이면 settings에서 읽는다."""
        weights = {"A": 0.5, "B": 0.5}
        inputs = [_make_input("A", confidence=7), _make_input("B", confidence=5)]

        mock_settings = type("S", (), {"sizing_tilt_mode": "linear"})()
        with patch(
            "src.config.get_settings", return_value=mock_settings,
        ):
            result = _apply_confidence_tilt(weights, inputs, tilt_mode=None)
            # linear 모드: 7/5=1.4 vs 5/5=1.0
            assert result["A"] > result["B"]

    def test_none_confidence_treated_as_neutral(self):
        """ai_confidence=None 이면 5로 처리."""
        weights = {"A": 0.5, "B": 0.5}
        inputs = [_make_input("A", confidence=None), _make_input("B", confidence=5)]

        result = _apply_confidence_tilt(weights, inputs, tilt_mode="sigmoid")
        assert result["A"] == pytest.approx(result["B"], abs=0.01)


# ──────────────────────────────────────────
# size_positions 통합 테스트 (모드 전환)
# ──────────────────────────────────────────


class TestSizePositionsWithTiltMode:
    """size_positions가 설정의 틸트 모드를 사용하는지 검증."""

    def test_sigmoid_default_works(self):
        """기본 sigmoid 모드로 정상 동작."""
        inputs = [_make_input("A", confidence=8), _make_input("B", confidence=3)]
        import numpy as np

        cov = np.array([[0.0001, 0.00003], [0.00003, 0.0001]])
        mock_settings = type("S", (), {"sizing_tilt_mode": "sigmoid"})()
        with patch(
            "src.config.get_settings", return_value=mock_settings,
        ):
            result = size_positions(inputs, cov, strategy="erc")
            assert result.weights["A"] > result.weights["B"]
            assert result.total_exposure <= 1.0 + 1e-6
