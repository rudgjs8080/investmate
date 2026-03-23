"""지지/저항 수준 자동 감지 — swing high/low 클러스터링."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PriceLevel:
    """가격 수준."""

    price: float
    strength: int  # 1-10 (터치 횟수 기반)
    touch_count: int
    level_type: str  # "support" or "resistance"


@dataclass(frozen=True)
class SRLevels:
    """지지/저항 수준 분석 결과."""

    supports: tuple[PriceLevel, ...] = ()
    resistances: tuple[PriceLevel, ...] = ()
    current_price: float = 0.0
    nearest_support_pct: float | None = None  # 현재가 대비 가장 가까운 지지선 %
    nearest_resistance_pct: float | None = None


def find_support_resistance(
    df: pd.DataFrame,
    window: int = 20,
    num_levels: int = 3,
    cluster_pct: float = 0.02,
) -> SRLevels:
    """가격 데이터에서 지지/저항 수준을 감지한다.

    Args:
        df: 가격 DataFrame (close, high, low 컬럼 필요).
        window: 피벗 감지 윈도우 크기.
        num_levels: 반환할 지지/저항 수 (각각).
        cluster_pct: 클러스터링 기준 (2% 이내 같은 레벨로 취급).

    Returns:
        SRLevels with detected support and resistance levels.
    """
    if df.empty or len(df) < window * 2:
        return SRLevels()

    closes = df["close"].values.astype(float)
    highs = df["high"].values.astype(float) if "high" in df.columns else closes
    lows = df["low"].values.astype(float) if "low" in df.columns else closes
    current_price = float(closes[-1])

    # 로컬 최솟값 (지지) 감지
    support_pivots = []
    for i in range(window, len(lows) - window):
        if lows[i] == min(lows[i - window:i + window + 1]):
            support_pivots.append(float(lows[i]))

    # 로컬 최댓값 (저항) 감지
    resistance_pivots = []
    for i in range(window, len(highs) - window):
        if highs[i] == max(highs[i - window:i + window + 1]):
            resistance_pivots.append(float(highs[i]))

    # 클러스터링 + 강도 계산
    def cluster_levels(pivots: list[float], level_type: str) -> list[PriceLevel]:
        if not pivots:
            return []
        pivots_sorted = sorted(pivots)
        clusters: list[list[float]] = [[pivots_sorted[0]]]

        for p in pivots_sorted[1:]:
            if (p - clusters[-1][-1]) / clusters[-1][-1] < cluster_pct:
                clusters[-1].append(p)
            else:
                clusters.append([p])

        levels = []
        for cluster in clusters:
            avg_price = sum(cluster) / len(cluster)
            touch_count = len(cluster)
            strength = min(10, max(1, touch_count * 2))
            levels.append(PriceLevel(
                price=round(avg_price, 2),
                strength=strength,
                touch_count=touch_count,
                level_type=level_type,
            ))

        # 터치 횟수로 정렬
        return sorted(levels, key=lambda l: -l.touch_count)

    supports = cluster_levels(
        [p for p in support_pivots if p < current_price],
        "support",
    )[:num_levels]

    resistances = cluster_levels(
        [p for p in resistance_pivots if p > current_price],
        "resistance",
    )[:num_levels]

    # 가장 가까운 S/R 까지 거리
    nearest_sup = None
    if supports:
        nearest_sup = round((current_price - supports[0].price) / current_price * 100, 2)

    nearest_res = None
    if resistances:
        nearest_res = round((resistances[0].price - current_price) / current_price * 100, 2)

    return SRLevels(
        supports=tuple(supports),
        resistances=tuple(resistances),
        current_price=current_price,
        nearest_support_pct=nearest_sup,
        nearest_resistance_pct=nearest_res,
    )
