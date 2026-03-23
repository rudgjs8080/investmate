"""AI 분석 캐시 — 동일 프롬프트 재사용 방지.

같은 날짜에 동일 프롬프트 해시로 이미 분석이 완료된 경우 재활용.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = Path("data/ai_cache")


def get_cache_key(prompt: str) -> str:
    """프롬프트 해시를 생성한다."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def get_cached_response(run_date: date, prompt: str) -> str | None:
    """캐시된 AI 응답이 있으면 반환한다."""
    cache_file = CACHE_DIR / f"{run_date.isoformat()}_{get_cache_key(prompt)}.json"
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            logger.info("AI 캐시 히트: %s", cache_file.name)
            return data.get("response")
        except (json.JSONDecodeError, KeyError):
            return None
    return None


def save_cached_response(run_date: date, prompt: str, response: str) -> None:
    """AI 응답을 캐시에 저장한다."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{run_date.isoformat()}_{get_cache_key(prompt)}.json"
    data = {
        "date": run_date.isoformat(),
        "prompt_hash": get_cache_key(prompt),
        "response": response,
    }
    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    logger.info("AI 캐시 저장: %s", cache_file.name)
