"""예측 만기 평가 + 정확도 점수 계산."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id, id_to_date
from src.db.models import FactDailyPrice, FactDeepDiveForecast
from src.db.repository import DeepDiveRepository
from src.deepdive.schemas import ForecastAccuracy

logger = logging.getLogger(__name__)

HORIZON_DAYS: dict[str, int] = {"1M": 30, "3M": 90, "6M": 180}


def evaluate_matured_forecasts(
    session: Session,
    as_of_date: date,
) -> int:
    """만기 도래 예측 찾아서 actual_price/hit_range 업데이트. 반환: 업데이트 건수."""
    matured = DeepDiveRepository.get_matured_forecasts(session, as_of_date)
    if not matured:
        return 0

    count = 0
    for f in matured:
        forecast_date = id_to_date(f.date_id)
        maturity_date = forecast_date + timedelta(
            days=HORIZON_DAYS.get(f.horizon, 30),
        )
        result = _get_actual_price_at_date(session, f.stock_id, maturity_date)
        if result is None:
            continue

        actual_price, actual_date = result
        hit = (
            f.price_low is not None
            and f.price_high is not None
            and f.price_low <= actual_price <= f.price_high
        )
        DeepDiveRepository.update_forecast_actual(
            session, f.forecast_id, actual_price, actual_date, hit,
        )
        count += 1

    return count


def _get_actual_price_at_date(
    session: Session,
    stock_id: int,
    target_date: date,
    max_lookback_days: int = 5,
) -> tuple[float, date] | None:
    """만기일 종가 조회. 비거래일이면 5일 이내 직전 거래일."""
    target_id = date_to_id(target_date)
    earliest_date = target_date - timedelta(days=max_lookback_days)
    earliest_id = date_to_id(earliest_date)

    stmt = (
        select(FactDailyPrice)
        .where(
            FactDailyPrice.stock_id == stock_id,
            FactDailyPrice.date_id <= target_id,
            FactDailyPrice.date_id >= earliest_id,
        )
        .order_by(FactDailyPrice.date_id.desc())
        .limit(1)
    )
    price = session.execute(stmt).scalar_one_or_none()
    if price is None:
        return None
    return float(price.close), id_to_date(price.date_id)


def compute_accuracy_scores(
    forecasts: list[FactDeepDiveForecast],
) -> list[ForecastAccuracy]:
    """평가 완료된 예측 → 종목별 정확도 점수."""
    by_ticker: dict[str, list[FactDeepDiveForecast]] = defaultdict(list)
    for f in forecasts:
        by_ticker[f.ticker].append(f)

    results = []
    for ticker, ticker_forecasts in sorted(by_ticker.items()):
        results.append(_score_single_ticker(ticker, ticker_forecasts))
    return results


def _score_single_ticker(
    ticker: str,
    ticker_forecasts: list[FactDeepDiveForecast],
) -> ForecastAccuracy:
    """단일 종목 정확도 계산."""
    total = len(ticker_forecasts)
    if total == 0:
        return ForecastAccuracy(
            ticker=ticker, total_evaluated=0, hit_count=0, hit_rate=0.0,
            direction_correct=0, direction_accuracy=0.0, overall_score=0.0,
        )

    hit_count = sum(1 for f in ticker_forecasts if f.hit_range is True)
    hit_rate = hit_count / total

    # 방향 정확도: BASE midpoint 기준
    base_midpoints = _build_base_midpoints(ticker_forecasts)
    direction_correct = 0
    direction_total = 0
    for f in ticker_forecasts:
        mid = base_midpoints.get((f.report_id, f.horizon))
        if f.actual_price is None:
            continue
        actual = float(f.actual_price)
        if f.scenario == "BASE":
            if f.hit_range is True:
                direction_correct += 1
            direction_total += 1
        elif mid is not None:
            direction_total += 1
            if f.scenario == "BULL" and actual > mid:
                direction_correct += 1
            elif f.scenario == "BEAR" and actual < mid:
                direction_correct += 1

    direction_accuracy = direction_correct / direction_total if direction_total else 0.0
    overall_score = hit_rate * 0.6 + direction_accuracy * 0.4

    by_horizon = _group_by_key(ticker_forecasts, "horizon")
    by_scenario = _group_by_key(ticker_forecasts, "scenario")

    return ForecastAccuracy(
        ticker=ticker,
        total_evaluated=total,
        hit_count=hit_count,
        hit_rate=hit_rate,
        direction_correct=direction_correct,
        direction_accuracy=direction_accuracy,
        overall_score=overall_score,
        by_horizon=by_horizon,
        by_scenario=by_scenario,
    )


def _build_base_midpoints(
    forecasts: list[FactDeepDiveForecast],
) -> dict[tuple[int, str], float]:
    """report_id+horizon별 BASE 시나리오 midpoint."""
    midpoints: dict[tuple[int, str], float] = {}
    for f in forecasts:
        if f.scenario == "BASE" and f.price_low is not None and f.price_high is not None:
            midpoints[(f.report_id, f.horizon)] = (
                float(f.price_low) + float(f.price_high)
            ) / 2
    return midpoints


def _group_by_key(
    forecasts: list[FactDeepDiveForecast], attr: str,
) -> dict[str, dict]:
    """속성 기준 그룹별 hit_rate/count 집계."""
    groups: dict[str, list[FactDeepDiveForecast]] = defaultdict(list)
    for f in forecasts:
        groups[getattr(f, attr)].append(f)

    result = {}
    for key, group in sorted(groups.items()):
        count = len(group)
        hits = sum(1 for f in group if f.hit_range is True)
        result[key] = {
            "hit_rate": hits / count if count else 0.0,
            "count": count,
        }
    return result


# ──────────────────────────────────────────
# Phase 9: 과거 정확도 기반 EV 디스카운트
# ──────────────────────────────────────────


def get_historical_hit_rates(
    session: Session,
    ticker: str,
    min_samples: int = 10,
) -> dict[str, float]:
    """종목의 horizon별 과거 hit_rate 조회.

    min_samples 미만이면 해당 horizon 생략.

    Returns:
        {"1M": 0.62, "3M": 0.45, ...} — 샘플 충분한 것만.
    """
    forecasts = DeepDiveRepository.get_evaluated_forecasts_by_ticker(session, ticker)
    if not forecasts:
        return {}

    by_horizon: dict[str, list] = defaultdict(list)
    for f in forecasts:
        if f.hit_range is not None:  # 평가된 것만
            by_horizon[f.horizon].append(f)

    result: dict[str, float] = {}
    for horizon, items in by_horizon.items():
        if len(items) < min_samples:
            continue
        hits = sum(1 for f in items if f.hit_range is True)
        result[horizon] = hits / len(items)
    return result


def apply_hit_rate_discount(
    ev_pct: dict,
    hit_rates: dict[str, float],
    floor: float = 0.30,
) -> dict:
    """EV에 과거 hit_rate를 가중 (미래 예측 캘리브레이션).

    hit_rates가 없거나 해당 horizon 데이터 없으면 원본 유지.
    hit_rate 낮아도 floor(0.30) 이상으로 바닥 설정 — 과도한 디스카운트 방지.

    Args:
        ev_pct: {"1M": 3.2, ...}
        hit_rates: {"1M": 0.55, ...}
        floor: 최소 적용 가중치

    Returns:
        디스카운트 적용된 ev dict
    """
    if not hit_rates:
        return dict(ev_pct)

    result: dict = {}
    for horizon, ev in ev_pct.items():
        if ev is None:
            result[horizon] = None
            continue
        rate = hit_rates.get(horizon)
        if rate is None:
            result[horizon] = ev
        else:
            weight = max(rate, floor)
            result[horizon] = round(ev * weight, 2)
    return result
