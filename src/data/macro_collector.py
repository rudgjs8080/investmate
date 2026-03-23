"""매크로 지표 수집 모듈."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import yfinance as yf
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.data.schemas import MacroData

logger = logging.getLogger(__name__)

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
def _download_macro_with_retry(tickers: list[str], start: str, end: str):
    """리트라이 로직이 적용된 매크로 다운로드."""
    return yf.download(
        tickers, start=start, end=end, progress=False,
        group_by="ticker" if len(tickers) > 1 else "column",
    )


MACRO_TICKERS = {
    "vix": "^VIX",
    "us_10y_yield": "^TNX",
    "us_13w_yield": "^IRX",
    "dollar_index": "DX-Y.NYB",
    "sp500": "^GSPC",
    "gold": "GC=F",
    "oil": "CL=F",
    "us_5y_yield": "^FVX",
}


def collect_macro(target_date: date) -> MacroData:
    """매크로 지표를 수집한다.

    yfinance 배치 다운로드로 VIX, 금리, 달러 인덱스, S&P 500을 1회에 수집하고
    S&P 500의 20일 SMA를 계산한다.
    """
    result: dict = {"date": target_date}
    all_tickers = list(MACRO_TICKERS.values())

    start = (target_date - timedelta(days=30)).isoformat()
    end = (target_date + timedelta(days=1)).isoformat()

    try:
        df = _download_macro_with_retry(all_tickers, start=start, end=end)
    except Exception as e:
        logger.warning("매크로 배치 다운로드 실패: %s", e)
        return MacroData(**result)

    if df.empty:
        logger.warning("매크로 데이터 비어 있음")
        return MacroData(**result)

    collected = 0
    for key, ticker in MACRO_TICKERS.items():
        try:
            # MultiIndex에서 해당 티커 추출
            if len(all_tickers) > 1 and ticker in df.columns.get_level_values(0):
                ticker_df = df[ticker].dropna(how="all")
            elif len(all_tickers) == 1:
                ticker_df = df
            else:
                continue

            if ticker_df.empty:
                continue

            # MultiIndex 컬럼 정리
            if hasattr(ticker_df.columns, "levels") and len(ticker_df.columns.levels) > 1:
                ticker_df.columns = ticker_df.columns.get_level_values(0)

            latest_close = float(ticker_df["Close"].iloc[-1])

            if key == "sp500":
                result["sp500_close"] = latest_close
                if len(ticker_df) >= 20:
                    result["sp500_sma20"] = float(ticker_df["Close"].tail(20).mean())
            elif key == "gold":
                result["gold_price"] = latest_close
            elif key == "oil":
                result["oil_price"] = latest_close
            elif key == "us_5y_yield":
                pass  # 5년물은 yield_spread 계산에만 사용
            else:
                result[key] = latest_close
            collected += 1

        except Exception as e:
            logger.warning("매크로 지표 추출 실패 [%s]: %s", ticker, e)

    if collected < 3:
        logger.warning("매크로 데이터 불충분: %d/%d 지표만 수집됨", collected, len(MACRO_TICKERS))

    # yield_spread 계산: 10년물 - 13주물
    us_10y = result.get("us_10y_yield")
    us_13w = result.get("us_13w_yield")
    if us_10y is not None and us_13w is not None:
        result["yield_spread"] = round(us_10y - us_13w, 4)

    return MacroData(**result)
