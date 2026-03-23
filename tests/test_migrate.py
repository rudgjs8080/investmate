"""DB 스키마 마이그레이션 유틸리티 테스트."""

from __future__ import annotations

from sqlalchemy import Column, Integer, Numeric, String, Text, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from src.db.migrate import _ensure_indexes, _get_db_columns, ensure_schema


def _make_engine():
    return create_engine("sqlite:///:memory:", echo=False)


class TestGetDbColumns:
    def test_returns_column_names(self):
        engine = _make_engine()
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE test_t (id INTEGER PRIMARY KEY, name TEXT)"))
        cols = _get_db_columns(engine, "test_t")
        assert cols == {"id", "name"}

    def test_empty_for_nonexistent_table(self):
        engine = _make_engine()
        cols = _get_db_columns(engine, "nonexistent")
        assert cols == set()


class TestEnsureSchema:
    def test_adds_missing_column(self):
        """ORM에 정의된 컬럼이 DB에 없으면 추가한다."""
        engine = _make_engine()
        # 1) return_10d 없이 테이블 생성
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE fact_daily_recommendations ("
                "  recommendation_id INTEGER PRIMARY KEY,"
                "  total_score NUMERIC,"
                "  return_1d NUMERIC,"
                "  return_5d NUMERIC,"
                "  return_20d NUMERIC"
                ")"
            ))

        # 2) ORM 모델 로드 (return_10d 포함)
        from src.db.models import Base  # noqa: F401

        # 3) 마이그레이션 실행
        added = ensure_schema(engine)
        assert added > 0

        # 4) return_10d 컬럼이 추가되었는지 확인
        cols = _get_db_columns(engine, "fact_daily_recommendations")
        assert "return_10d" in cols

    def test_idempotent(self):
        """이미 스키마가 일치하면 변경 없음."""
        engine = _make_engine()
        from src.db.models import Base
        Base.metadata.create_all(engine)

        added = ensure_schema(engine)
        assert added == 0

    def test_skips_nonexistent_tables(self):
        """DB에 없는 테이블은 건너뛴다."""
        engine = _make_engine()
        # 빈 DB에서 실행 — 테이블 없으므로 0
        added = ensure_schema(engine)
        assert added == 0

    def test_multiple_missing_columns(self):
        """여러 컬럼이 누락된 경우 모두 추가한다."""
        engine = _make_engine()
        # 최소 컬럼만 있는 테이블
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE fact_daily_recommendations ("
                "  recommendation_id INTEGER PRIMARY KEY"
                ")"
            ))

        from src.db.models import Base  # noqa: F401

        added = ensure_schema(engine)
        # total_score, return_1d, return_5d, return_10d, return_20d 등 다수 추가
        assert added >= 5

        cols = _get_db_columns(engine, "fact_daily_recommendations")
        assert "return_10d" in cols
        assert "total_score" in cols
        assert "return_1d" in cols

    def test_preserves_existing_data(self):
        """마이그레이션 후 기존 데이터가 보존된다."""
        engine = _make_engine()
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE fact_daily_recommendations ("
                "  recommendation_id INTEGER PRIMARY KEY,"
                "  total_score NUMERIC"
                ")"
            ))
            conn.execute(text(
                "INSERT INTO fact_daily_recommendations (recommendation_id, total_score) "
                "VALUES (1, 7.5)"
            ))

        from src.db.models import Base  # noqa: F401
        ensure_schema(engine)

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT total_score, return_10d FROM fact_daily_recommendations WHERE recommendation_id = 1"
            )).fetchone()
        assert float(row[0]) == 7.5
        assert row[1] is None  # 새 컬럼은 NULL


class TestEnsureIndexes:
    def test_ensure_indexes_creates_missing(self):
        """테이블에 인덱스가 없으면 ORM 정의에 따라 생성한다."""
        engine = _make_engine()
        from src.db.models import Base

        Base.metadata.create_all(engine)

        # 기존 인덱스 중 하나를 삭제
        with engine.begin() as conn:
            conn.execute(text("DROP INDEX IF EXISTS idx_prices_date"))

        # _ensure_indexes로 복구
        created = _ensure_indexes(engine)
        assert created >= 1

        # 인덱스가 다시 존재하는지 확인
        insp = inspect(engine)
        idx_names = {idx["name"] for idx in insp.get_indexes("fact_daily_prices")}
        assert "idx_prices_date" in idx_names

    def test_ensure_indexes_skips_existing(self):
        """이미 인덱스가 존재하면 에러 없이 스킵한다."""
        engine = _make_engine()
        from src.db.models import Base

        Base.metadata.create_all(engine)

        # 모든 인덱스가 이미 존재 -> 0개 생성
        created = _ensure_indexes(engine)
        assert created == 0

    def test_ensure_indexes_handles_invalid_table(self):
        """테이블이 DB에 없어도 에러 없이 처리한다."""
        engine = _make_engine()
        # 빈 DB — 테이블 없음
        created = _ensure_indexes(engine)
        assert created == 0
