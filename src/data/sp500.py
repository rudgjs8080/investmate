"""S&P 500 종목 목록 관리."""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy.orm import Session

from src.db.repository import StockRepository

logger = logging.getLogger(__name__)

WIKIPEDIA_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def fetch_sp500_list() -> list[dict]:
    """Wikipedia에서 현재 S&P 500 구성 종목 목록을 가져온다.

    Returns:
        [{"ticker": "AAPL", "name": "Apple Inc.", "sector": "Information Technology",
          "industry": "Consumer Electronics"}, ...]
    """
    try:
        import io

        import requests

        headers = {"User-Agent": "Mozilla/5.0 (investmate/1.0)"}
        resp = requests.get(WIKIPEDIA_SP500_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
        df = tables[0]

        results = []
        for _, row in df.iterrows():
            ticker = str(row.get("Symbol", "")).strip().replace(".", "-")
            name = str(row.get("Security", "")).strip()
            sector = str(row.get("GICS Sector", "")).strip()
            industry = str(row.get("GICS Sub-Industry", "")).strip()

            if ticker and name:
                results.append({
                    "ticker": ticker.upper(),
                    "name": name,
                    "sector": sector or None,
                    "industry": industry or None,
                })

        logger.info("S&P 500 목록 가져옴: %d개 종목", len(results))
        return results

    except Exception as e:
        logger.error("S&P 500 목록 가져오기 실패: %s", e)
        return []


def sync_sp500(session: Session, market_id: int) -> dict[str, int]:
    """S&P 500 종목을 dim_stocks와 동기화한다.

    Returns:
        {"added": N, "removed": N, "total": N}
    """
    sp500_list = fetch_sp500_list()
    if not sp500_list:
        logger.warning("S&P 500 목록이 비어있음, 동기화 스킵")
        return {"added": 0, "removed": 0, "total": 0}

    new_tickers = {item["ticker"] for item in sp500_list}
    sp500_map = {item["ticker"]: item for item in sp500_list}

    # 현재 DB의 S&P 500 종목
    current_sp500 = StockRepository.get_sp500_active(session)
    current_tickers = {s.ticker for s in current_sp500}

    # 신규 추가
    to_add = new_tickers - current_tickers
    added = 0
    for ticker in to_add:
        info = sp500_map[ticker]
        existing = StockRepository.get_by_ticker(session, ticker)

        if existing is not None:
            # 이미 있지만 S&P 500이 아닌 경우 → 마킹
            existing.is_sp500 = True
            existing.is_active = True
            session.flush()
        else:
            sector_id = None
            if info.get("sector"):
                sector_id = StockRepository.resolve_sector_id(
                    session, info["sector"], industry=info.get("industry")
                )

            StockRepository.add(
                session, ticker, info["name"], market_id,
                sector_id=sector_id, is_sp500=True,
            )
        added += 1

    # 제외된 종목
    to_remove = current_tickers - new_tickers
    for ticker in to_remove:
        stock = StockRepository.get_by_ticker(session, ticker)
        if stock is not None:
            stock.is_sp500 = False
            session.flush()

    session.flush()
    logger.info(
        "S&P 500 동기화 완료: 추가 %d, 제외 %d, 전체 %d",
        added, len(to_remove), len(new_tickers),
    )
    return {"added": added, "removed": len(to_remove), "total": len(new_tickers)}
