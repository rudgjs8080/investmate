"""멀티 에이전트 토론 프로토콜 — 3라운드 토론 오케스트레이션."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from src.ai.agents import (
    AgentResponse,
    call_agent,
    get_bear_persona,
    get_bull_persona,
    get_synthesizer_persona,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DebateRound:
    """토론 라운드 결과."""

    round_num: int
    bull: AgentResponse | None = None
    bear: AgentResponse | None = None
    synthesizer: AgentResponse | None = None


@dataclass(frozen=True)
class DebateResult:
    """전체 토론 결과."""

    rounds: tuple[DebateRound, ...] = ()
    final_parsed: list[dict] = field(default_factory=list)
    consensus_strength: str = "low"
    deep_dive: list[dict] | None = None


# ---------------------------------------------------------------------------
# 프롬프트에서 종목 데이터 추출
# ---------------------------------------------------------------------------


def _extract_stock_data(unified_prompt: str) -> str:
    """통합 프롬프트에서 종목 데이터 + 시장 데이터를 추출한다."""
    sections: list[str] = []

    # <market_data> 추출
    market_match = re.search(
        r"<market_data>(.*?)</market_data>", unified_prompt, re.DOTALL
    )
    if market_match:
        sections.append(f"<market_data>\n{market_match.group(1).strip()}\n</market_data>")

    # <candidate_stocks> 추출
    cand_match = re.search(
        r"<candidate_stocks>(.*?)</candidate_stocks>", unified_prompt, re.DOTALL
    )
    if cand_match:
        sections.append(f"<candidate_stocks>\n{cand_match.group(1).strip()}\n</candidate_stocks>")

    # <calibration> 추출
    cal_match = re.search(
        r"<calibration>(.*?)</calibration>", unified_prompt, re.DOTALL
    )
    if cal_match:
        sections.append(f"<calibration>\n{cal_match.group(1).strip()}\n</calibration>")

    if not sections:
        # 추출 실패 시 전체 프롬프트 사용 (폴백)
        logger.warning("종목 데이터 추출 실패 — 전체 프롬프트 사용")
        return unified_prompt

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# 라운드별 프롬프트 빌더
# ---------------------------------------------------------------------------


def _build_round1_prompt(stock_data: str) -> str:
    """R1 사용자 프롬프트 — 독립 분석용."""
    return (
        "아래 종목 데이터를 분석하라. 상대 에이전트의 의견은 아직 없다.\n"
        "각 종목에 대해 너의 관점에서 분석 결과를 JSON으로 제출하라.\n\n"
        f"{stock_data}"
    )


def _build_round2_bull_prompt(stock_data: str, bear_r1: AgentResponse) -> str:
    """R2 Bull 프롬프트 — Bear R1을 보고 반박."""
    return (
        "아래는 리스크 분석가(Bear Agent)의 의견이다.\n\n"
        f"<opponent_analysis>\n{bear_r1.analysis_text}\n</opponent_analysis>\n\n"
        "위 리스크 분석을 읽고:\n"
        "1. 반박할 수 있는 논거에 구체적 데이터로 반박하라\n"
        "2. 인정할 논거는 인정하되, 매수 관점이 더 강한 이유를 설명하라\n"
        "3. 새로운 매수 근거가 있으면 추가하라\n"
        "4. 최종적으로 각 종목의 매수 추천 여부를 명확히 하라\n\n"
        f"종목 데이터 (참고용):\n{stock_data}"
    )


def _build_round2_bear_prompt(stock_data: str, bull_r1: AgentResponse) -> str:
    """R2 Bear 프롬프트 — Bull R1을 보고 반박."""
    return (
        "아래는 성장 투자 전문가(Bull Agent)의 의견이다.\n\n"
        f"<opponent_analysis>\n{bull_r1.analysis_text}\n</opponent_analysis>\n\n"
        "위 매수 분석을 읽고:\n"
        "1. 반박할 수 있는 논거에 구체적 데이터로 반박하라\n"
        "2. 인정할 논거는 인정하되, 리스크가 더 큰 이유를 설명하라\n"
        "3. 새로운 리스크 요인이 있으면 추가하라\n"
        "4. 최종적으로 각 종목의 리스크 평가를 명확히 하라\n\n"
        f"종목 데이터 (참고용):\n{stock_data}"
    )


def _build_round3_prompt(
    stock_data: str,
    bull_r2: AgentResponse,
    bear_r2: AgentResponse,
) -> str:
    """R3 Synthesizer 프롬프트 — 양측 R2를 보고 판정."""
    return (
        "아래에 Bull Agent(매수 전문가)와 Bear Agent(리스크 전문가)의 "
        "교차 검증 결과가 제시되어 있다.\n\n"
        f"<bull_analysis>\n{bull_r2.analysis_text}\n</bull_analysis>\n\n"
        f"<bear_analysis>\n{bear_r2.analysis_text}\n</bear_analysis>\n\n"
        "양측 논거를 평가하여 각 종목에 대해 최종 판정을 내려라.\n"
        "- 논거의 구체성, 데이터 근거, 논리 일관성을 기준으로 판단\n"
        "- 팽팽한 경우 보수적으로(제외) 판정\n"
        "- submit_stock_analysis 도구로 결과를 제출하라\n\n"
        f"종목 원본 데이터:\n{stock_data}"
    )


# ---------------------------------------------------------------------------
# 합의 강도 계산
# ---------------------------------------------------------------------------


def _calculate_consensus(
    bull_r2: AgentResponse,
    bear_r2: AgentResponse,
    synth_parsed: list[dict],
) -> str:
    """토론 합의 강도를 계산한다.

    Returns:
        "high" | "medium" | "low"
    """
    approved_set = {p["ticker"] for p in synth_parsed if p.get("ai_approved")}
    excluded_set = {p["ticker"] for p in synth_parsed if not p.get("ai_approved")}

    bull_tickers = {a["ticker"] for a in bull_r2.key_arguments if a.get("ticker")}
    bear_tickers = {a["ticker"] for a in bear_r2.key_arguments if a.get("ticker")}

    if not approved_set and not excluded_set:
        return "low"

    total = len(approved_set) + len(excluded_set)
    if total == 0:
        return "low"

    # 합의도: Bull이 추천한 종목이 Synth에서도 추천된 비율 +
    #         Bear가 경고한 종목이 Synth에서 제외된 비율
    agreement = 0
    for ticker in approved_set:
        if ticker in bull_tickers:
            agreement += 1
    for ticker in excluded_set:
        if ticker in bear_tickers:
            agreement += 1

    ratio = agreement / total if total > 0 else 0

    if ratio >= 0.7:
        return "high"
    if ratio >= 0.4:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# 토론 실행 오케스트레이터
# ---------------------------------------------------------------------------


def run_debate(
    stock_data_prompt: str,
    constraints: object | None = None,
    model: str | None = None,
    timeout: int = 300,
) -> DebateResult:
    """3라운드 멀티 에이전트 토론을 실행한다.

    Args:
        stock_data_prompt: build_unified_prompt() 결과.
        constraints: ConstraintRules (Synthesizer hard_rules용).
        model: AI 모델 (기본 claude-sonnet-4-20250514).
        timeout: 라운드당 타임아웃 (초).

    Returns:
        DebateResult with final_parsed in same format as parse_ai_response.
    """
    from src.ai.claude_analyzer import STOCK_ANALYSIS_TOOL, _try_parse_json

    stock_data = _extract_stock_data(stock_data_prompt)
    bull_persona = get_bull_persona(constraints, model)
    bear_persona = get_bear_persona(constraints, model)
    synth_persona = get_synthesizer_persona(constraints, model)

    rounds: list[DebateRound] = []

    # ── Round 1: 독립 분석 (병렬) ──
    logger.info("토론 R1: Bull/Bear 독립 분석 시작")
    r1_prompt = _build_round1_prompt(stock_data)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut_bull = executor.submit(call_agent, bull_persona, r1_prompt, timeout)
        fut_bear = executor.submit(call_agent, bear_persona, r1_prompt, timeout)

        bull_r1 = fut_bull.result(timeout=timeout + 30)
        bear_r1 = fut_bear.result(timeout=timeout + 30)

    bull_r1 = AgentResponse(
        role=bull_r1.role, round_num=1, analysis_text=bull_r1.analysis_text,
        confidence=bull_r1.confidence, key_arguments=bull_r1.key_arguments,
        raw_response=bull_r1.raw_response,
    )
    bear_r1 = AgentResponse(
        role=bear_r1.role, round_num=1, analysis_text=bear_r1.analysis_text,
        confidence=bear_r1.confidence, key_arguments=bear_r1.key_arguments,
        raw_response=bear_r1.raw_response,
    )
    rounds.append(DebateRound(round_num=1, bull=bull_r1, bear=bear_r1))
    logger.info("토론 R1 완료: Bull %d자, Bear %d자",
                len(bull_r1.analysis_text), len(bear_r1.analysis_text))

    # 빈 응답 체크
    if not bull_r1.analysis_text and not bear_r1.analysis_text:
        logger.warning("R1 양측 모두 빈 응답 — 토론 중단")
        return DebateResult(rounds=tuple(rounds))

    # ── Round 2: 교차 반박 (병렬) ──
    logger.info("토론 R2: 교차 반박 시작")
    bull_r2_prompt = _build_round2_bull_prompt(stock_data, bear_r1)
    bear_r2_prompt = _build_round2_bear_prompt(stock_data, bull_r1)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut_bull2 = executor.submit(call_agent, bull_persona, bull_r2_prompt, timeout)
        fut_bear2 = executor.submit(call_agent, bear_persona, bear_r2_prompt, timeout)

        try:
            bull_r2 = fut_bull2.result(timeout=timeout + 30)
        except Exception as e:
            logger.warning("R2 Bull 실패, R1 결과 사용: %s", e)
            bull_r2 = bull_r1

        try:
            bear_r2 = fut_bear2.result(timeout=timeout + 30)
        except Exception as e:
            logger.warning("R2 Bear 실패, R1 결과 사용: %s", e)
            bear_r2 = bear_r1

    bull_r2 = AgentResponse(
        role=bull_r2.role, round_num=2, analysis_text=bull_r2.analysis_text,
        confidence=bull_r2.confidence, key_arguments=bull_r2.key_arguments,
        raw_response=bull_r2.raw_response,
    )
    bear_r2 = AgentResponse(
        role=bear_r2.role, round_num=2, analysis_text=bear_r2.analysis_text,
        confidence=bear_r2.confidence, key_arguments=bear_r2.key_arguments,
        raw_response=bear_r2.raw_response,
    )
    rounds.append(DebateRound(round_num=2, bull=bull_r2, bear=bear_r2))
    logger.info("토론 R2 완료")

    # ── Round 3: Synthesizer 판정 ──
    logger.info("토론 R3: Synthesizer 종합 판정 시작")
    r3_prompt = _build_round3_prompt(stock_data, bull_r2, bear_r2)

    synth_response = call_agent(
        synth_persona, r3_prompt, timeout,
        tools=[STOCK_ANALYSIS_TOOL],
        tool_choice={"type": "tool", "name": "submit_stock_analysis"},
    )
    synth_r3 = AgentResponse(
        role=synth_response.role, round_num=3,
        analysis_text=synth_response.analysis_text,
        confidence=synth_response.confidence,
        key_arguments=synth_response.key_arguments,
        raw_response=synth_response.raw_response,
    )

    # Synthesizer Tool Use 결과 파싱
    final_parsed: list[dict] = []
    deep_dive: list[dict] | None = None

    if isinstance(synth_r3.raw_response, dict):
        parsed = _try_parse_json(json.dumps(synth_r3.raw_response))
        if parsed:
            final_parsed = parsed
        deep_dive = synth_r3.raw_response.get("deep_dive")
    elif synth_r3.analysis_text:
        parsed = _try_parse_json(synth_r3.analysis_text)
        if parsed:
            final_parsed = parsed

    rounds.append(DebateRound(round_num=3, synthesizer=synth_r3))
    logger.info("토론 R3 완료: %d종목 파싱", len(final_parsed))

    # 합의 강도
    consensus = _calculate_consensus(bull_r2, bear_r2, final_parsed)
    logger.info("토론 합의 강도: %s", consensus)

    return DebateResult(
        rounds=tuple(rounds),
        final_parsed=final_parsed,
        consensus_strength=consensus,
        deep_dive=deep_dive,
    )


# ---------------------------------------------------------------------------
# 컨센서스 기반 신뢰도 보정
# ---------------------------------------------------------------------------


def apply_consensus_penalty(
    parsed: list[dict],
    consensus_strength: str,
    penalty: int = 1,
) -> list[dict]:
    """합의 강도에 따라 신뢰도를 보정한다.

    Args:
        parsed: parse_ai_response 결과 (수정됨).
        consensus_strength: "high" | "medium" | "low".
        penalty: low consensus 시 차감할 신뢰도 점수.

    Returns:
        보정된 parsed 리스트.
    """
    if consensus_strength == "low" and penalty > 0:
        for p in parsed:
            if p.get("ai_confidence") is not None:
                original = p["ai_confidence"]
                p["ai_confidence"] = max(1, original - penalty)
                if original != p["ai_confidence"]:
                    logger.info(
                        "%s 컨센서스 패널티: 신뢰도 %d → %d (합의 %s)",
                        p.get("ticker"), original, p["ai_confidence"], consensus_strength,
                    )
    return parsed


# ---------------------------------------------------------------------------
# DB 저장
# ---------------------------------------------------------------------------


def save_debate_rounds(
    session: Session,
    run_date_id: int,
    result: DebateResult,
) -> None:
    """토론 라운드를 fact_ai_debate에 저장한다."""
    from src.db.models import FactAIDebate

    for rd in result.rounds:
        for agent_resp in (rd.bull, rd.bear, rd.synthesizer):
            if agent_resp is None or not agent_resp.analysis_text:
                continue
            row = FactAIDebate(
                run_date_id=run_date_id,
                recommendation_id=None,
                agent_role=agent_resp.role,
                round_num=rd.round_num,
                analysis_text=agent_resp.analysis_text,
                confidence=agent_resp.confidence,
                key_arguments=agent_resp.key_arguments or None,
                consensus_strength=(
                    result.consensus_strength if rd.round_num == 3 else None
                ),
            )
            session.add(row)

    session.commit()
    logger.info("토론 %d라운드 DB 저장 완료", len(result.rounds))
