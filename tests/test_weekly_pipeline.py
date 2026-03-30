"""주간 파이프라인 테스트."""

from __future__ import annotations

from datetime import date, timedelta

import pytest


def test_weekly_pipeline_defaults():
    """기본값: 직전 주의 year/week 계산."""
    from src.weekly_pipeline import WeeklyPipeline
    from sqlalchemy import create_engine
    from src.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    pipeline = WeeklyPipeline(engine)

    # 직전 주 계산 확인
    yesterday = date.today() - timedelta(days=1)
    iso = yesterday.isocalendar()
    assert pipeline.year == iso[0]
    assert pipeline.week == iso[1]
    assert pipeline.skip_notify is False


def test_weekly_pipeline_custom_week():
    """커스텀 year/week 지정."""
    from src.weekly_pipeline import WeeklyPipeline
    from sqlalchemy import create_engine
    from src.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    pipeline = WeeklyPipeline(engine, year=2026, week=13)
    assert pipeline.year == 2026
    assert pipeline.week == 13


def test_weekly_pipeline_run_empty_db():
    """빈 DB에서 에러 없이 실행."""
    from src.weekly_pipeline import WeeklyPipeline
    from sqlalchemy import create_engine
    from src.db.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    pipeline = WeeklyPipeline(engine, year=2026, week=99, skip_notify=True)
    # 에러 없이 실행되어야 함
    pipeline.run()


def test_weekly_pipeline_idempotent(session, engine):
    """두 번 실행 시 체크포인트로 스킵."""
    from src.weekly_pipeline import WeeklyPipeline
    from src.db.models import FactCollectionLog
    from sqlalchemy import select

    pipeline = WeeklyPipeline(engine, year=2026, week=99, skip_notify=True)
    pipeline.run()

    # 첫 실행 후 로그 확인
    logs = list(session.execute(
        select(FactCollectionLog)
        .where(FactCollectionLog.step == "weekly_report")
    ).scalars().all())
    assert len(logs) >= 1

    # 두 번째 실행: 스킵
    pipeline2 = WeeklyPipeline(engine, year=2026, week=99, skip_notify=True)
    pipeline2.run()  # 에러 없이 스킵
