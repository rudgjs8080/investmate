"""데이터 수집 공용 유틸리티."""

from __future__ import annotations

import pandas as pd


def safe_float(val) -> float | None:  # noqa: ANN001
    """NaN-safe float 변환.

    None, NaN, 변환 불가 값은 None을 반환한다.
    """
    if val is None:
        return None
    try:
        f = float(val)
        return None if str(f) == "nan" else f
    except (TypeError, ValueError):
        return None


def flatten_multiindex(df: pd.DataFrame) -> pd.DataFrame:
    """MultiIndex 컬럼을 단일 레벨로 정리한다.

    yfinance가 멀티 티커 다운로드 시 반환하는 MultiIndex 컬럼을
    첫 번째 레벨만 남기고 평탄화한다. 이미 단일 레벨이면 그대로 반환.
    """
    if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def extract_ticker_data(
    df: pd.DataFrame,
    ticker: str,
    all_tickers: list[str],
) -> pd.DataFrame | None:
    """MultiIndex DataFrame에서 특정 티커 데이터를 추출한다.

    Args:
        df: yfinance 배치 다운로드 결과.
        ticker: 추출할 티커 심볼.
        all_tickers: 전체 다운로드된 티커 목록.

    Returns:
        해당 티커의 DataFrame, 없으면 None.
    """
    try:
        if len(all_tickers) > 1 and ticker in df.columns.get_level_values(0):
            ticker_df = df[ticker].dropna(how="all")
        elif len(all_tickers) == 1:
            ticker_df = df
        else:
            return None

        if ticker_df.empty:
            return None

        return flatten_multiindex(ticker_df)
    except (KeyError, TypeError):
        return None
