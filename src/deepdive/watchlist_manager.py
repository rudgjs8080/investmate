"""워치리스트 관리 — 로드, 자동등록, DTO."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from src.db.models import DimStock
from src.db.repository import StockRepository, WatchlistRepository

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# 불변 DTO
# ──────────────────────────────────────────


@dataclass(frozen=True)
class HoldingInfo:
    """보유 정보."""

    shares: int
    avg_cost: float
    opened_at: date | None


@dataclass(frozen=True)
class WatchlistEntry:
    """워치리스트 종목 + 보유정보."""

    ticker: str
    stock_id: int
    name: str
    name_kr: str | None
    sector: str | None
    is_sp500: bool
    holding: HoldingInfo | None


# ──────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────


def load_watchlist(session: Session) -> list[WatchlistEntry]:
    """active 워치리스트 로드 + holdings 매핑 + 자동 등록.

    Returns:
        WatchlistEntry 리스트 (ticker 정렬)
    """
    items = WatchlistRepository.get_active(session)
    if not items:
        return []

    holdings = WatchlistRepository.get_all_holdings(session)
    entries: list[WatchlistEntry] = []

    for item in items:
        stock = ensure_stock_registered(session, item.ticker)
        holding_row = holdings.get(item.ticker)
        holding = (
            HoldingInfo(
                shares=holding_row.shares,
                avg_cost=float(holding_row.avg_cost),
                opened_at=holding_row.opened_at,
            )
            if holding_row is not None
            else None
        )
        sector_name = stock.sector.sector_name if stock.sector else None
        entries.append(
            WatchlistEntry(
                ticker=item.ticker,
                stock_id=stock.stock_id,
                name=stock.name or item.ticker,
                name_kr=getattr(stock, "name_kr", None),
                sector=sector_name,
                is_sp500=stock.is_sp500,
                holding=holding,
            )
        )

    return sorted(entries, key=lambda e: e.ticker)


def ensure_stock_registered(session: Session, ticker: str) -> DimStock:
    """dim_stocks에 없으면 yfinance .info로 자동 등록."""
    ticker = ticker.upper()
    stock = StockRepository.get_by_ticker(session, ticker)
    if stock is not None:
        return stock

    logger.info("비S&P500 종목 자동 등록: %s", ticker)
    info = _fetch_stock_info(ticker)

    # 마켓 resolve (기본 US)
    market_id = StockRepository.resolve_market_id(session, "US")
    if market_id is None:
        from src.db.models import DimMarket

        m = DimMarket(code="US", name="US Stock Market", currency="USD", timezone="US/Eastern")
        session.add(m)
        session.flush()
        market_id = m.market_id

    # 섹터 resolve
    sector_id = None
    if info.get("sector"):
        sector_id = StockRepository.resolve_sector_id(
            session, info["sector"], industry=info.get("industry"),
        )

    return StockRepository.add(
        session,
        ticker=ticker,
        name=info.get("name", ticker),
        market_id=market_id,
        sector_id=sector_id,
        is_sp500=False,
    )


def _fetch_stock_info(ticker: str) -> dict:
    """yfinance .info에서 기본 정보 추출. 방어적 .get() 사용."""
    try:
        import yfinance as yf

        tk = yf.Ticker(ticker)
        info = tk.info or {}
        return {
            "name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector") or "Unknown",
            "industry": info.get("industry"),
        }
    except Exception as e:
        logger.warning("yfinance info 조회 실패 (%s): %s", ticker, e)
        return {"name": ticker, "sector": "Unknown", "industry": None}
