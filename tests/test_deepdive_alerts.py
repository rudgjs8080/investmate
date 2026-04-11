"""Phase 8 + Phase 11a: Alert 엔진 테스트."""

from __future__ import annotations

import pytest

from src.deepdive.alert_engine import (
    AlertTrigger,
    build_layer_snapshot,
    evaluate_alerts,
    evaluate_alerts_batch,
    format_alerts_summary,
)
from src.deepdive.invalidation_parser import LayerSnapshot


_GUIDE = {
    "buy_zone_low": 175.0,
    "buy_zone_high": 182.0,
    "stop_loss": 165.0,
    "target_1m": 190.0,
    "target_3m": 200.0,
    "target_6m": 215.0,
}


class TestBuyZoneEntered:
    def test_fresh_entry(self):
        """이전가 존 밖 → 현재가 존 안 = trigger."""
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=180.0, previous_price=170.0,
            execution_guide=_GUIDE,
        )
        assert any(t.trigger_type == "buy_zone_entered" for t in triggers)

    def test_already_in_zone_no_trigger(self):
        """이전가도 존 안 → 트리거 없음."""
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=180.0, previous_price=179.0,
            execution_guide=_GUIDE,
        )
        assert not any(t.trigger_type == "buy_zone_entered" for t in triggers)

    def test_exit_zone_no_trigger(self):
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=185.0, previous_price=180.0,
            execution_guide=_GUIDE,
        )
        assert not any(t.trigger_type == "buy_zone_entered" for t in triggers)


class TestStopProximity:
    def test_near_stop_warning(self):
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=166.5, previous_price=170.0,
            execution_guide=_GUIDE,
        )
        stop_triggers = [t for t in triggers if t.trigger_type == "stop_proximity"]
        assert len(stop_triggers) == 1
        assert stop_triggers[0].severity == "warning"

    def test_below_stop_critical(self):
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=163.5, previous_price=170.0,
            execution_guide=_GUIDE,
        )
        stop_triggers = [t for t in triggers if t.trigger_type == "stop_proximity"]
        assert len(stop_triggers) == 1
        assert stop_triggers[0].severity == "critical"

    def test_far_from_stop_no_trigger(self):
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=180.0, previous_price=179.0,
            execution_guide=_GUIDE,
        )
        assert not any(t.trigger_type == "stop_proximity" for t in triggers)


class TestTargetHit:
    def test_target_1m_hit(self):
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=190.5, previous_price=186.0,
            execution_guide=_GUIDE,
        )
        hit_triggers = [t for t in triggers if "target" in t.trigger_type]
        assert any(t.trigger_type == "target_1m_hit" for t in hit_triggers)

    def test_already_above_target_no_trigger(self):
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=192.0, previous_price=191.0,
            execution_guide=_GUIDE,
        )
        assert not any(t.trigger_type == "target_1m_hit" for t in triggers)

    def test_multiple_targets_hit(self):
        """200 돌파 → 1M, 3M 동시 히트."""
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=200.5, previous_price=185.0,
            execution_guide=_GUIDE,
        )
        hit_types = {t.trigger_type for t in triggers if "target" in t.trigger_type}
        assert "target_1m_hit" in hit_types
        assert "target_3m_hit" in hit_types


class TestGuardrails:
    def test_no_guide_returns_empty(self):
        assert evaluate_alerts(
            ticker="AAPL", current_price=100.0, previous_price=95.0,
            execution_guide=None,
        ) == []

    def test_zero_price_returns_empty(self):
        assert evaluate_alerts(
            ticker="AAPL", current_price=0.0, previous_price=None,
            execution_guide=_GUIDE,
        ) == []


class TestBatch:
    def test_batch_collects_all(self):
        entries = [
            {
                "ticker": "AAPL", "current_price": 180.0, "previous_price": 170.0,
                "execution_guide": _GUIDE,
            },
            {
                "ticker": "MSFT", "current_price": 100.0, "previous_price": 120.0,
                "execution_guide": {
                    "buy_zone_low": 115.0, "buy_zone_high": 125.0,
                    "stop_loss": 101.0, "target_1m": 130.0,
                },
            },
        ]
        results = evaluate_alerts_batch(entries)
        # AAPL buy_zone_entered + MSFT stop_proximity
        tickers_triggered = {t.ticker for t in results}
        assert "AAPL" in tickers_triggered
        assert "MSFT" in tickers_triggered

    def test_batch_swallows_exceptions(self):
        """한 종목 실패해도 전체는 계속."""
        entries = [
            {"ticker": "BAD", "current_price": None, "execution_guide": _GUIDE},
            {
                "ticker": "AAPL", "current_price": 180.0, "previous_price": 170.0,
                "execution_guide": _GUIDE,
            },
        ]
        results = evaluate_alerts_batch(entries)
        assert any(t.ticker == "AAPL" for t in results)


class TestFormatSummary:
    def test_empty(self):
        assert format_alerts_summary([]) == ""

    def test_sorted_by_severity(self):
        triggers = [
            AlertTrigger(ticker="A", trigger_type="target_1m_hit", severity="info",
                        message="A info", current_price=100),
            AlertTrigger(ticker="B", trigger_type="stop_proximity", severity="critical",
                        message="B critical", current_price=50),
            AlertTrigger(ticker="C", trigger_type="stop_proximity", severity="warning",
                        message="C warning", current_price=75),
        ]
        summary = format_alerts_summary(triggers)
        # Critical should appear first
        critical_idx = summary.find("B critical")
        warning_idx = summary.find("C warning")
        info_idx = summary.find("A info")
        assert critical_idx < warning_idx < info_idx

    def test_invalidation_hit_fires_on_rsi(self):
        snap = LayerSnapshot(
            rsi=38.0, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=None,
            high_52w=None, low_52w=None, f_score=None,
            sector_per_premium_pct=None, close=180.0,
        )
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=180.0, previous_price=179.0,
            execution_guide=_GUIDE,
            invalidation_conditions=["RSI 40 하회"],
            layer_snapshot=snap,
        )
        inv = [t for t in triggers if t.trigger_type == "invalidation_hit"]
        assert len(inv) == 1
        assert inv[0].severity == "critical"
        assert "RSI 40 하회" in inv[0].message
        assert "RSI=38.0" in inv[0].message

    def test_invalidation_hit_no_fire_when_safe(self):
        snap = LayerSnapshot(
            rsi=55.0, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=None,
            high_52w=None, low_52w=None, f_score=None,
            sector_per_premium_pct=None, close=180.0,
        )
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=180.0, previous_price=179.0,
            execution_guide=_GUIDE,
            invalidation_conditions=["RSI 40 하회"],
            layer_snapshot=snap,
        )
        assert not any(t.trigger_type == "invalidation_hit" for t in triggers)

    def test_invalidation_sma_below_close(self):
        snap = LayerSnapshot(
            rsi=None, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=155.0,
            high_52w=None, low_52w=None, f_score=None,
            sector_per_premium_pct=None, close=150.0,
        )
        triggers = evaluate_alerts(
            ticker="TSLA", current_price=150.0, previous_price=156.0,
            execution_guide={"buy_zone_low": 140.0, "buy_zone_high": 148.0,
                             "stop_loss": 135.0},
            invalidation_conditions=["200일 이평선 이탈"],
            layer_snapshot=snap,
        )
        inv = [t for t in triggers if t.trigger_type == "invalidation_hit"]
        assert len(inv) == 1

    def test_review_trigger_hit_is_warning(self):
        snap = LayerSnapshot(
            rsi=72.0, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=None,
            high_52w=None, low_52w=None, f_score=None,
            sector_per_premium_pct=None, close=200.0,
        )
        triggers = evaluate_alerts(
            ticker="NVDA", current_price=200.0, previous_price=199.0,
            execution_guide=_GUIDE,
            next_review_trigger="RSI 70 상회",
            layer_snapshot=snap,
        )
        rev = [t for t in triggers if t.trigger_type == "review_trigger_hit"]
        assert len(rev) == 1
        assert rev[0].severity == "warning"

    def test_dedup_same_day_single_fire(self):
        """같은 키는 dedup_keys가 공유될 때 한 번만 발화."""
        snap = LayerSnapshot(
            rsi=38.0, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=None,
            high_52w=None, low_52w=None, f_score=None,
            sector_per_premium_pct=None, close=180.0,
        )
        dedup: set[str] = set()
        t1 = evaluate_alerts(
            ticker="AAPL", current_price=180.0, previous_price=179.0,
            execution_guide=_GUIDE,
            invalidation_conditions=["RSI 40 하회"],
            layer_snapshot=snap,
            dedup_keys=dedup,
        )
        t2 = evaluate_alerts(
            ticker="AAPL", current_price=180.0, previous_price=179.0,
            execution_guide=_GUIDE,
            invalidation_conditions=["RSI 40 하회"],
            layer_snapshot=snap,
            dedup_keys=dedup,
        )
        inv1 = [t for t in t1 if t.trigger_type == "invalidation_hit"]
        inv2 = [t for t in t2 if t.trigger_type == "invalidation_hit"]
        assert len(inv1) == 1
        assert len(inv2) == 0

    def test_invalidation_without_snapshot_is_skipped(self):
        triggers = evaluate_alerts(
            ticker="AAPL", current_price=180.0, previous_price=179.0,
            execution_guide=_GUIDE,
            invalidation_conditions=["RSI 40 하회"],
            layer_snapshot=None,
        )
        assert not any(t.trigger_type == "invalidation_hit" for t in triggers)


class TestBuildLayerSnapshot:
    def test_builds_from_layer_dto(self):
        from src.deepdive.schemas import FundamentalHealth, TechnicalProfile

        layer1 = FundamentalHealth(
            health_grade="A", f_score=8, z_score=None,
            margin_trend="stable", gross_margin=None, operating_margin=None,
            net_margin=None, roe=None, debt_ratio=None,
            earnings_beat_streak=0, metrics={},
        )
        layer3 = TechnicalProfile(
            technical_grade="Bullish", trend_alignment="aligned_up",
            position_52w_pct=80.0, rsi=65.0, macd_signal="bullish",
            nearest_support=170.0, nearest_resistance=200.0,
            relative_strength_pct=None, atr_regime="Normal",
            metrics={"high_52w": 210.0, "low_52w": 150.0},
        )
        snap = build_layer_snapshot(
            {"layer1": layer1, "layer3": layer3},
            current_price=180.0,
        )
        assert snap.rsi == 65.0
        assert snap.high_52w == 210.0
        assert snap.low_52w == 150.0
        assert snap.f_score == 8
        assert snap.close == 180.0

    def test_builds_with_close_history_computes_sma(self):
        closes = [float(100 + i) for i in range(60)]  # 100, 101, ..., 159
        snap = build_layer_snapshot(
            {}, current_price=159.0, close_history=closes,
        )
        assert snap.sma_20 is not None
        assert snap.sma_50 is not None
        assert snap.sma_200 is None  # 60 < 200
        # SMA20 of last 20 values = mean(140..159) = 149.5
        assert abs(snap.sma_20 - 149.5) < 0.01

    def test_close_history_none_leaves_sma_none(self):
        snap = build_layer_snapshot({}, current_price=100.0)
        assert snap.sma_20 is None
        assert snap.sma_50 is None
        assert snap.macd_hist is None


    def test_truncates_beyond_20(self):
        triggers = [
            AlertTrigger(
                ticker=f"T{i}", trigger_type="target_1m_hit", severity="info",
                message=f"T{i} hit", current_price=100,
            )
            for i in range(25)
        ]
        summary = format_alerts_summary(triggers)
        assert "외 5건" in summary
