"""시나리오 예측 파싱 — Synthesizer JSON → ScenarioForecast 리스트."""

from __future__ import annotations

import logging

from src.deepdive.schemas import ScenarioForecast

logger = logging.getLogger(__name__)

HORIZONS = ("1M", "3M", "6M")
SCENARIOS = ("base", "bull", "bear")


def parse_scenarios(
    synth_parsed: dict, current_price: float,
) -> list[ScenarioForecast]:
    """Synthesizer JSON의 scenarios 필드 → ScenarioForecast 리스트.

    검증: probability 합계 ~1.0, price_low < price_high, 현재가 +-80%.
    반환: 최대 9개 (3 horizon x 3 scenario), 검증 실패 시 빈 리스트.
    """
    scenarios_raw = synth_parsed.get("scenarios")
    if not isinstance(scenarios_raw, dict):
        return []

    results: list[ScenarioForecast] = []
    for horizon in HORIZONS:
        h_data = scenarios_raw.get(horizon)
        if not isinstance(h_data, dict):
            continue

        for scenario in SCENARIOS:
            s_data = h_data.get(scenario)
            if not isinstance(s_data, dict):
                continue

            prob = _safe_float(s_data.get("prob"))
            low = _safe_float(s_data.get("low"))
            high = _safe_float(s_data.get("high"))
            trigger = s_data.get("trigger")

            if prob is None or low is None or high is None:
                continue
            if low > high:
                low, high = high, low
            # sanity: 현재가 +-80%
            if current_price > 0:
                floor = current_price * 0.2
                ceiling = current_price * 1.8
                if low < floor or high > ceiling:
                    continue

            prob = max(0.0, min(1.0, prob))

            results.append(ScenarioForecast(
                horizon=horizon,
                scenario=scenario.upper(),
                probability=round(prob, 3),
                price_low=round(low, 2),
                price_high=round(high, 2),
                trigger_condition=str(trigger)[:200] if trigger else None,
            ))

    return results


def _safe_float(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
