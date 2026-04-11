"""Deep Dive CLI 기반 3라운드 토론 오케스트레이터.

Phase 11d: asyncio 병렬화 옵션(run_debate_async / run_debate_smart).
SDK 백엔드는 NotImplementedError로 예약 — Phase 11d 2단계에서 구현 예정.
"""

from __future__ import annotations

import asyncio
import logging

from src.deepdive.ai_prompts import (
    DEEPDIVE_SYSTEM_PROMPT,
    _parse_ai_response,
    build_stock_context,
    run_deepdive_cli,
    run_deepdive_simple,
)
from src.deepdive.schemas import AIResult, CLIDebateResult, DebateRound

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# 프롬프트 상수
# ──────────────────────────────────────────

BULL_SYSTEM_PROMPT = """\
너는 30년 경력 성장주 롱온리 포트폴리오 매니저다.
이 종목을 보유하거나 추가 매수할 이유를 찾아라.
단계적으로 깊이 사고한 뒤 결론을 내려라.

분석 지침:
- 6개 레이어 데이터를 모두 활용, 매수 관점에 유리한 근거 집중
- 밸류에이션이 비싸도 성장 스토리로 정당화 가능한지 평가
- 기술적 약세는 "중장기 매수 기회"로 해석 가능한지 검토
- 내부자 매도는 세금/다각화 목적 가능성 고려
- 보유 종목은 평단가 대비 수익률, 보유기간 맥락 반영

반드시 아래 JSON 형식만 출력하라. 다른 텍스트 없이 JSON만:
{"action":"ADD"|"HOLD", "conviction":1-10,\
 "bull_case":["근거1","근거2","근거3"],\
 "scenarios":{"1M":{"base":{"prob":0.5,"low":가격,"high":가격},\
"bull":{"prob":0.3,"low":가격,"high":가격},"bear":{"prob":0.2,"low":가격,"high":가격}},\
"3M":{...},"6M":{...}},\
 "catalysts":["촉매1"], "key_risks_acknowledged":["인정 리스크1"]}"""

BEAR_SYSTEM_PROMPT = """\
너는 30년 경력 숏셀러 겸 리스크 매니저다.
이 종목의 하방 리스크와 매도/축소 이유를 찾아라.
단계적으로 깊이 사고한 뒤 결론을 내려라.

분석 지침:
- 리스크/약점 집중: 성장 둔화, 마진 압박, 경쟁 심화
- 밸류에이션 과열은 절대적+상대적 수치 모두 제시
- 기술적 약세 → 하방 시나리오 구체화
- 매크로 역풍 정량화
- 보유 종목: 큰 수익은 이익실현 적기, 손실은 추가 하락 리스크

반드시 아래 JSON 형식만 출력하라. 다른 텍스트 없이 JSON만:
{"action":"TRIM"|"EXIT"|"HOLD", "conviction":1-10,\
 "bear_case":["리스크1","리스크2","리스크3"],\
 "scenarios":{"1M":{...},"3M":{...},"6M":{...}},\
 "stop_loss_level":가격, "key_strengths_acknowledged":["인정 강점1"]}"""

SYNTH_SYSTEM_PROMPT = """\
너는 30년 경력 수석 CIO다. Bull/Bear 양측 토론을 종합하여 최종 판단을 내려라.
단계적으로 깊이 사고한 뒤 결론을 내려라.

판단 기준:
1. 논거의 구체성 + 데이터 근거
2. 논리적 일관성
3. 현재 시장 환경(레짐) 정합성
4. 리스크/보상 비대칭성
5. 포트폴리오 적합도 — <portfolio_context>가 있으면 섹터/종목 여유를 고려하여 conviction 하향 조정

보유자 관점 (보유 종목만):
- HOLD = 현 포지션 유지  - ADD = 추가 매수 (확신 높을 때)
- TRIM = 일부 매도       - EXIT = 전량 매도 (확신 높을 때)
- +30% 이상 수익 → 이익실현 검토  - -15% 이상 손실 → 손절 검토

근거 추적 의무:
- reasoning은 300~600자, 구체 수치 인용 (예: "RSI 72, F-Score 8/9, 섹터 PER 프리미엄 +15%")
- evidence_refs 배열에 사용한 데이터 포인트를 "layer{N}.field=value" 형태로 3~8개 기록
  (예: "layer3.rsi=72", "layer1.f_score=8", "layer2.per_5y_percentile=85")
- invalidation_conditions 배열에 이 판단이 깨지는 구체 조건 2~4개 (예: "RSI 40 하회", "다음 분기 EPS 미스")

반드시 아래 JSON 형식만 출력하라. 다른 텍스트 없이 JSON만:
{"action_grade":"HOLD"|"ADD"|"TRIM"|"EXIT",\
 "conviction":1-10, "uncertainty":"low"|"medium"|"high",\
 "reasoning":"300~600자 구체 수치 인용 종합 판단",\
 "scenarios":{"1M":{"base":{"prob":0.5,"low":가격,"high":가격},\
"bull":{"prob":0.3,"low":가격,"high":가격},"bear":{"prob":0.2,"low":가격,"high":가격}},\
"3M":{...},"6M":{...}},\
 "consensus_strength":"high"|"medium"|"low",\
 "what_missing":"반대 의견 강조",\
 "key_levels":{"support":가격,"resistance":가격,"stop_loss":가격},\
 "next_review_trigger":"재검토 트리거 조건",\
 "evidence_refs":["layer3.rsi=72","layer1.f_score=8"],\
 "invalidation_conditions":["RSI 40 하회","분기 EPS 미스"]}"""


# ──────────────────────────────────────────
# 토론 오케스트레이터
# ──────────────────────────────────────────


def run_deepdive_debate(
    entry, layers: dict, current_price: float, daily_change: float,
    timeout: int = 600, model: str = "opus",
    pair_results: list | None = None,
    portfolio_context: dict | None = None,
) -> CLIDebateResult | None:
    """3라운드 CLI 토론 실행. 5회 순차 호출.

    R1: Bull + Bear 독립 (2회)
    R2: Bull(Bear R1 반박) + Bear(Bull R1 반박) (2회)
    R3: Synthesizer(Bull R2 + Bear R2 종합) (1회)
    """
    context = build_stock_context(
        entry, layers, current_price, daily_change,
        pair_results=pair_results,
        portfolio_context=portfolio_context,
    )
    r1_prompt = _build_r1_prompt(context)
    rounds: list[DebateRound] = []

    # R1: Bull
    logger.info("[%s] R1 Bull 시작", entry.ticker)
    bull_r1_raw = run_deepdive_cli(r1_prompt, BULL_SYSTEM_PROMPT, timeout, model)
    bull_r1_parsed = _parse_round(bull_r1_raw) if bull_r1_raw else None
    rounds.append(DebateRound(1, "bull", bull_r1_raw or "", bull_r1_parsed))

    # R1: Bear
    logger.info("[%s] R1 Bear 시작", entry.ticker)
    bear_r1_raw = run_deepdive_cli(r1_prompt, BEAR_SYSTEM_PROMPT, timeout, model)
    bear_r1_parsed = _parse_round(bear_r1_raw) if bear_r1_raw else None
    rounds.append(DebateRound(1, "bear", bear_r1_raw or "", bear_r1_parsed))

    # R2: Bull 반박 (Bear R1 필요)
    bull_r2_raw = None
    if bull_r1_raw and bear_r1_raw:
        logger.info("[%s] R2 Bull 반박 시작", entry.ticker)
        r2_bull_prompt = _build_r2_bull_prompt(context, bear_r1_raw)
        bull_r2_raw = run_deepdive_cli(r2_bull_prompt, BULL_SYSTEM_PROMPT, timeout, model)
    bull_r2_text = bull_r2_raw or bull_r1_raw or ""
    rounds.append(DebateRound(2, "bull", bull_r2_text, _parse_round(bull_r2_raw) if bull_r2_raw else None))

    # R2: Bear 반박 (Bull R1 필요)
    bear_r2_raw = None
    if bear_r1_raw and bull_r1_raw:
        logger.info("[%s] R2 Bear 반박 시작", entry.ticker)
        r2_bear_prompt = _build_r2_bear_prompt(context, bull_r1_raw)
        bear_r2_raw = run_deepdive_cli(r2_bear_prompt, BEAR_SYSTEM_PROMPT, timeout, model)
    bear_r2_text = bear_r2_raw or bear_r1_raw or ""
    rounds.append(DebateRound(2, "bear", bear_r2_text, _parse_round(bear_r2_raw) if bear_r2_raw else None))

    # R3: Synthesizer
    logger.info("[%s] R3 Synthesizer 시작", entry.ticker)
    r3_prompt = _build_r3_prompt(context, bull_r2_text, bear_r2_text)
    synth_raw = run_deepdive_cli(r3_prompt, SYNTH_SYSTEM_PROMPT, timeout, model)
    synth_parsed = _parse_round(synth_raw) if synth_raw else None
    rounds.append(DebateRound(3, "synthesizer", synth_raw or "", synth_parsed))

    # 결과 조합
    if synth_parsed and "action_grade" in synth_parsed:
        final = _parse_ai_response(synth_raw)
        consensus = synth_parsed.get("consensus_strength", "medium")
        scenarios = synth_parsed.get("scenarios")
        return CLIDebateResult(
            rounds=tuple(rounds),
            final_result=final,
            scenarios=scenarios,
            consensus_strength=consensus if consensus in ("high", "medium", "low") else "medium",
            bull_summary=bull_r2_text[:2000] if bull_r2_text else None,
            bear_summary=bear_r2_text[:2000] if bear_r2_text else None,
        )

    # R3 실패 → simple 폴백
    logger.warning("[%s] R3 실패, simple 폴백", entry.ticker)
    fallback = run_deepdive_simple(entry, layers, current_price, daily_change, timeout, model)
    return CLIDebateResult(
        rounds=tuple(rounds),
        final_result=fallback,
        scenarios=None,
        consensus_strength="low",
        bull_summary=bull_r2_text[:2000] if bull_r2_text else None,
        bear_summary=bear_r2_text[:2000] if bear_r2_text else None,
    )


# ──────────────────────────────────────────
# Phase 11d: asyncio 병렬 경로 + 디스패처
# ──────────────────────────────────────────


async def run_deepdive_debate_async(
    entry, layers: dict, current_price: float, daily_change: float,
    timeout: int = 600, model: str = "opus",
    pair_results: list | None = None,
    portfolio_context: dict | None = None,
) -> CLIDebateResult | None:
    """3라운드 CLI 토론의 asyncio 병렬 버전.

    R1 Bull/Bear 병렬 → R2 Bull/Bear 병렬 → R3 단독.
    CLI subprocess는 asyncio.to_thread로 래핑해 블록 해제.
    """
    context = build_stock_context(
        entry, layers, current_price, daily_change,
        pair_results=pair_results,
        portfolio_context=portfolio_context,
    )
    r1_prompt = _build_r1_prompt(context)

    logger.info("[%s] R1 Bull/Bear 병렬 시작", entry.ticker)
    bull_r1_coro = asyncio.to_thread(
        run_deepdive_cli, r1_prompt, BULL_SYSTEM_PROMPT, timeout, model,
    )
    bear_r1_coro = asyncio.to_thread(
        run_deepdive_cli, r1_prompt, BEAR_SYSTEM_PROMPT, timeout, model,
    )
    bull_r1_raw, bear_r1_raw = await asyncio.gather(
        bull_r1_coro, bear_r1_coro, return_exceptions=True,
    )
    bull_r1_raw = _coerce_raw_or_none(bull_r1_raw, "[%s] R1 Bull 실패", entry.ticker)
    bear_r1_raw = _coerce_raw_or_none(bear_r1_raw, "[%s] R1 Bear 실패", entry.ticker)

    rounds: list[DebateRound] = []
    rounds.append(
        DebateRound(
            1, "bull", bull_r1_raw or "",
            _parse_round(bull_r1_raw) if bull_r1_raw else None,
        )
    )
    rounds.append(
        DebateRound(
            1, "bear", bear_r1_raw or "",
            _parse_round(bear_r1_raw) if bear_r1_raw else None,
        )
    )

    # R2 Bull/Bear 병렬 — 상대 R1 필요
    async def _bull_r2() -> str | None:
        if not (bull_r1_raw and bear_r1_raw):
            return None
        prompt = _build_r2_bull_prompt(context, bear_r1_raw)
        return await asyncio.to_thread(
            run_deepdive_cli, prompt, BULL_SYSTEM_PROMPT, timeout, model,
        )

    async def _bear_r2() -> str | None:
        if not (bull_r1_raw and bear_r1_raw):
            return None
        prompt = _build_r2_bear_prompt(context, bull_r1_raw)
        return await asyncio.to_thread(
            run_deepdive_cli, prompt, BEAR_SYSTEM_PROMPT, timeout, model,
        )

    logger.info("[%s] R2 Bull/Bear 병렬 시작", entry.ticker)
    bull_r2_raw, bear_r2_raw = await asyncio.gather(
        _bull_r2(), _bear_r2(), return_exceptions=True,
    )
    bull_r2_raw = _coerce_raw_or_none(bull_r2_raw, "[%s] R2 Bull 실패", entry.ticker)
    bear_r2_raw = _coerce_raw_or_none(bear_r2_raw, "[%s] R2 Bear 실패", entry.ticker)

    bull_r2_text = bull_r2_raw or bull_r1_raw or ""
    bear_r2_text = bear_r2_raw or bear_r1_raw or ""
    rounds.append(
        DebateRound(
            2, "bull", bull_r2_text,
            _parse_round(bull_r2_raw) if bull_r2_raw else None,
        )
    )
    rounds.append(
        DebateRound(
            2, "bear", bear_r2_text,
            _parse_round(bear_r2_raw) if bear_r2_raw else None,
        )
    )

    # R3 Synthesizer
    logger.info("[%s] R3 Synthesizer 시작", entry.ticker)
    r3_prompt = _build_r3_prompt(context, bull_r2_text, bear_r2_text)
    synth_raw = await asyncio.to_thread(
        run_deepdive_cli, r3_prompt, SYNTH_SYSTEM_PROMPT, timeout, model,
    )
    synth_parsed = _parse_round(synth_raw) if synth_raw else None
    rounds.append(DebateRound(3, "synthesizer", synth_raw or "", synth_parsed))

    if synth_parsed and "action_grade" in synth_parsed:
        final = _parse_ai_response(synth_raw)
        consensus = synth_parsed.get("consensus_strength", "medium")
        scenarios = synth_parsed.get("scenarios")
        return CLIDebateResult(
            rounds=tuple(rounds),
            final_result=final,
            scenarios=scenarios,
            consensus_strength=consensus if consensus in ("high", "medium", "low") else "medium",
            bull_summary=bull_r2_text[:2000] if bull_r2_text else None,
            bear_summary=bear_r2_text[:2000] if bear_r2_text else None,
        )

    logger.warning("[%s] R3 실패 (async), simple 폴백", entry.ticker)
    fallback = run_deepdive_simple(
        entry, layers, current_price, daily_change, timeout, model,
    )
    return CLIDebateResult(
        rounds=tuple(rounds),
        final_result=fallback,
        scenarios=None,
        consensus_strength="low",
        bull_summary=bull_r2_text[:2000] if bull_r2_text else None,
        bear_summary=bear_r2_text[:2000] if bear_r2_text else None,
    )


def run_debate_smart(
    entry, layers: dict, current_price: float, daily_change: float,
    *,
    timeout: int = 600,
    model: str = "opus",
    pair_results: list | None = None,
    portfolio_context: dict | None = None,
    parallel: bool = False,
    backend: str = "cli",
) -> CLIDebateResult | None:
    """Phase 11d: 병렬/백엔드 디스패처.

    backend="sdk"는 Phase 11d 2단계에서 구현 예정 — 현재는 NotImplementedError.
    backend="cli" + parallel=True → asyncio.run(run_deepdive_debate_async(...))
    backend="cli" + parallel=False → 기존 sync 경로(회귀 안전)
    """
    if backend == "sdk":
        raise NotImplementedError(
            "deepdive backend='sdk'는 Phase 11d 2단계에서 구현 예정입니다. "
            "현재는 'cli'만 지원합니다."
        )
    if backend not in ("cli", "auto"):
        raise ValueError(f"알 수 없는 deepdive_backend: {backend!r}")

    if parallel:
        return asyncio.run(
            run_deepdive_debate_async(
                entry, layers, current_price, daily_change,
                timeout=timeout, model=model,
                pair_results=pair_results,
                portfolio_context=portfolio_context,
            )
        )
    return run_deepdive_debate(
        entry, layers, current_price, daily_change,
        timeout=timeout, model=model,
        pair_results=pair_results,
        portfolio_context=portfolio_context,
    )


def _coerce_raw_or_none(result, log_fmt: str, *args) -> str | None:
    """asyncio.gather(..., return_exceptions=True) 결과 정규화."""
    if isinstance(result, BaseException):
        logger.warning(log_fmt + ": %s", *args, result)
        return None
    return result  # str | None


# ──────────────────────────────────────────
# 프롬프트 빌더
# ──────────────────────────────────────────


def _build_r1_prompt(context: str) -> str:
    return (
        "아래 종목 데이터를 분석하라. 6개 레이어 데이터를 모두 활용하여 최대한 강력한 논거를 제시하라.\n\n"
        f"{context}\n\n"
        "투자 참고용이며 투자 권유가 아닙니다."
    )


def _build_r2_bull_prompt(context: str, bear_r1_text: str) -> str:
    return (
        "아래는 리스크 분석가(Bear)의 R1 분석이다.\n\n"
        f"<opponent_r1>\n{bear_r1_text[:3000]}\n</opponent_r1>\n\n"
        "위 리스크 분석을 읽고:\n"
        "1. 반박할 수 있는 논거에 구체적 데이터로 반박하라\n"
        "2. 인정할 논거는 인정하되, 매수 관점이 더 강한 이유를 설명하라\n"
        "3. 새로운 매수 근거가 있으면 추가하라\n"
        "4. 최종 JSON을 갱신하라\n\n"
        f"{context}"
    )


def _build_r2_bear_prompt(context: str, bull_r1_text: str) -> str:
    return (
        "아래는 성장 투자 전문가(Bull)의 R1 분석이다.\n\n"
        f"<opponent_r1>\n{bull_r1_text[:3000]}\n</opponent_r1>\n\n"
        "위 매수 분석을 읽고:\n"
        "1. 반박할 수 있는 논거에 구체적 데이터로 반박하라\n"
        "2. 인정할 논거는 인정하되, 리스크가 더 큰 이유를 설명하라\n"
        "3. 새로운 리스크 요인이 있으면 추가하라\n"
        "4. 최종 JSON을 갱신하라\n\n"
        f"{context}"
    )


def _build_r3_prompt(context: str, bull_r2: str, bear_r2: str) -> str:
    return (
        "Bull Agent(매수 전문가)와 Bear Agent(리스크 전문가)의 교차 검증 결과이다.\n\n"
        f"<bull_r2>\n{bull_r2[:3000]}\n</bull_r2>\n\n"
        f"<bear_r2>\n{bear_r2[:3000]}\n</bear_r2>\n\n"
        "양측 논거를 평가하여 최종 판정을 JSON으로 출력하라.\n"
        "- 논거의 구체성, 데이터 근거, 논리 일관성을 기준으로 판단\n"
        "- 팽팽한 경우 보수적으로 판정 (HOLD 선호)\n"
        "- 시나리오별 가격 범위와 확률을 구체적으로 제시하라\n\n"
        f"{context}\n\n"
        "투자 참고용이며 투자 권유가 아닙니다."
    )


# ──────────────────────────────────────────
# 파싱
# ──────────────────────────────────────────

def _parse_round(raw: str | None) -> dict | None:
    """라운드 JSON 파싱."""
    if not raw:
        return None
    import json
    import re

    # ```json 블록
    m = re.search(r"```json\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass
    # 직접 JSON
    decoder = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(raw, i)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, ValueError):
                continue
    return None
