"""시그널 감지 모듈 — 매수/매도 시그널 생성."""

from __future__ import annotations

import pandas as pd

from src.data.schemas import SignalData

# RSI 임계값 상수
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# 시그널 가중치 (복합 강도 계산용)
_SIGNAL_WEIGHTS = {
    "golden_cross": 3,
    "death_cross": 3,
    "rsi_oversold": 2,
    "rsi_overbought": 2,
    "macd_bullish": 2,
    "macd_bearish": 2,
    "bb_lower_break": 1,
    "bb_upper_break": 1,
    "stoch_bullish": 1,
    "stoch_bearish": 1,
}


def detect_signals(
    indicators_df: pd.DataFrame, stock_id: int
) -> list[SignalData]:
    """최신 데이터에서 시그널을 감지한다.

    Args:
        indicators_df: 기술적 지표가 포함된 DataFrame (date를 인덱스로 사용).
        stock_id: 종목 ID (시그널 메타정보용).

    Returns:
        감지된 시그널 리스트.
    """
    if len(indicators_df) < 2:
        return []

    today = indicators_df.iloc[-1]
    yesterday = indicators_df.iloc[-2]
    signal_date = indicators_df.index[-1]

    signals: list[SignalData] = []

    # Golden Cross: SMA20이 SMA60을 상향 돌파
    if _crossover(yesterday, today, "sma_20", "sma_60"):
        signals.append(
            SignalData(
                date=signal_date,
                signal_type="golden_cross",
                direction="BUY",
                strength=8,
                description="SMA 20이 SMA 60을 상향 돌파 (골든크로스)",
            )
        )

    # Death Cross: SMA20이 SMA60을 하향 돌파
    if _crossunder(yesterday, today, "sma_20", "sma_60"):
        signals.append(
            SignalData(
                date=signal_date,
                signal_type="death_cross",
                direction="SELL",
                strength=8,
                description="SMA 20이 SMA 60을 하향 돌파 (데드크로스)",
            )
        )

    # RSI 과매도
    rsi = _safe_val(today, "rsi_14")
    if rsi is not None and rsi < RSI_OVERSOLD:
        strength = max(1, min(10, int((30 - rsi) / 3 + 5)))
        signals.append(
            SignalData(
                date=signal_date,
                signal_type="rsi_oversold",
                direction="BUY",
                strength=strength,
                description=f"RSI {rsi:.1f} — 과매도 구간 진입",
            )
        )

    # RSI 과매수
    if rsi is not None and rsi > RSI_OVERBOUGHT:
        strength = max(1, min(10, int((rsi - 70) / 3 + 5)))
        signals.append(
            SignalData(
                date=signal_date,
                signal_type="rsi_overbought",
                direction="SELL",
                strength=strength,
                description=f"RSI {rsi:.1f} — 과매수 구간 진입",
            )
        )

    # MACD 상향 돌파
    if _crossover(yesterday, today, "macd", "macd_signal"):
        signals.append(
            SignalData(
                date=signal_date,
                signal_type="macd_bullish",
                direction="BUY",
                strength=7,
                description="MACD가 시그널선을 상향 돌파",
            )
        )

    # MACD 하향 돌파
    if _crossunder(yesterday, today, "macd", "macd_signal"):
        signals.append(
            SignalData(
                date=signal_date,
                signal_type="macd_bearish",
                direction="SELL",
                strength=7,
                description="MACD가 시그널선을 하향 돌파",
            )
        )

    # 볼린저 밴드 하단 이탈
    close = _safe_val(today, "close")
    bb_lower = _safe_val(today, "bb_lower")
    if close is not None and bb_lower is not None and close < bb_lower:
        signals.append(
            SignalData(
                date=signal_date,
                signal_type="bb_lower_break",
                direction="BUY",
                strength=6,
                description="종가가 볼린저 밴드 하단을 이탈",
            )
        )

    # 볼린저 밴드 상단 이탈
    bb_upper = _safe_val(today, "bb_upper")
    if close is not None and bb_upper is not None and close > bb_upper:
        signals.append(
            SignalData(
                date=signal_date,
                signal_type="bb_upper_break",
                direction="SELL",
                strength=6,
                description="종가가 볼린저 밴드 상단을 이탈",
            )
        )

    # 스토캐스틱 과매도/매수 전환
    stoch_k = _safe_val(today, "stoch_k")
    stoch_d = _safe_val(today, "stoch_d")
    prev_stoch_k = _safe_val(yesterday, "stoch_k")
    if stoch_k is not None and stoch_d is not None:
        # K가 D를 상향 돌파 + K < 50 (매수 전환)
        if prev_stoch_k is not None and prev_stoch_k <= stoch_d and stoch_k > stoch_d and stoch_k < 50:
            signals.append(
                SignalData(
                    date=signal_date,
                    signal_type="stoch_bullish",
                    direction="BUY",
                    strength=5,
                    description=f"스토캐스틱 K({stoch_k:.0f})가 D({stoch_d:.0f})를 상향 돌파",
                )
            )
        # K가 D를 하향 돌파 + K > 50 (매도 전환)
        elif prev_stoch_k is not None and prev_stoch_k >= stoch_d and stoch_k < stoch_d and stoch_k > 50:
            signals.append(
                SignalData(
                    date=signal_date,
                    signal_type="stoch_bearish",
                    direction="SELL",
                    strength=5,
                    description=f"스토캐스틱 K({stoch_k:.0f})가 D({stoch_d:.0f})를 하향 돌파",
                )
            )

    return signals


def calculate_composite_strength(signals: list[SignalData]) -> int:
    """시그널들의 가중 합산 복합 강도를 계산한다 (1-10)."""
    if not signals:
        return 0

    total_weight = sum(
        _SIGNAL_WEIGHTS.get(s.signal_type, 1) for s in signals
    )
    weighted_sum = sum(
        s.strength * _SIGNAL_WEIGHTS.get(s.signal_type, 1) for s in signals
    )

    if total_weight == 0:
        return 0

    return max(1, min(10, round(weighted_sum / total_weight)))


def _safe_val(row: pd.Series, col: str) -> float | None:
    """시리즈에서 안전하게 값을 추출한다."""
    try:
        val = row.get(col)
        if val is not None and not pd.isna(val):
            return float(val)
    except (TypeError, ValueError):
        pass
    return None


def _crossover(
    prev: pd.Series, curr: pd.Series, fast: str, slow: str
) -> bool:
    """fast가 slow를 상향 돌파했는지 확인한다."""
    prev_fast = _safe_val(prev, fast)
    prev_slow = _safe_val(prev, slow)
    curr_fast = _safe_val(curr, fast)
    curr_slow = _safe_val(curr, slow)

    if any(v is None for v in [prev_fast, prev_slow, curr_fast, curr_slow]):
        return False

    return prev_fast <= prev_slow and curr_fast > curr_slow


def _crossunder(
    prev: pd.Series, curr: pd.Series, fast: str, slow: str
) -> bool:
    """fast가 slow를 하향 돌파했는지 확인한다."""
    prev_fast = _safe_val(prev, fast)
    prev_slow = _safe_val(prev, slow)
    curr_fast = _safe_val(curr, fast)
    curr_slow = _safe_val(curr, slow)

    if any(v is None for v in [prev_fast, prev_slow, curr_fast, curr_slow]):
        return False

    return prev_fast >= prev_slow and curr_fast < curr_slow
