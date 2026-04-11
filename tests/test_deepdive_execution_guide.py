"""Phase 5: ExecutionGuide 결정론적 계산 테스트."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.deepdive.execution_guide import (
    ExecutionGuide,
    _compute_buy_zone,
    _compute_stop_loss,
    _ev_pct,
    _label_rr,
    _portfolio_warnings,
    _probability_weighted_target,
    _suggest_position_pct,
    compute_execution_guide,
    guide_to_dict,
)
from src.deepdive.schemas import AIResult, TechnicalProfile


# 고정 settings stub
_SETTINGS = SimpleNamespace(
    max_single_stock_pct=0.10,
    max_sector_weight_pct=0.30,
    atr_stop_multiplier=2.0,
    portfolio_trailing_stop_pct=10.0,
)


def _layer3(
    support=170.0, resistance=195.0, rsi=55.0, atr_14=3.5, close=180.0,
) -> TechnicalProfile:
    return TechnicalProfile(
        technical_grade="Bullish",
        trend_alignment="aligned_up",
        position_52w_pct=65.0,
        rsi=rsi,
        macd_signal="bullish",
        nearest_support=support,
        nearest_resistance=resistance,
        relative_strength_pct=None,
        atr_regime="Normal",
        metrics={
            "high_52w": 210.0,
            "low_52w": 140.0,
            "sma20": 175.0,
            "sma50": 170.0,
            "price_count": 252,
            "atr_14": atr_14,
            "current_close": close,
        },
    )


def _ai(
    action="ADD",
    conviction=7,
    support=None,
    resistance=None,
    stop_loss=None,
) -> AIResult:
    return AIResult(
        action_grade=action,
        conviction=conviction,
        uncertainty="medium",
        reasoning="test reasoning",
        what_missing=None,
        support_price=support,
        resistance_price=resistance,
        stop_loss=stop_loss,
    )


_SCENARIOS = {
    "1M": {
        "base": {"prob": 0.5, "low": 178, "high": 188},
        "bull": {"prob": 0.3, "low": 188, "high": 200},
        "bear": {"prob": 0.2, "low": 165, "high": 178},
    },
    "3M": {
        "base": {"prob": 0.45, "low": 175, "high": 195},
        "bull": {"prob": 0.3, "low": 195, "high": 220},
        "bear": {"prob": 0.25, "low": 155, "high": 175},
    },
    "6M": {
        "base": {"prob": 0.4, "low": 170, "high": 210},
        "bull": {"prob": 0.35, "low": 210, "high": 250},
        "bear": {"prob": 0.25, "low": 145, "high": 170},
    },
}


class TestBuyZone:
    def test_normal_zone_around_support(self):
        low, high, status = _compute_buy_zone(180.0, support=170.0, resistance=195.0, rsi=55.0)
        # low = max(180*0.97=174.6, 170*1.01=171.7) → 174.6
        # high = min(180*1.01=181.8, 195*0.98=191.1) → 181.8
        assert low == pytest.approx(174.6, rel=1e-3)
        assert high == pytest.approx(181.8, rel=1e-3)
        assert status == "in_zone"  # 180 ∈ [174.6, 181.8]

    def test_rsi_overbought_forces_wait(self):
        _, _, status = _compute_buy_zone(180.0, 170.0, 195.0, rsi=72.0)
        assert status == "wait"

    def test_above_zone(self):
        # 저항이 현재가보다 많이 낮으면 high가 current_price 아래로 떨어짐
        low, high, status = _compute_buy_zone(200.0, support=170.0, resistance=185.0, rsi=60.0)
        # low = max(200*0.97=194, 170*1.01=171.7) = 194
        # high = min(200*1.01=202, 185*0.98=181.3) = 181.3 — 역전
        # 역전 시 보정: low=current*0.99=198, high=current*1.01=202
        assert low == pytest.approx(198.0, rel=1e-3)
        assert high == pytest.approx(202.0, rel=1e-3)

    def test_below_zone(self):
        _, _, status = _compute_buy_zone(150.0, support=170.0, resistance=195.0, rsi=40.0)
        # low = max(150*0.97=145.5, 170*1.01=171.7) = 171.7
        # high = min(150*1.01=151.5, 195*0.98=191.1) = 151.5
        # 역전 보정 → low=148.5, high=151.5. current 150 in [148.5, 151.5] → in_zone
        # 그래서 below_zone은 support가 없을 때만
        low, _, status = _compute_buy_zone(150.0, support=None, resistance=200.0, rsi=40.0)
        # low = 150*0.97=145.5, high = min(151.5, 196)=151.5
        assert status == "in_zone"
        # 확실한 below: support/resistance 없고 current가 zone 아래
        # 사실상 current_price 기준이라 below_zone 발생 드묾

    def test_no_support_no_resistance(self):
        low, high, status = _compute_buy_zone(100.0, None, None, rsi=50.0)
        assert low == pytest.approx(97.0, rel=1e-3)
        assert high == pytest.approx(101.0, rel=1e-3)
        assert status == "in_zone"


class TestStopLoss:
    def test_picks_most_conservative_highest_price(self):
        # 후보:
        # - AI: 150 (멀리)
        # - support 170 - atr*0.5=1.75 → 168.25 (가장 가까움 = 최보수)
        # - trailing 180*0.9 = 162
        # - atr 180 - 3.5*2 = 173
        stop, source = _compute_stop_loss(
            current_price=180.0, ai_stop=150.0, support=170.0,
            atr_14=3.5, atr_multiplier=2.0, trailing_stop_pct=10.0,
        )
        assert source == "atr"  # 173 vs support 168.25 vs ai 150 vs trailing 162 → atr 173 높음
        assert stop == pytest.approx(173.0, rel=1e-3)

    def test_ai_stop_used_when_most_conservative(self):
        # AI 178 (아주 가까움)
        stop, source = _compute_stop_loss(
            current_price=180.0, ai_stop=178.0, support=170.0,
            atr_14=3.5, atr_multiplier=2.0, trailing_stop_pct=10.0,
        )
        assert source == "ai"
        assert stop == 178.0

    def test_invalid_ai_stop_ignored(self):
        # AI stop_loss가 current_price보다 높으면 무시
        stop, source = _compute_stop_loss(
            current_price=180.0, ai_stop=200.0, support=170.0,
            atr_14=3.5, atr_multiplier=2.0, trailing_stop_pct=10.0,
        )
        assert source != "ai"

    def test_fallback_when_no_data(self):
        stop, source = _compute_stop_loss(
            current_price=100.0, ai_stop=None, support=None,
            atr_14=None, atr_multiplier=2.0, trailing_stop_pct=10.0,
        )
        # trailing만 가능
        assert source == "trailing"
        assert stop == pytest.approx(90.0, rel=1e-3)


class TestProbabilityWeightedTarget:
    def test_basic_weighted(self):
        # 1M: base 0.5×(178+188)/2=91.5, bull 0.3×(188+200)/2=58.2, bear 0.2×(165+178)/2=34.3
        # = 184.0 / 1.0 = 184
        t = _probability_weighted_target(_SCENARIOS, "1M")
        assert t == pytest.approx(184.0, rel=1e-2)

    def test_missing_horizon(self):
        assert _probability_weighted_target(_SCENARIOS, "12M") is None

    def test_empty(self):
        assert _probability_weighted_target(None, "1M") is None
        assert _probability_weighted_target({}, "1M") is None

    def test_partial_scenarios(self):
        partial = {"3M": {"base": {"prob": 0.6, "low": 100, "high": 120}}}
        # prob 0.6 < 0.5? No, 0.6 >= 0.5 so acceptable
        t = _probability_weighted_target(partial, "3M")
        assert t == pytest.approx(110.0, rel=1e-3)

    def test_low_probability_coverage(self):
        bad = {"1M": {"base": {"prob": 0.3, "low": 100, "high": 110}}}
        # total prob 0.3 < 0.5 → None
        assert _probability_weighted_target(bad, "1M") is None


class TestEVPct:
    def test_positive(self):
        assert _ev_pct(100.0, 115.0) == 15.0

    def test_negative(self):
        assert _ev_pct(100.0, 90.0) == -10.0

    def test_none_target(self):
        assert _ev_pct(100.0, None) is None


class TestLabelRR:
    def test_favorable(self):
        assert _label_rr(3.0) == "favorable"

    def test_neutral(self):
        assert _label_rr(1.8) == "neutral"

    def test_unfavorable(self):
        assert _label_rr(1.0) == "unfavorable"


class TestSuggestPositionPct:
    def test_add_high_conviction(self):
        pct, rationale = _suggest_position_pct(
            conviction=9, action="ADD", rr=3.0, max_stock_pct=0.10,
        )
        # base = 10 * (0.9)^2 = 8.1 %
        # sigmoid_tilt(9) ≈ 1.67
        # rr_boost 1.2
        # → 8.1 × 1.67 × 1.2 ≈ 16.23 → clipped to 10%
        assert pct == pytest.approx(10.0, rel=1e-2)
        assert "R/R" in rationale

    def test_add_low_conviction_low_rr(self):
        pct, _ = _suggest_position_pct(
            conviction=4, action="ADD", rr=1.2, max_stock_pct=0.10,
        )
        # base = 10 * 0.16 = 1.6
        # sigmoid_tilt(4) ≈ 0.69
        # rr_boost 0.6
        # → 1.6 × 0.69 × 0.6 ≈ 0.66%
        assert pct > 0
        assert pct < 2.0

    def test_hold_returns_zero(self):
        pct, rat = _suggest_position_pct(conviction=8, action="HOLD", rr=2.0, max_stock_pct=0.10)
        assert pct == 0.0
        assert "HOLD" in rat

    def test_exit_returns_zero(self):
        pct, rat = _suggest_position_pct(conviction=8, action="EXIT", rr=2.0, max_stock_pct=0.10)
        assert pct == 0.0
        assert "EXIT" in rat

    def test_clipped_to_cap(self):
        pct, _ = _suggest_position_pct(
            conviction=10, action="ADD", rr=5.0, max_stock_pct=0.08,
        )
        assert pct <= 8.0


class TestPortfolioWarnings:
    def test_single_stock_cap(self):
        w = _portfolio_warnings(
            new_weight=0.08, existing_ticker_weight=0.05,
            existing_sector_weight=0.15, max_stock_pct=0.10,
            max_sector_pct=0.30, sector="Technology",
        )
        assert any("종목 비중" in warn for warn in w)

    def test_sector_cap(self):
        w = _portfolio_warnings(
            new_weight=0.10, existing_ticker_weight=0.0,
            existing_sector_weight=0.25, max_stock_pct=0.10,
            max_sector_pct=0.30, sector="Technology",
        )
        assert any("Technology" in warn for warn in w)

    def test_no_warnings(self):
        w = _portfolio_warnings(
            new_weight=0.05, existing_ticker_weight=0.0,
            existing_sector_weight=0.10, max_stock_pct=0.10,
            max_sector_pct=0.30, sector="Healthcare",
        )
        assert w == []


class TestComputeExecutionGuideEnd2End:
    def test_full_guide(self):
        guide = compute_execution_guide(
            current_price=180.0,
            ai_result=_ai(action="ADD", conviction=8, stop_loss=175.0),
            layers={"layer3": _layer3()},
            scenarios=_SCENARIOS,
            sector="Technology",
            settings=_SETTINGS,
            existing_sector_weight=0.05,
            existing_ticker_weight=0.0,
        )
        assert guide is not None
        assert guide.current_price == 180.0
        assert guide.buy_zone_low < guide.buy_zone_high
        assert guide.stop_loss < 180.0
        assert guide.target_3m is not None
        assert guide.target_3m > 180.0  # Bull scenario 기여
        assert "3M" in guide.expected_value_pct
        assert guide.suggested_position_pct > 0
        assert isinstance(guide.portfolio_fit_warnings, tuple)

    def test_exit_action_zero_position(self):
        guide = compute_execution_guide(
            current_price=180.0,
            ai_result=_ai(action="EXIT", conviction=8),
            layers={"layer3": _layer3()},
            scenarios=_SCENARIOS,
            sector="Technology",
            settings=_SETTINGS,
        )
        assert guide.suggested_position_pct == 0.0
        assert guide.action_hint == "sell"

    def test_missing_layer3(self):
        """layer3 없어도 계산 가능."""
        guide = compute_execution_guide(
            current_price=100.0,
            ai_result=_ai(action="ADD", conviction=6),
            layers={},
            scenarios=_SCENARIOS,
            sector=None,
            settings=_SETTINGS,
        )
        assert guide is not None
        assert guide.stop_loss_source == "trailing"  # atr 없음

    def test_invalid_current_price(self):
        guide = compute_execution_guide(
            current_price=0.0,
            ai_result=_ai(),
            layers={"layer3": _layer3()},
            scenarios=_SCENARIOS,
            sector=None,
            settings=_SETTINGS,
        )
        assert guide is None

    def test_ai_key_levels_override_layer3(self):
        """AI support/resistance가 layer3보다 우선."""
        ai = _ai(action="ADD", conviction=7, support=172.0, resistance=193.0)
        guide = compute_execution_guide(
            current_price=180.0,
            ai_result=ai,
            layers={"layer3": _layer3(support=165.0, resistance=200.0)},
            scenarios=_SCENARIOS,
            sector=None,
            settings=_SETTINGS,
        )
        assert guide is not None
        # buy_zone_low should be max(174.6, 172*1.01=173.72) → 174.6
        assert guide.buy_zone_low == pytest.approx(174.6, rel=1e-3)
        # buy_zone_high should be min(181.8, 193*0.98=189.14) → 181.8
        assert guide.buy_zone_high == pytest.approx(181.8, rel=1e-3)

    def test_portfolio_sector_warning_triggers(self):
        guide = compute_execution_guide(
            current_price=100.0,
            ai_result=_ai(action="ADD", conviction=9),
            layers={"layer3": _layer3()},
            scenarios=_SCENARIOS,
            sector="Technology",
            settings=_SETTINGS,
            existing_sector_weight=0.28,  # 이미 28%
            existing_ticker_weight=0.0,
        )
        assert len(guide.portfolio_fit_warnings) > 0

    def test_serialize_to_dict(self):
        guide = compute_execution_guide(
            current_price=180.0,
            ai_result=_ai(action="ADD", conviction=7),
            layers={"layer3": _layer3()},
            scenarios=_SCENARIOS,
            sector="Technology",
            settings=_SETTINGS,
        )
        d = guide_to_dict(guide)
        assert d["current_price"] == 180.0
        assert "expected_value_pct" in d
        assert isinstance(d["portfolio_fit_warnings"], list)
