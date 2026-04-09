"""Layer 6: 거시 민감도."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FactDailyPrice, FactMacroIndicator
from src.deepdive.layers_utils import round_or_none, sf
from src.deepdive.schemas import MacroSensitivity

logger = logging.getLogger(__name__)


def compute_layer6_macro(
    session: Session, stock_id: int, sector_id: int | None, date_id: int,
) -> MacroSensitivity | None:
    """거시 민감도: 베타 회귀, 섹터 모멘텀, 레짐별 행동."""
    try:
        return _compute(session, stock_id, sector_id, date_id)
    except Exception as e:
        logger.warning("Layer 6 계산 실패 (stock_id=%d): %s", stock_id, e)
        return None


def _compute(
    session: Session, stock_id: int, sector_id: int | None, date_id: int,
) -> MacroSensitivity | None:
    # 종목 일간 수익률 (252일)
    prices = list(
        session.execute(
            select(FactDailyPrice)
            .where(FactDailyPrice.stock_id == stock_id, FactDailyPrice.date_id <= date_id)
            .order_by(FactDailyPrice.date_id.desc())
            .limit(253)
        ).scalars().all()
    )
    if len(prices) < 60:
        return None

    prices.reverse()
    price_df = pd.DataFrame([
        {"date_id": p.date_id, "close": float(p.close)} for p in prices
    ])
    price_df["return"] = price_df["close"].pct_change()
    price_df = price_df.dropna(subset=["return"])

    # 매크로 데이터
    date_ids = price_df["date_id"].tolist()
    macros = list(
        session.execute(
            select(FactMacroIndicator)
            .where(FactMacroIndicator.date_id.in_(date_ids))
            .order_by(FactMacroIndicator.date_id)
        ).scalars().all()
    )
    if len(macros) < 30:
        return MacroSensitivity(
            macro_grade="Neutral",
            beta_vix=None, beta_10y=None, beta_dollar=None,
            sector_momentum_rank=None, sector_momentum_total=None,
            current_regime=None, regime_avg_return=None,
            metrics={"data_insufficient": True},
        )

    macro_df = pd.DataFrame([
        {
            "date_id": m.date_id,
            "vix": sf(m.vix),
            "us_10y": sf(m.us_10y_yield),
            "dollar": sf(getattr(m, "dollar_index", None)),
        }
        for m in macros
    ])

    # merge
    merged = price_df.merge(macro_df, on="date_id", how="inner")
    if len(merged) < 30:
        return MacroSensitivity(
            macro_grade="Neutral",
            beta_vix=None, beta_10y=None, beta_dollar=None,
            sector_momentum_rank=None, sector_momentum_total=None,
            current_regime=None, regime_avg_return=None,
            metrics={"merged_count": len(merged)},
        )

    # 베타 회귀
    beta_vix = _compute_beta(merged, "vix")
    beta_10y = _compute_beta(merged, "us_10y")
    beta_dollar = _compute_beta(merged, "dollar")

    # 섹터 모멘텀
    momentum_rank, momentum_total = _sector_momentum(session, sector_id)

    # 레짐
    current_regime = _detect_current_regime(session)
    regime_avg = _regime_avg_return(merged, current_regime)

    # 그레이드
    grade = _grade_macro(beta_vix, current_regime)

    return MacroSensitivity(
        macro_grade=grade,
        beta_vix=round_or_none(beta_vix, 3),
        beta_10y=round_or_none(beta_10y, 3),
        beta_dollar=round_or_none(beta_dollar, 3),
        sector_momentum_rank=momentum_rank,
        sector_momentum_total=momentum_total,
        current_regime=current_regime,
        regime_avg_return=round_or_none(regime_avg, 2),
        metrics={"data_points": len(merged)},
    )


def _compute_beta(df: pd.DataFrame, macro_col: str) -> float | None:
    if macro_col not in df.columns:
        return None
    macro_series = df[macro_col].dropna()
    if len(macro_series) < 20:
        return None

    macro_change = macro_series.pct_change().dropna()
    stock_return = df["return"].loc[macro_change.index]

    valid = macro_change.notna() & stock_return.notna()
    x = macro_change[valid].values
    y = stock_return[valid].values

    if len(x) < 20:
        return None

    try:
        from scipy.stats import linregress

        result = linregress(x, y)
        return result.slope
    except Exception:
        # numpy fallback
        try:
            coef = np.polyfit(x, y, 1)
            return float(coef[0])
        except Exception:
            return None


def _sector_momentum(session: Session, sector_id: int | None) -> tuple[int | None, int | None]:
    if sector_id is None:
        return None, None
    try:
        from src.analysis.external import calculate_sector_momentum

        momentum = calculate_sector_momentum(session)
        if not momentum:
            return None, None
        # momentum: dict[sector_name, float]
        total = len(momentum)
        # 현재 섹터 순위
        from src.db.models import DimSector

        sector = session.execute(
            select(DimSector).where(DimSector.sector_id == sector_id)
        ).scalar_one_or_none()
        if not sector:
            return None, total
        name = sector.sector_name
        if name not in momentum:
            return None, total
        sorted_sectors = sorted(momentum.items(), key=lambda x: x[1], reverse=True)
        rank = next((i + 1 for i, (n, _) in enumerate(sorted_sectors) if n == name), None)
        return rank, total
    except Exception as e:
        logger.debug("섹터 모멘텀 실패: %s", e)
        return None, None


def _detect_current_regime(session: Session) -> str | None:
    try:
        from src.ai.regime import detect_regime

        return detect_regime(session)
    except Exception:
        return None


def _regime_avg_return(df: pd.DataFrame, regime: str | None) -> float | None:
    if regime is None or df.empty:
        return None
    # 간이: 전체 기간 평균 일간 수익률 × 252 (연환산)
    avg_daily = df["return"].mean()
    return avg_daily * 252 * 100 if avg_daily is not None else None


def _grade_macro(beta_vix: float | None, regime: str | None) -> str:
    if beta_vix is None:
        return "Neutral"
    # VIX 베타 음수 = VIX 상승 시 종목 하락 (일반적)
    # VIX 베타 양수 = VIX 상승 시 종목 상승 (방어적)
    if regime in ("bull", "range"):
        if beta_vix > 0:
            return "Favorable"
        if beta_vix < -0.5:
            return "Headwind"
    elif regime in ("bear", "crisis"):
        if beta_vix > 0.3:
            return "Favorable"
        if beta_vix < -0.3:
            return "Headwind"
    return "Neutral"
