"""초보자 친화 설명 생성기 -- 기술적/기본적 지표를 쉬운 한국어로 번역한다."""

from __future__ import annotations

from dataclasses import dataclass

from src.reports.report_models import (
    MacroEnvironment,
    StockRecommendationDetail,
)


@dataclass(frozen=True)
class BeginnerExplanation:
    """초보자 친화적 종목 설명."""

    headline: str  # 한 줄 요약
    why_recommended: str  # 왜 추천하나요? (쉬운 설명)
    numbers_backing: str  # 숫자로 보면
    risk_simple: str  # 주의할 점


def explain_stock(rec: StockRecommendationDetail) -> BeginnerExplanation:
    """추천 종목에 대한 초보자 친화 설명을 생성한다."""
    headline = _generate_headline(rec)
    why = _generate_why(rec)
    numbers = _generate_numbers(rec)
    risk = _generate_risk_simple(rec)
    return BeginnerExplanation(
        headline=headline,
        why_recommended=why,
        numbers_backing=numbers,
        risk_simple=risk,
    )


def market_investment_opinion(macro: MacroEnvironment, num_recs: int) -> str:
    """시장 상황에 따른 투자 의견을 생성한다."""
    score = macro.market_score or 5
    if score >= 7:
        return (
            f"현재 시장은 강세 국면이에요. "
            f"추천 종목 {num_recs}개 중 3-5개를 분할 매수하는 전략을 고려해보세요."
        )
    elif score >= 4:
        return (
            f"시장이 관망세라 신중한 접근이 필요해요. "
            f"추천 종목 중 기본적 분석 점수가 높은 1-3개만 소액으로 시작하세요."
        )
    else:
        return (
            f"시장 분위기가 좋지 않아요 (점수 {score}/10). "
            f"지금은 현금 비중을 높이고, 정말 확신이 있는 종목만 소량 매수하세요. "
            f"과매도 반등을 노리는 전략이 유효할 수 있어요."
        )


def summarize_market(macro: MacroEnvironment) -> str:
    """시장 환경을 한 줄로 요약한다."""
    parts: list[str] = []

    if macro.mood == "강세":
        parts.append("시장 전반적으로 분위기가 좋습니다")
    elif macro.mood == "약세":
        parts.append("시장 분위기가 다소 불안합니다")
    else:
        parts.append("시장은 관망세입니다")

    if macro.vix is not None:
        if macro.vix >= 30:
            parts.append(f"투자자 불안감이 높은 상태예요 (VIX {macro.vix:.0f})")
        elif macro.vix >= 20:
            parts.append(f"약간의 경계감이 있어요 (VIX {macro.vix:.0f})")

    if macro.sp500_trend == "하락":
        parts.append("S&P 500이 단기 평균선 아래에 있어 조심할 필요가 있어요")
    elif macro.sp500_trend == "상승":
        parts.append("S&P 500이 상승 추세를 유지하고 있어요")

    return ". ".join(parts) + "."


def summarize_recommendations_oneliner(
    recs: tuple[StockRecommendationDetail, ...],
) -> str:
    """추천 종목을 한 줄로 요약한다."""
    if not recs:
        return "오늘은 뚜렷한 매수 추천 종목이 없습니다."

    items = []
    for rec in recs[:5]:
        reason = _short_reason(rec)
        items.append(f"**{rec.ticker}**({reason})")
    rest = len(recs) - 5
    result = ", ".join(items)
    if rest > 0:
        result += f" 외 {rest}종목"
    return result


# ──────────────────────────────────────────
# 내부 생성 함수
# ──────────────────────────────────────────


def _generate_headline(rec: StockRecommendationDetail) -> str:
    """종목 한 줄 요약 생성."""
    parts: list[str] = []
    t = rec.technical
    f = rec.fundamental

    # 기술적 상태
    if t.sma_alignment == "정배열":
        parts.append("상승 추세")
    elif t.sma_alignment == "역배열":
        parts.append("하락 추세 주의")

    if t.rsi is not None:
        if t.rsi < 30:
            parts.append("과매도 반등 기대")
        elif t.rsi > 70:
            parts.append("과매수 구간")

    # 시그널
    buy_sigs = [s for s in t.signals if s.direction == "BUY"]
    sell_sigs = [s for s in t.signals if s.direction == "SELL"]
    if buy_sigs:
        names = _translate_signals([s.signal_type for s in buy_sigs[:2]])
        parts.append(" + ".join(names))

    # 기본적 상태
    if f.summary == "우수":
        parts.append("우수한 펀더멘털")
    elif f.composite_score >= 6:
        parts.append("양호한 재무구조")

    # 모멘텀
    if rec.momentum_score >= 8:
        parts.append("강한 상승 모멘텀")
    elif rec.momentum_score >= 6:
        parts.append("양호한 모멘텀")

    if not parts:
        parts.append("종합 분석 기반 추천")

    return ", ".join(parts)


def _generate_why(rec: StockRecommendationDetail) -> str:
    """왜 추천하나요? 초보자 눈높이 설명."""
    points: list[str] = []
    t = rec.technical
    f = rec.fundamental

    # 가격 추세
    if t.sma_alignment == "정배열":
        points.append(
            "이 종목의 주가는 단기/중기/장기 이동평균선이 모두 상승 방향으로 "
            "정렬되어 있어요. 쉽게 말해, 꾸준히 오르는 중이라는 뜻이에요."
        )
    elif t.sma_alignment == "역배열":
        points.append(
            "주가가 하락 추세에 있지만, 다른 지표들이 반등 가능성을 보여주고 있어요."
        )

    # RSI
    if t.rsi is not None:
        if t.rsi < 30:
            points.append(
                f"RSI가 {t.rsi:.0f}으로 매우 낮아요. "
                "이는 많은 사람이 팔아서 가격이 많이 내려간 상태라, "
                "반등할 가능성이 높다는 신호예요."
            )
        elif t.rsi < 45:
            points.append(
                f"RSI가 {t.rsi:.0f}으로, 아직 살 여지가 있는 구간이에요."
            )
        elif t.rsi > 70:
            points.append(
                f"RSI가 {t.rsi:.0f}으로 다소 높아요. "
                "많은 사람이 이미 매수한 상태라 단기 조정 가능성이 있어요."
            )

    # MACD
    has_macd_signal = any(s.signal_type == "macd_bullish" for s in t.signals if s.direction == "BUY")
    if t.macd_status == "상승" and not has_macd_signal:
        points.append(
            "MACD 지표가 상승 전환했어요. "
            "주가의 상승 힘이 커지고 있다는 의미예요."
        )

    # 매수 시그널
    buy_sigs = [s for s in t.signals if s.direction == "BUY"]
    if buy_sigs:
        for sig in buy_sigs[:2]:
            explanation = _explain_signal_for_beginner(sig.signal_type)
            if explanation:
                points.append(explanation)

    # 기본적 분석
    if f.per is not None and 0 < f.per < 20:
        points.append(
            f"PER이 {f.per:.1f}배로, 기업이 벌어들이는 이익에 비해 "
            "주가가 비교적 저렴한 편이에요."
        )
    elif f.per is not None and f.per > 30:
        points.append(
            f"PER이 {f.per:.1f}배로 다소 높지만, "
            "성장 기대감이 반영된 결과일 수 있어요."
        )

    if f.roe is not None:
        roe_pct = f.roe * 100 if abs(f.roe) < 1 else f.roe
        if roe_pct > 15:
            points.append(
                f"ROE가 {roe_pct:.1f}%로, 주주의 돈을 효율적으로 "
                "활용해서 수익을 잘 내고 있어요."
            )

    if not points:
        points.append(
            "종합적인 기술적/기본적 분석 결과, "
            "현재 매수하기에 적합한 조건을 갖추고 있어요."
        )

    return " ".join(points)


def _generate_numbers(rec: StockRecommendationDetail) -> str:
    """핵심 숫자 요약."""
    t = rec.technical
    f = rec.fundamental
    items: list[str] = []

    if t.rsi is not None:
        items.append(f"RSI {t.rsi:.0f}")
    items.append(f"MACD {t.macd_status}")
    items.append(f"이동평균 {t.sma_alignment}")

    if f.per is not None:
        items.append(f"PER {f.per:.1f}")
    if f.roe is not None:
        roe_pct = f.roe * 100 if abs(f.roe) < 1 else f.roe
        items.append(f"ROE {roe_pct:.1f}%")
    if f.debt_ratio is not None:
        items.append(f"부채비율 {f.debt_ratio:.0%}")

    items.append(f"종합점수 {rec.total_score:.1f}/10")
    return " | ".join(items)


def _generate_risk_simple(rec: StockRecommendationDetail) -> str:
    """주의할 점을 쉽게 설명."""
    risks: list[str] = []
    t = rec.technical
    f = rec.fundamental

    if t.rsi is not None and t.rsi > 65:
        risks.append(
            f"RSI가 {t.rsi:.0f}으로 높은 편이에요. "
            "단기적으로 가격이 조정될 수 있어요."
        )

    if t.sma_alignment == "역배열":
        risks.append("전체적인 추세가 하락 방향이라 신중한 접근이 필요해요.")

    if f.per is not None and f.per > 30:
        risks.append(f"PER {f.per:.1f}배로 다소 비싼 편이에요.")

    if f.debt_ratio is not None and f.debt_ratio > 0.6:
        risks.append(
            f"부채비율이 {f.debt_ratio:.0%}로 높아서, "
            "금리 인상 시 부담이 될 수 있어요."
        )

    sell_sigs = [s for s in t.signals if s.direction == "SELL"]
    if sell_sigs:
        risks.append(
            f"매도 시그널이 {len(sell_sigs)}개 감지되었어요. "
            "매수 타이밍을 신중히 잡으세요."
        )

    # AI 리스크 평가 반영
    if rec.ai_approved is False:
        risks.append(f"AI가 이 종목을 제외했어요: {rec.ai_reason or '이유 미제공'}")
    elif rec.ai_risk_level == "HIGH":
        risks.append("AI가 이 종목의 리스크를 '높음'으로 평가했어요. 소액으로 시작하세요.")
    elif rec.ai_confidence is not None and rec.ai_confidence <= 4:
        risks.append(f"AI 신뢰도가 {rec.ai_confidence}/10으로 낮은 편이에요.")

    if not risks:
        return "현재 뚜렷한 위험 신호는 없지만, 분산 투자를 권장해요."

    return " ".join(risks)


def _short_reason(rec: StockRecommendationDetail) -> str:
    """종목의 짧은 한줄 이유 (종목마다 다르게)."""
    t = rec.technical
    f = rec.fundamental

    # 가장 두드러진 특징 선택
    if t.rsi is not None and t.rsi < 35:
        return f"과매도 RSI {t.rsi:.0f}"

    buy_sigs = [s for s in t.signals if s.direction == "BUY"]
    if buy_sigs:
        sig_name = _translate_signals([buy_sigs[0].signal_type])[0]
        return sig_name

    parts = []
    if f.composite_score >= 7:
        parts.append("우수 재무")
    elif f.per is not None and f.per < 15:
        parts.append(f"저PER {f.per:.0f}")

    if t.sma_alignment == "정배열":
        parts.append("상승추세")
    if rec.momentum_score >= 8:
        parts.append("강모멘텀")

    if parts:
        return "+".join(parts[:2])

    return f"종합 {rec.total_score:.1f}"


def _translate_signals(signal_types: list[str]) -> list[str]:
    """시그널 코드를 한국어로 번역."""
    translations = {
        "golden_cross": "골든크로스",
        "death_cross": "데스크로스",
        "rsi_oversold": "RSI 과매도",
        "rsi_overbought": "RSI 과매수",
        "macd_bullish": "MACD 매수 전환",
        "macd_bearish": "MACD 매도 전환",
        "bb_lower_break": "볼린저 하단 돌파",
        "bb_upper_break": "볼린저 상단 돌파",
        "stoch_bullish": "스토캐스틱 매수 전환",
        "stoch_bearish": "스토캐스틱 매도 전환",
    }
    return [translations.get(s, s) for s in signal_types]


def _explain_signal_for_beginner(signal_type: str) -> str | None:
    """개별 시그널을 초보자용으로 설명."""
    explanations = {
        "golden_cross": (
            "골든크로스가 발생했어요! 단기 이동평균선이 장기선을 "
            "위로 돌파한 것으로, 상승 추세 전환의 대표적인 신호예요."
        ),
        "death_cross": (
            "데스크로스가 발생했어요. 단기 이동평균선이 장기선 아래로 내려가서, "
            "하락 추세가 시작될 수 있다는 경고 신호예요."
        ),
        "rsi_oversold": (
            "RSI 과매도 신호가 나왔어요. 많이 팔려서 가격이 "
            "바닥 근처일 수 있다는 뜻이에요."
        ),
        "rsi_overbought": (
            "RSI 과매수 신호예요. 이미 많이 올라서 "
            "단기적으로 쉬어갈 수 있는 구간이에요."
        ),
        "macd_bullish": (
            "MACD가 매수 신호를 보내고 있어요. "
            "주가의 상승 동력이 생기고 있다는 의미예요."
        ),
        "macd_bearish": (
            "MACD가 매도 신호를 보내고 있어요. "
            "상승 동력이 약해지고 있어서 주의가 필요해요."
        ),
        "bb_lower_break": (
            "주가가 볼린저밴드 하단을 터치했어요. "
            "통계적으로 평균 가격 쪽으로 되돌아올 가능성이 높아요."
        ),
        "bb_upper_break": (
            "주가가 볼린저밴드 상단을 돌파했어요. "
            "강한 상승세지만, 단기 과열 가능성도 있어요."
        ),
        "stoch_bullish": (
            "스토캐스틱 지표에서 매수 전환 신호가 나왔어요. "
            "단기적으로 반등 가능성이 높아지고 있다는 뜻이에요."
        ),
        "stoch_bearish": (
            "스토캐스틱 지표에서 매도 전환 신호가 나왔어요. "
            "단기 상승세가 둔화되고 있어서 주의가 필요해요."
        ),
    }
    return explanations.get(signal_type)
