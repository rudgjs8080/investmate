"""토론 컨센서스 패널티 + 에이전트 제약 컨텍스트 테스트 (Phase 2)."""

from __future__ import annotations

from src.ai.agents import (
    _build_constraint_context,
    get_bear_persona,
    get_bull_persona,
)
from src.ai.debate import apply_consensus_penalty
from src.ai.feedback import ConstraintRules


class TestApplyConsensusPenalty:
    """컨센서스 기반 신뢰도 보정 테스트."""

    def test_low_consensus_applies_penalty(self):
        """합의가 낮으면 신뢰도를 차감한다."""
        parsed = [
            {"ticker": "AAPL", "ai_approved": True, "ai_confidence": 8},
            {"ticker": "MSFT", "ai_approved": True, "ai_confidence": 5},
        ]
        result = apply_consensus_penalty(parsed, "low", penalty=1)
        assert result[0]["ai_confidence"] == 7
        assert result[1]["ai_confidence"] == 4

    def test_high_consensus_no_penalty(self):
        """합의가 높으면 변경 없다."""
        parsed = [
            {"ticker": "AAPL", "ai_approved": True, "ai_confidence": 8},
        ]
        result = apply_consensus_penalty(parsed, "high", penalty=1)
        assert result[0]["ai_confidence"] == 8

    def test_medium_consensus_no_penalty(self):
        """합의가 중간이면 변경 없다."""
        parsed = [
            {"ticker": "AAPL", "ai_approved": True, "ai_confidence": 6},
        ]
        result = apply_consensus_penalty(parsed, "medium", penalty=1)
        assert result[0]["ai_confidence"] == 6

    def test_confidence_minimum_clamp(self):
        """신뢰도는 최소 1로 클램프된다."""
        parsed = [
            {"ticker": "AAPL", "ai_approved": True, "ai_confidence": 1},
        ]
        result = apply_consensus_penalty(parsed, "low", penalty=2)
        assert result[0]["ai_confidence"] == 1

    def test_zero_penalty_no_change(self):
        """패널티 0이면 변경 없다."""
        parsed = [
            {"ticker": "AAPL", "ai_approved": True, "ai_confidence": 5},
        ]
        result = apply_consensus_penalty(parsed, "low", penalty=0)
        assert result[0]["ai_confidence"] == 5

    def test_none_confidence_skipped(self):
        """신뢰도가 None이면 건너뛴다."""
        parsed = [
            {"ticker": "AAPL", "ai_approved": True, "ai_confidence": None},
        ]
        result = apply_consensus_penalty(parsed, "low", penalty=1)
        assert result[0]["ai_confidence"] is None


class TestBuildConstraintContext:
    """에이전트 제약 컨텍스트 빌더 테스트."""

    def test_none_constraints_empty(self):
        """제약 없으면 빈 문자열."""
        assert _build_constraint_context(None) == ""

    def test_includes_blocked_sectors(self):
        """차단 섹터가 포함된다."""
        constraints = ConstraintRules(
            confidence_ceiling=7,
            max_recommendations=5,
            blocked_sectors=("Energy", "Utilities"),
            strong_sectors=(),
            feedback_commands=(),
            calibration_table={},
            confidence_penalty=0,
            default_action="neutral",
        )
        ctx = _build_constraint_context(constraints)
        assert "Energy" in ctx
        assert "Utilities" in ctx
        assert "차단 섹터" in ctx

    def test_includes_calibration_table(self):
        """캘리브레이션 테이블이 포함된다."""
        constraints = ConstraintRules(
            confidence_ceiling=8,
            max_recommendations=7,
            blocked_sectors=(),
            strong_sectors=(),
            feedback_commands=(),
            calibration_table={7: 55.0, 8: 62.0},
            confidence_penalty=0,
            default_action="neutral",
        )
        ctx = _build_constraint_context(constraints)
        assert "신뢰도 7" in ctx
        assert "55.0%" in ctx

    def test_includes_feedback_commands(self):
        """피드백 규칙이 포함된다."""
        constraints = ConstraintRules(
            confidence_ceiling=8,
            max_recommendations=7,
            blocked_sectors=(),
            strong_sectors=(),
            feedback_commands=("목표가를 보수적으로 설정하세요.",),
            calibration_table={},
            confidence_penalty=0,
            default_action="neutral",
        )
        ctx = _build_constraint_context(constraints)
        assert "목표가를 보수적" in ctx


class TestPersonaWithConstraints:
    """에이전트 페르소나가 제약을 포함하는지 테스트."""

    def test_bull_includes_constraints(self):
        """Bull 페르소나에 제약 컨텍스트가 삽입된다."""
        constraints = ConstraintRules(
            confidence_ceiling=7,
            max_recommendations=5,
            blocked_sectors=("Energy",),
            strong_sectors=(),
            feedback_commands=("엄격한 기준을 적용하세요.",),
            calibration_table={8: 60.0},
            confidence_penalty=2,
            default_action="exclude",
        )
        persona = get_bull_persona(constraints)
        assert "Energy" in persona.system_prompt
        assert "엄격한 기준" in persona.system_prompt

    def test_bear_includes_constraints(self):
        """Bear 페르소나에 제약 컨텍스트가 삽입된다."""
        constraints = ConstraintRules(
            confidence_ceiling=6,
            max_recommendations=3,
            blocked_sectors=("Healthcare",),
            strong_sectors=(),
            feedback_commands=(),
            calibration_table={},
            confidence_penalty=0,
            default_action="neutral",
        )
        persona = get_bear_persona(constraints)
        assert "Healthcare" in persona.system_prompt

    def test_no_constraints_no_context(self):
        """제약 없으면 기본 프롬프트만."""
        persona = get_bull_persona(None)
        assert "<context>" not in persona.system_prompt
