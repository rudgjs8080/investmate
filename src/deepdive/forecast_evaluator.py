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
