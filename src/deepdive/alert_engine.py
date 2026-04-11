"""Deep Dive 알림 엔진 — 실행 가이드 + 현재가 → 트리거 평가.

순수 함수: DB 직접 접근 없이 state 주입받아 AlertTrigger 리스트 반환.
파이프라인(또는 별도 cron)이 이 리스트를 Telegram/Slack으로 푸시한다.

Phase 11 확장:
- invalidation_hit / review_trigger_hit — AI 무효화 조건 자동 감지 (룰 파서)
- earnings_imminent / ex_dividend_imminent — Layer 5 촉매 캘린더 연계
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from src.deepdive.execution_guide import ExecutionGuide
from src.deepdive.invalidation_parser import (
    LayerSnapshot,
    ParsedCondition,
    evaluate_condition,
    parse_conditions,
)
from src.deepdive.schemas import UpcomingCatalyst

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
    invalidation_conditions: Sequence[str] | None = None,
    next_review_trigger: str | None = None,
    layer_snapshot: LayerSnapshot | None = None,
    dedup_keys: set[str] | None = None,
) -> list[AlertTrigger]:
    """한 종목에 대한 알림 트리거들을 평가한다.

    Args:
        ticker: 종목 티커
        current_price: 최신 종가
        previous_price: 직전 거래일 종가 (buy_zone_entered 판정 시 '새로 진입' 확인용)
        execution_guide: ExecutionGuide 또는 report_json에서 읽은 dict
        invalidation_conditions: AI 무효화 조건 (Phase 11a 룰 파서로 자동 감지)
        next_review_trigger: AI 재검토 트리거 (Phase 11a — 자동 감지)
        layer_snapshot: Phase 11a — invalidation 평가용 지표 스냅샷 (없으면 invalidation 스킵)
        dedup_keys: Phase 11a — 일일 중복 방지용 (이미 발화된 "invalidation:{ticker}:{raw}" 키)

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

    # 4. Phase 11a: Invalidation 자동 감지
    if invalidation_conditions and layer_snapshot is not None:
        triggers.extend(
            _evaluate_invalidation_triggers(
                ticker=ticker,
                current_price=current_price,
                raws=invalidation_conditions,
                snap=layer_snapshot,
                dedup_keys=dedup_keys,
                trigger_type="invalidation_hit",
                severity="critical",
                label="무효화 조건 발생",
            )
        )

    # 5. Phase 11a: 재검토 트리거 감지
    if next_review_trigger and layer_snapshot is not None:
        triggers.extend(
            _evaluate_invalidation_triggers(
                ticker=ticker,
                current_price=current_price,
                raws=[next_review_trigger],
                snap=layer_snapshot,
                dedup_keys=dedup_keys,
                trigger_type="review_trigger_hit",
                severity="warning",
                label="재검토 트리거",
            )
        )

    return triggers


def _evaluate_invalidation_triggers(
    *,
    ticker: str,
    current_price: float,
    raws: Sequence[str],
    snap: LayerSnapshot,
    dedup_keys: set[str] | None,
    trigger_type: str,
    severity: str,
    label: str,
) -> list[AlertTrigger]:
    """파싱 → 평가 → 중복 방지까지 처리해 트리거 리스트 반환."""
    parse_result = parse_conditions(raws)
    out: list[AlertTrigger] = []
    for cond in parse_result.parsed:
        if not evaluate_condition(cond, snap):
            continue
        key = f"{trigger_type}:{ticker}:{cond.raw}"
        if dedup_keys is not None:
            if key in dedup_keys:
                continue
            dedup_keys.add(key)
        out.append(
            AlertTrigger(
                ticker=ticker,
                trigger_type=trigger_type,
                severity=severity,
                message=f"{ticker} {label}: \"{cond.raw}\" ({_describe_actual(cond, snap)})",
                current_price=current_price,
                reference_price=None,
            )
        )
    return out


def _describe_actual(cond: ParsedCondition, snap: LayerSnapshot) -> str:
    """조건 발화 시 현재 값을 한 줄로 설명."""
    ind = cond.indicator
    if ind == "rsi" and snap.rsi is not None:
        return f"RSI={snap.rsi:.1f}"
    if ind == "sma_20" and snap.sma_20 is not None:
        return f"close={snap.close:.2f} vs SMA20={snap.sma_20:.2f}"
    if ind == "sma_50" and snap.sma_50 is not None:
        return f"close={snap.close:.2f} vs SMA50={snap.sma_50:.2f}"
    if ind == "sma_200" and snap.sma_200 is not None:
        return f"close={snap.close:.2f} vs SMA200={snap.sma_200:.2f}"
    if ind == "low_52w" and snap.low_52w is not None:
        return f"close={snap.close:.2f} < 52w low {snap.low_52w:.2f}"
    if ind == "high_52w" and snap.high_52w is not None:
        return f"close={snap.close:.2f} > 52w high {snap.high_52w:.2f}"
    if ind == "f_score" and snap.f_score is not None:
        return f"F-Score={snap.f_score}"
    if ind == "macd_signal":
        return "MACD 히스토그램 부호 반전"
    if ind == "sector_per_premium" and snap.sector_per_premium_pct is not None:
        return f"섹터 PER 프리미엄={snap.sector_per_premium_pct:.1f}%"
    return f"현재가 {snap.close:.2f}"


def build_layer_snapshot(
    layers: dict,
    current_price: float,
    close_history: Sequence[float] | None = None,
) -> LayerSnapshot:
    """Phase 11a: 레이어 결과 + 최근 종가 이력 → LayerSnapshot.

    순수 함수. layers dict에서 각 Layer DTO(또는 dict)를 꺼내 필요 지표만 모은다.
    close_history가 주어지면 SMA20/50/200, MACD 히스토그램을 즉석 계산.
    """
    rsi = _layer_attr(layers.get("layer3"), "rsi")
    high_52w = _layer_metric(layers.get("layer3"), "high_52w")
    low_52w = _layer_metric(layers.get("layer3"), "low_52w")
    f_score = _layer_attr(layers.get("layer1"), "f_score")
    sector_per_premium = _layer_attr(layers.get("layer2"), "sector_per_premium")

    sma_20, sma_50, sma_200, macd_hist, macd_hist_prev = _compute_history_snapshots(
        close_history,
    )

    return LayerSnapshot(
        rsi=float(rsi) if rsi is not None else None,
        macd_hist=macd_hist,
        macd_hist_prev=macd_hist_prev,
        sma_20=sma_20,
        sma_50=sma_50,
        sma_200=sma_200,
        high_52w=float(high_52w) if high_52w is not None else None,
        low_52w=float(low_52w) if low_52w is not None else None,
        f_score=int(f_score) if f_score is not None else None,
        sector_per_premium_pct=float(sector_per_premium) if sector_per_premium is not None else None,
        close=float(current_price),
    )


def _layer_attr(layer, name: str):
    if layer is None:
        return None
    if isinstance(layer, dict):
        return layer.get(name)
    return getattr(layer, name, None)


def _layer_metric(layer, key: str):
    if layer is None:
        return None
    metrics = layer.get("metrics") if isinstance(layer, dict) else getattr(layer, "metrics", None)
    if not metrics:
        return None
    return metrics.get(key)


def _compute_history_snapshots(
    close_history: Sequence[float] | None,
) -> tuple[float | None, float | None, float | None, float | None, float | None]:
    """최근 종가 이력 → (sma20, sma50, sma200, macd_hist, macd_hist_prev).

    close_history는 오름차순(오래된 → 최근). 부족하면 해당 값은 None.
    """
    if not close_history:
        return None, None, None, None, None

    closes = list(close_history)
    n = len(closes)

    def _sma(window: int) -> float | None:
        if n < window:
            return None
        return sum(closes[-window:]) / window

    sma_20 = _sma(20)
    sma_50 = _sma(50)
    sma_200 = _sma(200)

    macd_hist, macd_hist_prev = _compute_macd_hist(closes)

    return sma_20, sma_50, sma_200, macd_hist, macd_hist_prev


def _compute_macd_hist(
    closes: Sequence[float],
) -> tuple[float | None, float | None]:
    """EMA 기반 MACD histogram의 최근 2개 값 계산. 부족하면 None 튜플."""
    if len(closes) < 35:
        return None, None

    def _ema(values: Sequence[float], span: int) -> list[float]:
        k = 2 / (span + 1)
        out: list[float] = []
        ema = values[0]
        for v in values:
            ema = v * k + ema * (1 - k)
            out.append(ema)
        return out

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    signal = _ema(macd_line, 9)
    hist = [m - s for m, s in zip(macd_line, signal)]
    if len(hist) < 2:
        return None, None
    return float(hist[-1]), float(hist[-2])


def evaluate_alerts_batch(
    entries: list[dict],
    *,
    dedup_keys: set[str] | None = None,
) -> list[AlertTrigger]:
    """여러 종목 일괄 평가.

    entries: 각 원소는 {
        "ticker": str, "current_price": float, "previous_price": float|None,
        "execution_guide": dict|ExecutionGuide,
        "invalidation_conditions": list|None,
        "next_review_trigger": str|None,
        "layer_snapshot": LayerSnapshot|None,
    }
    dedup_keys: 배치 전체에서 공유되는 일일 중복 방지 set. 호출자가 관리.
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
                next_review_trigger=e.get("next_review_trigger"),
                layer_snapshot=e.get("layer_snapshot"),
                dedup_keys=dedup_keys,
            )
            all_triggers.extend(triggers)
        except Exception as exc:
            logger.warning("alert 평가 실패 (%s): %s", e.get("ticker"), exc)
    return all_triggers


def evaluate_catalyst_alerts(
    ticker: str,
    current_price: float,
    catalysts: Sequence[UpcomingCatalyst],
) -> list[AlertTrigger]:
    """Phase 11b: 임박 촉매 기반 알림.

    - earnings_imminent: days_until ∈ {1, 3}
    - ex_dividend_imminent: days_until == 1
    - fomc_imminent: days_until ∈ {1, 3} (info)
    """
    out: list[AlertTrigger] = []
    if current_price <= 0 or not catalysts:
        return out

    for cat in catalysts:
        if cat.kind == "earnings" and cat.days_until in (1, 3):
            out.append(
                AlertTrigger(
                    ticker=ticker,
                    trigger_type="earnings_imminent",
                    severity="info",
                    message=(
                        f"{ticker} 실적 발표 D-{cat.days_until} "
                        f"({cat.event_date.isoformat()})"
                    ),
                    current_price=current_price,
                    reference_price=None,
                )
            )
        elif cat.kind == "ex_dividend" and cat.days_until == 1:
            out.append(
                AlertTrigger(
                    ticker=ticker,
                    trigger_type="ex_dividend_imminent",
                    severity="info",
                    message=(
                        f"{ticker} 배당락 D-1 ({cat.event_date.isoformat()})"
                    ),
                    current_price=current_price,
                    reference_price=None,
                )
            )
        elif cat.kind == "fomc" and cat.days_until in (1, 3):
            out.append(
                AlertTrigger(
                    ticker=ticker,
                    trigger_type="fomc_imminent",
                    severity="info",
                    message=(
                        f"{ticker} FOMC D-{cat.days_until} "
                        f"({cat.event_date.isoformat()})"
                    ),
                    current_price=current_price,
                    reference_price=None,
                )
            )
    return out


def format_catalyst_block(
    items: Sequence[tuple[str, Sequence[UpcomingCatalyst]]],
    max_items: int = 20,
) -> str:
    """Phase 11b: 임박 촉매 텔레그램 블록 포맷.

    items: [(ticker, catalysts), ...] — 파이프라인이 종목 단위로 모은 촉매 리스트.
    """
    rows: list[tuple[str, UpcomingCatalyst]] = []
    for ticker, cats in items:
        for cat in cats:
            if 0 <= cat.days_until <= 7:
                rows.append((ticker, cat))
    if not rows:
        return ""

    rows.sort(key=lambda r: (r[1].days_until, r[0]))

    lines = [f"📅 임박 촉매 ({len(rows)}건)"]
    for ticker, cat in rows[:max_items]:
        lines.append(
            f"• {ticker} {cat.label} ({cat.event_date.isoformat()})"
        )
    if len(rows) > max_items:
        lines.append(f"...외 {len(rows) - max_items}건")
    return "\n".join(lines)


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
