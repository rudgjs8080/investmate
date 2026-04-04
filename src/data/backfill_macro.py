"""매크로 데이터 히스토리 백필 — 과거 2년치 VIX/금리/달러/S&P500을 한번에 수집."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import yfinance as yf

from src.config import get_settings
from src.data.macro_collector import MACRO_TICKERS
from src.data.utils import extract_ticker_data, flatten_multiindex
from src.db.engine import create_db_engine, get_session
from src.db.helpers import date_to_id, ensure_date_ids
from src.db.repository import MacroRepository

logger = logging.getLogger(__name__)


def backfill_macro(days_back: int = 730) -> int:
    """과거 N일의 매크로 데이터를 DB에 백필한다.

    Args:
        days_back: 몇 일 전까지 수집할지 (기본 730 = 2년).

    Returns:
        저장된 레코드 수.
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days_back)

    logger.info("매크로 백필 시작: %s ~ %s (%d일)", start_date, end_date, days_back)

    # 모든 매크로 티커 배치 다운로드
    tickers = list(MACRO_TICKERS.values())
    try:
        df = yf.download(
            tickers,
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            progress=True,
            group_by="ticker",
        )
    except Exception as e:
        logger.error("매크로 배치 다운로드 실패: %s", e)
        return 0

    if df.empty:
        logger.warning("매크로 데이터 비어 있음")
        return 0

    # 날짜별로 매크로 레코드 생성
    engine = create_db_engine(get_settings().db_path)
    count = 0

    with get_session(engine) as session:
        # dim_date 보장
        all_dates = [d.date() for d in df.index]
        ensure_date_ids(session, all_dates)

        for idx in df.index:
            d = idx.date() if hasattr(idx, "date") else idx
            did = date_to_id(d)

            data: dict = {}

            for key, ticker in MACRO_TICKERS.items():
                try:
                    ticker_df = extract_ticker_data(df, ticker, tickers)
                    if ticker_df is None or idx not in ticker_df.index:
                        continue

                    val = ticker_df.loc[idx, "Close"]
                    if val is not None and str(val) != "nan":
                        if key == "sp500":
                            data["sp500_close"] = float(val)
                        else:
                            data[key] = float(val)
                except Exception:
                    continue

            # S&P 500 SMA20 계산
            if "sp500_close" in data:
                try:
                    sp500_df = extract_ticker_data(df, MACRO_TICKERS["sp500"], tickers)
                    if sp500_df is not None:
                        sp_close = sp500_df["Close"]
                        pos = list(sp_close.index).index(idx)
                        if pos >= 19:
                            sma20 = float(sp_close.iloc[pos - 19:pos + 1].mean())
                            data["sp500_sma20"] = sma20
                except Exception:
                    pass

            if data:
                try:
                    MacroRepository.upsert(session, did, data)
                    count += 1
                except Exception as e:
                    logger.debug("매크로 저장 실패 [%d]: %s", did, e)

            if count % 100 == 0 and count > 0:
                logger.info("매크로 백필 진행: %d건", count)

    logger.info("매크로 백필 완료: %d건 저장", count)
    return count


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, stream=sys.stdout,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 730
    result = backfill_macro(days)
    print(f"\n매크로 백필 완료: {result}건")
