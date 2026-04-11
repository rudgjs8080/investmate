"""Layer 5: 내러티브 + 촉매."""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import BridgeNewsStock, FactNews
from src.deepdive.layers_utils import round_or_none, sf
from src.deepdive.schemas import NarrativeProfile, UpcomingCatalyst

logger = logging.getLogger(__name__)

_EXEC_PATTERN = re.compile(
    r"\b(CEO|CFO|COO|CTO|resign|appoint|임명|사임|해임|선임)\b", re.IGNORECASE,
)
_NEGATIVE_KEYWORDS = re.compile(
    r"\b(lawsuit|sue|fraud|scandal|recall|breach|fine|penalty|violation|investigation)\b",
    re.IGNORECASE,
)


def compute_layer5_narrative(
    session: Session, stock_id: int, ticker: str, reference_date: date,
) -> NarrativeProfile | None:
    """내러티브 + 촉매: 뉴스 감성, 임박 이벤트, 리스크, 경영진 변화."""
    try:
        return _compute(session, stock_id, ticker, reference_date)
    except Exception as e:
        logger.warning("Layer 5 계산 실패 (stock_id=%d): %s", stock_id, e)
        return None


def _compute(
    session: Session, stock_id: int, ticker: str, reference_date: date,
) -> NarrativeProfile | None:
    from datetime import datetime as dt

    cutoff_90_dt = dt.combine(reference_date - timedelta(days=90), dt.min.time())

    # 뉴스 조회 (BridgeNewsStock JOIN, published_at 기반 필터)
    news_rows = list(
        session.execute(
            select(FactNews)
            .join(BridgeNewsStock, FactNews.news_id == BridgeNewsStock.news_id)
            .where(
                BridgeNewsStock.stock_id == stock_id,
                FactNews.published_at >= cutoff_90_dt,
            )
            .order_by(FactNews.published_at.desc())
        ).scalars().all()
    )

    # 감성 윈도우별 평균 (published_at 기반)
    cutoff_30_dt = dt.combine(reference_date - timedelta(days=30), dt.min.time())
    cutoff_60_dt = dt.combine(reference_date - timedelta(days=60), dt.min.time())

    s30 = _avg_sentiment_dt(news_rows, cutoff_30_dt)
    s60 = _avg_sentiment_dt(news_rows, cutoff_60_dt)
    s90 = _avg_sentiment_dt(news_rows, cutoff_90_dt)

    # 추이
    trend = _detect_trend(s30, s60, s90)

    # 촉매 (legacy 문자열 리스트)
    catalysts = _detect_catalysts(ticker, reference_date)
    # Phase 11b: 구조화된 촉매
    structured_catalysts = _detect_catalysts_structured(ticker, reference_date)

    # 리스크 이벤트 (최근 7일 부정 뉴스)
    cutoff_7_dt = dt.combine(reference_date - timedelta(days=7), dt.min.time())
    recent_news = [n for n in news_rows if n.published_at >= cutoff_7_dt]
    risk_events = _detect_risks(recent_news)

    # 경영진 변화
    exec_changes = _detect_exec_changes(news_rows)

    # 그레이드
    grade = _grade_narrative(s30, trend)

    return NarrativeProfile(
        narrative_grade=grade,
        sentiment_30d=round_or_none(s30),
        sentiment_60d=round_or_none(s60),
        sentiment_90d=round_or_none(s90),
        sentiment_trend=trend,
        upcoming_catalysts=catalysts,
        risk_events=risk_events,
        exec_changes=exec_changes,
        metrics={
            "news_count_90d": len(news_rows),
            "negative_7d": len(risk_events),
        },
        upcoming_catalysts_structured=structured_catalysts,
    )


def _avg_sentiment_dt(news: list, cutoff_dt) -> float | None:
    scores = []
    for n in news:
        if n.published_at >= cutoff_dt:
            s = sf(getattr(n, "sentiment_score", None))
            if s is not None:
                scores.append(s)
    return sum(scores) / len(scores) if scores else None


def _detect_trend(s30: float | None, s60: float | None, s90: float | None) -> str:
    vals = [v for v in (s30, s60, s90) if v is not None]
    if len(vals) < 2:
        return "stable"
    if vals[0] > vals[-1] + 0.05:
        return "improving"
    if vals[0] < vals[-1] - 0.05:
        return "declining"
    return "stable"


def _detect_catalysts(ticker: str, reference_date: date) -> list[str]:
    """Legacy 문자열 리스트 — 기존 렌더러 호환 유지."""
    return [c.label for c in _detect_catalysts_structured(ticker, reference_date)]


def _detect_catalysts_structured(
    ticker: str, reference_date: date,
) -> tuple[UpcomingCatalyst, ...]:
    """Phase 11b: 구조화된 촉매 수집 — alert 엔진/UI/DTO 공용.

    D+0 ~ D+30의 earnings, D+0 ~ D+14의 FOMC를 수집.
    """
    items: list[UpcomingCatalyst] = []

    try:
        from src.data.event_collector import collect_earnings_calendar

        cal = collect_earnings_calendar([ticker], reference_date)
        ctx = cal.get(ticker) if isinstance(cal, dict) else None
        if ctx is not None and ctx.next_earnings is not None:
            ev = ctx.next_earnings
            days = ev.days_until
            if 0 <= days <= 30:
                label = f"실적 발표 {days}일 후" if days > 0 else "실적 발표 당일"
                items.append(
                    UpcomingCatalyst(
                        kind="earnings",
                        event_date=ev.earnings_date,
                        days_until=days,
                        label=label,
                    )
                )
    except Exception as e:
        logger.debug("구조화 실적 캘린더 수집 실패 [%s]: %s", ticker, e)

    try:
        from src.data.event_collector import get_next_fomc_date

        fomc = get_next_fomc_date(reference_date)
        if fomc is not None:
            if isinstance(fomc, tuple):
                fomc_date, days = fomc
            else:
                fomc_date = fomc
                days = (fomc_date - reference_date).days
            if 0 <= days <= 14:
                items.append(
                    UpcomingCatalyst(
                        kind="fomc",
                        event_date=fomc_date,
                        days_until=days,
                        label=f"FOMC {days}일 후" if days > 0 else "FOMC 당일",
                    )
                )
    except Exception as e:
        logger.debug("FOMC 일정 조회 실패: %s", e)

    return tuple(items)


def _detect_risks(recent_news: list) -> list[str]:
    risks = []
    neg_count = 0
    for n in recent_news:
        title = getattr(n, "title", "") or ""
        if _NEGATIVE_KEYWORDS.search(title):
            neg_count += 1
            if len(risks) < 3:
                risks.append(title[:80])
    if neg_count >= 5 and not risks:
        risks.append(f"최근 7일 부정 뉴스 {neg_count}건 감지")
    return risks


def _detect_exec_changes(news: list) -> list[str]:
    changes = []
    for n in news:
        title = getattr(n, "title", "") or ""
        if _EXEC_PATTERN.search(title):
            changes.append(title[:80])
            if len(changes) >= 3:
                break
    return changes


def _grade_narrative(s30: float | None, trend: str) -> str:
    if s30 is not None and s30 > 0.2 and trend == "improving":
        return "Positive"
    if s30 is not None and s30 < -0.2 and trend == "declining":
        return "Negative"
    return "Neutral"
