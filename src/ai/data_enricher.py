"""AI 프롬프트용 추가 데이터 수집 — yfinance에서 실시간 보강 데이터를 가져온다."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnrichedStockData:
    """AI 프롬프트용 보강 데이터."""

    ticker: str
    high_52w: float | None = None  # 52주 최고가
    low_52w: float | None = None  # 52주 최저가
    pct_from_52w_high: float | None = None  # 고점 대비 %
    pct_from_52w_low: float | None = None  # 저점 대비 %
    beta: float | None = None  # 시장 베타
    forward_per: float | None = None  # 선행 PER
    peg_ratio: float | None = None  # PEG 비율
    revenue_growth: float | None = None  # 매출 성장률 (YoY)
    earnings_growth: float | None = None  # 이익 성장률 (YoY)
    free_cashflow: float | None = None  # 잉여현금흐름
    institutional_pct: float | None = None  # 기관 보유 비율
    short_pct_float: float | None = None  # 공매도 비율
    avg_volume_10d: int | None = None  # 10일 평균 거래량
    sector_per_avg: float | None = None  # 섹터 평균 PER (별도 계산 필요)
    target_mean_price: float | None = None  # 애널리스트 평균 목표가
    target_high_price: float | None = None  # 애널리스트 최고 목표가
    target_low_price: float | None = None  # 애널리스트 최저 목표가
    recommendation_mean: float | None = None  # 애널리스트 평균 추천 (1=Strong Buy ~ 5=Sell)


def fetch_enriched_stock_data(tickers: list[str]) -> dict[str, EnrichedStockData]:
    """종목별 보강 데이터를 yfinance에서 수집한다.

    Args:
        tickers: 종목 코드 리스트.

    Returns:
        {ticker: EnrichedStockData} 딕셔너리.
    """
    result: dict[str, EnrichedStockData] = {}

    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            if not info:
                continue

            current = info.get("currentPrice") or info.get("regularMarketPrice")
            high_52w = info.get("fiftyTwoWeekHigh")
            low_52w = info.get("fiftyTwoWeekLow")

            pct_high = None
            pct_low = None
            if current and high_52w and high_52w > 0:
                pct_high = round((current - high_52w) / high_52w * 100, 1)
            if current and low_52w and low_52w > 0:
                pct_low = round((current - low_52w) / low_52w * 100, 1)

            result[ticker] = EnrichedStockData(
                ticker=ticker,
                high_52w=high_52w,
                low_52w=low_52w,
                pct_from_52w_high=pct_high,
                pct_from_52w_low=pct_low,
                beta=info.get("beta"),
                forward_per=info.get("forwardPE"),
                peg_ratio=info.get("pegRatio"),
                revenue_growth=_pct(info.get("revenueGrowth")),
                earnings_growth=_pct(info.get("earningsGrowth")),
                free_cashflow=info.get("freeCashflow"),
                institutional_pct=_pct(info.get("heldPercentInstitutions")),
                short_pct_float=_pct(info.get("shortPercentOfFloat")),
                avg_volume_10d=info.get("averageVolume10days"),
                target_mean_price=info.get("targetMeanPrice"),
                target_high_price=info.get("targetHighPrice"),
                target_low_price=info.get("targetLowPrice"),
                recommendation_mean=info.get("recommendationMean"),
            )
        except Exception as e:
            logger.debug("보강 데이터 수집 실패 [%s]: %s", ticker, e)

    return result


def compute_sector_per_averages(tickers_with_sector: list[tuple[str, str, float | None]]) -> dict[str, float]:
    """섹터별 평균 PER을 계산한다.

    Args:
        tickers_with_sector: [(ticker, sector, per), ...]

    Returns:
        {sector: avg_per}
    """
    sector_pers: dict[str, list[float]] = {}
    for _, sector, per in tickers_with_sector:
        if per is not None and 0 < per < 200:
            sector_pers.setdefault(sector, []).append(per)

    return {
        sector: round(sum(pers) / len(pers), 1)
        for sector, pers in sector_pers.items()
        if pers
    }


def _pct(v: float | None) -> float | None:
    """소수점 비율을 % 형태로 변환."""
    if v is None:
        return None
    if abs(v) < 1:
        return round(v * 100, 1)
    return round(v, 1)
