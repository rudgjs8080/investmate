"""주간 확신 종목 초보자 설명 생성기 — explainer.py 패턴 재사용."""

from __future__ import annotations

from dataclasses import dataclass

from src.reports.weekly_models import ConvictionPick, ConvictionTechnical


@dataclass(frozen=True)
class WeeklyBeginnerExplanation:
    """확신 종목 초보자 설명."""

    ticker: str
    headline: str
    why_recommended: str
    technical_summary: str
    risk_simple: str


def explain_conviction_pick(
    pick: ConvictionPick,
    tech: ConvictionTechnical | None = None,
) -> WeeklyBeginnerExplanation:
    """확신 종목에 대한 초보자 친화 설명을 생성한다."""
    return WeeklyBeginnerExplanation(
        ticker=pick.ticker,
        headline=_generate_headline(pick, tech),
        why_recommended=_generate_why(pick),
        technical_summary=_generate_technical(tech),
        risk_simple=_generate_risk(pick, tech),
    )


def _generate_headline(pick: ConvictionPick, tech: ConvictionTechnical | None) -> str:
    """한줄 헤드라인."""
    parts: list[str] = []

    if pick.consecutive_days >= 4:
        parts.append(f"{pick.consecutive_days}일 연속 추천")
    elif pick.days_recommended >= 3:
        parts.append(f"주 {pick.days_recommended}일 추천")

    if tech:
        if tech.sma_alignment == "정배열":
            parts.append("상승 추세")
        elif tech.sma_alignment == "역배열":
            parts.append("반등 기대")
        if tech.rsi_14 is not None and tech.rsi_14 < 30:
            parts.append("과매도 구간")
        elif tech.rsi_14 is not None and tech.rsi_14 > 70:
            parts.append("과매수 주의")

    if pick.avg_total_score >= 7.5:
        parts.append("우수 종합점수")
    if pick.ai_consensus == "추천":
        parts.append("AI 추천")

    return ", ".join(parts[:3]) if parts else "종합 분석 기반 추천"


def _generate_why(pick: ConvictionPick) -> str:
    """왜 이 종목이 반복 추천되었는지 설명."""
    lines: list[str] = []

    lines.append(
        f"이 종목은 이번 주 {pick.days_recommended}거래일 중 "
        f"{pick.days_recommended}일 추천되었어요."
    )

    if pick.consecutive_days >= pick.days_recommended:
        lines.append("매일 꾸준히 추천된 종목이라 신뢰도가 높아요.")
    elif pick.consecutive_days >= 3:
        lines.append(f"{pick.consecutive_days}일 연속 추천되어 강한 모멘텀을 보여요.")

    lines.append(f"종합 점수는 {pick.avg_total_score:.1f}/10으로 ")
    if pick.avg_total_score >= 7.5:
        lines[-1] += "상위권에 해당해요."
    elif pick.avg_total_score >= 6.0:
        lines[-1] += "양호한 수준이에요."
    else:
        lines[-1] += "보통 수준이에요."

    if pick.ai_consensus == "추천":
        lines.append("AI도 이 종목을 추천했어요.")
    elif pick.ai_consensus == "혼재":
        lines.append("AI 의견은 추천과 제외가 혼재되어 있어요.")
    elif pick.ai_consensus == "제외":
        lines.append("AI는 이 종목을 제외했으니 신중하게 접근하세요.")

    return " ".join(lines)


def _generate_technical(tech: ConvictionTechnical | None) -> str:
    """기술적 지표 한줄 요약."""
    if not tech:
        return "기술적 데이터 없음"

    parts: list[str] = []

    if tech.rsi_14 is not None:
        if tech.rsi_14 < 30:
            parts.append(f"RSI {tech.rsi_14:.0f} (과매도)")
        elif tech.rsi_14 > 70:
            parts.append(f"RSI {tech.rsi_14:.0f} (과매수)")
        else:
            parts.append(f"RSI {tech.rsi_14:.0f}")

    parts.append(f"MACD {tech.macd_signal}")
    parts.append(f"이동평균 {tech.sma_alignment}")
    parts.append(f"BB {tech.bb_position}")

    if tech.support_price:
        parts.append(f"지지 ${tech.support_price:,.0f}")
    if tech.resistance_price:
        parts.append(f"저항 ${tech.resistance_price:,.0f}")

    return " | ".join(parts)


def _generate_risk(pick: ConvictionPick, tech: ConvictionTechnical | None) -> str:
    """초보자 리스크 설명."""
    risks: list[str] = []

    if tech:
        if tech.rsi_14 is not None and tech.rsi_14 > 70:
            risks.append("RSI가 높아 단기 조정 가능성이 있어요.")
        if tech.sma_alignment == "역배열":
            risks.append("이동평균선이 역배열이라 하락 추세에 있어요. 반등을 확인 후 진입하세요.")
        if tech.bb_position == "상단":
            risks.append("볼린저밴드 상단에 있어 변동성 확대에 주의하세요.")

    if pick.ai_consensus == "제외":
        risks.append("AI가 이 종목을 제외했어요. 소액으로 접근하세요.")
    elif pick.ai_consensus == "혼재":
        risks.append("AI 의견이 엇갈려요. 분할 매수를 권장합니다.")

    if pick.weekly_return_pct is not None and pick.weekly_return_pct < -2:
        risks.append("이번 주 하락세를 보였어요. 추가 하락에 대비하세요.")

    return " ".join(risks) if risks else "현재 뚜렷한 위험 신호는 없지만, 분산 투자를 권장해요."
