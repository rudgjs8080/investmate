"""변경 감지 로직 테스트."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.deepdive.diff_detector import (
    _detect_action_change,
    _detect_new_risks,
    _detect_probability_shifts,
    detect_changes,
)
from src.deepdive.schemas import AIResult


@pytest.fixture
def current_ai():
    return AIResult(
        action_grade="ADD", conviction=8, uncertainty="low",
        reasoning="Strong buy", what_missing=None,
    )


@pytest.fixture
def prev_report_mock():
    """이전 리포트 mock (HOLD, conviction=5)."""
    report = MagicMock()
    report.action_grade = "HOLD"
    report.conviction = 5
    report.report_json = json.dumps({
        "ai_result": {"action_grade": "HOLD", "conviction": 5},
        "layers": {"layer5": {"risk_events": ["earnings miss"]}},
    })
    return report


class TestDetectActionChange:
    """액션 등급 변경 감지."""

    def test_grade_changed(self):
        """HOLD -> ADD -> severity=critical."""
        changes = _detect_action_change("ADD", "HOLD", 8, 5)
        assert any(c.change_type == "action_changed" and c.severity == "critical" for c in changes)

    def test_grade_same(self):
        """동일 등급 -> 변경 없음."""
        changes = _detect_action_change("HOLD", "HOLD", 5, 5)
        assert not any(c.change_type == "action_changed" for c in changes)

    def test_conviction_shift(self):
        """|7 - 4| = 3 >= 2 -> severity=warning."""
        changes = _detect_action_change("HOLD", "HOLD", 7, 4)
        assert any(c.change_type == "conviction_shift" and c.severity == "warning" for c in changes)

    def test_conviction_small_change(self):
        """|5 - 4| = 1 < 2 -> 변경 없음."""
        changes = _detect_action_change("HOLD", "HOLD", 5, 4)
        assert not any(c.change_type == "conviction_shift" for c in changes)


class TestDetectProbabilityShifts:
    """시나리오 확률 변화 감지."""

    def test_large_shift(self):
        """50% -> 35% = 15pp >= 10 -> info."""
        prev_forecasts = [MagicMock(horizon="1M", scenario="BASE", probability=0.50)]
        current = [{"horizon": "1M", "scenario": "BASE", "probability": 0.35}]
        changes = _detect_probability_shifts(current, prev_forecasts)
        assert len(changes) == 1
        assert changes[0].severity == "info"

    def test_small_shift(self):
        """50% -> 45% = 5pp < 10 -> 변경 없음."""
        prev_forecasts = [MagicMock(horizon="1M", scenario="BASE", probability=0.50)]
        current = [{"horizon": "1M", "scenario": "BASE", "probability": 0.45}]
        changes = _detect_probability_shifts(current, prev_forecasts)
        assert len(changes) == 0


class TestDetectNewRisks:
    """신규 리스크 이벤트 감지."""

    def test_new_risk(self):
        """이전에 없던 리스크 -> warning."""
        changes = _detect_new_risks(
            ["earnings miss", "SEC investigation"],
            ["earnings miss"],
        )
        assert len(changes) == 1
        assert changes[0].change_type == "new_risk"
        assert changes[0].severity == "warning"

    def test_no_new_risk(self):
        """동일 리스크 -> 변경 없음."""
        changes = _detect_new_risks(["earnings miss"], ["earnings miss"])
        assert len(changes) == 0


class TestDetectChangesIntegration:
    """detect_changes 통합 테스트."""

    def test_no_previous_report(self, current_ai):
        """이전 리포트 없음 -> 빈 리스트."""
        changes = detect_changes(
            current_ai, {}, None, None, None,
        )
        assert changes == []

    def test_with_previous_report(self, current_ai, prev_report_mock):
        """이전 리포트 대비 변경 감지."""
        changes = detect_changes(
            current_ai, {"layer5": MagicMock(risk_events=["earnings miss", "tariff risk"])},
            None, prev_report_mock, None,
        )
        # action_changed (HOLD->ADD) + conviction_shift (5->8, |3|>=2) + new_risk (tariff risk)
        types = [c.change_type for c in changes]
        assert "action_changed" in types
        assert "conviction_shift" in types
        assert "new_risk" in types

    def test_no_changes(self):
        """동일 결과 -> 빈 리스트."""
        ai = AIResult(
            action_grade="HOLD", conviction=5, uncertainty="medium",
            reasoning="No change", what_missing=None,
        )
        report = MagicMock()
        report.report_json = json.dumps({
            "ai_result": {"action_grade": "HOLD", "conviction": 5},
            "layers": {"layer5": {"risk_events": []}},
        })
        changes = detect_changes(ai, {"layer5": MagicMock(risk_events=[])}, None, report, None)
        assert changes == []
