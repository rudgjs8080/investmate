"""yfinance 래퍼 — 배치 다운로드 지원."""

from __future__ import annotations

import concurrent.futures
import logging
import time
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.data.schemas import DailyPriceData, FinancialRecord, StockInfo, ValuationRecord

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 50
BATCH_DELAY_SEC = 1.0


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------

class CircuitBreaker:
    """간이 서킷브레이커 — 연속 실패 시 호출 차단."""

    def __init__(self, fail_threshold: int = 5, reset_seconds: int = 60):
        self._failures = 0
        self._threshold = fail_threshold
        self._reset_at = 0.0
        self._reset_seconds = reset_seconds

    def record_failure(self):
        self._failures += 1
        if self._failures >= self._threshold:
            self._reset_at = time.time() + self._reset_seconds
            logger.warning("서킷브레이커 OPEN: %d회 연속 실패", self._failures)

    def record_success(self):
        if self._failures > 0:
            self._failures = 0

    @property
    def is_open(self) -> bool:
        if self._failures < self._threshold:
            return False
        if time.time() >= self._reset_at:
            self._failures = 0  # Reset after timeout
            return False
        return True


_yf_breaker = CircuitBreaker(fail_threshold=5, reset_seconds=60)


# ---------------------------------------------------------------------------
# Retry + Timeout helpers
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


def batch_download_prices(
    tickers: list[str],
    start_date: date,
    end_date: date,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[dict[str, list[DailyPriceData]], list[str]]:
    """여러 종목의 일봉 데이터를 배치로 다운로드한다.

    Returns:
        (성공 데이터, 실패 티커 리스트) 튜플.
    """
    result: dict[str, list[DailyPriceData]] = {}
    failed_tickers: list[str] = []
    end_str = (end_date + timedelta(days=1)).isoformat()
    start_str = start_date.isoformat()

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        logger.info(
            "배치 다운로드 %d/%d (%d종목)",
            i // batch_size + 1,
            (len(tickers) + batch_size - 1) // batch_size,
            len(batch),
        )

        if _yf_breaker.is_open:
            logger.warning("서킷브레이커 OPEN, 배치 스킵: %s", batch[:3])
            failed_tickers.extend(batch)
            continue

        try:
            df = _download_with_timeout(
                batch,
                start_str,
                end_str,
                group_by="ticker" if len(batch) > 1 else "column",
            )
            _yf_breaker.record_success()

            if df.empty:
                continue

            if len(batch) == 1:
                # 단일 티커: 컬럼이 바로 OHLCV
                ticker = batch[0]
                result[ticker] = _df_to_prices(df)
            else:
                # 멀티 티커: MultiIndex 컬럼 (ticker, field)
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
            _yf_breaker.record_failure()
            logger.warning("배치 다운로드 실패 [%d-%d]: %s", i, i + len(batch), e)
            failed_tickers.extend(batch)

        if i + batch_size < len(tickers):
            time.sleep(BATCH_DELAY_SEC)

    if failed_tickers:
        logger.warning(
            "배치 실패 종목 %d개: %s",
            len(failed_tickers),
            ", ".join(failed_tickers[:10]) + ("..." if len(failed_tickers) > 10 else ""),
        )
    logger.info("배치 다운로드 완료: %d/%d 종목 성공", len(result), len(tickers))
    return result, failed_tickers


def _df_to_prices(df: pd.DataFrame) -> list[DailyPriceData]:
    """DataFrame을 DailyPriceData 리스트로 변환한다."""
    # MultiIndex 컬럼 정리
    if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
        df.columns = df.columns.get_level_values(0)

    prices = []
    for idx, row in df.iterrows():
        try:
            price_date = idx.date() if hasattr(idx, "date") else idx
            vol = int(row["Volume"])
            if vol <= 0:
                continue  # 거래량 0 = 데이터 불완전, skip
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


def fetch_stock_info(ticker: str) -> StockInfo | None:
    """종목 기본 정보를 조회한다."""
    try:
        info = yf.Ticker(ticker).info
        if not info or info.get("regularMarketPrice") is None:
            return None

        name = info.get("shortName") or info.get("longName") or ticker
        sector = info.get("sector")
        industry = info.get("industry")

        return StockInfo(ticker=ticker, name=name, sector=sector, industry=industry)
    except Exception as e:
        logger.warning("종목 정보 조회 실패 [%s]: %s", ticker, e)
        return None


def fetch_financial_data(
    ticker: str,
) -> tuple[list[FinancialRecord], ValuationRecord | None]:
    """분기별 재무 데이터와 밸류에이션을 조회한다.

    Returns:
        (원본 재무 리스트, 최신 밸류에이션)
    """
    try:
        t = yf.Ticker(ticker)
        financials: list[FinancialRecord] = []

        q_income = t.quarterly_income_stmt
        q_balance = t.quarterly_balance_sheet

        if q_income is not None and not q_income.empty:
            for col in q_income.columns:
                period = _date_to_quarter(col)
                rec: dict = {"period": period}

                rec["revenue"] = _safe_float(q_income, "Total Revenue", col)
                rec["operating_income"] = _safe_float(q_income, "Operating Income", col)
                rec["net_income"] = _safe_float(q_income, "Net Income", col)

                if q_balance is not None and col in q_balance.columns:
                    rec["total_assets"] = _safe_float(q_balance, "Total Assets", col)
                    rec["total_liabilities"] = _safe_float(
                        q_balance, "Total Liabilities Net Minority Interest", col
                    ) or _safe_float(q_balance, "Total Debt", col)
                    rec["total_equity"] = _safe_float(
                        q_balance, "Total Equity Gross Minority Interest", col
                    ) or _safe_float(q_balance, "Stockholders Equity", col)

                # 현금흐름
                try:
                    q_cf = t.quarterly_cashflow
                    if q_cf is not None and col in q_cf.columns:
                        rec["operating_cashflow"] = _safe_float(
                            q_cf, "Operating Cash Flow", col
                        )
                except Exception:
                    pass

                financials.append(FinancialRecord(**rec))

        # 밸류에이션
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
            # debt_ratio 계산
            if financials and financials[0].total_assets and financials[0].total_liabilities:
                valuation = valuation.model_copy(update={
                    "debt_ratio": financials[0].total_liabilities / financials[0].total_assets,
                })

        return financials, valuation

    except Exception as e:
        logger.warning("재무 데이터 조회 실패 [%s]: %s", ticker, e)
        return [], None


def _safe_float(df, row_name: str, col) -> float | None:  # noqa: ANN001
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
