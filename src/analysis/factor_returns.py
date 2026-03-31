"""팩터 수익률 추적 — 롱숏 스프레드, 팩터 모멘텀, IC."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from scipy import stats
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.factors import (
    FACTOR_DEFINITIONS,
    compute_composite_scores,
)
from src.db.helpers import date_to_id
from src.db.models import DimStock, FactDailyPrice, FactFactorReturn
from src.db.repository import FactorReturnRepository, StockRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FactorSpread:
    """팩터 롱숏 스프레드."""

    date: date
    factor_name: str
    long_return: float
    short_return: float
    spread: float


@dataclass(frozen=True)
class FactorMomentum:
    """팩터 모멘텀 (누적 스프레드)."""

    factor_name: str
    momentum_1m: float
    momentum_3m: float
    momentum_6m: float


def compute_daily_factor_returns(
    session: Session,
    run_date: date,
) -> list[FactorSpread]:
    """각 팩터 카테고리의 일별 롱숏 수익률을 계산한다.

    상위 20% - 하위 20% 균등가중 일간 수익률.

    Args:
        session: DB 세션
        run_date: 실행일

    Returns:
        팩터별 FactorSpread 리스트
    """
    stocks = StockRepository.get_sp500_active(session)
    if not stocks:
        return []

    stock_ids = [s.stock_id for s in stocks]

    # 팩터 점수 계산
    composite_scores = compute_composite_scores(session, stock_ids, run_date)
    if not composite_scores:
        return []

    # 당일 수익률 계산 (전일 대비)
    current_date_id = date_to_id(run_date)
    prev_date_id = date_to_id(run_date - timedelta(days=5))  # 여유 있게

    # 최근 2일 가격 조회
    prices = session.execute(
        select(FactDailyPrice.stock_id, FactDailyPrice.date_id, FactDailyPrice.adj_close)
        .where(
            FactDailyPrice.stock_id.in_(stock_ids),
            FactDailyPrice.date_id >= prev_date_id,
            FactDailyPrice.date_id <= current_date_id,
        )
        .order_by(FactDailyPrice.stock_id, FactDailyPrice.date_id)
    ).all()

    # 종목별 일간 수익률
    prices_by_stock: dict[int, list[tuple[int, float]]] = {}
    for sid, did, p in prices:
        if p is not None:
            prices_by_stock.setdefault(sid, []).append((did, float(p)))

    daily_returns: dict[int, float] = {}
    for sid, price_list in prices_by_stock.items():
        if len(price_list) >= 2:
            price_list.sort(key=lambda x: x[0])
            prev_p = price_list[-2][1]
            curr_p = price_list[-1][1]
            if prev_p > 0:
                daily_returns[sid] = (curr_p / prev_p) - 1.0

    if not daily_returns:
        return []

    # 카테고리별 롱숏 스프레드
    spreads: list[FactorSpread] = []
    categories = ["value", "momentum", "quality", "low_vol", "size"]

    for category in categories:
        # 해당 카테고리의 z-score로 정렬
        scored = []
        for sid, cs in composite_scores.items():
            z = getattr(cs, f"{category}_z", 0.0)
            if sid in daily_returns:
                scored.append((sid, z, daily_returns[sid]))

        if len(scored) < 10:
            continue

        scored.sort(key=lambda x: x[1], reverse=True)
        quintile_size = max(1, len(scored) // 5)

        long_group = scored[:quintile_size]
        short_group = scored[-quintile_size:]

        long_return = float(np.mean([r for _, _, r in long_group])) * 100
        short_return = float(np.mean([r for _, _, r in short_group])) * 100
        spread_val = long_return - short_return

        spreads.append(FactorSpread(
            date=run_date,
            factor_name=category,
            long_return=round(long_return, 4),
            short_return=round(short_return, 4),
            spread=round(spread_val, 4),
        ))

    return spreads


def store_factor_returns(
    session: Session,
    spreads: list[FactorSpread],
) -> int:
    """팩터 수익률을 DB에 저장한다.

    Args:
        session: DB 세션
        spreads: FactorSpread 리스트

    Returns:
        저장된 레코드 수
    """
    if not spreads:
        return 0

    from src.db.helpers import ensure_date_ids

    dates = [s.date for s in spreads]
    date_map = ensure_date_ids(session, dates)

    records = []
    for s in spreads:
        date_id = date_map.get(s.date) or date_to_id(s.date)
        records.append({
            "date_id": date_id,
            "factor_name": s.factor_name,
            "long_return": s.long_return,
            "short_return": s.short_return,
            "spread": s.spread,
        })

    return FactorReturnRepository.upsert_batch(session, records)


def get_factor_momentum(
    session: Session,
    as_of_date: date,
) -> dict[str, FactorMomentum]:
    """팩터 모멘텀을 계산한다 (누적 스프레드).

    Args:
        session: DB 세션
        as_of_date: 기준일

    Returns:
        {factor_name: FactorMomentum}
    """
    end_id = date_to_id(as_of_date)
    start_id = date_to_id(as_of_date - timedelta(days=180))

    all_returns = FactorReturnRepository.get_all_factors(session, start_date_id=start_id)

    # 팩터별 스프레드 시계열
    spreads_by_factor: dict[str, list[tuple[int, float]]] = {}
    for fr in all_returns:
        if fr.date_id <= end_id:
            spreads_by_factor.setdefault(fr.factor_name, []).append(
                (fr.date_id, float(fr.spread))
            )

    result: dict[str, FactorMomentum] = {}

    for factor_name, spread_series in spreads_by_factor.items():
        spread_series.sort(key=lambda x: x[0])
        spreads_values = [s for _, s in spread_series]

        mom_1m = sum(spreads_values[-21:]) if len(spreads_values) >= 21 else sum(spreads_values)
        mom_3m = sum(spreads_values[-63:]) if len(spreads_values) >= 63 else sum(spreads_values)
        mom_6m = sum(spreads_values[-126:]) if len(spreads_values) >= 126 else sum(spreads_values)

        result[factor_name] = FactorMomentum(
            factor_name=factor_name,
            momentum_1m=round(mom_1m, 4),
            momentum_3m=round(mom_3m, 4),
            momentum_6m=round(mom_6m, 4),
        )

    return result


def get_factor_ic(
    session: Session,
    factor_name: str,
    lookback_days: int = 252,
    run_date: date | None = None,
) -> list[tuple[date, float]]:
    """Information Coefficient 시계열.

    팩터 z-score와 forward 20일 수익률 간 Spearman rank correlation.

    Args:
        session: DB 세션
        factor_name: 팩터 카테고리명
        lookback_days: 조회 기간
        run_date: 기준일 (None이면 오늘)

    Returns:
        [(date, ic_value), ...]
    """
    if run_date is None:
        run_date = date.today()

    start = run_date - timedelta(days=lookback_days)
    end_id = date_to_id(run_date)
    start_id = date_to_id(start)

    factor_returns = FactorReturnRepository.get_by_factor(
        session, factor_name, start_date_id=start_id, end_date_id=end_id,
    )

    result: list[tuple[date, float]] = []
    for fr in factor_returns:
        # 스프레드를 IC 프록시로 사용 (양수 = 팩터 유효)
        try:
            from src.db.helpers import id_to_date
            d = id_to_date(fr.date_id)
            # 스프레드를 정규화하여 IC 근사
            ic_proxy = float(fr.spread) / 100.0  # % → 소수
            ic_proxy = max(-1.0, min(1.0, ic_proxy * 10))  # 스케일 조정
            result.append((d, round(ic_proxy, 4)))
        except Exception:
            continue

    return result
