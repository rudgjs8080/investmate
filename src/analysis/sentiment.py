"""LLM 기반 뉴스 감성 분석 모듈."""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def analyze_sentiment_llm(
    articles: list[dict], max_articles: int = 10,
) -> list[dict]:
    """뉴스 기사들의 감성을 LLM으로 분석한다.

    Args:
        articles: [{"title": "...", "url": "..."}, ...]
        max_articles: 최대 분석 기사 수 (토큰 절약)

    Returns:
        [{"title": "...", "sentiment": 0.7, "reason": "긍정적 실적"}, ...]
    """
    if not articles:
        return []

    batch = articles[:max_articles]
    prompt = _build_sentiment_prompt(batch)
    response = _call_llm(prompt)

    if response is None:
        return []  # LLM 불가 -> 호출자가 키워드 방식 폴백

    return _parse_sentiment_response(response, batch)


def _build_sentiment_prompt(articles: list[dict]) -> str:
    """감성 분석용 프롬프트를 구성한다."""
    lines = [
        "다음 뉴스 기사 제목들의 주식 시장 관점 감성을 분석하세요.",
        "각 기사에 대해 sentiment(-1.0=매우 부정 ~ +1.0=매우 긍정)과 이유를 JSON 배열로 응답하세요.",
        "",
    ]
    for i, a in enumerate(articles, 1):
        lines.append(f"{i}. {a.get('title', 'N/A')}")

    lines.append("")
    lines.append("응답 형식 (JSON만, 다른 텍스트 없이):")
    lines.append('[{"index": 1, "sentiment": 0.5, "reason": "이유"}]')

    return "\n".join(lines)


def _call_llm(prompt: str) -> str | None:
    """LLM을 호출한다 (SDK 우선, CLI 폴백). 감성 분석 전용 모델 사용."""
    try:
        from src.ai.claude_analyzer import run_analysis
        from src.config import get_settings
        response, _ = run_analysis(
            prompt, timeout=60, model=get_settings().ai_model_sentiment,
        )
        return response
    except Exception as e:
        logger.warning("LLM 감성 분석 실패: %s", e)
        return None


def _parse_sentiment_response(response: str, articles: list[dict]) -> list[dict]:
    """LLM 감성 분석 응답을 파싱한다."""
    results = []

    try:
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            parsed = json.loads(json_match.group())
            for item in parsed:
                idx = item.get("index", 0) - 1
                if 0 <= idx < len(articles):
                    sentiment = max(-1.0, min(1.0, float(item.get("sentiment", 0))))
                    results.append({
                        "title": articles[idx].get("title", ""),
                        "sentiment": sentiment,
                        "reason": item.get("reason", ""),
                    })
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning("감성 응답 파싱 실패: %s", e)

    return results
