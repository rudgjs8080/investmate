"""멀티 에이전트 토론 시스템 — Bull/Bear/Synthesizer 에이전트 정의."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentPersona:
    """에이전트 페르소나 정의."""

    role: str  # "bull" | "bear" | "synthesizer"
    system_prompt: str
    model: str = "claude-sonnet-4-20250514"


@dataclass(frozen=True)
class AgentResponse:
    """에이전트 응답 결과."""

    role: str
    round_num: int  # 1, 2, 3
    analysis_text: str = ""
    confidence: int | None = None
    key_arguments: list[dict] = field(default_factory=list)
    raw_response: str | dict = ""


# ---------------------------------------------------------------------------
# 공통 제약 컨텍스트 빌더
# ---------------------------------------------------------------------------


def _build_constraint_context(constraints: object | None) -> str:
    """ConstraintRules에서 Bull/Bear 에이전트에 주입할 컨텍스트를 생성한다."""
    if constraints is None:
        return ""

    parts: list[str] = ["\n\n<context>"]

    blocked = getattr(constraints, "blocked_sectors", ())
    if blocked:
        parts.append(f"차단 섹터 (과거 승률 40% 미만): {', '.join(blocked)}")

    cal_table = getattr(constraints, "calibration_table", {})
    if cal_table:
        parts.append("과거 신뢰도별 실제 승률:")
        for k, v in sorted(cal_table.items()):
            parts.append(f"  신뢰도 {k}: {v}%")

    commands = getattr(constraints, "feedback_commands", ())
    if commands:
        parts.append("피드백 규칙:")
        for cmd in commands[:5]:
            parts.append(f"  - {cmd}")

    parts.append("</context>")
    return "\n".join(parts) if len(parts) > 2 else ""


# ---------------------------------------------------------------------------
# 페르소나 생성
# ---------------------------------------------------------------------------


def get_bull_persona(
    constraints: object | None = None,
    model: str | None = None,
) -> AgentPersona:
    """매수 관점 에이전트 페르소나를 생성한다."""
    system = (
        "너는 '성장 투자 전문가'다. 각 종목의 매수 논거를 찾아라.\n\n"
        "분석 방향:\n"
        "- 성장성, 모멘텀, 카탈리스트에 집중하라\n"
        "- 기술적 돌파 신호: RSI 반등, MACD 골든크로스, 이동평균 정배열\n"
        "- 펀더멘털 강점: 실적 서프라이즈, 매출/이익 성장률, FCF 개선\n"
        "- 수급 신호: 내부자 매수, 기관 매집, 공매도 감소\n"
        "- 이벤트 카탈리스트: 신제품, M&A, 규제 완화\n\n"
        "출력 형식 (각 종목별):\n"
        "- bull_case: 매수 근거 3가지 (각각 구체적 데이터 포함)\n"
        "- confidence: 1-10 (매수 확신도)\n"
        "- target_price: 20거래일 목표가\n\n"
        "규칙:\n"
        "- 뻔한 말 금지. 반드시 해당 종목의 구체적 숫자/지표를 인용하라\n"
        "- 예시: 'RSI 35에서 반등 중, 3분기 연속 EPS 상회(+12%/+8%/+15%)'\n"
        "- JSON 형식으로 응답하라\n"
    )
    system += _build_constraint_context(constraints)
    return AgentPersona(
        role="bull",
        system_prompt=system,
        model=model or "claude-sonnet-4-20250514",
    )


def get_bear_persona(
    constraints: object | None = None,
    model: str | None = None,
) -> AgentPersona:
    """리스크 관점 에이전트 페르소나를 생성한다."""
    system = (
        "너는 '리스크 매니저 겸 공매도 전문가'다. 각 종목의 리스크를 분석하라.\n\n"
        "분석 방향:\n"
        "- 밸류에이션 과열: PER/PBR 섹터 대비 프리미엄, PEG 과대\n"
        "- 하방 리스크: 기술적 약화, 데스크로스, 지지선 이탈\n"
        "- 매크로 역풍: 금리 상승, 달러 강세, VIX 상승, 섹터 약세\n"
        "- 수급 악화: 내부자 매도, 공매도 증가, 기관 축소\n"
        "- 이벤트 리스크: 실적 발표 불확실성, FOMC, 규제 리스크\n\n"
        "출력 형식 (각 종목별):\n"
        "- bear_case: 리스크 3가지 (각각 구체적 데이터 포함)\n"
        "- downside_risk: 하방 리스크 추정치 (%)\n"
        "- stop_loss: 손절가\n\n"
        "규칙:\n"
        "- 뻔한 말 금지. 반드시 해당 종목의 구체적 숫자/지표를 인용하라\n"
        "- 예시: 'PER 32x (섹터 평균 22x 대비 45% 프리미엄), 내부자 3건 매도'\n"
        "- JSON 형식으로 응답하라\n"
    )
    system += _build_constraint_context(constraints)
    return AgentPersona(
        role="bear",
        system_prompt=system,
        model=model or "claude-sonnet-4-20250514",
    )


def get_synthesizer_persona(
    constraints: object | None = None,
    model: str | None = None,
) -> AgentPersona:
    """종합 판정 에이전트 페르소나를 생성한다."""
    hard_rules = ""
    if constraints:
        rules: list[str] = []
        ceiling = getattr(constraints, "confidence_ceiling", 8)
        rules.append(f"- 신뢰도 상한: {ceiling} (이를 초과하는 신뢰도 부여 금지)")
        max_recs = getattr(constraints, "max_recommendations", 7)
        rules.append(f"- 최대 추천 수: {max_recs}개")
        blocked = getattr(constraints, "blocked_sectors", ())
        if blocked:
            rules.append(f"- 차단 섹터: {', '.join(blocked)} (추천 금지)")
        cal_table = getattr(constraints, "calibration_table", {})
        if cal_table:
            cal_lines = [f"  신뢰도 {k}: 실제 승률 {v}%" for k, v in sorted(cal_table.items())]
            rules.append("- 캘리브레이션:\n" + "\n".join(cal_lines))
        hard_rules = "\n<hard_rules>\n" + "\n".join(rules) + "\n</hard_rules>\n"

    system = (
        "너는 '수석 포트폴리오 매니저'다. Bull과 Bear 에이전트의 토론 결과를 종합 평가하라.\n\n"
        "평가 기준:\n"
        "1. 논거의 구체성: 구체적 숫자/데이터가 있는 논거가 우선\n"
        "2. 데이터 근거의 신뢰성: 검증 가능한 지표 기반 논거가 우선\n"
        "3. 논리적 일관성: 자기 모순이 없는 논거가 우선\n"
        "4. 시의성: 현재 시장 환경에 부합하는 논거가 우선\n\n"
        "판정 방법:\n"
        "- 각 종목에 대해 Bull과 Bear 중 어느 쪽이 더 설득력 있는지 판단\n"
        "- Bull이 우세하면 매수 추천, Bear가 우세하면 제외\n"
        "- 양측 논거가 팽팽하면 보수적으로 제외 권장\n\n"
        "submit_stock_analysis 도구를 사용하여 최종 결과를 제출하라.\n"
        f"{hard_rules}"
    )
    return AgentPersona(
        role="synthesizer",
        system_prompt=system,
        model=model or "claude-sonnet-4-20250514",
    )


# ---------------------------------------------------------------------------
# API 호출 래퍼
# ---------------------------------------------------------------------------


def call_agent(
    persona: AgentPersona,
    user_prompt: str,
    timeout: int = 300,
    tools: list[dict] | None = None,
    tool_choice: dict | None = None,
) -> AgentResponse:
    """에이전트 API를 호출한다.

    Args:
        persona: 에이전트 페르소나.
        user_prompt: 사용자 프롬프트 (종목 데이터).
        timeout: API 타임아웃 (초).
        tools: Tool Use 스키마 (Synthesizer만 사용).
        tool_choice: Tool 선택 강제 옵션.

    Returns:
        AgentResponse with analysis text and key arguments.
    """
    try:
        from anthropic import Anthropic

        from src.ai.claude_analyzer import _log_usage

        client = Anthropic()
        max_tokens = 8192 if tools else 4096

        kwargs: dict = {
            "model": persona.model,
            "max_tokens": max_tokens,
            "system": persona.system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
            "timeout": timeout,
        }
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice

        message = client.messages.create(**kwargs)
        _log_usage(message)

        # Tool Use 응답 (Synthesizer)
        for block in message.content:
            if block.type == "tool_use":
                logger.info("[%s] Tool Use 완료", persona.role)
                return AgentResponse(
                    role=persona.role,
                    round_num=0,  # 호출자가 설정
                    analysis_text=json.dumps(block.input, ensure_ascii=False),
                    key_arguments=_extract_arguments_from_tool(block.input),
                    raw_response=block.input,
                )

        # 텍스트 응답 (Bull/Bear)
        text = ""
        for block in message.content:
            if block.type == "text":
                text += block.text

        logger.info("[%s] 텍스트 응답 수신 (%d자)", persona.role, len(text))
        return AgentResponse(
            role=persona.role,
            round_num=0,
            analysis_text=text,
            key_arguments=_extract_arguments_from_text(text),
            raw_response=text,
        )

    except ImportError:
        logger.warning("anthropic 패키지 미설치 — %s 에이전트 스킵", persona.role)
        return AgentResponse(role=persona.role, round_num=0)
    except Exception as e:
        logger.warning("[%s] API 호출 실패: %s", persona.role, e)
        return AgentResponse(role=persona.role, round_num=0)


# ---------------------------------------------------------------------------
# 응답에서 핵심 논거 추출
# ---------------------------------------------------------------------------


def _extract_arguments_from_tool(tool_input: dict) -> list[dict]:
    """Tool Use 응답에서 종목별 핵심 논거를 추출한다."""
    args: list[dict] = []
    for item in tool_input.get("analysis", []):
        ticker = item.get("ticker", "")
        reason = item.get("reason", "")
        if ticker:
            args.append({
                "ticker": ticker,
                "argument": reason[:200],
                "evidence": "",
            })
    return args


def _extract_arguments_from_text(text: str) -> list[dict]:
    """텍스트 응답에서 종목별 핵심 논거를 추출한다 (간략 파싱)."""
    import re

    args: list[dict] = []
    # 티커 패턴: ## AAPL 또는 **AAPL** 등
    ticker_pattern = re.compile(r'(?:^##?\s*|^\*\*|\b)([A-Z]{2,5})\b', re.MULTILINE)
    matches = ticker_pattern.findall(text)

    non_tickers = {"TOP", "BUY", "USD", "RSI", "VIX", "ETF", "IPO", "CEO",
                   "CFO", "THE", "FOR", "AND", "NOT", "JSON", "EPS", "PER"}
    seen: set[str] = set()
    for ticker in matches:
        if ticker in non_tickers or ticker in seen:
            continue
        seen.add(ticker)
        # 해당 티커 부근 텍스트에서 요약 추출
        idx = text.find(ticker)
        snippet = text[idx:idx + 300] if idx >= 0 else ""
        args.append({
            "ticker": ticker,
            "argument": snippet[:200].replace("\n", " ").strip(),
            "evidence": "",
        })

    return args
