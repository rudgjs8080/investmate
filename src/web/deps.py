"""FastAPI 의존성 주입 — DB 세션, 설정 등."""

from __future__ import annotations

from typing import Generator

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings
from src.db.engine import create_db_engine, create_session_factory

_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    """싱글턴 엔진을 반환한다."""
    global _engine
    if _engine is None:
        _engine = create_db_engine(get_settings().db_path)
        from src.db.migrate import ensure_schema
        ensure_schema(_engine)
    return _engine


def get_db() -> Generator[Session, None, None]:
    """FastAPI 의존성: DB 세션을 yield한다."""
    global _session_factory
    if _session_factory is None:
        _session_factory = create_session_factory(get_engine())
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
