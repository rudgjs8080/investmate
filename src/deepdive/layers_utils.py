"""Deep Dive 레이어 공용 유틸리티."""

from __future__ import annotations

import pandas as pd


def sf(v) -> float | None:
    """safe float 변환."""
    if v is None:
        return None
    try:
        f = float(v)
        return f if not pd.isna(f) else None
    except (TypeError, ValueError):
        return None


def calc_ratio(numerator: float | None, denominator: float | None) -> float | None:
    """안전한 비율 계산 (% 스케일)."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator * 100, 2)


def round_or_none(v: float | None, digits: int = 2) -> float | None:
    """None-safe 반올림."""
    return round(v, digits) if v is not None else None
