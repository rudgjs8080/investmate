"""yfinance 기반 데이터 프로바이더 구현체.

기존 yahoo_client.py와 macro_collector.py의 핵심 로직을 Protocol 구현체로 캡슐화한다.
기존 모듈은 하위 호환을 위해 이 구현체를 위임 호출한다.
"""

from __future__ import annotations

import concurrent.futures
import logging
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.data.circuit_breaker import CircuitBreaker
from src.data.schemas import DailyPriceData, FinancialRecord, MacroData, ValuationRecord
from src.data.utils import extract_ticker_data, flatten_multiindex

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50
BATCH_DELAY_SEC = 1.0


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
def _download_with_retry(tickers: list[str], start: str, end: str, **kwargs):
    """리트라이 로직이 적용된 yfinance 다운로드."""
    return yf.download(tickers, start=start, end=end, progress=False, auto_adjust=False, **kwargs)


def _download_with_timeout(tickers: list[str], start: str, end: str, timeout_sec: int = 60, **kwargs):
    """타임아웃이 적용된 다운로드."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_download_with_retry, tickers, start, end, **kwargs)
        try:
            return future.result(timeout=timeout_sec)
        except concurrent.futures.TimeoutError:
            logger.warning("yfinance 타임아웃 (%ds): %s", timeout_sec, tickers[:3])
            raise TimeoutError(f"Download timed out after {timeout_sec}s")


def _df_to_prices(df: pd.DataFrame) -> list[DailyPriceData]:
    """DataFrame을 DailyPriceData 리스트로 변환한다."""
    df = flatten_multiindex(df)

    prices = []
    for idx, row in df.iterrows():
        try:
            price_date = idx.date() if hasattr(idx, "date") else idx
            vol = int(row["Volume"])
            if vol <= 0:
                continue
            prices.append(DailyPriceData(
                date=price_date,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=vol,
                adj_close=float(row.get("Adj Close", row["Close"])),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    return prices


def _safe_float_df(df, row_name: str, col) -> float | None:  # noqa: ANN001
    """DataFrame에서 안전하게 float 값을 추출한다."""
    try:
        if row_name in df.index:
            val = df.loc[row_name, col]
            if val is not None and str(val) != "nan":
                return float(val)
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _date_to_quarter(dt) -> str:  # noqa: ANN001
    """날짜를 분기 문자열로 변환한다."""
    if hasattr(dt, "quarter"):
        return f"{dt.year}Q{dt.quarter}"
    return str(dt)


# ---------------------------------------------------------------------------
# PriceProvider 구현
# ---------------------------------------------------------------------------

class YFinancePriceProvider:
    """yfinance 기반 가격 데이터 프로바이더."""

    def __init__(
        self,
        batch_size: int = DEFAULT_BATCH_SIZE,
        batch_delay: float = BATCH_DELAY_SEC,
        breaker: CircuitBreaker | None = None,
    ):
        self._batch_size = batch_size
        self._batch_delay = batch_delay
        self._breaker = breaker or CircuitBreaker(fail_threshold=5, reset_seconds=60)

    def fetch_prices(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        batch_size: int | None = None,
    ) -> tuple[dict[str, list[DailyPriceData]], list[str]]:
        """여러 종목의 일봉 데이터를 배치로 다운로드한다."""
        bs = batch_size or self._batch_size
        result: dict[str, list[DailyPriceData]] = {}
        failed_tickers: list[str] = []
        end_str = (end_date + timedelta(days=1)).isoformat()
        start_str = start_date.isoformat()

        for i in range(0, len(tickers), bs):
            batch = tickers[i:i + bs]
            logger.info(
                "배치 다운로드 %d/%d (%d종목)",
                i // bs + 1,
                (len(tickers) + bs - 1) // bs,
                len(batch),
            )

            if self._breaker.is_open:
                logger.warning("서킷브레이커 OPEN, 배치 스킵: %s", batch[:3])
                failed_tickers.extend(batch)
                continue

            try:
                df = _download_with_timeout(
                    batch, start_str, end_str,
                    group_by="ticker" if len(batch) > 1 else "column",
                )
                self._breaker.record_success()

                if df.empty:
                    continue

                if len(batch) == 1:
                    result[batch[0]] = _df_to_prices(df)
                else:
                    for ticker in batch:
                        try:
                            if ticker in df.columns.get_level_values(0):
                                ticker_df = df[ticker].dropna(how="all")
                                if not ticker_df.empty:
                                    result[ticker] = _df_to_prices(ticker_df)
                                else:
                                    failed_tickers.append(ticker)
                            else:
                                failed_tickers.append(ticker)
                        except (KeyError, TypeError):
                            failed_tickers.append(ticker)

            except Exception as e:
                self._breaker.record_failure()
                logger.warning("배치 다운로드 실패 [%d-%d]: %s", i, i + len(batch), e)
                failed_tickers.extend(batch)

            if i + bs < len(tickers):
                time.sleep(self._batch_delay)

        if failed_tickers:
            logger.warning(
                "배치 실패 종목 %d개: %s",
                len(failed_tickers),
                ", ".join(failed_tickers[:10]) + ("..." if len(failed_tickers) > 10 else ""),
            )
        logger.info("배치 다운로드 완료: %d/%d 종목 성공", len(result), len(tickers))
        return result, failed_tickers


# ---------------------------------------------------------------------------
# FinancialProvider 구현
# ---------------------------------------------------------------------------

class YFinanceFinancialProvider:
    """yfinance 기반 재무 데이터 프로바이더."""

    def fetch_financials(
        self, ticker: str,
    ) -> tuple[list[FinancialRecord], ValuationRecord | None]:
        """분기별 재무 데이터와 밸류에이션을 조회한다."""
        try:
            t = yf.Ticker(ticker)
            financials: list[FinancialRecord] = []

            q_income = t.quarterly_income_stmt
            q_balance = t.quarterly_balance_sheet

            if q_income is not None and not q_income.empty:
                for col in q_income.columns:
                    period = _date_to_quarter(col)
                    rec: dict = {"period": period}

                    rec["revenue"] = _safe_float_df(q_income, "Total Revenue", col)
                    rec["operating_income"] = _safe_float_df(q_income, "Operating Income", col)
                    rec["net_income"] = _safe_float_df(q_income, "Net Income", col)

                    if q_balance is not None and col in q_balance.columns:
                        rec["total_assets"] = _safe_float_df(q_balance, "Total Assets", col)
                        rec["total_liabilities"] = _safe_float_df(
                            q_balance, "Total Liabilities Net Minority Interest", col
                        ) or _safe_float_df(q_balance, "Total Debt", col)
                        rec["total_equity"] = _safe_float_df(
                            q_balance, "Total Equity Gross Minority Interest", col
                        ) or _safe_float_df(q_balance, "Stockholders Equity", col)

                    try:
                        q_cf = t.quarterly_cashflow
                        if q_cf is not None and col in q_cf.columns:
                            rec["operating_cashflow"] = _safe_float_df(
                                q_cf, "Operating Cash Flow", col
                            )
                    except Exception:
                        pass

                    financials.append(FinancialRecord(**rec))

            info = t.info
            valuation = None
            if info:
                valuation = ValuationRecord(
                    date=date.today(),
                    market_cap=info.get("marketCap"),
                    per=info.get("trailingPE"),
                    pbr=info.get("priceToBook"),
                    roe=info.get("returnOnEquity"),
                    dividend_yield=info.get("dividendYield"),
                    ev_ebitda=info.get("enterpriseToEbitda"),
                )
                if financials and financials[0].total_assets and financials[0].total_liabilities:
                    valuation = valuation.model_copy(update={
                        "debt_ratio": financials[0].total_liabilities / financials[0].total_assets,
                    })

            return financials, valuation

        except Exception as e:
            logger.warning("재무 데이터 조회 실패 [%s]: %s", ticker, e)
            return [], None


# ---------------------------------------------------------------------------
# MacroProvider 구현
# ---------------------------------------------------------------------------

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


class YFinanceMacroProvider:
    """yfinance 기반 매크로 지표 프로바이더."""

    def fetch_macro(self, target_date: date) -> MacroData:
        """매크로 지표를 수집한다."""
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
                ticker_df = extract_ticker_data(df, ticker, all_tickers)
                if ticker_df is None:
                    continue

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
                    pass  # yield_spread 계산에만 사용
                else:
                    result[key] = latest_close
                collected += 1

            except Exception as e:
                logger.warning("매크로 지표 추출 실패 [%s]: %s", ticker, e)

        if collected < 3:
            logger.warning("매크로 데이터 불충분: %d/%d 지표만 수집됨", collected, len(MACRO_TICKERS))

        # yield_spread 계산
        us_10y = result.get("us_10y_yield")
        us_13w = result.get("us_13w_yield")
        if us_10y is not None and us_13w is not None:
            result["yield_spread"] = round(us_10y - us_13w, 4)

        return MacroData(**result)
