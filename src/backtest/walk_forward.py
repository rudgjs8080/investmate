"""Walk-Forward 백테스트 — 과적합 탐지를 위한 롤링 윈도우 검증."""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id, id_to_date
from src.db.models import FactDailyRecommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WindowResult:
    """단일 윈도우 결과."""

    train_start: date
    train_end: date
    test_start: date
    test_end: date
    is_sharpe: float  # in-sample
    oos_sharpe: float  # out-of-sample
    oos_return: float  # OOS 평균 수익률 %
    oos_win_rate: float  # OOS 승률 %
    n_recommendations: int


@dataclass(frozen=True)
class WalkForwardResult:
    """Walk-Forward 전체 결과."""

    windows: tuple[WindowResult, ...]
    avg_oos_sharpe: float
    avg_is_sharpe: float
    degradation_ratio: float  # oos/is (>0.7 양호)
    total_oos_recommendations: int
    avg_oos_return: float
    avg_oos_win_rate: float


def _get_recommendation_dates(session: Session) -> list[date]:
    """DB에서 추천이 존재하는 모든 날짜를 조회한다."""
    stmt = (
        select(FactDailyRecommendation.run_date_id)
        .distinct()
        .order_by(FactDailyRecommendation.run_date_id)
    )
    date_ids = session.execute(stmt).scalars().all()
    result = []
    for did in date_ids:
        try:
            result.append(id_to_date(did))
        except ValueError:
            continue
    return result


def _get_returns_for_period(
    session: Session, start: date, end: date,
) -> list[float]:
    """주어진 기간의 추천 20일 수익률 목록을 반환한다."""
    start_id = date_to_id(start)
    end_id = date_to_id(end)
    stmt = (
        select(FactDailyRecommendation.return_20d)
        .where(FactDailyRecommendation.run_date_id >= start_id)
        .where(FactDailyRecommendation.run_date_id <= end_id)
        .where(FactDailyRecommendation.return_20d.isnot(None))
    )
    rows = session.execute(stmt).scalars().all()
    return [float(r) for r in rows]


def _count_recommendations(session: Session, start: date, end: date) -> int:
    """주어진 기간의 추천 수를 반환한다."""
    start_id = date_to_id(start)
    end_id = date_to_id(end)
    stmt = (
        select(FactDailyRecommendation.recommendation_id)
        .where(FactDailyRecommendation.run_date_id >= start_id)
        .where(FactDailyRecommendation.run_date_id <= end_id)
    )
    return len(session.execute(stmt).scalars().all())


def _calculate_sharpe(returns: list[float], risk_free: float = 0.0) -> float:
    """샤프 비율 계산. 데이터 부족 시 0.0 반환."""
    if len(returns) < 2:
        return 0.0
    mean_r = statistics.mean(returns)
    std_r = statistics.stdev(returns)
    if std_r == 0:
        return 0.0
    raw_sharpe = (mean_r - risk_free) / std_r
    annualization_factor = math.sqrt(252 / 20)
    return round(raw_sharpe * annualization_factor, 3)


def _win_rate(returns: list[float]) -> float:
    """양수 수익 비율 (%). 빈 리스트면 0.0."""
    if not returns:
        return 0.0
    wins = sum(1 for v in returns if v > 0)
    return wins / len(returns) * 100


def _generate_windows(
    start_date: date,
    end_date: date,
    train_months: int,
    test_months: int,
) -> list[tuple[date, date, date, date]]:
    """롤링 윈도우 구간을 생성한다.

    Returns:
        [(train_start, train_end, test_start, test_end), ...]
    """
    windows: list[tuple[date, date, date, date]] = []
    current = start_date

    while True:
        train_start = current
        train_end = _add_months(train_start, train_months) - timedelta(days=1)
        test_start = train_end + timedelta(days=1)
        test_end = _add_months(test_start, test_months) - timedelta(days=1)

        if test_end > end_date:
            break

        windows.append((train_start, train_end, test_start, test_end))
        # 다음 윈도우: test_months만큼 슬라이드
        current = _add_months(current, test_months)

    return windows


def _add_months(d: date, months: int) -> date:
    """날짜에 월을 더한다."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    """해당 월의 일수를 반환한다."""
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - date(year, month, 1)).days


def run_walk_forward(
    session: Session,
    train_months: int = 6,
    test_months: int = 1,
    start_date: date | None = None,
    end_date: date | None = None,
) -> WalkForwardResult:
    """Walk-Forward 분석을 실행한다.

    Args:
        session: DB 세션.
        train_months: 훈련 기간 (월).
        test_months: 테스트 기간 (월).
        start_date: 분석 시작일 (None이면 DB 최초 추천일).
        end_date: 분석 종료일 (None이면 DB 최종 추천일).

    Returns:
        WalkForwardResult 전체 결과.
    """
    rec_dates = _get_recommendation_dates(session)
    if not rec_dates:
        return WalkForwardResult(
            windows=(),
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            degradation_ratio=0.0,
            total_oos_recommendations=0,
            avg_oos_return=0.0,
            avg_oos_win_rate=0.0,
        )

    actual_start = start_date if start_date else rec_dates[0]
    actual_end = end_date if end_date else rec_dates[-1]

    windows_spec = _generate_windows(
        actual_start, actual_end, train_months, test_months,
    )

    if not windows_spec:
        return WalkForwardResult(
            windows=(),
            avg_oos_sharpe=0.0,
            avg_is_sharpe=0.0,
            degradation_ratio=0.0,
            total_oos_recommendations=0,
            avg_oos_return=0.0,
            avg_oos_win_rate=0.0,
        )

    window_results: list[WindowResult] = []
    total_oos_recs = 0

    for train_start, train_end, test_start, test_end in windows_spec:
        is_returns = _get_returns_for_period(session, train_start, train_end)
        oos_returns = _get_returns_for_period(session, test_start, test_end)
        n_oos_recs = _count_recommendations(session, test_start, test_end)

        is_sharpe = _calculate_sharpe(is_returns)
        oos_sharpe = _calculate_sharpe(oos_returns)
        oos_return = statistics.mean(oos_returns) if oos_returns else 0.0
        oos_wr = _win_rate(oos_returns)

        window_results.append(WindowResult(
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            is_sharpe=is_sharpe,
            oos_sharpe=oos_sharpe,
            oos_return=oos_return,
            oos_win_rate=oos_wr,
            n_recommendations=n_oos_recs,
        ))
        total_oos_recs += n_oos_recs

    # 집계
    avg_is = statistics.mean(w.is_sharpe for w in window_results)
    avg_oos = statistics.mean(w.oos_sharpe for w in window_results)

    if avg_is != 0:
        degradation = avg_oos / avg_is
    else:
        degradation = 0.0 if avg_oos == 0 else 1.0

    oos_returns_all = [w.oos_return for w in window_results]
    oos_wr_all = [w.oos_win_rate for w in window_results]

    return WalkForwardResult(
        windows=tuple(window_results),
        avg_oos_sharpe=round(avg_oos, 3),
        avg_is_sharpe=round(avg_is, 3),
        degradation_ratio=round(degradation, 3),
        total_oos_recommendations=total_oos_recs,
        avg_oos_return=round(statistics.mean(oos_returns_all), 3) if oos_returns_all else 0.0,
        avg_oos_win_rate=round(statistics.mean(oos_wr_all), 1) if oos_wr_all else 0.0,
    )
