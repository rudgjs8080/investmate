"""Phase 8: Alert 엔진 테스트."""

from __future__ import annotations

import pytest

from src.deepdive.alert_engine import (
    AlertTrigger,
    evaluate_alerts,
    evaluate_alerts_batch,
    format_alerts_summary,
)


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
