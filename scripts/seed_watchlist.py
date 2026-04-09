"""초기 워치리스트 12종목 시드 스크립트.

사용법:
    python scripts/seed_watchlist.py

멱등성 보장 — 이미 존재하는 종목은 재활성화만 수행.
"""

from __future__ import annotations

import sys
from pathlib import Path

# 프로젝트 루트를 PYTHONPATH에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rich.console import Console

from src.config import get_settings
from src.db.engine import create_db_engine, get_session, init_db
from src.db.migrate import ensure_schema
from src.db.repository import WatchlistRepository
from src.deepdive.watchlist_manager import ensure_stock_registered

console = Console()

INITIAL_WATCHLIST = [
    "NVDA", "UNH", "TSLA", "PLTR", "SMR", "GOOG",
    "AMZN", "MSFT", "AVGO", "META", "NFLX", "AAPL",
]


def seed_watchlist(engine) -> int:
    """초기 12종목 시드. 반환: 신규 추가 수."""
    added = 0
    with get_session(engine) as session:
        for ticker in INITIAL_WATCHLIST:
            existing = WatchlistRepository.add_ticker(session, ticker)
            # 신규인지 재활성화인지 판별: added_at == updated_at 이면 신규
            if existing.created_at == existing.updated_at:
                added += 1
            # dim_stocks 자동 등록 (비S&P500 포함)
            ensure_stock_registered(session, ticker)
    return added


if __name__ == "__main__":
    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    ensure_schema(engine)
    init_db(engine)

    count = seed_watchlist(engine)
    console.print(f"[green]워치리스트 시드 완료[/green]: {count}개 신규, 총 {len(INITIAL_WATCHLIST)}개")
