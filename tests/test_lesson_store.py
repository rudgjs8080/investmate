"""교훈 저장소 (lesson_store.py) 테스트."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.ai.lesson_store import (
    LessonInput,
    expire_old_lessons,
    get_active_lessons,
    increment_applied_count,
    store_lessons,
    update_lesson_effectiveness,
)
from src.db.helpers import date_to_id
from src.db.models import (
    DimDate,
    DimMarket,
    DimStock,
    FactAIFeedback,
    FactAILesson,
    FactDailyRecommendation,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _seed_dates(session):
    """테스트용 날짜 + 종목 시딩."""
    base = date(2026, 1, 1)
    for i in range(120):
        d = base + timedelta(days=i)
        session.add(DimDate(
            date_id=date_to_id(d),
            date=d,
            year=d.year,
            quarter=(d.month - 1) // 3 + 1,
            month=d.month,
            week_of_year=d.isocalendar()[1],
            day_of_week=d.weekday(),
            is_trading_day=d.weekday() < 5,
        ))
    session.add(DimMarket(market_id=1, code="US", name="US Market", currency="USD", timezone="US/Eastern"))
    session.add(DimStock(
        stock_id=1, ticker="AAPL", name="Apple Inc.",
        market_id=1, is_active=True, is_sp500=True,
    ))
    session.commit()


def _make_rec(session, run_date_id, stock_id=1, **overrides):
    """NOT NULL 필드를 채운 추천 레코드를 생성한다."""
    defaults = dict(
        run_date_id=run_date_id,
        stock_id=stock_id,
        rank=1,
        total_score=80.0,
        technical_score=7.0,
        fundamental_score=7.0,
        smart_money_score=6.0,
        external_score=6.0,
        momentum_score=7.0,
        recommendation_reason="test",
        price_at_recommendation=150.0,
    )
    defaults.update(overrides)
    rec = FactDailyRecommendation(**defaults)
    session.add(rec)
    session.flush()
    return rec


@pytest.fixture
def _seed_recommendation(session, _seed_dates):
    """테스트용 추천 레코드."""
    rec = _make_rec(session, date_to_id(date(2026, 1, 15)))
    session.commit()
    return rec


# ---------------------------------------------------------------------------
# store_lessons
# ---------------------------------------------------------------------------


class TestStoreLessons:
    def test_store_basic(self, session, _seed_recommendation):
        """교훈 저장 기본 동작."""
        run_date_id = date_to_id(date(2026, 2, 1))
        lessons = [
            LessonInput(
                lesson_text="VIX 25+ 시 기술주 신뢰도 7 이상 부여하지 말 것",
                category="regime",
                source_recommendation_id=1,
                source_ticker="AAPL",
                source_sector="Technology",
                source_return_20d=-5.0,
            ),
        ]
        count = store_lessons(session, lessons, run_date_id)
        assert count == 1

        stored = session.query(FactAILesson).all()
        assert len(stored) == 1
        assert stored[0].lesson_text == "VIX 25+ 시 기술주 신뢰도 7 이상 부여하지 말 것"
        assert stored[0].category == "regime"
        assert stored[0].is_active is True
        assert stored[0].times_applied == 0

    def test_store_dedup(self, session, _seed_recommendation):
        """중복 교훈은 저장하지 않는다."""
        run_date_id = date_to_id(date(2026, 2, 1))
        inp = LessonInput(
            lesson_text="중복 교훈",
            category="general",
            source_recommendation_id=1,
            source_ticker="AAPL",
            source_return_20d=-3.0,
        )
        store_lessons(session, [inp], run_date_id)
        count = store_lessons(session, [inp], run_date_id)
        assert count == 0

    def test_invalid_category_defaults_to_general(self, session, _seed_recommendation):
        """유효하지 않은 카테고리는 general로 저장된다."""
        run_date_id = date_to_id(date(2026, 2, 1))
        inp = LessonInput(
            lesson_text="카테고리 테스트",
            category="invalid_cat",
            source_recommendation_id=1,
            source_ticker="AAPL",
            source_return_20d=0.0,
        )
        store_lessons(session, [inp], run_date_id)
        stored = session.query(FactAILesson).first()
        assert stored.category == "general"

    def test_expires_at_set(self, session, _seed_recommendation):
        """expires_at이 90일 후로 설정된다."""
        run_date_id = date_to_id(date(2026, 2, 1))
        inp = LessonInput(
            lesson_text="만료 테스트",
            category="timing",
            source_recommendation_id=1,
            source_ticker="AAPL",
            source_return_20d=0.0,
        )
        store_lessons(session, [inp], run_date_id)
        stored = session.query(FactAILesson).first()
        expected = date(2026, 2, 1) + timedelta(days=90)
        assert stored.expires_at == expected


# ---------------------------------------------------------------------------
# get_active_lessons
# ---------------------------------------------------------------------------


class TestGetActiveLessons:
    def test_returns_active_only(self, session, _seed_recommendation):
        """활성 교훈만 반환한다."""
        run_date_id = date_to_id(date(2026, 2, 1))

        session.add(FactAILesson(
            created_date_id=run_date_id,
            lesson_text="활성 교훈",
            category="general",
            source_recommendation_id=1,
            source_ticker="AAPL",
            is_active=True,
            expires_at=date(2026, 5, 1),
        ))
        session.add(FactAILesson(
            created_date_id=run_date_id,
            lesson_text="비활성 교훈",
            category="general",
            source_recommendation_id=1,
            source_ticker="AAPL",
            is_active=False,
            expires_at=date(2026, 5, 1),
        ))
        session.commit()

        lessons = get_active_lessons(session, date(2026, 3, 1))
        assert len(lessons) == 1
        assert lessons[0].lesson_text == "활성 교훈"

    def test_top_n_limit(self, session, _seed_recommendation):
        """top_n 제한이 동작한다."""
        run_date_id = date_to_id(date(2026, 2, 1))
        for i in range(15):
            session.add(FactAILesson(
                created_date_id=run_date_id,
                lesson_text=f"교훈 {i}",
                category="general",
                source_recommendation_id=1,
                source_ticker="AAPL",
                is_active=True,
                expires_at=date(2026, 5, 1),
            ))
        session.commit()

        lessons = get_active_lessons(session, date(2026, 3, 1), top_n=5)
        assert len(lessons) == 5


# ---------------------------------------------------------------------------
# expire_old_lessons
# ---------------------------------------------------------------------------


class TestExpireOldLessons:
    def test_expire(self, session, _seed_recommendation):
        """만료된 교훈을 비활성화한다."""
        session.add(FactAILesson(
            created_date_id=date_to_id(date(2026, 1, 1)),
            lesson_text="만료될 교훈",
            category="general",
            source_recommendation_id=1,
            source_ticker="AAPL",
            is_active=True,
            expires_at=date(2026, 3, 1),
        ))
        session.add(FactAILesson(
            created_date_id=date_to_id(date(2026, 2, 1)),
            lesson_text="유효한 교훈",
            category="general",
            source_recommendation_id=1,
            source_ticker="AAPL",
            is_active=True,
            expires_at=date(2026, 6, 1),
        ))
        session.commit()

        expired = expire_old_lessons(session, date(2026, 4, 1))
        assert expired == 1

        active = session.query(FactAILesson).filter(FactAILesson.is_active == True).all()
        assert len(active) == 1
        assert active[0].lesson_text == "유효한 교훈"


# ---------------------------------------------------------------------------
# increment_applied_count
# ---------------------------------------------------------------------------


class TestIncrementAppliedCount:
    def test_increment(self, session, _seed_recommendation):
        """적용 횟수가 증가한다."""
        session.add(FactAILesson(
            created_date_id=date_to_id(date(2026, 2, 1)),
            lesson_text="카운트 테스트",
            category="general",
            source_recommendation_id=1,
            source_ticker="AAPL",
            is_active=True,
            times_applied=3,
            expires_at=date(2026, 5, 1),
        ))
        session.commit()

        lesson = session.query(FactAILesson).first()
        increment_applied_count(session, [lesson.lesson_id])

        session.refresh(lesson)
        assert lesson.times_applied == 4

    def test_empty_list(self, session):
        """빈 리스트로 호출해도 에러 없음."""
        increment_applied_count(session, [])
