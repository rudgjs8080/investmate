"""데이터 프로바이더 Protocol 정의.

yfinance, Polygon.io, EODHD 등 데이터 소스를 교체 가능하게 하는 인터페이스.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from src.data.schemas import DailyPriceData, FinancialRecord, MacroData, ValuationRecord


class PriceProvider(Protocol):
    """가격 데이터 프로바이더."""

    def fetch_prices(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        batch_size: int = 50,
    ) -> tuple[dict[str, list[DailyPriceData]], list[str]]:
        """여러 종목의 일봉 데이터를 배치로 다운로드한다.

        Returns:
            (성공 데이터, 실패 티커 리스트) 튜플.
        """
        ...


class FinancialProvider(Protocol):
    """재무 데이터 프로바이더."""

    def fetch_financials(
        self, ticker: str,
    ) -> tuple[list[FinancialRecord], ValuationRecord | None]:
        """분기별 재무 데이터와 밸류에이션을 조회한다.

        Returns:
            (재무 리스트, 최신 밸류에이션) 튜플.
        """
        ...


class MacroProvider(Protocol):
    """매크로 지표 프로바이더."""

    def fetch_macro(self, target_date: date) -> MacroData:
        """매크로 지표를 수집한다."""
        ...
