"""SQLAlchemy 엔진 및 세션 팩토리."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.config import get_settings


def _set_sqlite_wal(dbapi_conn, connection_record) -> None:  # noqa: ANN001
    """SQLite WAL 모드 + 성능 최적화 PRAGMA 설정."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA synchronous=NORMAL")   # FULL→NORMAL (WAL 모드에서 안전)
    cursor.execute("PRAGMA cache_size=-64000")     # 64MB 캐시
    cursor.execute("PRAGMA busy_timeout=5000")     # 5초 lock 대기
    cursor.execute("PRAGMA temp_store=MEMORY")     # 임시 테이블 메모리 사용
    cursor.close()


def create_db_engine(db_path: str | None = None) -> Engine:
    """SQLAlchemy 엔진을 생성한다.

    Args:
        db_path: DB 파일 경로. None이면 설정에서 로드.
    """
    if db_path is None:
        db_path = get_settings().db_path

    # DB 디렉토리 생성
    db_file = Path(db_path)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    url = f"sqlite:///{db_path}"
    engine = create_engine(url, echo=False)

    event.listen(engine, "connect", _set_sqlite_wal)

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """세션 팩토리를 생성한다."""
    return sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def get_session(
    engine: Engine | None = None,
) -> Generator[Session, None, None]:
    """세션 컨텍스트 매니저.

    commit/rollback을 자동으로 처리한다.
    """
    if engine is None:
        engine = create_db_engine()
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def batch_write_mode(engine: Engine):
    """배치 쓰기 최적화 — synchronous OFF + WAL checkpoint."""
    with engine.connect() as conn:
        conn.execute(text("PRAGMA synchronous=OFF"))
        conn.commit()
        try:
            yield
        finally:
            conn.execute(text("PRAGMA synchronous=NORMAL"))
            try:
                conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            except Exception:
                pass
            conn.commit()


def init_db(engine: Engine) -> None:
    """모든 테이블을 생성한다."""
    from src.db.models import Base

    Base.metadata.create_all(engine)
