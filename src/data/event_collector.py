"""이벤트 캘린더 수집 — 실적 발표일, Fed 일정 등 주가에 영향을 주는 이벤트."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from dataclasses import dataclass

import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EarningsEvent:
    """실적 발표 이벤트."""

    ticker: str
    earnings_date: date
    days_until: int  # 양수 = 미래, 음수 = 과거
    eps_estimate: float | None = None
    revenue_estimate: float | None = None


@dataclass(frozen=True)
class StockEventContext:
    """종목별 이벤트 컨텍스트 (프롬프트 삽입용)."""

    ticker: str
    next_earnings: EarningsEvent | None = None
    recent_earnings: EarningsEvent | None = None
    is_pre_earnings: bool = False  # 2주 내 실적 발표 예정
    is_post_earnings: bool = False  # 최근 2주 내 실적 발표 완료


# 2026 FOMC 일정 (hardcoded — 매년 초 업데이트)
FOMC_DATES_2026 = [
    date(2026, 1, 28), date(2026, 1, 29),
    date(2026, 3, 18), date(2026, 3, 19),
    date(2026, 5, 6), date(2026, 5, 7),
    date(2026, 6, 17), date(2026, 6, 18),
    date(2026, 7, 29), date(2026, 7, 30),
    date(2026, 9, 16), date(2026, 9, 17),
    date(2026, 11, 4), date(2026, 11, 5),
    date(2026, 12, 16), date(2026, 12, 17),
]


def get_next_fomc_date(from_date: date) -> tuple[date, int] | None:
    """다음 FOMC 회의일과 남은 일수를 반환한다."""
    for fomc in FOMC_DATES_2026:
        if fomc >= from_date:
            return fomc, (fomc - from_date).days
    return None


def collect_earnings_calendar(tickers: list[str], reference_date: date) -> dict[str, StockEventContext]:
    """종목별 실적 발표 일정을 수집한다.

    Args:
        tickers: 조회할 종목 리스트.
        reference_date: 기준일.

    Returns:
        {ticker: StockEventContext}
    """
    result: dict[str, StockEventContext] = {}

    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None or (isinstance(cal, dict) and not cal):
                result[ticker] = StockEventContext(ticker=ticker)
                continue

            # yfinance calendar 형태가 다양함
            earnings_date = None
            if isinstance(cal, dict):
                ed = cal.get("Earnings Date")
                if ed and isinstance(ed, list) and len(ed) > 0:
                    if isinstance(ed[0], datetime):
                        earnings_date = ed[0].date()
                    elif isinstance(ed[0], date):
                        earnings_date = ed[0]

            if earnings_date is None:
                result[ticker] = StockEventContext(ticker=ticker)
                continue

            days_until = (earnings_date - reference_date).days

            event = EarningsEvent(
                ticker=ticker,
                earnings_date=earnings_date,
                days_until=days_until,
            )

            result[ticker] = StockEventContext(
                ticker=ticker,
                next_earnings=event if days_until > 0 else None,
                recent_earnings=event if days_until <= 0 else None,
                is_pre_earnings=0 < days_until <= 14,
                is_post_earnings=-14 <= days_until <= 0,
            )
        except Exception as e:
            logger.debug("실적 캘린더 수집 실패 [%s]: %s", ticker, e)
            result[ticker] = StockEventContext(ticker=ticker)

    return result
