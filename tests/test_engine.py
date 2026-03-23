"""DB 엔진/세션 테스트."""

from unittest.mock import patch

from src.db.engine import create_db_engine, get_session


class TestCreateDbEngine:
    def test_creates_sqlite_engine(self):
        engine = create_db_engine(":memory:")
        assert engine is not None
        assert "memory" in str(engine.url)

    def test_default_db_path(self):
        engine = create_db_engine()
        assert engine is not None


class TestGetSession:
    def test_session_context_manager(self):
        engine = create_db_engine(":memory:")
        with get_session(engine) as session:
            assert session is not None
            # Session should be usable
            result = session.execute(__import__("sqlalchemy").text("SELECT 1"))
            assert result.scalar() == 1

    def test_session_commits_on_exit(self):
        engine = create_db_engine(":memory:")
        # Just verify no exception on normal exit
        with get_session(engine) as session:
            session.execute(__import__("sqlalchemy").text("SELECT 1"))
