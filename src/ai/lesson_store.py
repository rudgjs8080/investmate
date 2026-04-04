"""AI 자기학습 교훈 저장소 — 교훈 저장/조회/만료/효과성 추적."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import (
    DimDate,
    FactAIFeedback,
    FactAILesson,
    FactDailyRecommendation,
)

logger = logging.getLogger(__name__)

VALID_CATEGORIES = frozenset({"sector", "regime", "timing", "valuation", "general"})
LESSON_EXPIRY_DAYS = 90
MIN_EFFECTIVENESS_SAMPLES = 15


@dataclass(frozen=True)
class LessonInput:
    """교훈 저장 입력 데이터."""

    lesson_text: str
    category: str
    source_recommendation_id: int
    source_ticker: str
    source_sector: str | None = None
    source_regime: str | None = None
    source_vix_level: float | None = None
    source_return_20d: float = 0.0


def store_lessons(
    session: Session,
    lessons: list[LessonInput],
    run_date_id: int,
) -> int:
    """교훈을 DB에 저장한다. 중복 텍스트는 건너뛴다.

    Returns:
        저장된 교훈 수.
    """
    from src.db.helpers import id_to_date

    run_date = id_to_date(run_date_id)
    expires = run_date + timedelta(days=LESSON_EXPIRY_DAYS)

    existing_texts = set(
        session.scalars(
            select(FactAILesson.lesson_text).where(FactAILesson.is_active == True)
        ).all()
    )

    stored = 0
    for inp in lessons:
        category = inp.category if inp.category in VALID_CATEGORIES else "general"

        if inp.lesson_text in existing_texts:
            logger.debug("중복 교훈 스킵: %s", inp.lesson_text[:40])
            continue

        lesson = FactAILesson(
            created_date_id=run_date_id,
            lesson_text=inp.lesson_text,
            category=category,
            source_recommendation_id=inp.source_recommendation_id,
            source_ticker=inp.source_ticker,
            source_sector=inp.source_sector,
            source_regime=inp.source_regime,
            source_vix_level=inp.source_vix_level,
            source_return_20d=inp.source_return_20d,
            times_applied=0,
            effectiveness_score=None,
            is_active=True,
            expires_at=expires,
        )
        session.add(lesson)
        existing_texts.add(inp.lesson_text)
        stored += 1

    if stored > 0:
        session.commit()
        logger.info("교훈 %d건 저장 완료", stored)

    return stored


def get_active_lessons(
    session: Session,
    run_date: date,
    top_n: int = 10,
) -> list[FactAILesson]:
    """활성 교훈을 효과성 순으로 조회한다.

    effectiveness_score가 있는 교훈 우선, 없으면 최신순.
    """
    run_date_id = date_to_id(run_date)

    stmt = (
        select(FactAILesson)
        .where(
            FactAILesson.is_active == True,
            FactAILesson.created_date_id <= run_date_id,
        )
        .order_by(
            # effectiveness_score NULL은 뒤로
            FactAILesson.effectiveness_score.is_(None).asc(),
            FactAILesson.effectiveness_score.desc(),
            FactAILesson.created_date_id.desc(),
        )
        .limit(top_n)
    )

    return list(session.scalars(stmt).all())


def expire_old_lessons(session: Session, run_date: date) -> int:
    """만료된 교훈을 비활성화한다.

    Returns:
        비활성화된 교훈 수.
    """
    stmt = (
        update(FactAILesson)
        .where(
            FactAILesson.is_active == True,
            FactAILesson.expires_at <= run_date,
        )
        .values(is_active=False)
    )
    result = session.execute(stmt)
    count = result.rowcount
    if count > 0:
        session.commit()
        logger.info("만료 교훈 %d건 비활성화", count)
    return count


def increment_applied_count(session: Session, lesson_ids: list[int]) -> None:
    """프롬프트에 삽입된 교훈의 적용 횟수를 증가시킨다."""
    if not lesson_ids:
        return

    stmt = (
        update(FactAILesson)
        .where(FactAILesson.lesson_id.in_(lesson_ids))
        .values(times_applied=FactAILesson.times_applied + 1)
    )
    session.execute(stmt)
    session.commit()


def update_lesson_effectiveness(session: Session, run_date: date) -> int:
    """각 활성 교훈의 효과성을 측정하여 갱신한다.

    교훈 생성일 기준, 동일 카테고리/컨텍스트에서:
    - 이전 추천의 승률 vs 이후 추천의 승률 비교
    - look-ahead 보호: 20일 수익률이 확정된 추천만 사용

    Returns:
        갱신된 교훈 수.
    """
    run_date_id = date_to_id(run_date)
    cutoff_date_id = date_to_id(run_date - timedelta(days=25))

    active_lessons = list(
        session.scalars(
            select(FactAILesson).where(FactAILesson.is_active == True)
        ).all()
    )

    updated = 0
    for lesson in active_lessons:
        score = _compute_effectiveness(
            session, lesson, cutoff_date_id, run_date_id
        )
        if score is not None and score != lesson.effectiveness_score:
            lesson.effectiveness_score = score
            updated += 1

    if updated > 0:
        session.commit()
        logger.info("교훈 효과성 %d건 갱신", updated)

    return updated


def _compute_effectiveness(
    session: Session,
    lesson: FactAILesson,
    cutoff_date_id: int,
    run_date_id: int,
) -> float | None:
    """교훈 생성 전/후 승률 차이를 계산한다.

    Returns:
        승률 변화(%p) 또는 샘플 부족 시 None.
    """
    created = lesson.created_date_id

    base_filter = [
        FactAIFeedback.ai_approved == True,
        FactAIFeedback.return_20d.isnot(None),
        FactAIFeedback.run_date_id <= cutoff_date_id,
    ]

    if lesson.category == "sector" and lesson.source_sector:
        base_filter.append(FactAIFeedback.sector == lesson.source_sector)

    # 이전 기간: 교훈 생성 전 90일
    from src.db.helpers import id_to_date as _id2d
    before_start = date_to_id(_id2d(created) - timedelta(days=90))
    before_filters = base_filter + [
        FactAIFeedback.run_date_id >= before_start,
        FactAIFeedback.run_date_id < created,
    ]

    before_total = session.scalar(
        select(func.count()).select_from(FactAIFeedback).where(*before_filters)
    )
    if before_total is None or before_total < MIN_EFFECTIVENESS_SAMPLES:
        return None

    before_wins = session.scalar(
        select(func.count())
        .select_from(FactAIFeedback)
        .where(*before_filters, FactAIFeedback.return_20d > 0)
    )
    before_rate = (before_wins or 0) / before_total * 100

    # 이후 기간: 교훈 생성 후 ~ cutoff
    after_filters = base_filter + [
        FactAIFeedback.run_date_id >= created,
    ]

    after_total = session.scalar(
        select(func.count()).select_from(FactAIFeedback).where(*after_filters)
    )
    if after_total is None or after_total < MIN_EFFECTIVENESS_SAMPLES:
        return None

    after_wins = session.scalar(
        select(func.count())
        .select_from(FactAIFeedback)
        .where(*after_filters, FactAIFeedback.return_20d > 0)
    )
    after_rate = (after_wins or 0) / after_total * 100

    return round(after_rate - before_rate, 1)
