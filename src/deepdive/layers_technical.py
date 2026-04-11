"""Layer 3: 멀티타임프레임 기술적 분석."""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FactDailyPrice
from src.deepdive.layers_utils import round_or_none
from src.deepdive.schemas import TechnicalProfile

logger = logging.getLogger(__name__)


def compute_layer3_technical(
    session: Session, stock_id: int, date_id: int,
) -> TechnicalProfile | None:
    """멀티TF 기술적 분석: 추세 정렬, 52주 위치, RSI, S/R, 상대강도."""
    try:
        return _compute(session, stock_id, date_id)
    except Exception as e:
        logger.warning("Layer 3 계산 실패 (stock_id=%d): %s", stock_id, e)
        return None


def _compute(session: Session, stock_id: int, date_id: int) -> TechnicalProfile | None:
    prices = list(
        session.execute(
            select(FactDailyPrice)
            .where(FactDailyPrice.stock_id == stock_id, FactDailyPrice.date_id <= date_id)
            .order_by(FactDailyPrice.date_id.desc())
            .limit(252)
        ).scalars().all()
    )
    if len(prices) < 50:
        return None

    prices.reverse()
    df = pd.DataFrame([
        {
            "date": p.date_id,
            "open": float(p.open), "high": float(p.high),
            "low": float(p.low), "close": float(p.close),
            "volume": int(p.volume) if p.volume else 0,
        }
        for p in prices
    ])
    close = df["close"].iloc[-1]

    from src.analysis.technical import calculate_indicators

    ind_df = calculate_indicators(df)

    rsi = float(ind_df["rsi_14"].iloc[-1]) if "rsi_14" in ind_df.columns and pd.notna(ind_df["rsi_14"].iloc[-1]) else None

    macd_signal = None
    if "macd" in ind_df.columns and "macd_signal" in ind_df.columns:
        macd_val = ind_df["macd"].iloc[-1]
        macd_sig = ind_df["macd_signal"].iloc[-1]
        if pd.notna(macd_val) and pd.notna(macd_sig):
            macd_signal = "bullish" if macd_val > macd_sig else "bearish"

    high_52w = df["high"].tail(252).max()
    low_52w = df["low"].tail(252).min()
    pos_52w = ((close - low_52w) / (high_52w - low_52w) * 100) if high_52w > low_52w else 50.0

    sma20 = _safe_sma(ind_df, "sma_20")
    sma50 = _safe_sma(ind_df, "sma_50")
    trend_alignment = _detect_trend_alignment(close, sma20, sma50)

    from src.analysis.support_resistance import find_support_resistance

    sr = find_support_resistance(df)
    nearest_support = sr.supports[0].price if sr.supports else None
    nearest_resistance = sr.resistances[0].price if sr.resistances else None

    atr_regime = _detect_atr_regime(df)
    atr_14_value = _compute_atr_14(df)

    bullish_count = 0
    bearish_count = 0
    if rsi is not None:
        if rsi < 30:
            bullish_count += 1
        elif rsi > 70:
            bearish_count += 1
    if macd_signal == "bullish":
        bullish_count += 1
    elif macd_signal == "bearish":
        bearish_count += 1
    if trend_alignment == "aligned_up":
        bullish_count += 2
    elif trend_alignment == "aligned_down":
        bearish_count += 2
    if pos_52w > 80:
        bullish_count += 1
    elif pos_52w < 20:
        bearish_count += 1

    grade = "Bullish" if bullish_count >= 3 else ("Bearish" if bearish_count >= 3 else "Neutral")

    return TechnicalProfile(
        technical_grade=grade,
        trend_alignment=trend_alignment,
        position_52w_pct=round(pos_52w, 1),
        rsi=round(rsi, 1) if rsi is not None else None,
        macd_signal=macd_signal,
        nearest_support=round_or_none(nearest_support),
        nearest_resistance=round_or_none(nearest_resistance),
        relative_strength_pct=None,
        atr_regime=atr_regime,
        metrics={
            "high_52w": round(high_52w, 2), "low_52w": round(low_52w, 2),
            "sma20": round(sma20, 2) if sma20 else None,
            "sma50": round(sma50, 2) if sma50 else None,
            "price_count": len(prices),
            "atr_14": round(atr_14_value, 4) if atr_14_value is not None else None,
            "current_close": round(float(close), 2),
        },
    )


def _safe_sma(ind_df: pd.DataFrame, col: str) -> float | None:
    if col not in ind_df.columns:
        return None
    val = ind_df[col].iloc[-1]
    return float(val) if pd.notna(val) else None


def _detect_trend_alignment(close: float, sma20: float | None, sma50: float | None) -> str:
    if sma20 is None or sma50 is None:
        return "mixed"
    if close > sma20 > sma50:
        return "aligned_up"
    if close < sma20 < sma50:
        return "aligned_down"
    return "mixed"


def _detect_atr_regime(df: pd.DataFrame) -> str:
    if len(df) < 34:
        return "Normal"
    high = df["high"].tail(14)
    low = df["low"].tail(14)
    close_prev = df["close"].shift(1).tail(14)
    tr = pd.concat([high - low, (high - close_prev).abs(), (low - close_prev).abs()], axis=1).max(axis=1)
    atr_14 = tr.mean()
    close_now = df["close"].iloc[-1]
    atr_pct = (atr_14 / close_now * 100) if close_now > 0 else 0
    if atr_pct > 3.0:
        return "High"
    if atr_pct < 1.0:
        return "Low"
    return "Normal"


def _compute_atr_14(df: pd.DataFrame) -> float | None:
    """ATR(14) 원시값 — execution guide가 손절 계산에 사용."""
    if len(df) < 15:
        return None
    high = df["high"].tail(14)
    low = df["low"].tail(14)
    close_prev = df["close"].shift(1).tail(14)
    tr = pd.concat(
        [high - low, (high - close_prev).abs(), (low - close_prev).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.mean()
    return float(atr) if pd.notna(atr) else None
