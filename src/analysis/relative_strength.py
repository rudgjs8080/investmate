"""상대 강도 (Relative Strength) 지표 모듈.

S&P 500 대비 개별 종목의 상대 성과를 측정한다.
RS 백분위가 높을수록 시장 대비 강한 종목.
"""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# RS 계산 기간 (거래일 기준)
RS_LOOKBACK_DAYS = 63  # 약 3개월


def calculate_rs_ranks(
    session: Session,
    run_date: date,
) -> dict[int, float]:
    """전 종목의 S&P 500 대비 상대 강도 백분위를 계산한다.

    Args:
        session: DB 세션.
        run_date: 기준일.

    Returns:
        {stock_id: rs_percentile (0-100)}. 높을수록 시장 대비 강세.
    """
    from src.db.helpers import date_to_id
    from src.db.models import FactDailyPrice, FactMacroIndicator
    from src.db.repository import StockRepository

    run_date_id = date_to_id(run_date)

    # S&P 500 최근 종가 조회
    sp500_prices = _get_sp500_prices(session, run_date_id)
    if len(sp500_prices) < RS_LOOKBACK_DAYS:
        logger.warning("S&P 500 가격 데이터 부족: %d일", len(sp500_prices))
        return {}

    sp500_return = _cumulative_return(sp500_prices, RS_LOOKBACK_DAYS)
    if sp500_return is None:
        return {}

    # 전 종목 RS 계산
    stocks = StockRepository.get_sp500_active(session)
    rs_values: dict[int, float] = {}

    for stock in stocks:
        stock_prices = _get_stock_prices(session, stock.stock_id, run_date_id)
        if len(stock_prices) < RS_LOOKBACK_DAYS:
            continue

        stock_return = _cumulative_return(stock_prices, RS_LOOKBACK_DAYS)
        if stock_return is None:
            continue

        # RS = 종목 수익률 / 시장 수익률 (비율)
        # sp500_return이 0이면 종목 수익률로만 판단
        if sp500_return != 0:
            rs_values[stock.stock_id] = stock_return - sp500_return
        else:
            rs_values[stock.stock_id] = stock_return

    if not rs_values:
        return {}

    # 백분위 계산
    return _to_percentiles(rs_values)


def _get_sp500_prices(
    session: Session,
    run_date_id: int,
) -> list[float]:
    """S&P 500 최근 종가를 조회한다 (오래된 순)."""
    from sqlalchemy import select

    from src.db.models import FactMacroIndicator

    rows = session.execute(
        select(FactMacroIndicator.sp500_close)
        .where(
            FactMacroIndicator.date_id <= run_date_id,
            FactMacroIndicator.sp500_close.isnot(None),
        )
        .order_by(FactMacroIndicator.date_id.desc())
        .limit(RS_LOOKBACK_DAYS + 5)
    ).scalars().all()

    return [float(p) for p in reversed(rows)]


def _get_stock_prices(
    session: Session,
    stock_id: int,
    run_date_id: int,
) -> list[float]:
    """종목 최근 종가를 조회한다 (오래된 순)."""
    from sqlalchemy import select

    from src.db.models import FactDailyPrice

    rows = session.execute(
        select(FactDailyPrice.adj_close)
        .where(
            FactDailyPrice.stock_id == stock_id,
            FactDailyPrice.date_id <= run_date_id,
            FactDailyPrice.adj_close.isnot(None),
        )
        .order_by(FactDailyPrice.date_id.desc())
        .limit(RS_LOOKBACK_DAYS + 5)
    ).scalars().all()

    return [float(p) for p in reversed(rows)]


def _cumulative_return(prices: list[float], lookback: int) -> float | None:
    """최근 lookback일 기준 누적 수익률(%)."""
    if len(prices) < lookback or prices[-lookback] == 0:
        return None
    return (prices[-1] / prices[-lookback] - 1) * 100


def _to_percentiles(values: dict[int, float]) -> dict[int, float]:
    """값을 백분위(0-100)로 변환한다."""
    if not values:
        return {}

    sorted_items = sorted(values.items(), key=lambda x: x[1])
    n = len(sorted_items)

    result: dict[int, float] = {}
    for rank, (stock_id, _) in enumerate(sorted_items):
        result[stock_id] = round(rank / max(1, n - 1) * 100, 1)

    return result
