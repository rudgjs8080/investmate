"""ML 피처 엔지니어링 — 학습/추론용 피처 추출."""

from __future__ import annotations

import logging
import statistics
from datetime import date

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import (
    DimIndicatorType,
    DimStock,
    FactDailyPrice,
    FactDailyRecommendation,
    FactIndicatorValue,
    FactMacroIndicator,
    FactValuation,
)

logger = logging.getLogger(__name__)

# 피처 목록 (28개)
FEATURE_NAMES = [
    # 기술적 (10)
    "rsi_14", "macd_hist", "bb_position", "sma20_dist", "sma60_dist",
    "volume_ratio", "stoch_k", "stoch_d", "momentum_5d", "momentum_20d",
    # 펀더멘털 (8)
    "per", "pbr", "roe", "debt_ratio", "fcf_margin",
    "dividend_yield", "f_score", "z_score",
    # 수급 (5)
    "analyst_upside", "short_pct", "institutional_pct",
    "insider_net", "earnings_surprise_pct",
    # 외부 (5)
    "vix", "sp500_vs_sma20", "sector_momentum",
    "yield_spread", "market_score",
]


def build_features_for_stock(
    session: Session, stock_id: int, date_id: int,
) -> dict[str, float | None]:
    """단일 종목의 ML 피처를 추출한다."""
    features: dict[str, float | None] = {name: None for name in FEATURE_NAMES}

    # 기술적 지표 로드
    indicator_types = {
        row.code: row.indicator_type_id
        for row in session.execute(select(DimIndicatorType)).scalars()
    }

    for code, feat_name in [
        ("RSI_14", "rsi_14"),
        ("MACD_HIST", "macd_hist"),
        ("STOCH_K", "stoch_k"),
        ("STOCH_D", "stoch_d"),
    ]:
        type_id = indicator_types.get(code)
        if type_id:
            val = session.execute(
                select(FactIndicatorValue.value)
                .where(
                    FactIndicatorValue.stock_id == stock_id,
                    FactIndicatorValue.indicator_type_id == type_id,
                    FactIndicatorValue.date_id <= date_id,
                )
                .order_by(FactIndicatorValue.date_id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if val is not None:
                features[feat_name] = float(val)

    # 가격 기반 피처
    prices = session.execute(
        select(FactDailyPrice.close, FactDailyPrice.volume)
        .where(
            FactDailyPrice.stock_id == stock_id,
            FactDailyPrice.date_id <= date_id,
        )
        .order_by(FactDailyPrice.date_id.desc())
        .limit(60)
    ).all()

    if prices and len(prices) >= 20:
        closes = [float(p.close) for p in reversed(prices)]
        volumes = [float(p.volume) for p in reversed(prices)]

        current = closes[-1]
        sma20 = sum(closes[-20:]) / 20
        features["sma20_dist"] = (
            (current - sma20) / sma20 * 100 if sma20 else None
        )

        if len(closes) >= 60:
            sma60 = sum(closes[-60:]) / 60
            features["sma60_dist"] = (
                (current - sma60) / sma60 * 100 if sma60 else None
            )

        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
        features["volume_ratio"] = (
            volumes[-1] / avg_vol if avg_vol and avg_vol > 0 else None
        )

        if len(closes) >= 5:
            features["momentum_5d"] = (closes[-1] / closes[-5] - 1) * 100
        if len(closes) >= 20:
            features["momentum_20d"] = (closes[-1] / closes[-20] - 1) * 100

        # BB position
        if len(closes) >= 20:
            std = statistics.stdev(closes[-20:])
            if std > 0:
                features["bb_position"] = (current - sma20) / (2 * std)

    # 밸류에이션
    val = session.execute(
        select(FactValuation)
        .where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if val:
        features["per"] = float(val.per) if val.per else None
        features["pbr"] = float(val.pbr) if val.pbr else None
        features["roe"] = float(val.roe) if val.roe else None
        features["debt_ratio"] = float(val.debt_ratio) if val.debt_ratio else None
        features["dividend_yield"] = (
            float(val.dividend_yield) if val.dividend_yield else None
        )

    # 매크로
    macro = session.execute(
        select(FactMacroIndicator)
        .where(FactMacroIndicator.date_id <= date_id)
        .order_by(FactMacroIndicator.date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if macro:
        features["vix"] = float(macro.vix) if macro.vix else None
        features["market_score"] = (
            float(macro.market_score) if macro.market_score else None
        )
        if macro.sp500_close and macro.sp500_sma20:
            features["sp500_vs_sma20"] = (
                (float(macro.sp500_close) - float(macro.sp500_sma20))
                / float(macro.sp500_sma20)
                * 100
            )
        if macro.yield_spread is not None:
            features["yield_spread"] = float(macro.yield_spread)

    return features


def build_training_data(
    session: Session, min_days: int = 60,
) -> pd.DataFrame:
    """학습 데이터를 구축한다. 과거 추천 + 실현 수익률 기반."""
    recs = list(
        session.execute(
            select(FactDailyRecommendation).where(
                FactDailyRecommendation.return_20d.isnot(None)
            )
        )
        .scalars()
        .all()
    )

    if len(recs) < min_days:
        logger.info("학습 데이터 부족: %d/%d건", len(recs), min_days)
        return pd.DataFrame()

    rows = []
    for rec in recs:
        features = build_features_for_stock(session, rec.stock_id, rec.run_date_id)
        features["return_20d"] = float(rec.return_20d)
        features["stock_id"] = rec.stock_id
        features["date_id"] = rec.run_date_id
        rows.append(features)

    return pd.DataFrame(rows)
