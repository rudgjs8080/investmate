"""뉴스 스크래핑 모듈."""

from __future__ import annotations

import logging
from datetime import datetime

import yfinance as yf

from src.data.schemas import NewsArticleData

logger = logging.getLogger(__name__)


def scrape_news(ticker: str, count: int = 10) -> list[NewsArticleData]:
    """종목 관련 뉴스를 수집한다.

    yfinance 내장 뉴스 피드를 사용한다 (v2 API 구조 대응).
    """
    try:
        t = yf.Ticker(ticker)
        raw_news = t.news

        if not raw_news:
            return []

        articles = []
        seen_urls: set[str] = set()

        for item in raw_news[:count]:
            # v2 구조: item["content"]["title"], item["content"]["canonicalUrl"]["url"]
            content = item.get("content") or item

            # URL 추출
            url = ""
            canonical = content.get("canonicalUrl")
            if isinstance(canonical, dict):
                url = canonical.get("url", "")
            if not url:
                url = content.get("link") or content.get("url") or item.get("link") or item.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            # 제목
            title = content.get("title") or item.get("title", "제목 없음")

            # 요약
            summary = content.get("summary") or content.get("description") or item.get("summary")

            # 출처
            provider = content.get("provider")
            if isinstance(provider, dict):
                source = provider.get("displayName", "Unknown")
            else:
                source = item.get("publisher", "Unknown")

            # 날짜 (파싱 실패 시 기사 skip — 감성 분석 편향 방지)
            pub_str = content.get("pubDate") or content.get("displayTime")
            published_at = None
            if pub_str and isinstance(pub_str, str):
                try:
                    published_at = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass
            elif isinstance(pub_str, (int, float)):
                try:
                    published_at = datetime.fromtimestamp(pub_str)
                except (ValueError, OSError):
                    pass

            if published_at is None:
                logger.debug("뉴스 날짜 파싱 실패, 건너뜀: %s", title[:50])
                continue

            articles.append(
                NewsArticleData(
                    title=title,
                    summary=summary,
                    url=url,
                    source=source,
                    published_at=published_at,
                )
            )

        return articles

    except (ConnectionError, TimeoutError, OSError) as e:
        logger.warning("뉴스 수집 네트워크 오류 [%s]: %s", ticker, e)
        return []
    except Exception as e:
        logger.warning("뉴스 수집 실패 [%s]: %s — %s", ticker, type(e).__name__, e)
        return []


def scrape_market_news(count: int = 20) -> list[NewsArticleData]:
    """시장 전체 뉴스를 수집한다 (S&P 500 지수 기반)."""
    # ^GSPC 뉴스가 없을 수 있으므로 SPY ETF도 시도
    articles = scrape_news("^GSPC", count=count)
    if not articles:
        articles = scrape_news("SPY", count=count)
    return articles
