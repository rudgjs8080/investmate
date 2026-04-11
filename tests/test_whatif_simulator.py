"""Phase 12c: What-if 시뮬레이터 순수 함수 테스트.

핵심 불변식: 입력 sequence는 절대 수정되지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.deepdive.rebalance_advisor import Holding
from src.deepdive.whatif_simulator import (
    Modification,
    StockInfo,
    simulate_holdings_change,
)


@dataclass
class _FakeGuide:
    suggested_position_pct: float
    expected_value_pct: dict
    risk_reward_ratio: float | None = None
    portfolio_fit_warnings: tuple = ()


def _sample_holdings() -> list[Holding]:
    return [
        Holding(
            ticker="AAPL", shares=100.0, avg_cost=150.0,
            current_price=180.0, sector="Technology",
        ),
        Holding(
            ticker="MSFT", shares=50.0, avg_cost=300.0,
            current_price=350.0, sector="Technology",
        ),
        Holding(
            ticker="JNJ", shares=30.0, avg_cost=160.0,
            current_price=155.0, sector="Healthcare",
        ),
    ]


def _sample_guides() -> dict:
    return {
        "AAPL": _FakeGuide(
            suggested_position_pct=8.0,
            expected_value_pct={"3M": 12.0},
            risk_reward_ratio=2.5,
        ),
        "MSFT": _FakeGuide(
            suggested_position_pct=6.0,
            expected_value_pct={"3M": 5.0},
            risk_reward_ratio=1.5,
        ),
        "JNJ": _FakeGuide(
            suggested_position_pct=5.0,
            expected_value_pct={"3M": 3.0},
            risk_reward_ratio=1.2,
        ),
        "NVDA": _FakeGuide(
            suggested_position_pct=7.0,
            expected_value_pct={"3M": 10.0},
            risk_reward_ratio=2.0,
        ),
    }


class TestModificationValidation:
    def test_requires_exactly_one_of_shares_or_delta(self):
        with pytest.raises(ValueError):
            Modification(ticker="AAPL")
        with pytest.raises(ValueError):
            Modification(ticker="AAPL", shares=100, shares_delta=10)

    def test_rejects_negative_shares(self):
        with pytest.raises(ValueError):
            Modification(ticker="AAPL", shares=-5)

    def test_accepts_absolute_shares(self):
        m = Modification(ticker="AAPL", shares=100)
        assert m.shares == 100
        assert m.shares_delta is None

    def test_accepts_delta(self):
        m = Modification(ticker="AAPL", shares_delta=-50)
        assert m.shares is None
        assert m.shares_delta == -50


class TestSimulationBasics:
    def test_empty_modifications_returns_same_plan(self):
        current = _sample_holdings()
        result = simulate_holdings_change(
            current=current,
            modifications=[],
            guides=_sample_guides(),
        )
        # before/after 동일해야 함
        assert result.before_total_value == result.after_total_value

    def test_does_not_mutate_input(self):
        current = _sample_holdings()
        original_snapshot = [
            (h.ticker, h.shares, h.current_price, h.sector) for h in current
        ]
        simulate_holdings_change(
            current=current,
            modifications=[Modification(ticker="AAPL", shares_delta=100)],
            guides=_sample_guides(),
        )
        # 원본은 변경되지 않아야 함 (불변성 — 30년 트레이더의 핵심 원칙)
        after_snapshot = [
            (h.ticker, h.shares, h.current_price, h.sector) for h in current
        ]
        assert original_snapshot == after_snapshot


class TestSharesDelta:
    def test_delta_increases_shares(self):
        result = simulate_holdings_change(
            current=_sample_holdings(),
            modifications=[Modification(ticker="AAPL", shares_delta=50)],
            guides=_sample_guides(),
        )
        assert result.after_total_value > result.before_total_value
        # AAPL 이 modified_tickers 에 포함
        assert "AAPL" in result.modified_tickers

    def test_negative_delta_decreases_shares(self):
        result = simulate_holdings_change(
            current=_sample_holdings(),
            modifications=[Modification(ticker="AAPL", shares_delta=-30)],
            guides=_sample_guides(),
        )
        assert result.after_total_value < result.before_total_value

    def test_delta_exceeding_holding_violates(self):
        result = simulate_holdings_change(
            current=_sample_holdings(),
            modifications=[Modification(ticker="AAPL", shares_delta=-200)],
            guides=_sample_guides(),
        )
        assert any("초과" in v for v in result.violations)


class TestAbsoluteShares:
    def test_zero_shares_removes_holding(self):
        result = simulate_holdings_change(
            current=_sample_holdings(),
            modifications=[Modification(ticker="MSFT", shares=0)],
            guides=_sample_guides(),
        )
        # after에 MSFT가 사라짐 → total_value 감소
        assert result.after_total_value < result.before_total_value
        assert "MSFT" in result.modified_tickers
        # after sector 에 Technology 비중 감소 확인
        before_tech = dict(result.before_sector_weights).get("Technology", 0)
        after_tech = dict(result.after_sector_weights).get("Technology", 0)
        assert after_tech < before_tech

    def test_absolute_shares_overwrites(self):
        result = simulate_holdings_change(
            current=_sample_holdings(),
            modifications=[Modification(ticker="AAPL", shares=500)],
            guides=_sample_guides(),
        )
        # AAPL 500주 × $180 = $90k, 포트폴리오 총액 훨씬 증가
        assert result.after_total_value > result.before_total_value * 1.5


class TestNewTickerFromUniverse:
    def test_adds_new_holding_using_universe(self):
        universe = {
            "NVDA": StockInfo(current_price=900.0, sector="Technology"),
        }
        result = simulate_holdings_change(
            current=_sample_holdings(),
            modifications=[Modification(ticker="NVDA", shares=10)],
            guides=_sample_guides(),
            universe=universe,
        )
        assert result.after_total_value > result.before_total_value
        assert "NVDA" in result.modified_tickers
        # after_sector_weights 에 Technology 비중 증가
        before_tech = dict(result.before_sector_weights).get("Technology", 0)
        after_tech = dict(result.after_sector_weights).get("Technology", 0)
        assert after_tech > before_tech

    def test_missing_universe_entry_violates(self):
        result = simulate_holdings_change(
            current=_sample_holdings(),
            modifications=[Modification(ticker="GOOGL", shares=10)],
            guides=_sample_guides(),
            universe=None,  # GOOGL 정보 없음
        )
        assert any("GOOGL" in v for v in result.violations)


class TestSectorCapViolation:
    def test_sector_over_cap_reports_violation(self):
        """Technology 섹터에 과대 집중시켜 violation 발생 확인."""
        universe = {
            "NVDA": StockInfo(current_price=900.0, sector="Technology"),
        }
        result = simulate_holdings_change(
            current=_sample_holdings(),
            modifications=[
                Modification(ticker="NVDA", shares=100),  # $90k Technology
                Modification(ticker="JNJ", shares=0),     # Healthcare 제거
            ],
            guides=_sample_guides(),
            universe=universe,
            max_sector_weight=0.30,
        )
        # Technology 비중이 30%를 넘어야 함
        after_tech = dict(result.after_sector_weights).get("Technology", 0)
        assert after_tech > 0.30
        # violation 있어야 함
        assert any("Technology" in v for v in result.violations)


class TestSectorDistribution:
    def test_sector_weights_sum_to_one(self):
        result = simulate_holdings_change(
            current=_sample_holdings(),
            modifications=[],
            guides=_sample_guides(),
        )
        total = sum(w for _, w in result.before_sector_weights)
        assert abs(total - 1.0) < 1e-6

    def test_single_sector_100pct(self):
        single = [
            Holding(
                ticker="AAPL", shares=100, avg_cost=150,
                current_price=180, sector="Technology",
            ),
        ]
        result = simulate_holdings_change(
            current=single,
            modifications=[],
            guides=_sample_guides(),
        )
        sectors = dict(result.before_sector_weights)
        assert sectors["Technology"] == pytest.approx(1.0)


class TestEmptyHoldings:
    def test_empty_current_with_new_ticker(self):
        universe = {
            "AAPL": StockInfo(current_price=180.0, sector="Technology"),
        }
        result = simulate_holdings_change(
            current=[],
            modifications=[Modification(ticker="AAPL", shares=100)],
            guides=_sample_guides(),
            universe=universe,
        )
        assert result.before_total_value == 0
        assert result.after_total_value == pytest.approx(100 * 180.0)
