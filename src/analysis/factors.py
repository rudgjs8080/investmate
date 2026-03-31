"""팩터 투자 프레임워크 — 학술적 팩터 정의 + z-score 정규화."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import (
    DimDate,
    FactDailyPrice,
    FactFinancial,
    FactValuation,
)

logger = logging.getLogger(__name__)

# 최소 관측치 수 (z-score 정규화에 필요)
MIN_OBSERVATIONS = 30

# 기본 카테고리 가중치
DEFAULT_CATEGORY_WEIGHTS: dict[str, float] = {
    "value": 0.25,
    "momentum": 0.25,
    "quality": 0.25,
    "low_vol": 0.15,
    "size": 0.10,
}

# 레짐별 팩터 가중치
FACTOR_REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "bull": {"value": 0.20, "momentum": 0.35, "quality": 0.20, "low_vol": 0.10, "size": 0.15},
    "bear": {"value": 0.30, "momentum": 0.10, "quality": 0.30, "low_vol": 0.20, "size": 0.10},
    "range": {"value": 0.25, "momentum": 0.25, "quality": 0.25, "low_vol": 0.15, "size": 0.10},
    "crisis": {"value": 0.25, "momentum": 0.05, "quality": 0.35, "low_vol": 0.25, "size": 0.10},
}

# 팩터 정의: 카테고리 → 서브팩터 목록
FACTOR_DEFINITIONS: dict[str, list[str]] = {
    "value": ["earnings_yield", "book_to_price", "fcf_yield"],
    "momentum": ["momentum_12_1", "high_52w_ratio"],
    "quality": ["roe_stability", "gross_profit_to_assets", "accruals"],
    "low_vol": ["realized_vol_60d", "downside_deviation"],
    "size": ["ln_market_cap"],
}


@dataclass(frozen=True)
class FactorValue:
    """개별 팩터 값."""

    stock_id: int
    ticker: str
    factor_name: str
    category: str
    raw_value: float
    z_score: float


@dataclass(frozen=True)
class CompositeFactorScore:
    """종목별 복합 팩터 점수."""

    stock_id: int
    ticker: str
    value_z: float
    momentum_z: float
    quality_z: float
    low_vol_z: float
    size_z: float
    composite: float
    category_details: dict[str, dict[str, float]]


def normalize_cross_section(
    raw_values: dict[int, float],
    winsorize_sigma: float = 3.0,
) -> dict[int, float]:
    """Cross-sectional z-score 정규화.

    Args:
        raw_values: {stock_id: raw_value}
        winsorize_sigma: 윈저화 임계값 (±sigma)

    Returns:
        {stock_id: z_score}
    """
    valid = {k: v for k, v in raw_values.items() if not math.isnan(v) and not math.isinf(v)}
    if len(valid) < MIN_OBSERVATIONS:
        return {k: 0.0 for k in raw_values}

    values = np.array(list(valid.values()))
    mean = float(np.mean(values))
    std = float(np.std(values, ddof=1))

    if std < 1e-10:
        return {k: 0.0 for k in raw_values}

    result: dict[int, float] = {}
    for stock_id, raw in raw_values.items():
        if stock_id not in valid:
            result[stock_id] = 0.0
            continue
        z = (raw - mean) / std
        z = max(-winsorize_sigma, min(winsorize_sigma, z))
        result[stock_id] = round(z, 4)

    return result


def compute_composite_scores(
    session: Session,
    stock_ids: list[int],
    run_date: date,
    category_weights: dict[str, float] | None = None,
    regime: str | None = None,
) -> dict[int, CompositeFactorScore]:
    """전체 팩터 파이프라인: raw → z-score → 카테고리 합산 → 가중 합계.

    Args:
        session: DB 세션
        stock_ids: 대상 종목 ID 리스트
        run_date: 실행일
        category_weights: 카테고리별 가중치 (None이면 기본값)
        regime: 시장 레짐 ("bull"/"bear"/"range"/"crisis", None이면 기본값)

    Returns:
        {stock_id: CompositeFactorScore}
    """
    if not stock_ids:
        return {}

    # 가중치 결정
    if regime and regime in FACTOR_REGIME_WEIGHTS:
        weights = FACTOR_REGIME_WEIGHTS[regime]
    elif category_weights:
        weights = category_weights
    else:
        weights = DEFAULT_CATEGORY_WEIGHTS

    # 티커 맵 구축
    from src.db.models import DimStock
    stocks = session.execute(
        select(DimStock.stock_id, DimStock.ticker)
        .where(DimStock.stock_id.in_(stock_ids))
    ).all()
    ticker_map = {s[0]: s[1] for s in stocks}

    # 카테고리별 서브팩터 z-score 계산
    factor_z_scores: dict[str, dict[str, dict[int, float]]] = {}
    # {category: {sub_factor: {stock_id: z_score}}}

    compute_funcs = {
        "earnings_yield": lambda: _compute_earnings_yield(session, stock_ids),
        "book_to_price": lambda: _compute_book_to_price(session, stock_ids),
        "fcf_yield": lambda: _compute_fcf_yield(session, stock_ids),
        "momentum_12_1": lambda: _compute_momentum_12_1(session, stock_ids, run_date),
        "high_52w_ratio": lambda: _compute_52w_high_ratio(session, stock_ids, run_date),
        "roe_stability": lambda: _compute_roe_stability(session, stock_ids),
        "gross_profit_to_assets": lambda: _compute_gross_profit_to_assets(session, stock_ids),
        "accruals": lambda: _compute_accruals(session, stock_ids),
        "realized_vol_60d": lambda: _compute_realized_vol(session, stock_ids, run_date),
        "downside_deviation": lambda: _compute_downside_deviation(session, stock_ids, run_date),
        "ln_market_cap": lambda: _compute_size(session, stock_ids),
    }

    for category, sub_factors in FACTOR_DEFINITIONS.items():
        factor_z_scores[category] = {}
        for sf in sub_factors:
            func = compute_funcs.get(sf)
            if func is None:
                continue
            try:
                raw = func()
                z_scores = normalize_cross_section(raw)
                factor_z_scores[category][sf] = z_scores
            except Exception as e:
                logger.warning("팩터 %s 계산 실패: %s", sf, e)
                factor_z_scores[category][sf] = {sid: 0.0 for sid in stock_ids}

    # 종목별 복합 점수 조립
    result: dict[int, CompositeFactorScore] = {}

    for stock_id in stock_ids:
        ticker = ticker_map.get(stock_id, "")
        category_zs: dict[str, float] = {}
        details: dict[str, dict[str, float]] = {}

        for category, sub_factors_data in factor_z_scores.items():
            sub_z_values: list[float] = []
            detail: dict[str, float] = {}
            for sf_name, z_map in sub_factors_data.items():
                z = z_map.get(stock_id, 0.0)
                sub_z_values.append(z)
                detail[sf_name] = z

            category_z = float(np.mean(sub_z_values)) if sub_z_values else 0.0
            category_zs[category] = round(category_z, 4)
            details[category] = detail

        composite = sum(
            category_zs.get(cat, 0.0) * weights.get(cat, 0.0)
            for cat in weights
        )

        result[stock_id] = CompositeFactorScore(
            stock_id=stock_id,
            ticker=ticker,
            value_z=category_zs.get("value", 0.0),
            momentum_z=category_zs.get("momentum", 0.0),
            quality_z=category_zs.get("quality", 0.0),
            low_vol_z=category_zs.get("low_vol", 0.0),
            size_z=category_zs.get("size", 0.0),
            composite=round(composite, 4),
            category_details=details,
        )

    return result


# ──────────────────────────────────────────
# 서브팩터 계산 함수 (private)
# ──────────────────────────────────────────


def _compute_earnings_yield(
    session: Session, stock_ids: list[int],
) -> dict[int, float]:
    """Earnings Yield = operating_income / market_cap."""
    vals = session.execute(
        select(FactValuation.stock_id, FactValuation.market_cap)
        .where(FactValuation.stock_id.in_(stock_ids))
        .order_by(FactValuation.date_id.desc())
    ).all()
    mcap_map: dict[int, float] = {}
    for sid, mcap in vals:
        if sid not in mcap_map and mcap and float(mcap) > 0:
            mcap_map[sid] = float(mcap)

    fins = session.execute(
        select(FactFinancial.stock_id, FactFinancial.operating_income)
        .where(FactFinancial.stock_id.in_(stock_ids))
        .order_by(FactFinancial.period.desc())
    ).all()
    oi_map: dict[int, float] = {}
    for sid, oi in fins:
        if sid not in oi_map and oi is not None:
            oi_map[sid] = float(oi)

    result: dict[int, float] = {}
    for sid in stock_ids:
        mcap = mcap_map.get(sid)
        oi = oi_map.get(sid)
        if mcap and oi and mcap > 0:
            result[sid] = oi / mcap
        else:
            result[sid] = float("nan")
    return result


def _compute_book_to_price(
    session: Session, stock_ids: list[int],
) -> dict[int, float]:
    """Book-to-Price = 1/PBR."""
    vals = session.execute(
        select(FactValuation.stock_id, FactValuation.pbr)
        .where(FactValuation.stock_id.in_(stock_ids))
        .order_by(FactValuation.date_id.desc())
    ).all()
    pbr_map: dict[int, float] = {}
    for sid, pbr in vals:
        if sid not in pbr_map and pbr and float(pbr) > 0:
            pbr_map[sid] = float(pbr)

    result: dict[int, float] = {}
    for sid in stock_ids:
        pbr = pbr_map.get(sid)
        result[sid] = 1.0 / pbr if pbr and pbr > 0 else float("nan")
    return result


def _compute_fcf_yield(
    session: Session, stock_ids: list[int],
) -> dict[int, float]:
    """FCF Yield = operating_cashflow / market_cap."""
    vals = session.execute(
        select(FactValuation.stock_id, FactValuation.market_cap)
        .where(FactValuation.stock_id.in_(stock_ids))
        .order_by(FactValuation.date_id.desc())
    ).all()
    mcap_map: dict[int, float] = {}
    for sid, mcap in vals:
        if sid not in mcap_map and mcap and float(mcap) > 0:
            mcap_map[sid] = float(mcap)

    fins = session.execute(
        select(FactFinancial.stock_id, FactFinancial.operating_cashflow)
        .where(FactFinancial.stock_id.in_(stock_ids))
        .order_by(FactFinancial.period.desc())
    ).all()
    ocf_map: dict[int, float] = {}
    for sid, ocf in fins:
        if sid not in ocf_map and ocf is not None:
            ocf_map[sid] = float(ocf)

    result: dict[int, float] = {}
    for sid in stock_ids:
        mcap = mcap_map.get(sid)
        ocf = ocf_map.get(sid)
        if mcap and ocf and mcap > 0:
            result[sid] = ocf / mcap
        else:
            result[sid] = float("nan")
    return result


def _compute_momentum_12_1(
    session: Session, stock_ids: list[int], run_date: date,
) -> dict[int, float]:
    """12-1개월 모멘텀: 12개월 수익률에서 최근 1개월 제외."""
    from src.db.helpers import date_to_id

    end_1m = run_date - timedelta(days=21)
    start_12m = run_date - timedelta(days=252)

    end_id = date_to_id(end_1m)
    start_id = date_to_id(start_12m)
    current_id = date_to_id(run_date)

    # 현재가
    current_prices = _get_latest_closes(session, stock_ids, current_id)
    # 1개월 전 가
    prices_1m = _get_latest_closes(session, stock_ids, end_id)
    # 12개월 전 가
    prices_12m = _get_latest_closes(session, stock_ids, start_id)

    result: dict[int, float] = {}
    for sid in stock_ids:
        p_now = current_prices.get(sid)
        p_1m = prices_1m.get(sid)
        p_12m = prices_12m.get(sid)
        if p_now and p_1m and p_12m and p_12m > 0 and p_1m > 0:
            ret_12m = (p_1m / p_12m) - 1.0  # 12개월 ~ 1개월 전 구간
            result[sid] = ret_12m
        else:
            result[sid] = float("nan")
    return result


def _compute_52w_high_ratio(
    session: Session, stock_ids: list[int], run_date: date,
) -> dict[int, float]:
    """52주 고가 대비 현재가 비율."""
    from src.db.helpers import date_to_id

    start_id = date_to_id(run_date - timedelta(days=252))
    current_id = date_to_id(run_date)

    current_prices = _get_latest_closes(session, stock_ids, current_id)

    # 52주 고가
    highs = session.execute(
        select(
            FactDailyPrice.stock_id,
            FactDailyPrice.high,
        )
        .where(
            FactDailyPrice.stock_id.in_(stock_ids),
            FactDailyPrice.date_id >= start_id,
            FactDailyPrice.date_id <= current_id,
        )
    ).all()

    high_map: dict[int, float] = {}
    for sid, h in highs:
        if h is not None:
            current_high = high_map.get(sid, 0.0)
            high_map[sid] = max(current_high, float(h))

    result: dict[int, float] = {}
    for sid in stock_ids:
        p = current_prices.get(sid)
        h = high_map.get(sid)
        if p and h and h > 0:
            result[sid] = p / h
        else:
            result[sid] = float("nan")
    return result


def _compute_roe_stability(
    session: Session, stock_ids: list[int],
) -> dict[int, float]:
    """ROE 안정성: ROE 표준편차의 역수 (안정적일수록 높음)."""
    fins = session.execute(
        select(
            FactFinancial.stock_id,
            FactFinancial.net_income,
            FactFinancial.total_equity,
        )
        .where(FactFinancial.stock_id.in_(stock_ids))
        .order_by(FactFinancial.stock_id, FactFinancial.period)
    ).all()

    roe_by_stock: dict[int, list[float]] = {}
    for sid, ni, eq in fins:
        if ni is not None and eq is not None and float(eq) > 0:
            roe = float(ni) / float(eq)
            roe_by_stock.setdefault(sid, []).append(roe)

    result: dict[int, float] = {}
    for sid in stock_ids:
        roes = roe_by_stock.get(sid, [])
        if len(roes) >= 2:
            std = float(np.std(roes, ddof=1))
            result[sid] = -std  # 역수: 안정적(낮은 std)이 높은 점수
        else:
            result[sid] = float("nan")
    return result


def _compute_gross_profit_to_assets(
    session: Session, stock_ids: list[int],
) -> dict[int, float]:
    """Gross Profit / Total Assets."""
    fins = session.execute(
        select(
            FactFinancial.stock_id,
            FactFinancial.revenue,
            FactFinancial.operating_income,
            FactFinancial.total_assets,
        )
        .where(FactFinancial.stock_id.in_(stock_ids))
        .order_by(FactFinancial.period.desc())
    ).all()

    latest: dict[int, tuple] = {}
    for sid, rev, oi, ta in fins:
        if sid not in latest:
            latest[sid] = (rev, oi, ta)

    result: dict[int, float] = {}
    for sid in stock_ids:
        data = latest.get(sid)
        if data and data[0] is not None and data[2] is not None and float(data[2]) > 0:
            # Gross profit proxy: revenue (COGS not stored, use revenue as upper bound)
            gp = float(data[0]) * 0.6  # 대형주 평균 gross margin ~60%
            if data[1] is not None:
                gp = max(float(data[1]), gp)  # operating_income as floor
            result[sid] = gp / float(data[2])
        else:
            result[sid] = float("nan")
    return result


def _compute_accruals(
    session: Session, stock_ids: list[int],
) -> dict[int, float]:
    """발생주의: (NI - OCF) / TA. 낮을수록 높은 품질 (역수)."""
    fins = session.execute(
        select(
            FactFinancial.stock_id,
            FactFinancial.net_income,
            FactFinancial.operating_cashflow,
            FactFinancial.total_assets,
        )
        .where(FactFinancial.stock_id.in_(stock_ids))
        .order_by(FactFinancial.period.desc())
    ).all()

    latest: dict[int, tuple] = {}
    for sid, ni, ocf, ta in fins:
        if sid not in latest:
            latest[sid] = (ni, ocf, ta)

    result: dict[int, float] = {}
    for sid in stock_ids:
        data = latest.get(sid)
        if data and all(d is not None for d in data) and float(data[2]) > 0:
            accrual = (float(data[0]) - float(data[1])) / float(data[2])
            result[sid] = -accrual  # 역수: 낮은 발생(높은 품질)이 높은 점수
        else:
            result[sid] = float("nan")
    return result


def _compute_realized_vol(
    session: Session, stock_ids: list[int], run_date: date,
    window: int = 60,
) -> dict[int, float]:
    """60일 실현 변동성 (역수: 낮을수록 높은 점수)."""
    from src.db.helpers import date_to_id

    start_id = date_to_id(run_date - timedelta(days=window + 30))
    end_id = date_to_id(run_date)

    prices = session.execute(
        select(FactDailyPrice.stock_id, FactDailyPrice.adj_close)
        .where(
            FactDailyPrice.stock_id.in_(stock_ids),
            FactDailyPrice.date_id >= start_id,
            FactDailyPrice.date_id <= end_id,
        )
        .order_by(FactDailyPrice.stock_id, FactDailyPrice.date_id)
    ).all()

    prices_by_stock: dict[int, list[float]] = {}
    for sid, p in prices:
        if p is not None:
            prices_by_stock.setdefault(sid, []).append(float(p))

    result: dict[int, float] = {}
    for sid in stock_ids:
        p_list = prices_by_stock.get(sid, [])
        if len(p_list) < window:
            result[sid] = float("nan")
            continue
        p_arr = np.array(p_list[-window:])
        returns = np.diff(p_arr) / p_arr[:-1]
        vol = float(np.std(returns, ddof=1))
        result[sid] = -vol  # 역수
    return result


def _compute_downside_deviation(
    session: Session, stock_ids: list[int], run_date: date,
    window: int = 60,
) -> dict[int, float]:
    """하방 편차 (역수: 낮을수록 높은 점수)."""
    from src.db.helpers import date_to_id

    start_id = date_to_id(run_date - timedelta(days=window + 30))
    end_id = date_to_id(run_date)

    prices = session.execute(
        select(FactDailyPrice.stock_id, FactDailyPrice.adj_close)
        .where(
            FactDailyPrice.stock_id.in_(stock_ids),
            FactDailyPrice.date_id >= start_id,
            FactDailyPrice.date_id <= end_id,
        )
        .order_by(FactDailyPrice.stock_id, FactDailyPrice.date_id)
    ).all()

    prices_by_stock: dict[int, list[float]] = {}
    for sid, p in prices:
        if p is not None:
            prices_by_stock.setdefault(sid, []).append(float(p))

    result: dict[int, float] = {}
    for sid in stock_ids:
        p_list = prices_by_stock.get(sid, [])
        if len(p_list) < window:
            result[sid] = float("nan")
            continue
        p_arr = np.array(p_list[-window:])
        returns = np.diff(p_arr) / p_arr[:-1]
        negative = returns[returns < 0]
        if len(negative) < 2:
            result[sid] = 0.0
            continue
        dd = float(np.std(negative, ddof=1))
        result[sid] = -dd  # 역수
    return result


def _compute_size(
    session: Session, stock_ids: list[int],
) -> dict[int, float]:
    """Size = -ln(market_cap). 소형주 프리미엄 (역수)."""
    vals = session.execute(
        select(FactValuation.stock_id, FactValuation.market_cap)
        .where(FactValuation.stock_id.in_(stock_ids))
        .order_by(FactValuation.date_id.desc())
    ).all()
    mcap_map: dict[int, float] = {}
    for sid, mcap in vals:
        if sid not in mcap_map and mcap and float(mcap) > 0:
            mcap_map[sid] = float(mcap)

    result: dict[int, float] = {}
    for sid in stock_ids:
        mcap = mcap_map.get(sid)
        if mcap and mcap > 0:
            result[sid] = -math.log(mcap)  # 역수: 소형주가 높은 점수
        else:
            result[sid] = float("nan")
    return result


# ──────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────


def _get_latest_closes(
    session: Session, stock_ids: list[int], max_date_id: int,
) -> dict[int, float]:
    """각 종목의 특정 날짜 이전 최근 종가를 조회한다."""
    rows = session.execute(
        select(FactDailyPrice.stock_id, FactDailyPrice.adj_close)
        .where(
            FactDailyPrice.stock_id.in_(stock_ids),
            FactDailyPrice.date_id <= max_date_id,
        )
        .order_by(FactDailyPrice.stock_id, FactDailyPrice.date_id.desc())
    ).all()

    result: dict[int, float] = {}
    for sid, close in rows:
        if sid not in result and close is not None:
            result[sid] = float(close)
    return result
