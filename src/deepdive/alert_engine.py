"""Deep Dive 알림 엔진 — 실행 가이드 + 현재가 → 트리거 평가.

순수 함수: DB 직접 접근 없이 state 주입받아 AlertTrigger 리스트 반환.
파이프라인(또는 별도 cron)이 이 리스트를 Telegram/Slack으로 푸시한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.deepdive.execution_guide import ExecutionGuide

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertTrigger:
    """단일 알림 트리거."""

    ticker: str
    trigger_type: str  # buy_zone_entered | stop_proximity | target_hit | invalidation_hit
    severity: str      # info | warning | critical
    message: str       # 사람이 읽을 한 줄 요약
    current_price: float
    reference_price: float | None = None


# 임계치 상수
_STOP_PROXIMITY_PCT = 2.0   # 손절가의 ±2% 이내면 경고
_TARGET_HIT_MARGIN = 0.005  # 0.5% 이내면 도달로 간주


def evaluate_alerts(
    *,
    ticker: str,
    current_price: float,
    previous_price: float | None,
    execution_guide: ExecutionGuide | dict | None,
    invalidation_conditions: list[str] | tuple[str, ...] | None = None,
) -> list[AlertTrigger]:
    """한 종목에 대한 알림 트리거들을 평가한다.

    Args:
        ticker: 종목 티커
        current_price: 최신 종가
        previous_price: 직전 거래일 종가 (buy_zone_entered 판정 시 '새로 진입' 확인용)
        execution_guide: ExecutionGuide 또는 report_json에서 읽은 dict
        invalidation_conditions: AI가 제시한 무효화 조건 (로그용 — 자동 판정은 미구현)

    Returns:
        발생한 트리거 리스트 (비어 있을 수 있음)
    """
    if current_price <= 0 or execution_guide is None:
        return []

    guide = _coerce_guide_dict(execution_guide)

    triggers: list[AlertTrigger] = []

    # 1. Buy Zone 신규 진입
    bz_low = guide.get("buy_zone_low")
    bz_high = guide.get("buy_zone_high")
    if bz_low and bz_high and bz_low <= current_price <= bz_high:
        # 직전엔 zone 밖이었는지 확인 (신규 진입만 트리거)
        newly_entered = (
            previous_price is None
            or previous_price < bz_low
            or previous_price > bz_high
        )
        if newly_entered:
            triggers.append(
                AlertTrigger(
                    ticker=ticker,
                    trigger_type="buy_zone_entered",
                    severity="info",
                    message=f"{ticker} Buy Zone 진입: ${current_price:.2f} ∈ [${bz_low:.2f}, ${bz_high:.2f}]",
                    current_price=current_price,
                    reference_price=bz_low,
                )
            )

    # 2. 손절가 근접
    stop = guide.get("stop_loss")
    if stop and stop > 0:
        distance_pct = (current_price - stop) / stop * 100
        if abs(distance_pct) <= _STOP_PROXIMITY_PCT:
            severity = "critical" if current_price <= stop else "warning"
            triggers.append(
                AlertTrigger(
                    ticker=ticker,
                    trigger_type="stop_proximity",
                    severity=severity,
                    message=(
                        f"{ticker} 손절가 근접: ${current_price:.2f} "
                        f"vs stop ${stop:.2f} ({distance_pct:+.1f}%)"
                    ),
                    current_price=current_price,
                    reference_price=stop,
                )
            )

    # 3. 목표가 도달
    for horizon_key, label in (("target_1m", "1M"), ("target_3m", "3M"), ("target_6m", "6M")):
        target = guide.get(horizon_key)
        if target is None or target <= 0:
            continue
        if current_price >= target * (1 - _TARGET_HIT_MARGIN):
            # 직전엔 미도달이었는지
            if previous_price is None or previous_price < target * (1 - _TARGET_HIT_MARGIN):
                triggers.append(
                    AlertTrigger(
                        ticker=ticker,
                        trigger_type=f"target_{label.lower()}_hit",
                        severity="info",
                        message=f"{ticker} {label} 목표가 도달: ${current_price:.2f} >= ${target:.2f}",
                        current_price=current_price,
                        reference_price=target,
                    )
                )

    return triggers


def evaluate_alerts_batch(
    entries: list[dict],
) -> list[AlertTrigger]:
    """여러 종목 일괄 평가.

    entries: 각 원소는 {
        "ticker": str, "current_price": float, "previous_price": float|None,
        "execution_guide": dict|ExecutionGuide, "invalidation_conditions": list|None,
    }
    """
    all_triggers: list[AlertTrigger] = []
    for e in entries:
        try:
            triggers = evaluate_alerts(
                ticker=e["ticker"],
                current_price=e["current_price"],
                previous_price=e.get("previous_price"),
                execution_guide=e.get("execution_guide"),
                invalidation_conditions=e.get("invalidation_conditions"),
            )
            all_triggers.extend(triggers)
        except Exception as exc:
            logger.warning("alert 평가 실패 (%s): %s", e.get("ticker"), exc)
    return all_triggers


def format_alerts_summary(triggers: list[AlertTrigger]) -> str:
    """Telegram/Slack 1블록 요약 텍스트."""
    if not triggers:
        return ""

    # 심각도 정렬: critical > warning > info
    order = {"critical": 0, "warning": 1, "info": 2}
    sorted_triggers = sorted(triggers, key=lambda t: (order.get(t.severity, 99), t.ticker))

    lines = [f"🔔 Deep Dive 알림 ({len(triggers)}건)"]
    icon_map = {"critical": "🔴", "warning": "🟡", "info": "🔵"}
    for t in sorted_triggers[:20]:  # 상위 20건만
        icon = icon_map.get(t.severity, "•")
        lines.append(f"{icon} {t.message}")
    if len(triggers) > 20:
        lines.append(f"...외 {len(triggers) - 20}건")
    return "\n".join(lines)


def _coerce_guide_dict(guide: ExecutionGuide | dict) -> dict:
    """ExecutionGuide dataclass 또는 dict → dict."""
    if isinstance(guide, dict):
        return guide
    # dataclass
    return {
        "buy_zone_low": getattr(guide, "buy_zone_low", None),
        "buy_zone_high": getattr(guide, "buy_zone_high", None),
        "stop_loss": getattr(guide, "stop_loss", None),
        "target_1m": getattr(guide, "target_1m", None),
        "target_3m": getattr(guide, "target_3m", None),
        "target_6m": getattr(guide, "target_6m", None),
    }
