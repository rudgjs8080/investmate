"""AI 분석 결과 검증기 — 내부 일관성 체크."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def validate_ai_results(
    parsed: list[dict],
    rec_prices: dict[str, float],
) -> list[str]:
    """AI 분석 결과의 내부 일관성을 검증한다.

    Args:
        parsed: parse_ai_response 결과 리스트.
        rec_prices: {ticker: current_price} 매핑.

    Returns:
        경고 메시지 리스트 (빈 리스트 = 문제 없음).
    """
    warnings: list[str] = []

    for p in parsed:
        ticker = p.get("ticker", "?")
        approved = p.get("ai_approved", False)
        target = p.get("ai_target_price")
        stop = p.get("ai_stop_loss")
        confidence = p.get("ai_confidence")
        current = rec_prices.get(ticker)

        if not current or current <= 0:
            continue

        # 승인 종목: 목표가 > 현재가
        if approved and target is not None and target <= current:
            warnings.append(
                f"{ticker}: 목표가(${target:.0f})가 현재가(${current:.0f}) 이하 -- 자동 보정"
            )
            p["ai_target_price"] = round(current * 1.10, 2)  # 10% 상향 보정

        # 손절가 < 현재가
        if approved and stop is not None and stop >= current:
            warnings.append(
                f"{ticker}: 손절가(${stop:.0f})가 현재가(${current:.0f}) 이상 -- 자동 보정"
            )
            p["ai_stop_loss"] = round(current * 0.93, 2)  # 7% 하향 보정

        # 목표가 > 손절가
        if target is not None and stop is not None and target <= stop:
            warnings.append(
                f"{ticker}: 목표가(${target:.0f}) <= 손절가(${stop:.0f}) -- 값 교환"
            )
            p["ai_target_price"], p["ai_stop_loss"] = p["ai_stop_loss"], p["ai_target_price"]

        # 신뢰도-승인 일관성
        if confidence is not None:
            if approved and confidence <= 2:
                warnings.append(
                    f"{ticker}: 추천이지만 신뢰도 {confidence}/10 (매우 낮음)"
                )
            if not approved and confidence >= 8:
                warnings.append(
                    f"{ticker}: 제외지만 신뢰도 {confidence}/10 (매우 높음)"
                )

    if warnings:
        for w in warnings:
            logger.warning("AI 검증: %s", w)

    return warnings


def enforce_constraints(
    parsed: list[dict],
    constraints: object,
    rec_prices: dict[str, float] | None = None,
) -> list[str]:
    """제약 규칙을 강제 적용한다. parsed를 in-place 수정.

    Args:
        parsed: AI 파싱 결과 리스트.
        constraints: ConstraintRules 인스턴스.
        rec_prices: {ticker: current_price} 매핑 (선택).

    Returns:
        수정 경고 메시지 리스트.
    """
    warnings: list[str] = []

    if not constraints:
        return warnings

    # 1. 신뢰도 상한 절삭
    ceiling = constraints.confidence_ceiling
    for p in parsed:
        conf = p.get("ai_confidence")
        if conf is not None and conf > ceiling:
            warnings.append(
                f"{p.get('ticker', '?')}: 신뢰도 {conf}→{ceiling} (VIX 상한 절삭)"
            )
            p["ai_confidence"] = ceiling

    # 2. 신뢰도 페널티 적용
    penalty = constraints.confidence_penalty
    if penalty > 0:
        for p in parsed:
            conf = p.get("ai_confidence")
            if conf is not None and p.get("ai_approved"):
                new_conf = max(1, conf - penalty)
                if new_conf != conf:
                    p["ai_confidence"] = new_conf

    # 3. 차단 섹터 강제 제외
    blocked = set(constraints.blocked_sectors)
    if blocked:
        for p in parsed:
            sector = p.get("sector", "")
            if sector in blocked and p.get("ai_approved"):
                p["ai_approved"] = False
                p["ai_reason"] = f"약점 섹터 자동 제외 ({sector}, 승률 40% 미만)"
                warnings.append(
                    f"{p.get('ticker', '?')}: 차단 섹터 '{sector}' 강제 제외"
                )

    # 4. 추천 수 제한 (신뢰도 하위부터 제거)
    max_recs = constraints.max_recommendations
    approved = [p for p in parsed if p.get("ai_approved")]
    if len(approved) > max_recs:
        approved.sort(key=lambda x: x.get("ai_confidence", 0))
        excess = len(approved) - max_recs
        for p in approved[:excess]:
            p["ai_approved"] = False
            reason = p.get("ai_reason", "")
            p["ai_reason"] = f"{reason} (추천 수 {max_recs}개 제한으로 제외)".strip()
            warnings.append(
                f"{p.get('ticker', '?')}: 추천 수 초과 제거 (신뢰도 {p.get('ai_confidence')})"
            )

    if warnings:
        for w in warnings:
            logger.warning("제약 적용: %s", w)
        if len(warnings) >= 3:
            logger.warning(
                "AI 제약 위반 %d건 — 프롬프트 조정 필요 신호", len(warnings)
            )

    return warnings


def calibrate_confidence(raw_confidence: int, calibration_curve: dict[int, dict]) -> int:
    """과거 캘리브레이션 커브 기반으로 신뢰도를 보정한다."""
    if not calibration_curve:
        return raw_confidence
    entry = calibration_curve.get(raw_confidence)
    if not entry or entry["count"] < 5:
        return raw_confidence
    calibrated = round(entry["actual"] * 10)
    return max(1, min(10, calibrated))
