"""외부 요인 분석 모듈 — 매크로, 뉴스 감성, 섹터 모멘텀."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)
from datetime import datetime, timezone

from src.data.schemas import MacroData, NewsArticleData

# 감성 분석용 키워드 (간단 구현)
POSITIVE_KEYWORDS = [
    "surge", "rally", "gain", "rise", "bull", "growth", "beat", "record",
    "upgrade", "optimism", "recover", "strong", "profit", "boost",
    "breakthrough", "acquisition", "outperform", "guidance raised",
    "beat expectations", "expansion", "partnership", "innovation",
    "milestone", "record revenue", "buyback", "dividend increase",
    "momentum", "recovery",
]

NEGATIVE_KEYWORDS = [
    "crash", "plunge", "fall", "drop", "bear", "recession", "loss", "miss",
    "downgrade", "fear", "risk", "weak", "debt", "cut", "layoff", "warning",
    "underperform", "guidance lowered", "miss expectations", "recall",
    "investigation", "lawsuit", "restructuring", "default", "bankruptcy",
    "delisted", "fraud", "write-down",
]


def analyze_macro(
    macro: MacroData,
    previous_macro: MacroData | None = None,
) -> int:
    """매크로 환경 종합 점수를 산출한다 (1-10).

    높을수록 투자에 유리한 환경.
    유효 지표가 5개 미만이면 중립(5) 반환.
    previous_macro가 제공되면 전일 대비 추세 보정을 적용한다.
    """
    # 완전성 검증: 유효 지표 수 확인
    valid_count = sum(1 for v in [macro.vix, macro.sp500_close, macro.us_10y_yield, macro.dollar_index, macro.sp500_sma20] if v is not None)
    if valid_count < 5:
        logger.warning("매크로 불충분: %d개 지표", valid_count)
        return 5  # 데이터 불충분 → 중립

    score = 5.0  # 기본 중립

    # VIX (공포지수)
    if macro.vix is not None:
        if macro.vix < 15:
            score += 2.0
        elif macro.vix < 20:
            score += 1.0
        elif macro.vix > 30:
            score -= 2.0
        elif macro.vix > 25:
            score -= 1.0

    # S&P 500 vs 20일 SMA
    if macro.sp500_close is not None and macro.sp500_sma20 is not None:
        if macro.sp500_close > macro.sp500_sma20:
            score += 1.0
        else:
            score -= 1.0

    # 10년 국채 금리 (높으면 주식에 불리)
    if macro.us_10y_yield is not None:
        if macro.us_10y_yield > 5.0:
            score -= 1.0
        elif macro.us_10y_yield < 3.0:
            score += 1.0

    # 달러 인덱스 (강달러 = 수출주 불리, 약달러 = 유리)
    if macro.dollar_index is not None:
        if macro.dollar_index > 105:
            score -= 1.0  # 강달러 = 수출 역풍
        elif macro.dollar_index < 95:
            score += 1.0  # 약달러 = 수출 순풍

    # Fear & Greed 보정
    if macro.fear_greed_index is not None:
        if macro.fear_greed_index <= 25:
            score += 1.0  # Extreme Fear → 역발상 매수 기회
        elif macro.fear_greed_index > 75:
            score -= 1.0  # Extreme Greed → 과열 주의

    # 전일 대비 추세 보정
    if previous_macro is not None:
        score += _macro_trend_adjustment(macro, previous_macro)

    return max(1, min(10, round(score)))


def _macro_trend_adjustment(
    current: MacroData,
    previous: MacroData,
) -> float:
    """전일 대비 매크로 추세 보정값을 계산한다.

    VIX 하락, 금리 하락, 달러 약세, S&P 상승 → 양수 보정.
    """
    adj = 0.0

    # VIX 추세: 3pt 이상 변화 시 보정
    if current.vix is not None and previous.vix is not None:
        vix_delta = current.vix - previous.vix
        if vix_delta <= -3.0:
            adj += 0.5  # 공포 완화
        elif vix_delta >= 3.0:
            adj -= 0.5  # 공포 급등

    # 10Y 금리 추세: 0.1 이상 변화 시 보정
    if current.us_10y_yield is not None and previous.us_10y_yield is not None:
        yield_delta = current.us_10y_yield - previous.us_10y_yield
        if yield_delta <= -0.1:
            adj += 0.3  # 금리 하락 = 완화
        elif yield_delta >= 0.1:
            adj -= 0.3  # 금리 상승 = 긴축

    # 달러 인덱스 추세: 1.0 이상 변화 시 보정
    if current.dollar_index is not None and previous.dollar_index is not None:
        dx_delta = current.dollar_index - previous.dollar_index
        if dx_delta <= -1.0:
            adj += 0.3  # 달러 약세 = 유리
        elif dx_delta >= 1.0:
            adj -= 0.3  # 달러 강세 = 역풍

    # S&P 500 전일대비 방향
    if current.sp500_close is not None and previous.sp500_close is not None:
        if current.sp500_close > previous.sp500_close:
            adj += 0.3
        elif current.sp500_close < previous.sp500_close:
            adj -= 0.3

    return adj


def _news_time_decay(published_at: datetime, now: datetime | None = None) -> float:
    """뉴스 시간 감쇠 가중치를 계산한다.

    최신 뉴스일수록 높은 가중치. 하루 경과 시 0.2씩 감소, 최소 0.2.
    - 0일: 1.0
    - 1일: 0.8
    - 2일: 0.6
    - 3일: 0.4
    - 4일+: 0.2
    """
    if now is None:
        now = datetime.now(timezone.utc)
    # published_at이 naive면 UTC로 간주
    pub = published_at
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    age_days = (now - pub).total_seconds() / 86400.0
    return max(0.2, 1.0 - age_days * 0.2)


def analyze_news_sentiment(
    articles: list[NewsArticleData],
    *,
    now: datetime | None = None,
) -> float:
    """뉴스 감성 점수를 산출한다 (-1.0 ~ 1.0).

    LLM 감성 분석 우선, 실패 시 키워드 기반 폴백. 시간 감쇠 적용.
    """
    if not articles:
        return 0.0

    # LLM 감성 분석 시도 (키워드 폴백)
    try:
        from src.analysis.sentiment import analyze_sentiment_llm
        article_dicts = [{"title": a.title, "url": getattr(a, "url", "")} for a in articles]
        llm_results = analyze_sentiment_llm(article_dicts)
        if llm_results:
            total_sentiment = sum(r["sentiment"] for r in llm_results) / len(llm_results)
            return round(total_sentiment, 3)
    except Exception:
        pass  # LLM 불가 시 기존 키워드 방식 계속

    weighted_positive = 0.0
    weighted_negative = 0.0

    for article in articles:
        text = (article.title + " " + (article.summary or "")).lower()
        weight = _news_time_decay(article.published_at, now)
        pos = sum(1 for kw in POSITIVE_KEYWORDS if re.search(r'\b' + re.escape(kw) + r'\b', text))
        neg = sum(1 for kw in NEGATIVE_KEYWORDS if re.search(r'\b' + re.escape(kw) + r'\b', text))
        weighted_positive += pos * weight
        weighted_negative += neg * weight

    total = weighted_positive + weighted_negative
    if total == 0:
        return 0.0

    return round((weighted_positive - weighted_negative) / total, 2)


def calculate_sector_momentum(
    sector_returns: dict[str, float],
) -> dict[str, float]:
    """섹터별 모멘텀 점수를 계산한다.

    Args:
        sector_returns: {sector_name: avg_return_pct} 최근 수익률.

    Returns:
        {sector_name: momentum_score (0-10)}
    """
    if not sector_returns:
        return {}

    values = list(sector_returns.values())
    min_ret = min(values)
    max_ret = max(values)
    spread = max_ret - min_ret

    if spread == 0:
        return {s: 5.0 for s in sector_returns}

    result = {}
    for sector, ret in sector_returns.items():
        raw_score = (ret - min_ret) / spread * 9 + 1
        # 플랫 마켓 감쇄: spread < 1%이면 5.0 방향으로 압축
        if spread < 1.0:
            raw_score = 5.0 + (raw_score - 5.0) * spread
        result[sector] = round(raw_score, 1)

    return result
