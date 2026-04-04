"""AI 비용 추적기 테스트."""

from __future__ import annotations

from src.ai.cost_tracker import CostTracker, MODEL_PRICING


class TestCostTracker:
    def test_record_adds_entry(self):
        tracker = CostTracker()
        record = tracker.record("claude-sonnet-4-20250514", 1000, 500, "test")
        assert tracker.call_count == 1
        assert record.cost_usd > 0
        assert record.purpose == "test"

    def test_cost_calculation(self):
        tracker = CostTracker()
        record = tracker.record("claude-sonnet-4-20250514", 1_000_000, 0, "test_input")
        # 1M input tokens * $3/1M = $3.00
        assert abs(record.cost_usd - 3.0) < 0.01

    def test_daily_summary(self):
        tracker = CostTracker()
        tracker.record("claude-sonnet-4-20250514", 1000, 500, "debate_r1")
        tracker.record("claude-haiku-4-5-20251001", 2000, 300, "chat")
        summary = tracker.daily_summary()
        assert summary["call_count"] == 2
        assert summary["total_cost_usd"] > 0
        assert len(summary["by_model"]) == 2
        assert len(summary["by_purpose"]) == 2

    def test_check_budget_within(self):
        tracker = CostTracker()
        tracker.record("claude-sonnet-4-20250514", 100, 50, "test")
        assert tracker.check_budget(daily_limit=5.0) is True

    def test_check_budget_exceeded(self):
        tracker = CostTracker()
        # 10M output tokens * $15/1M = $150
        tracker.record("claude-sonnet-4-20250514", 0, 10_000_000, "test")
        assert tracker.check_budget(daily_limit=5.0) is False

    def test_total_cost_property(self):
        tracker = CostTracker()
        tracker.record("claude-sonnet-4-20250514", 1000, 500, "a")
        tracker.record("claude-sonnet-4-20250514", 2000, 300, "b")
        assert tracker.total_cost > 0

    def test_empty_tracker(self):
        tracker = CostTracker()
        assert tracker.call_count == 0
        assert tracker.total_cost == 0
        summary = tracker.daily_summary()
        assert summary["call_count"] == 0


class TestModelPricing:
    def test_sonnet_pricing_exists(self):
        assert "claude-sonnet-4-20250514" in MODEL_PRICING

    def test_haiku_pricing_cheaper(self):
        sonnet = MODEL_PRICING["claude-sonnet-4-20250514"]
        haiku = MODEL_PRICING["claude-haiku-4-5-20251001"]
        assert haiku["input"] < sonnet["input"]
        assert haiku["output"] < sonnet["output"]
