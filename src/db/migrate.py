"""경량 스키마 마이그레이션 유틸리티.

ORM 모델에 정의된 컬럼이 실제 SQLite 테이블에 없을 때 ALTER TABLE ADD COLUMN을 실행한다.
Alembic 없이도 기존 DB를 안전하게 업그레이드할 수 있다.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import inspect, inspect as sa_inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _get_db_columns(engine: Engine, table_name: str) -> set[str]:
    """실제 DB 테이블의 컬럼 이름 집합을 반환한다."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table_name})")).fetchall()
    return {row[1] for row in rows}


def _get_orm_columns(engine: Engine, table_name: str) -> dict[str, Any]:
    """ORM 모델에서 정의된 컬럼 정보를 반환한다.

    Returns:
        {컬럼이름: Column 객체} 딕셔너리
    """
    insp = inspect(engine)
    try:
        return {col["name"]: col for col in insp.get_columns(table_name)}
    except Exception:
        return {}


def _sqlite_type(col_info: dict[str, Any]) -> str:
    """SQLAlchemy 컬럼 정보에서 SQLite 타입 문자열을 추출한다."""
    col_type = str(col_info.get("type", "TEXT"))
    for prefix in ("NUMERIC", "INTEGER", "TEXT", "REAL", "BLOB", "BOOLEAN", "VARCHAR"):
        if prefix in col_type.upper():
            return prefix
    return "TEXT"


def ensure_schema(engine: Engine) -> int:
    """ORM 모델과 실제 DB 스키마를 비교하여 누락 컬럼을 추가한다.

    Returns:
        추가된 컬럼 수.
    """
    from src.db.models import Base

    added = 0
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())

    for table_name, table in Base.metadata.tables.items():
        if table_name not in existing_tables:
            # 신규 테이블 자동 생성
            try:
                table.create(bind=engine)
                logger.info("신규 테이블 생성: %s", table_name)
                added += 1
            except Exception as e:
                logger.warning("테이블 생성 실패 (%s): %s", table_name, e)
            continue

        db_cols = _get_db_columns(engine, table_name)
        orm_cols = {col.name: col for col in table.columns}

        for col_name, col in orm_cols.items():
            if col_name in db_cols:
                continue

            col_type = str(col.type)
            nullable = col.nullable if col.nullable is not None else True
            null_clause = "" if nullable else " NOT NULL"
            default_clause = ""
            if col.default is not None and col.default.arg is not None:
                default_val = col.default.arg
                if isinstance(default_val, bool):
                    default_clause = f" DEFAULT {int(default_val)}"
                elif isinstance(default_val, (int, float)):
                    default_clause = f" DEFAULT {default_val}"
                elif isinstance(default_val, str):
                    default_clause = f" DEFAULT '{default_val}'"

            # NOT NULL without DEFAULT is invalid for ALTER TABLE ADD COLUMN in SQLite
            if null_clause and not default_clause:
                null_clause = ""  # fallback to nullable

            sql = f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}{null_clause}{default_clause}"
            logger.info("스키마 마이그레이션: %s", sql)
            with engine.begin() as conn:
                conn.execute(text(sql))
            added += 1

    if added:
        logger.info("스키마 마이그레이션 완료: %d개 컬럼 추가", added)
    else:
        logger.debug("스키마 마이그레이션: 변경 없음")

    _ensure_indexes(engine)

    return added


def _ensure_indexes(engine: Engine) -> int:
    """ORM 모델의 Index 정의를 DB에 반영한다.

    Returns:
        생성된 인덱스 수.
    """
    from src.db.models import Base

    inspector = sa_inspect(engine)
    created = 0

    for table in Base.metadata.sorted_tables:
        try:
            existing_names = {idx["name"] for idx in inspector.get_indexes(table.name)}
        except Exception:
            continue
        for index in table.indexes:
            if index.name and index.name not in existing_names:
                try:
                    with engine.begin() as conn:
                        index.create(bind=conn)
                    logger.info("인덱스 생성: %s on %s", index.name, table.name)
                    created += 1
                except Exception as e:
                    logger.debug("인덱스 생성 스킵 (%s): %s", index.name, e)

    if created:
        logger.info("인덱스 마이그레이션 완료: %d개 생성", created)
    else:
        logger.debug("인덱스 마이그레이션: 변경 없음")

    return created
