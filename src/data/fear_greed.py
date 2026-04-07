"""CNN Fear & Greed Index 수집 모듈."""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

import requests

from src.data.schemas import MacroData

logger = logging.getLogger(__name__)

_CNN_API_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://edition.cnn.com/markets/fear-and-greed",
}
_TIMEOUT = 10


def _classify_rating(score: float) -> str:
    """0-100 점수를 등급 문자열로 변환한다."""
    if score <= 25:
        return "Extreme Fear"
    elif score <= 45:
        return "Fear"
    elif score <= 55:
        return "Neutral"
    elif score <= 75:
        return "Greed"
    return "Extreme Greed"


def _fetch_api() -> dict | None:
    """CNN Fear & Greed API를 호출한다."""
    try:
        resp = requests.get(_CNN_API_URL, headers=_HEADERS, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Fear & Greed API 호출 실패: %s", e)
        return None


def fetch_fear_greed() -> tuple[float, str] | None:
    """CNN Fear & Greed Index 최신값을 조회한다.

    Returns:
        (score, rating) 튜플 또는 실패 시 None.
    """
    data = _fetch_api()
    if not data:
        return None

    fg = data.get("fear_and_greed", {})
    score = fg.get("score")
    if score is None:
        return None

    score = round(float(score), 1)
    rating = _classify_rating(score)
    logger.info("Fear & Greed Index: %.1f (%s)", score, rating)
    return score, rating


def fetch_fear_greed_history() -> list[tuple[date, float, str]]:
    """CNN API에서 ~1년치 히스토리를 조회한다 (초기 백필용).

    Returns:
        [(date, score, rating), ...] 리스트.
    """
    data = _fetch_api()
    if not data:
        return []

    items = data.get("fear_and_greed_historical", {}).get("data", [])
    results: list[tuple[date, float, str]] = []
    for item in items:
        try:
            ts_ms = item.get("x")
            score = item.get("y")
            if ts_ms is None or score is None:
                continue
            d = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()
            score = round(float(score), 1)
            rating = _classify_rating(score)
            results.append((d, score, rating))
        except Exception:
            continue

    logger.info("Fear & Greed 히스토리 조회: %d건", len(results))
    return results


def backfill_fear_greed_to_db(engine: object) -> int:
    """DB에 Fear & Greed 히스토리를 백필한다.

    CNN API ~1년치 데이터를 fact_macro_indicators에 UPSERT.

    Returns:
        신규 적재 건수.
    """
    from src.db.engine import get_session
    from src.db.helpers import date_to_id, ensure_date_ids
    from src.db.repository import MacroRepository

    history = fetch_fear_greed_history()
    if not history:
        logger.warning("Fear & Greed 히스토리 없음")
        return 0

    count = 0
    with get_session(engine) as session:
        dates = [d for d, _, _ in history]
        ensure_date_ids(session, dates)

        for d, score, rating in history:
            try:
                did = date_to_id(d)
                MacroRepository.upsert(session, did, {
                    "fear_greed_index": score,
                    "fear_greed_rating": rating,
                })
                count += 1
            except Exception as e:
                logger.debug("F&G 백필 실패 [%s]: %s", d, e)

    logger.info("Fear & Greed 백필 완료: %d건", count)
    return count
