"""일일 변경 감지 — 이전 분석 대비 변경점 추출."""

from __future__ import annotations

import json
import logging

from src.db.models import FactDeepDiveForecast, FactDeepDiveReport
from src.deepdive.schemas import AIResult, ChangeRecord

logger = logging.getLogger(__name__)


def detect_changes(
    current_ai_result: AIResult,
    current_layers: dict,
    current_forecasts: list | None,
    previous_report: FactDeepDiveReport | None,
    previous_forecasts: list[FactDeepDiveForecast] | None,
) -> list[ChangeRecord]:
    """현재 분석 결과와 이전 리포트 비교. 반환: 변경 목록."""
    if previous_report is None:
        return []

    prev_data = _extract_previous_data(previous_report)
    if prev_data is None:
        return []

    changes: list[ChangeRecord] = []

    # 1. 액션 등급 + 확신도 변경
    changes.extend(_detect_action_change(
        current_ai_result.action_grade,
        prev_data.get("action_grade"),
        current_ai_result.conviction,
        prev_data.get("conviction"),
    ))

    # 2. 시나리오 확률 변화
    if current_forecasts and previous_forecasts:
        changes.extend(_detect_probability_shifts(
            current_forecasts, previous_forecasts,
        ))

    # 3. 신규 리스크 이벤트
    current_risks = _get_risk_events(current_layers)
    prev_risks = prev_data.get("risk_events", [])
    changes.extend(_detect_new_risks(current_risks, prev_risks))

    # 4. 트리거 도달
    prev_trigger = prev_data.get("next_review_trigger")
    changes.extend(_detect_trigger_hits(
        prev_trigger, current_layers, current_ai_result,
    ))

    return changes


def _detect_action_change(
    current_grade: str,
    prev_grade: str | None,
    current_conviction: int,
    prev_conviction: int | None,
) -> list[ChangeRecord]:
    """액션 등급 변경 + 확신도 변화 감지."""
    changes: list[ChangeRecord] = []

    if prev_grade and current_grade != prev_grade:
        changes.append(ChangeRecord(
            change_type="action_changed",
            description=f"액션 등급 변경: {prev_grade} -> {current_grade}",
            severity="critical",
        ))

    if prev_conviction is not None and abs(current_conviction - prev_conviction) >= 2:
        direction = "상승" if current_conviction > prev_conviction else "하락"
        changes.append(ChangeRecord(
            change_type="conviction_shift",
            description=f"확신도 {direction}: {prev_conviction} -> {current_conviction}",
            severity="warning",
        ))

    return changes


def _detect_probability_shifts(
    current_forecasts: list,
    previous_forecasts: list[FactDeepDiveForecast],
    threshold_pp: float = 10.0,
) -> list[ChangeRecord]:
    """시나리오 확률 10%p 이상 변화 감지."""
    changes: list[ChangeRecord] = []

    prev_map: dict[tuple[str, str], float] = {}
    for f in previous_forecasts:
        if f.probability is not None:
            prev_map[(f.horizon, f.scenario)] = float(f.probability)

    for f in current_forecasts:
        horizon = f.get("horizon", "") if isinstance(f, dict) else getattr(f, "horizon", "")
        scenario = f.get("scenario", "") if isinstance(f, dict) else getattr(f, "scenario", "")
        prob = f.get("probability", 0) if isinstance(f, dict) else getattr(f, "probability", 0)

        key = (horizon, scenario)
        if key in prev_map:
            diff_pp = abs(float(prob) - prev_map[key]) * 100
            if diff_pp >= threshold_pp:
                changes.append(ChangeRecord(
                    change_type="probability_shift",
                    description=(
                        f"{horizon} {scenario} 확률 변화: "
                        f"{prev_map[key]*100:.0f}% -> {float(prob)*100:.0f}% "
                        f"({diff_pp:+.0f}pp)"
                    ),
                    severity="info",
                ))

    return changes


def _detect_new_risks(
    current_risk_events: list[str],
    previous_risk_events: list[str],
) -> list[ChangeRecord]:
    """신규 리스크 이벤트 감지."""
    prev_set = set(previous_risk_events)
    changes: list[ChangeRecord] = []
    for risk in current_risk_events:
        if risk not in prev_set:
            changes.append(ChangeRecord(
                change_type="new_risk",
                description=f"신규 리스크: {risk}",
                severity="warning",
            ))
    return changes


def _detect_trigger_hits(
    previous_trigger: str | None,
    current_layers: dict,
    current_ai_result: AIResult,
) -> list[ChangeRecord]:
    """이전 next_review_trigger 조건 도달 감지."""
    if not previous_trigger:
        return []

    trigger_lower = previous_trigger.lower()
    hit = False

    # RSI 관련 트리거
    layer3 = current_layers.get("layer3")
    if layer3 is not None:
        rsi = getattr(layer3, "rsi", None)
        if rsi is not None:
            if "rsi" in trigger_lower:
                if ("30" in trigger_lower or "oversold" in trigger_lower) and rsi <= 30:
                    hit = True
                elif ("70" in trigger_lower or "overbought" in trigger_lower) and rsi >= 70:
                    hit = True

    # 지지/저항 트리거
    if layer3 is not None and not hit:
        support = getattr(layer3, "nearest_support", None)
        resistance = getattr(layer3, "nearest_resistance", None)
        if support and ("support" in trigger_lower or "지지" in trigger_lower):
            hit = True
        if resistance and ("resistance" in trigger_lower or "저항" in trigger_lower):
            hit = True

    if hit:
        return [ChangeRecord(
            change_type="trigger_hit",
            description=f"이전 리뷰 트리거 도달: {previous_trigger}",
            severity="critical",
        )]
    return []


def _extract_previous_data(report: FactDeepDiveReport) -> dict | None:
    """이전 리포트의 report_json에서 비교에 필요한 데이터 추출."""
    try:
        data = json.loads(report.report_json)
    except (json.JSONDecodeError, TypeError):
        return None

    ai_result = data.get("ai_result", {})
    layers = data.get("layers", {})
    layer5 = layers.get("layer5", {})

    return {
        "action_grade": ai_result.get("action_grade") or report.action_grade,
        "conviction": ai_result.get("conviction") or report.conviction,
        "risk_events": layer5.get("risk_events", []),
        "next_review_trigger": ai_result.get("next_review_trigger"),
    }


def _get_risk_events(current_layers: dict) -> list[str]:
    """현재 레이어에서 리스크 이벤트 추출."""
    layer5 = current_layers.get("layer5")
    if layer5 is None:
        return []
    return getattr(layer5, "risk_events", []) or []
