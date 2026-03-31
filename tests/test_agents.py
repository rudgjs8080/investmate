"""에이전트 정의 (agents.py) 테스트."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.ai.agents import (
    AgentPersona,
    AgentResponse,
    _extract_arguments_from_text,
    _extract_arguments_from_tool,
    call_agent,
    get_bear_persona,
    get_bull_persona,
    get_synthesizer_persona,
)


# ---------------------------------------------------------------------------
# 페르소나 생성 테스트
# ---------------------------------------------------------------------------


class TestPersonas:
    def test_bull_persona_basic(self):
        """Bull 페르소나 기본 생성."""
        persona = get_bull_persona()
        assert persona.role == "bull"
        assert "성장 투자 전문가" in persona.system_prompt
        assert "매수" in persona.system_prompt
        assert persona.model == "claude-sonnet-4-20250514"

    def test_bear_persona_basic(self):
        """Bear 페르소나 기본 생성."""
        persona = get_bear_persona()
        assert persona.role == "bear"
        assert "리스크 매니저" in persona.system_prompt
        assert "공매도" in persona.system_prompt

    def test_synthesizer_persona_basic(self):
        """Synthesizer 페르소나 기본 생성."""
        persona = get_synthesizer_persona()
        assert persona.role == "synthesizer"
        assert "포트폴리오 매니저" in persona.system_prompt

    def test_synthesizer_with_constraints(self):
        """Synthesizer에 제약 규칙이 삽입된다."""
        mock_constraints = MagicMock()
        mock_constraints.confidence_ceiling = 6
        mock_constraints.max_recommendations = 5
        mock_constraints.blocked_sectors = ("Materials", "Utilities")
        mock_constraints.calibration_table = {7: 45, 8: 38}

        persona = get_synthesizer_persona(constraints=mock_constraints)
        assert "신뢰도 상한: 6" in persona.system_prompt
        assert "최대 추천 수: 5" in persona.system_prompt
        assert "Materials" in persona.system_prompt
        assert "hard_rules" in persona.system_prompt

    def test_custom_model(self):
        """커스텀 모델 지정."""
        persona = get_bull_persona(model="claude-haiku-4-5-20251001")
        assert persona.model == "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# 응답 파싱 테스트
# ---------------------------------------------------------------------------


class TestExtractArguments:
    def test_extract_from_tool(self):
        """Tool Use 응답에서 논거 추출."""
        tool_input = {
            "approved": ["AAPL"],
            "excluded": ["MSFT"],
            "analysis": [
                {"ticker": "AAPL", "reason": "Strong growth momentum with RSI bounce"},
                {"ticker": "MSFT", "reason": "Overvalued at 35x PER"},
            ],
        }
        args = _extract_arguments_from_tool(tool_input)
        assert len(args) == 2
        assert args[0]["ticker"] == "AAPL"
        assert "growth" in args[0]["argument"].lower()

    def test_extract_from_tool_empty(self):
        """빈 Tool Use 응답."""
        args = _extract_arguments_from_tool({})
        assert args == []

    def test_extract_from_text(self):
        """텍스트 응답에서 티커 추출."""
        text = """
## AAPL (Apple Inc.)
- Strong quarterly earnings, RSI at 35 bouncing
- Institutional accumulation detected

## MSFT (Microsoft)
- Cloud growth continues with Azure momentum
"""
        args = _extract_arguments_from_text(text)
        tickers = [a["ticker"] for a in args]
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_extract_from_text_filters_non_tickers(self):
        """비티커 단어는 필터링된다."""
        text = "RSI is at 35. VIX shows ETF outflows. BUY AAPL now."
        args = _extract_arguments_from_text(text)
        tickers = [a["ticker"] for a in args]
        assert "RSI" not in tickers
        assert "VIX" not in tickers
        assert "ETF" not in tickers
        assert "BUY" not in tickers
        assert "AAPL" in tickers


# ---------------------------------------------------------------------------
# call_agent 테스트 (mock)
# ---------------------------------------------------------------------------


class TestCallAgent:
    def test_import_error_returns_empty(self):
        """anthropic 미설치 시 빈 응답."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "anthropic":
                raise ImportError("mocked")
            return original_import(name, *args, **kwargs)

        persona = get_bull_persona()
        with patch("builtins.__import__", side_effect=mock_import):
            response = call_agent(persona, "test prompt")
        assert response.role == "bull"
        assert response.analysis_text == ""

    def test_agent_response_immutable(self):
        """AgentResponse가 frozen이다."""
        resp = AgentResponse(role="bull", round_num=1, analysis_text="test")
        with pytest.raises(AttributeError):
            resp.role = "bear"
