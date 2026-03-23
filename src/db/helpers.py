"""DB 헬퍼 유틸리티 — 날짜 ID 변환 등."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import DimDate


def date_to_id(d: date) -> int:
    """date → YYYYMMDD 정수 변환."""
    return d.year * 10000 + d.month * 100 + d.day


def id_to_date(date_id: int) -> date:
    """YYYYMMDD 정수 → date 변환.

    Raises:
        ValueError: 유효하지 않은 날짜 (예: 20250132).
    """
    year = date_id // 10000
    month = (date_id % 10000) // 100
    day = date_id % 100
    if not (1 <= month <= 12 and 1 <= day <= 31):
        raise ValueError(f"유효하지 않은 date_id: {date_id}")
    return date(year, month, day)


def _make_dim_date(d: date) -> DimDate:
    """date로부터 DimDate 레코드를 생성한다."""
    return DimDate(
        date_id=date_to_id(d),
        date=d,
        year=d.year,
        quarter=(d.month - 1) // 3 + 1,
        month=d.month,
        week_of_year=d.isocalendar()[1],
        day_of_week=d.weekday(),
        is_trading_day=d.weekday() < 5,  # 주말 제외 (공휴일은 별도 처리)
        fiscal_quarter=f"{d.year}Q{(d.month - 1) // 3 + 1}",
    )


def ensure_date_ids(
    session: Session, dates: list[date]
) -> dict[date, int]:
    """주어진 날짜들이 dim_date에 존재하는지 확인하고, 없으면 INSERT한다.

    Returns:
        {date: date_id} 매핑 딕셔너리.
    """
    if not dates:
        return {}

    target_ids = {date_to_id(d) for d in dates}

    # 이미 존재하는 date_id 조회
    stmt = select(DimDate.date_id).where(DimDate.date_id.in_(target_ids))
    existing_ids = set(session.execute(stmt).scalars().all())

    # 없는 날짜 INSERT
    missing_dates = [d for d in dates if date_to_id(d) not in existing_ids]
    for d in missing_dates:
        session.add(_make_dim_date(d))

    if missing_dates:
        session.flush()

    return {d: date_to_id(d) for d in dates}
