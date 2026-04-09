"""예측 만기 평가 + 정확도 점수 테스트."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

from src.deepdive.forecast_evaluator import (
    HORIZON_DAYS,
    _score_single_ticker,
    compute_accuracy_scores,
)
from src.deepdive.schemas import ForecastAccuracy


class TestMaturityDateCalculation:
    """만기일 계산 테스트."""

    def test_1m_horizon(self):
        """1M = 30일."""
        assert HORIZON_DAYS["1M"] == 30

    def test_3m_horizon(self):
        """3M = 90일."""
        assert HORIZON_DAYS["3M"] == 90

    def test_6m_horizon(self):
        """6M = 180일."""
        assert HORIZON_DAYS["6M"] == 180

    def test_maturity_calculation(self):
        """기준일 + horizon일 = 만기일."""
        forecast_date = date(2025, 1, 15)
        maturity = forecast_date + timedelta(days=HORIZON_DAYS["1M"])
        assert maturity == date(2025, 2, 14)


class TestHitRange:
    """적중 범위 판정 테스트."""

    def test_in_range(self):
        """actual_price가 [low, high] 범위 내 -> True."""
        low, high, actual = 100.0, 120.0, 110.0
        assert low <= actual <= high

    def test_outside_range_high(self):
        """actual_price > high -> False."""
        low, high, actual = 100.0, 120.0, 130.0
        assert not (low <= actual <= high)

    def test_outside_range_low(self):
        """actual_price < low -> False."""
        low, high, actual = 100.0, 120.0, 95.0
        assert not (low <= actual <= high)

    def test_boundary_hit(self):
        """경계값 포함."""
        assert 100.0 <= 100.0 <= 120.0
        assert 100.0 <= 120.0 <= 120.0


def _mock_forecast(
    ticker="AAPL", horizon="1M", scenario="BASE",
    price_low=100.0, price_high=120.0,
    actual_price=110.0, hit_range=True,
    report_id=1, probability=0.50,
):
    """평가 완료 예측 mock."""
    f = MagicMock()
    f.ticker = ticker
    f.horizon = horizon
    f.scenario = scenario
    f.price_low = price_low
    f.price_high = price_high
    f.actual_price = actual_price
    f.hit_range = hit_range
    f.report_id = report_id
    f.probability = probability
    return f


class TestAccuracyScoreCalculation:
    """정확도 점수 계산 테스트."""

    def test_perfect_score(self):
        """전부 적중 -> hit_rate=1.0."""
        forecasts = [
            _mock_forecast(hit_range=True, scenario="BASE"),
            _mock_forecast(hit_range=True, scenario="BASE", horizon="3M"),
        ]
        result = _score_single_ticker("AAPL", forecasts)
        assert result.hit_rate == 1.0

    def test_zero_score(self):
        """전부 미적중 -> hit_rate=0.0."""
        forecasts = [
            _mock_forecast(hit_range=False, scenario="BULL", actual_price=90.0),
            _mock_forecast(hit_range=False, scenario="BEAR", actual_price=130.0),
        ]
        result = _score_single_ticker("AAPL", forecasts)
        assert result.hit_rate == 0.0

    def test_weighted_formula(self):
        """hit_rate * 0.6 + direction_accuracy * 0.4 검증."""
        # 2/4 hit, direction depends on BASE midpoint
        forecasts = [
            _mock_forecast(hit_range=True, scenario="BASE", actual_price=110),
            _mock_forecast(hit_range=True, scenario="BASE", horizon="3M", actual_price=115),
            _mock_forecast(hit_range=False, scenario="BULL", actual_price=90, report_id=1, horizon="1M"),
            _mock_forecast(hit_range=False, scenario="BEAR", actual_price=130, report_id=1, horizon="3M"),
        ]
        result = _score_single_ticker("AAPL", forecasts)
        assert result.hit_rate == 0.5
        # overall = hit_rate * 0.6 + direction * 0.4
        assert result.overall_score == pytest.approx(
            result.hit_rate * 0.6 + result.direction_accuracy * 0.4,
        )

    def test_empty_forecasts(self):
        """평가 0건 -> overall_score = 0."""
        result = _score_single_ticker("AAPL", [])
        assert result.overall_score == 0.0
        assert result.total_evaluated == 0

    def test_by_horizon_grouping(self):
        """by_horizon 그룹별 집계."""
        forecasts = [
            _mock_forecast(horizon="1M", hit_range=True),
            _mock_forecast(horizon="1M", hit_range=False),
            _mock_forecast(horizon="3M", hit_range=True),
        ]
        result = _score_single_ticker("AAPL", forecasts)
        assert "1M" in result.by_horizon
        assert result.by_horizon["1M"]["count"] == 2


class TestComputeAccuracyScores:
    """compute_accuracy_scores 통합 테스트."""

    def test_multiple_tickers(self):
        """여러 종목 정확도 집계."""
        forecasts = [
            _mock_forecast(ticker="AAPL", hit_range=True),
            _mock_forecast(ticker="AAPL", hit_range=False),
            _mock_forecast(ticker="MSFT", hit_range=True),
        ]
        results = compute_accuracy_scores(forecasts)
        assert len(results) == 2
        aapl = next(r for r in results if r.ticker == "AAPL")
        assert aapl.total_evaluated == 2
        msft = next(r for r in results if r.ticker == "MSFT")
        assert msft.total_evaluated == 1
