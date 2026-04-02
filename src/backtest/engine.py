"""백테스트 엔진 — 과거 추천 데이터 기반 성과 분석.

fact_daily_recommendations에 저장된 추천과 사후 수익률을 활용하여
알고리즘의 역사적 성과를 측정한다.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.risk_metrics import (
    calculate_calmar,
    calculate_max_drawdown,
    calculate_max_drawdown_days,
    calculate_omega,
    calculate_sharpe,
    calculate_sortino,
)
from src.config import get_settings
from src.db.helpers import date_to_id, id_to_date
from src.db.models import DimStock, FactDailyRecommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BacktestConfig:
    """백테스트 설정."""

    start_date: date
    end_date: date
    top_n: int = 10
    holdout_pct: float = 0.0  # 0.0~1.0, OOS 홀드아웃 비율


@dataclass(frozen=True)
class DailyResult:
    """일별 백테스트 결과."""

    run_date: date
    recommendation_count: int
    avg_return_1d: float | None = None
    avg_return_5d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None


@dataclass(frozen=True)
class HoldoutResult:
    """홀드아웃 분할 결과."""

    is_avg_return_20d: float | None = None
    is_win_rate_20d: float | None = None
    is_sharpe: float | None = None
    oos_avg_return_20d: float | None = None
    oos_win_rate_20d: float | None = None
    oos_sharpe: float | None = None
    is_count: int = 0
    oos_count: int = 0


@dataclass(frozen=True)
class BacktestResult:
    """전체 백테스트 결과."""

    config: BacktestConfig
    total_days: int
    total_recommendations: int
    avg_return_1d: float | None = None
    avg_return_5d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    win_rate_1d: float | None = None
    win_rate_5d: float | None = None
    win_rate_20d: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown: float | None = None
    best_pick: tuple[str, float, str] | None = None  # (ticker, return%, date)
    worst_pick: tuple[str, float, str] | None = None
    by_date: tuple[DailyResult, ...] = field(default_factory=tuple)
    # 확장 지표
    sortino_ratio: float | None = None
    calmar_ratio: float | None = None
    omega_ratio: float | None = None
    monthly_win_rate: float | None = None
    max_drawdown_days: int | None = None
    # 홀드아웃 결과 (holdout_pct > 0일 때)
    holdout: HoldoutResult | None = None


def _estimate_tx_cost(volume: int | None, price: float | None, base_bps: int = 20) -> float:
    """유동성 기반 거래비용을 추정한다 (bps).

    - 달러 거래량 > $10M: base_bps
    - $1M ~ $10M: base_bps + 5
    - < $1M: base_bps + 15
    """
    if volume is None or price is None or volume == 0:
        return base_bps + 15  # worst case

    dollar_volume = volume * price
    if dollar_volume >= 10_000_000:
        return base_bps
    elif dollar_volume >= 1_000_000:
        return base_bps + 5
    else:
        return base_bps + 15


class BacktestEngine:
    """과거 추천 데이터로 백테스트를 실행한다."""

    def run(self, session: Session, config: BacktestConfig) -> BacktestResult:
        """주어진 기간의 추천 데이터를 분석한다."""
        start_id = date_to_id(config.start_date)
        end_id = date_to_id(config.end_date)

        stmt = (
            select(FactDailyRecommendation, DimStock.ticker)
            .join(DimStock, FactDailyRecommendation.stock_id == DimStock.stock_id)
            .where(FactDailyRecommendation.run_date_id >= start_id)
            .where(FactDailyRecommendation.run_date_id <= end_id)
            .where(FactDailyRecommendation.rank <= config.top_n)
            .order_by(FactDailyRecommendation.run_date_id, FactDailyRecommendation.rank)
        )
        rows = session.execute(stmt).all()

        if not rows:
            return BacktestResult(
                config=config, total_days=0, total_recommendations=0,
            )

        settings = get_settings()

        # 일별 그룹핑
        by_date: dict[int, list[tuple]] = {}
        all_returns_1d: list[float] = []
        all_returns_5d: list[float] = []
        all_returns_10d: list[float] = []
        all_returns_20d: list[float] = []

        best = None  # (ticker, return_20d, date_str)
        worst = None

        for rec, ticker in rows:
            date_id = rec.run_date_id
            by_date.setdefault(date_id, []).append((rec, ticker))

            # 수익률은 performance.py에서 이미 거래비용 차감 완료 — 재차감 금지
            if rec.return_1d is not None:
                all_returns_1d.append(float(rec.return_1d))
            if rec.return_5d is not None:
                all_returns_5d.append(float(rec.return_5d))
            if rec.return_10d is not None:
                all_returns_10d.append(float(rec.return_10d))
            if rec.return_20d is not None:
                r20 = float(rec.return_20d)
                all_returns_20d.append(r20)
                try:
                    d_str = id_to_date(date_id).isoformat()
                except Exception:
                    d_str = str(date_id)
                if best is None or r20 > best[1]:
                    best = (ticker, r20, d_str)
                if worst is None or r20 < worst[1]:
                    worst = (ticker, r20, d_str)

        # 일별 결과
        daily_results = []
        for d_id in sorted(by_date.keys()):
            recs = by_date[d_id]
            r1 = [float(r.return_1d) for r, _ in recs if r.return_1d is not None]
            r5 = [float(r.return_5d) for r, _ in recs if r.return_5d is not None]
            r10 = [float(r.return_10d) for r, _ in recs if r.return_10d is not None]
            r20 = [float(r.return_20d) for r, _ in recs if r.return_20d is not None]
            try:
                run_date = id_to_date(d_id)
            except Exception:
                run_date = config.start_date
            daily_results.append(DailyResult(
                run_date=run_date,
                recommendation_count=len(recs),
                avg_return_1d=_safe_mean(r1),
                avg_return_5d=_safe_mean(r5),
                avg_return_10d=_safe_mean(r10),
                avg_return_20d=_safe_mean(r20),
            ))

        # 집계 — 20일 기간에 맞게 무위험 수익률을 스케일링
        # 연간 % → 20 거래일 기간 %
        rf_20d = settings.risk_free_rate_pct * (20 / 252)
        sharpe = calculate_sharpe(all_returns_20d, risk_free=rf_20d)
        max_dd = calculate_max_drawdown(all_returns_20d)

        # 확장 지표 계산
        sortino = calculate_sortino(all_returns_20d, risk_free=rf_20d)
        calmar = calculate_calmar(all_returns_20d, max_dd)
        omega = calculate_omega(all_returns_20d)
        monthly_wr = _calculate_monthly_win_rate(daily_results)
        mdd_days = calculate_max_drawdown_days(all_returns_20d)

        # 홀드아웃 분석
        holdout = None
        if config.holdout_pct > 0 and all_returns_20d:
            holdout = _compute_holdout(all_returns_20d, config.holdout_pct, rf_20d)

        return BacktestResult(
            config=config,
            total_days=len(by_date),
            total_recommendations=len(rows),
            avg_return_1d=_safe_mean(all_returns_1d),
            avg_return_5d=_safe_mean(all_returns_5d),
            avg_return_10d=_safe_mean(all_returns_10d),
            avg_return_20d=_safe_mean(all_returns_20d),
            win_rate_1d=_win_rate(all_returns_1d),
            win_rate_5d=_win_rate(all_returns_5d),
            win_rate_20d=_win_rate(all_returns_20d),
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            best_pick=best,
            worst_pick=worst,
            by_date=tuple(daily_results),
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            omega_ratio=omega,
            monthly_win_rate=monthly_wr,
            max_drawdown_days=mdd_days,
            holdout=holdout,
        )


def _safe_mean(values: list[float]) -> float | None:
    """빈 리스트면 None, 아니면 평균."""
    return statistics.mean(values) if values else None


def _win_rate(values: list[float]) -> float | None:
    """양수 수익 비율."""
    if not values:
        return None
    wins = sum(1 for v in values if v > 0)
    return wins / len(values) * 100


# 리스크 지표 함수는 src.analysis.risk_metrics 모듈로 이관됨.
# 하위 호환용 별칭:
_calculate_sharpe = calculate_sharpe
_calculate_max_drawdown = calculate_max_drawdown
_calculate_sortino = calculate_sortino
_calculate_calmar = calculate_calmar
_calculate_omega = calculate_omega


def _calculate_monthly_win_rate(daily_results: list[DailyResult]) -> float | None:
    """월별 승률 — 평균 수익이 양수인 월의 비율.

    DailyResult의 run_date를 기준으로 월별 그룹핑 후 계산한다.
    """
    if not daily_results:
        return None

    monthly_returns: dict[str, list[float]] = {}
    for dr in daily_results:
        if dr.avg_return_20d is None:
            continue
        key = f"{dr.run_date.year}-{dr.run_date.month:02d}"
        monthly_returns.setdefault(key, []).append(dr.avg_return_20d)

    if not monthly_returns:
        return None

    winning_months = 0
    for returns in monthly_returns.values():
        if statistics.mean(returns) > 0:
            winning_months += 1

    return round(winning_months / len(monthly_returns) * 100, 1)


_calculate_max_drawdown_days = calculate_max_drawdown_days


def _compute_holdout(
    all_returns: list[float], holdout_pct: float, rf_20d: float,
) -> HoldoutResult:
    """수익률 리스트를 IS/OOS로 분할하여 결과를 반환한다."""
    n = len(all_returns)
    split_idx = max(1, int(n * (1.0 - holdout_pct)))

    is_returns = all_returns[:split_idx]
    oos_returns = all_returns[split_idx:]

    return HoldoutResult(
        is_avg_return_20d=_safe_mean(is_returns),
        is_win_rate_20d=_win_rate(is_returns),
        is_sharpe=calculate_sharpe(is_returns, risk_free=rf_20d),
        oos_avg_return_20d=_safe_mean(oos_returns),
        oos_win_rate_20d=_win_rate(oos_returns),
        oos_sharpe=calculate_sharpe(oos_returns, risk_free=rf_20d),
        is_count=len(is_returns),
        oos_count=len(oos_returns),
    )
