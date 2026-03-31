"""토론 프로토콜 (debate.py) 테스트."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.ai.agents import AgentResponse
from src.ai.debate import (
    DebateResult,
    DebateRound,
    _build_round1_prompt,
    _build_round2_bear_prompt,
    _build_round2_bull_prompt,
    _build_round3_prompt,
    _calculate_consensus,
    _extract_stock_data,
    save_debate_rounds,
)
from src.db.helpers import date_to_id
from src.db.models import DimDate, FactAIDebate


# ---------------------------------------------------------------------------
# 프롬프트 추출 테스트
# ---------------------------------------------------------------------------


class TestExtractStockData:
    def test_extracts_candidate_stocks(self):
        """<candidate_stocks> 섹션을 추출한다."""
        prompt = (
            "Some preamble\n"
            "<market_data>\nVIX: 22.5\nS&P 500: 5200\n</market_data>\n"
            "middle text\n"
            "<candidate_stocks>\n## TOP 10\nAAPL score=85\n</candidate_stocks>\n"
            "trailing text\n"
        )
        result = _extract_stock_data(prompt)
        assert "<candidate_stocks>" in result
        assert "AAPL" in result
        assert "<market_data>" in result
        assert "VIX: 22.5" in result
        assert "preamble" not in result

    def test_fallback_on_no_sections(self):
        """섹션이 없으면 전체 프롬프트 반환."""
        prompt = "Plain text prompt without any sections"
        result = _extract_stock_data(prompt)
        assert result == prompt

    def test_extracts_calibration(self):
        """<calibration> 섹션도 추출한다."""
        prompt = (
            "<calibration>\nconf 7: 45%\n</calibration>\n"
            "<candidate_stocks>\nAAPL\n</candidate_stocks>"
        )
        result = _extract_stock_data(prompt)
        assert "calibration" in result
        assert "conf 7" in result


# ---------------------------------------------------------------------------
# 라운드 프롬프트 빌더 테스트
# ---------------------------------------------------------------------------


class TestRoundPrompts:
    def test_round1_prompt(self):
        """R1 프롬프트 구조."""
        prompt = _build_round1_prompt("AAPL data here")
        assert "AAPL data here" in prompt
        assert "독립 분석" in prompt or "분석" in prompt

    def test_round2_bull_includes_bear_analysis(self):
        """R2 Bull 프롬프트에 Bear R1이 포함된다."""
        bear_r1 = AgentResponse(
            role="bear", round_num=1,
            analysis_text="AAPL: PER 과열, 목표가 하향 필요",
        )
        prompt = _build_round2_bull_prompt("stock data", bear_r1)
        assert "opponent_analysis" in prompt
        assert "PER 과열" in prompt
        assert "반박" in prompt

    def test_round2_bear_includes_bull_analysis(self):
        """R2 Bear 프롬프트에 Bull R1이 포함된다."""
        bull_r1 = AgentResponse(
            role="bull", round_num=1,
            analysis_text="AAPL: RSI 반등, 실적 서프라이즈 기대",
        )
        prompt = _build_round2_bear_prompt("stock data", bull_r1)
        assert "opponent_analysis" in prompt
        assert "RSI 반등" in prompt
        assert "반박" in prompt

    def test_round3_includes_both(self):
        """R3 프롬프트에 양측 R2가 포함된다."""
        bull_r2 = AgentResponse(
            role="bull", round_num=2,
            analysis_text="Bull final arguments",
        )
        bear_r2 = AgentResponse(
            role="bear", round_num=2,
            analysis_text="Bear final arguments",
        )
        prompt = _build_round3_prompt("stock data", bull_r2, bear_r2)
        assert "bull_analysis" in prompt
        assert "bear_analysis" in prompt
        assert "Bull final arguments" in prompt
        assert "Bear final arguments" in prompt
        assert "submit_stock_analysis" in prompt


# ---------------------------------------------------------------------------
# 합의 강도 계산 테스트
# ---------------------------------------------------------------------------


class TestCalculateConsensus:
    def test_high_consensus(self):
        """Bull과 Synth 일치 시 high."""
        bull_r2 = AgentResponse(
            role="bull", round_num=2,
            key_arguments=[
                {"ticker": "AAPL", "argument": "buy"},
                {"ticker": "MSFT", "argument": "buy"},
                {"ticker": "GOOGL", "argument": "buy"},
            ],
        )
        bear_r2 = AgentResponse(
            role="bear", round_num=2,
            key_arguments=[{"ticker": "TSLA", "argument": "risk"}],
        )
        synth_parsed = [
            {"ticker": "AAPL", "ai_approved": True},
            {"ticker": "MSFT", "ai_approved": True},
            {"ticker": "GOOGL", "ai_approved": True},
            {"ticker": "TSLA", "ai_approved": False},
        ]
        result = _calculate_consensus(bull_r2, bear_r2, synth_parsed)
        assert result == "high"

    def test_low_consensus(self):
        """합의 없으면 low."""
        bull_r2 = AgentResponse(
            role="bull", round_num=2,
            key_arguments=[{"ticker": "AAPL", "argument": "buy"}],
        )
        bear_r2 = AgentResponse(
            role="bear", round_num=2,
            key_arguments=[{"ticker": "MSFT", "argument": "risk"}],
        )
        synth_parsed = [
            {"ticker": "AAPL", "ai_approved": False},  # Bull 추천했지만 Synth 제외
            {"ticker": "MSFT", "ai_approved": True},   # Bear 경고했지만 Synth 추천
        ]
        result = _calculate_consensus(bull_r2, bear_r2, synth_parsed)
        assert result == "low"

    def test_empty_parsed(self):
        """빈 파싱 결과."""
        bull = AgentResponse(role="bull", round_num=2)
        bear = AgentResponse(role="bear", round_num=2)
        result = _calculate_consensus(bull, bear, [])
        assert result == "low"


# ---------------------------------------------------------------------------
# DB 저장 테스트
# ---------------------------------------------------------------------------


@pytest.fixture
def _seed_dates(session):
    """테스트 날짜."""
    d = date(2026, 3, 15)
    session.add(DimDate(
        date_id=date_to_id(d), date=d, year=d.year,
        quarter=1, month=d.month, week_of_year=11,
        day_of_week=d.weekday(), is_trading_day=True,
    ))
    session.commit()


class TestSaveDebateRounds:
    def test_saves_rounds(self, session, _seed_dates):
        """토론 라운드가 DB에 저장된다."""
        result = DebateResult(
            rounds=(
                DebateRound(
                    round_num=1,
                    bull=AgentResponse(
                        role="bull", round_num=1,
                        analysis_text="Bull R1 analysis",
                    ),
                    bear=AgentResponse(
                        role="bear", round_num=1,
                        analysis_text="Bear R1 analysis",
                    ),
                ),
                DebateRound(
                    round_num=3,
                    synthesizer=AgentResponse(
                        role="synthesizer", round_num=3,
                        analysis_text="Synth R3 verdict",
                    ),
                ),
            ),
            final_parsed=[{"ticker": "AAPL", "ai_approved": True}],
            consensus_strength="high",
        )

        run_date_id = date_to_id(date(2026, 3, 15))
        save_debate_rounds(session, run_date_id, result)

        rows = session.query(FactAIDebate).all()
        assert len(rows) == 3  # Bull R1, Bear R1, Synth R3

        roles = {r.agent_role for r in rows}
        assert roles == {"bull", "bear", "synthesizer"}

        synth_row = [r for r in rows if r.agent_role == "synthesizer"][0]
        assert synth_row.consensus_strength == "high"
        assert synth_row.round_num == 3

    def test_skips_empty_responses(self, session, _seed_dates):
        """빈 응답은 저장하지 않는다."""
        result = DebateResult(
            rounds=(
                DebateRound(
                    round_num=1,
                    bull=AgentResponse(role="bull", round_num=1, analysis_text=""),
                    bear=AgentResponse(role="bear", round_num=1, analysis_text="Bear ok"),
                ),
            ),
            consensus_strength="low",
        )
        run_date_id = date_to_id(date(2026, 3, 15))
        save_debate_rounds(session, run_date_id, result)

        rows = session.query(FactAIDebate).all()
        assert len(rows) == 1
        assert rows[0].agent_role == "bear"
