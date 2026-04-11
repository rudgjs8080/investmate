"""Deep Dive 실행 가이드 계산 엔진.

AI 출력과 레이어 데이터를 결정론적 매매 가이드로 변환:
- buy_zone / stop_loss / targets
- expected value / risk-reward ratio
- 포트폴리오 제약 기반 제안 포지션 비중

모든 계산은 순수 함수 — AI 없이도 재현 가능하고 단위 테스트 가능하다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.deepdive.schemas import AIResult

logger = logging.getLogger(__name__)


# 시나리오 구조: {"1M": {"base": {"prob": 0.5, "low": x, "high": y}, "bull": {...}, "bear": {...}}, ...}
_HORIZONS = ("1M", "3M", "6M")
_SCENARIOS = ("base", "bull", "bear")


@dataclass(frozen=True)
class ExecutionGuide:
    """정량 매매 가이드. report_json.execution_guide로 저장된다."""

    current_price: float
    buy_zone_low: float
    buy_zone_high: float
    buy_zone_status: str  # "wait" | "in_zone" | "above_zone" | "below_zone"

    stop_loss: float
    stop_loss_source: str  # "ai" | "atr" | "support" | "trailing"
    stop_loss_pct: float   # 현재가 대비 -% (음수)

    target_1m: float | None
    target_3m: float | None
    target_6m: float | None

    expected_value_pct: dict  # {"1M": 2.3, "3M": 6.1, "6M": 11.4}
    risk_reward_ratio: float | None
    risk_reward_label: str    # "favorable" | "neutral" | "unfavorable"

    suggested_position_pct: float  # 포트폴리오 제안 비중 % (0.0~MAX_STOCK_PCT*100)
    position_rationale: str
    portfolio_fit_warnings: tuple[str, ...] = field(default_factory=tuple)

    action_hint: str = ""  # "now" | "wait_pullback" | "avoid" | "hold" — UI 조언


# ──────────────────────────────────────────
# Public API
# ──────────────────────────────────────────


def compute_execution_guide(
    *,
    current_price: float,
    ai_result: AIResult | None,
    layers: dict,
    scenarios: dict | None,
    sector: str | None,
    settings,
    existing_sector_weight: float = 0.0,
    existing_ticker_weight: float = 0.0,
) -> ExecutionGuide | None:
    """실행 가이드 계산 진입점.

    Args:
        current_price: 최신 종가
        ai_result: Phase 4 확장 AIResult (key_levels 포함)
        layers: {"layer1": ..., "layer3": TechnicalProfile, ...}
        scenarios: Synthesizer가 내놓은 raw scenarios dict (CLIDebateResult.scenarios)
        sector: 종목 섹터명
        settings: get_settings() 결과 (max_single_stock_pct 등)
        existing_sector_weight: 해당 섹터의 기존 포트폴리오 비중 (0~1)
        existing_ticker_weight: 이 종목의 기존 포트폴리오 비중 (0~1)

    Returns:
        ExecutionGuide. 현재가 <= 0 이면 None.
    """
    if current_price <= 0:
        logger.warning("execution_guide: current_price=%s 유효하지 않음", current_price)
        return None

    layer3 = layers.get("layer3") if layers else None
    support = _layer3_attr(layer3, "nearest_support")
    resistance = _layer3_attr(layer3, "nearest_resistance")
    rsi = _layer3_attr(layer3, "rsi")
    atr_14 = _layer3_metric(layer3, "atr_14")

    # AI key_levels가 있으면 우선 (Phase 4 복구)
    if ai_result:
        support = ai_result.support_price or support
        resistance = ai_result.resistance_price or resistance

    # 1) Buy Zone
    buy_low, buy_high, zone_status = _compute_buy_zone(
        current_price, support, resistance, rsi,
    )

    # 2) Stop Loss (가장 보수적 = 가장 높은 가격 선택)
    stop_loss, stop_source = _compute_stop_loss(
        current_price=current_price,
        ai_stop=ai_result.stop_loss if ai_result else None,
        support=support,
        atr_14=atr_14,
        atr_multiplier=float(getattr(settings, "atr_stop_multiplier", 2.0)),
        trailing_stop_pct=float(getattr(settings, "portfolio_trailing_stop_pct", 10.0)),
    )
    stop_loss_pct = round((stop_loss - current_price) / current_price * 100, 2)

    # 3) Targets (시나리오 확률 가중 평균)
    target_1m = _probability_weighted_target(scenarios, "1M")
    target_3m = _probability_weighted_target(scenarios, "3M")
    target_6m = _probability_weighted_target(scenarios, "6M")

    # 4) Expected Value
    ev = {
        "1M": _ev_pct(current_price, target_1m),
        "3M": _ev_pct(current_price, target_3m),
        "6M": _ev_pct(current_price, target_6m),
    }

    # 5) Risk/Reward (3M 기준)
    rr = None
    rr_label = "neutral"
    if target_3m is not None and stop_loss < current_price:
        upside = target_3m - current_price
        downside = current_price - stop_loss
        if downside > 0 and upside > 0:
            rr = round(upside / downside, 2)
            rr_label = _label_rr(rr)
        elif upside <= 0:
            rr_label = "unfavorable"

    # 6) Suggested Position Size
    conviction = ai_result.conviction if ai_result else 5
    action = ai_result.action_grade if ai_result else "HOLD"
    pos_pct, pos_rationale = _suggest_position_pct(
        conviction=conviction,
        action=action,
        rr=rr,
        max_stock_pct=float(getattr(settings, "max_single_stock_pct", 0.10)),
    )

    # 7) Portfolio Fit Warnings
    warnings = _portfolio_warnings(
        new_weight=pos_pct / 100.0,
        existing_ticker_weight=existing_ticker_weight,
        existing_sector_weight=existing_sector_weight,
        max_stock_pct=float(getattr(settings, "max_single_stock_pct", 0.10)),
        max_sector_pct=float(getattr(settings, "max_sector_weight_pct", 0.30)),
        sector=sector,
    )

    # 8) Action Hint
    hint = _action_hint(
        action=action,
        zone_status=zone_status,
        rr_label=rr_label,
        has_warnings=bool(warnings),
    )

    return ExecutionGuide(
        current_price=round(current_price, 2),
        buy_zone_low=round(buy_low, 2),
        buy_zone_high=round(buy_high, 2),
        buy_zone_status=zone_status,
        stop_loss=round(stop_loss, 2),
        stop_loss_source=stop_source,
        stop_loss_pct=stop_loss_pct,
        target_1m=round(target_1m, 2) if target_1m else None,
        target_3m=round(target_3m, 2) if target_3m else None,
        target_6m=round(target_6m, 2) if target_6m else None,
        expected_value_pct=ev,
        risk_reward_ratio=rr,
        risk_reward_label=rr_label,
        suggested_position_pct=round(pos_pct, 2),
        position_rationale=pos_rationale,
        portfolio_fit_warnings=tuple(warnings),
        action_hint=hint,
    )


# ──────────────────────────────────────────
# Buy Zone
# ──────────────────────────────────────────


def _compute_buy_zone(
    current_price: float,
    support: float | None,
    resistance: float | None,
    rsi: float | None,
) -> tuple[float, float, str]:
    """진입 존 계산.

    - 하단: max(현재가 × 0.97, support × 1.01) → 현재가보다 살짝 낮거나 지지선 바로 위
    - 상단: min(현재가 × 1.01, resistance × 0.98) → 현재가 근처, 저항 아래
    - 저항이 현재가보다 낮으면 → "below_zone" (이미 돌파)
    - RSI > 70 이면 → "wait" (과매수, zone 범위 유지)
    """
    low = current_price * 0.97
    if support is not None and support > 0:
        low = max(low, support * 1.01)

    high = current_price * 1.01
    if resistance is not None and resistance > 0:
        high = min(high, resistance * 0.98)

    # 역전 방지: 하단이 상단보다 크면 현재가 ±1%로 보정
    if low > high:
        low = current_price * 0.99
        high = current_price * 1.01

    if rsi is not None and rsi >= 70:
        status = "wait"  # 과매수, 풀백 대기
    elif low <= current_price <= high:
        status = "in_zone"
    elif current_price < low:
        status = "below_zone"  # 더 싸져서 zone 이탈 (매수 기회일 수도)
    else:
        status = "above_zone"  # zone 위로 이미 이탈

    return low, high, status


# ──────────────────────────────────────────
# Stop Loss
# ──────────────────────────────────────────


def _compute_stop_loss(
    *,
    current_price: float,
    ai_stop: float | None,
    support: float | None,
    atr_14: float | None,
    atr_multiplier: float,
    trailing_stop_pct: float,
) -> tuple[float, str]:
    """손절 레벨 선택.

    후보:
    - AI 제시 stop_loss
    - Support - ATR × 0.5 (지지선 아래 버퍼)
    - 현재가 × (1 - trailing_stop_pct/100)  (기본 -10%)
    - 현재가 - ATR × multiplier (default 2.0)

    가장 **보수적** = 현재가에 가장 가까운 손절가(가장 높은 값).
    """
    candidates: list[tuple[float, str]] = []

    if ai_stop is not None and 0 < ai_stop < current_price:
        candidates.append((ai_stop, "ai"))

    if support is not None and support > 0:
        buffer = (atr_14 * 0.5) if atr_14 else (current_price * 0.01)
        support_stop = support - buffer
        if 0 < support_stop < current_price:
            candidates.append((support_stop, "support"))

    trailing = current_price * (1.0 - trailing_stop_pct / 100.0)
    if trailing > 0:
        candidates.append((trailing, "trailing"))

    if atr_14 is not None and atr_14 > 0:
        atr_stop = current_price - atr_14 * atr_multiplier
        if atr_stop > 0:
            candidates.append((atr_stop, "atr"))

    if not candidates:
        # fallback: -8%
        return current_price * 0.92, "trailing"

    # 가장 높은 가격(=가장 작은 낙폭) 선택 = 가장 보수적
    best = max(candidates, key=lambda x: x[0])
    return best


# ──────────────────────────────────────────
# Targets (시나리오 확률 가중)
# ──────────────────────────────────────────


def _probability_weighted_target(scenarios: dict | None, horizon: str) -> float | None:
    """특정 horizon에 대해 Σ(prob × midpoint)."""
    if not scenarios or horizon not in scenarios:
        return None

    h = scenarios[horizon]
    if not isinstance(h, dict):
        return None

    total_prob = 0.0
    weighted = 0.0
    for scen in _SCENARIOS:
        s = h.get(scen)
        if not isinstance(s, dict):
            continue
        prob = s.get("prob")
        low = s.get("low")
        high = s.get("high")
        try:
            prob_f = float(prob)
            low_f = float(low)
            high_f = float(high)
        except (TypeError, ValueError):
            continue
        if prob_f <= 0 or low_f <= 0 or high_f <= 0:
            continue
        mid = (low_f + high_f) / 2.0
        weighted += prob_f * mid
        total_prob += prob_f

    if total_prob < 0.5:  # 확률 합이 너무 낮으면 신뢰 불가
        return None

    return weighted / total_prob if total_prob > 0 else None


def _ev_pct(current: float, target: float | None) -> float | None:
    if target is None or current <= 0:
        return None
    return round((target - current) / current * 100, 2)


# ──────────────────────────────────────────
# Risk/Reward label
# ──────────────────────────────────────────


def _label_rr(rr: float) -> str:
    if rr >= 2.5:
        return "favorable"
    if rr >= 1.5:
        return "neutral"
    return "unfavorable"


# ──────────────────────────────────────────
# Position Sizing
# ──────────────────────────────────────────


def _suggest_position_pct(
    *,
    conviction: int,
    action: str,
    rr: float | None,
    max_stock_pct: float,
) -> tuple[float, str]:
    """포지션 비중 제안.

    base = max_stock_pct × (conviction/10)²  (비선형 — 확신도 높을 때만 크게)
    × RR 부스트 (favorable=1.2, unfavorable=0.6)
    × action 스케일 (ADD=1.0, HOLD=기존 유지 암시 0.0 제안, TRIM=-1, EXIT=0)

    EXIT/TRIM은 0% 제안 (매도 신호).
    HOLD은 기존 비중 유지이므로 "신규" 제안은 0.
    """
    if action in ("EXIT", "TRIM"):
        return 0.0, f"{action}: 신규 매수 제안 없음"
    if action == "HOLD":
        return 0.0, "HOLD: 기존 비중 유지"

    from src.portfolio.position_sizer import sigmoid_tilt

    conv = max(1, min(10, conviction))
    tilt = sigmoid_tilt(conv)  # 0.3 ~ 1.8
    # 비선형 베이스: 확신도 10에서 max, 5에서 25% 수준
    base = max_stock_pct * (conv / 10.0) ** 2

    rr_boost = 1.0
    rr_note = ""
    if rr is not None:
        if rr >= 2.5:
            rr_boost = 1.2
            rr_note = f"R/R {rr:.1f} 우호"
        elif rr < 1.5:
            rr_boost = 0.6
            rr_note = f"R/R {rr:.1f} 비우호 축소"

    pos_pct = base * tilt * rr_boost * 100.0  # 퍼센트로
    pos_pct = min(pos_pct, max_stock_pct * 100.0)  # 상한 클립
    pos_pct = max(pos_pct, 0.0)

    rationale_parts = [f"conviction {conv}/10", f"sigmoid tilt ×{tilt:.2f}"]
    if rr_note:
        rationale_parts.append(rr_note)
    rationale_parts.append(f"상한 {max_stock_pct*100:.0f}%")

    return pos_pct, " · ".join(rationale_parts)


# ──────────────────────────────────────────
# Portfolio Fit
# ──────────────────────────────────────────


def _portfolio_warnings(
    *,
    new_weight: float,
    existing_ticker_weight: float,
    existing_sector_weight: float,
    max_stock_pct: float,
    max_sector_pct: float,
    sector: str | None,
) -> list[str]:
    warnings: list[str] = []

    total_ticker = existing_ticker_weight + new_weight
    if total_ticker > max_stock_pct:
        warnings.append(
            f"추가 후 종목 비중 {total_ticker*100:.1f}% > 상한 {max_stock_pct*100:.0f}%"
        )

    total_sector = existing_sector_weight + new_weight
    if total_sector > max_sector_pct:
        warnings.append(
            f"추가 후 {sector or '섹터'} 비중 {total_sector*100:.1f}% > 상한 {max_sector_pct*100:.0f}%"
        )

    return warnings


# ──────────────────────────────────────────
# Action Hint
# ──────────────────────────────────────────


def _action_hint(
    *,
    action: str,
    zone_status: str,
    rr_label: str,
    has_warnings: bool,
) -> str:
    if action in ("TRIM", "EXIT"):
        return "sell"
    if action == "HOLD":
        return "hold"

    # ADD
    if rr_label == "unfavorable" or has_warnings:
        return "avoid"
    if zone_status == "wait":
        return "wait_pullback"
    if zone_status == "in_zone":
        return "now"
    if zone_status == "below_zone":
        return "now"
    # above_zone → 저항 아래로 올 때까지 대기
    return "wait_pullback"


# ──────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────


def _layer3_attr(layer3, attr: str):
    """layer3가 pydantic 모델이든 dict든 동일하게 접근."""
    if layer3 is None:
        return None
    if hasattr(layer3, attr):
        return getattr(layer3, attr)
    if isinstance(layer3, dict):
        return layer3.get(attr)
    return None


def _layer3_metric(layer3, key: str):
    if layer3 is None:
        return None
    metrics = None
    if hasattr(layer3, "metrics"):
        metrics = getattr(layer3, "metrics")
    elif isinstance(layer3, dict):
        metrics = layer3.get("metrics")
    if not isinstance(metrics, dict):
        return None
    return metrics.get(key)


def guide_to_dict(guide: ExecutionGuide) -> dict:
    """report_json 저장용 dict 직렬화."""
    return {
        "current_price": guide.current_price,
        "buy_zone_low": guide.buy_zone_low,
        "buy_zone_high": guide.buy_zone_high,
        "buy_zone_status": guide.buy_zone_status,
        "stop_loss": guide.stop_loss,
        "stop_loss_source": guide.stop_loss_source,
        "stop_loss_pct": guide.stop_loss_pct,
        "target_1m": guide.target_1m,
        "target_3m": guide.target_3m,
        "target_6m": guide.target_6m,
        "expected_value_pct": dict(guide.expected_value_pct),
        "risk_reward_ratio": guide.risk_reward_ratio,
        "risk_reward_label": guide.risk_reward_label,
        "suggested_position_pct": guide.suggested_position_pct,
        "position_rationale": guide.position_rationale,
        "portfolio_fit_warnings": list(guide.portfolio_fit_warnings),
        "action_hint": guide.action_hint,
    }
