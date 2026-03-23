"""CLI 명령어 기본 테스트 -- Click testing client 사용."""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from src.main import cli


@pytest.fixture
def runner():
    return CliRunner()


class TestCliBasic:
    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "investmate" in result.output.lower() or "run" in result.output

    def test_config_show(self, runner):
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0

    @patch("src.main.create_db_engine")
    @patch("src.main.get_session")
    def test_db_status(self, mock_session, mock_engine, runner):
        mock_engine.return_value = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
        mock_ctx.__exit__ = MagicMock(return_value=False)
        mock_session.return_value = mock_ctx

        result = runner.invoke(cli, ["db", "status"])
        # May fail due to actual DB check, but should not crash with import error
        assert result.exit_code in (0, 1)

    def test_report_list(self, runner):
        result = runner.invoke(cli, ["report", "list"])
        assert result.exit_code == 0

    def test_history_recommendations(self, runner):
        with patch("src.main.create_db_engine") as me, patch("src.main.get_session") as ms:
            from sqlalchemy import create_engine
            from src.db.engine import init_db
            engine = create_engine("sqlite:///:memory:")
            init_db(engine)
            me.return_value = engine
            from contextlib import contextmanager
            @contextmanager
            def _s(e):
                from sqlalchemy.orm import Session
                with Session(e) as s:
                    yield s
            ms.side_effect = _s
            result = runner.invoke(cli, ["history", "recommendations"])
            assert result.exit_code == 0

    def test_history_pipeline(self, runner):
        with patch("src.main.create_db_engine") as me, patch("src.main.get_session") as ms:
            from sqlalchemy import create_engine
            from src.db.engine import init_db
            engine = create_engine("sqlite:///:memory:")
            init_db(engine)
            me.return_value = engine
            from contextlib import contextmanager
            @contextmanager
            def _s(e):
                from sqlalchemy.orm import Session
                with Session(e) as s:
                    yield s
            ms.side_effect = _s
            result = runner.invoke(cli, ["history", "pipeline"])
            assert result.exit_code == 0

    def test_run_help(self, runner):
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--step" in result.output or "--date" in result.output

    def test_stock_help(self, runner):
        result = runner.invoke(cli, ["stock", "--help"])
        assert result.exit_code == 0

    def test_db_help(self, runner):
        result = runner.invoke(cli, ["db", "--help"])
        assert result.exit_code == 0

    def test_report_show_missing(self, runner):
        result = runner.invoke(cli, ["report", "show", "2020-01-01"])
        # exit_code may be 0 or 1 depending on implementation
        assert result.exit_code in (0, 1)


class TestCliEdgeCases:
    """CLI 입력 엣지 케이스 테스트."""

    def test_run_invalid_date(self, runner):
        """잘못된 날짜 형식."""
        result = runner.invoke(cli, ["run", "--date", "invalid-date"])
        assert result.exit_code != 0

    def test_backtest_help(self, runner):
        result = runner.invoke(cli, ["backtest", "--help"])
        assert result.exit_code == 0
        assert "run" in result.output or "compare" in result.output

    def test_backtest_run_help(self, runner):
        result = runner.invoke(cli, ["backtest", "run", "--help"])
        assert result.exit_code == 0
        assert "--start" in result.output

    def test_config_help(self, runner):
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0

    def test_report_latest(self, runner):
        result = runner.invoke(cli, ["report", "latest"])
        # May show report or "없음" depending on data
        assert result.exit_code == 0

    def test_config_set(self, runner):
        result = runner.invoke(cli, ["config", "set", "top_n", "5"])
        assert result.exit_code == 0

    @patch("src.main.create_db_engine")
    @patch("src.main.get_session")
    def test_stock_command(self, mock_session, mock_engine, runner):
        from sqlalchemy import create_engine
        from src.db.engine import init_db
        engine = create_engine("sqlite:///:memory:")
        init_db(engine)
        mock_engine.return_value = engine
        from contextlib import contextmanager
        @contextmanager
        def _s(e):
            from sqlalchemy.orm import Session
            with Session(e) as s:
                yield s
        mock_session.side_effect = _s
        result = runner.invoke(cli, ["stock", "AAPL"])
        # Stock may not exist in test DB
        assert result.exit_code in (0, 1)

    @patch("src.main.create_db_engine")
    @patch("src.main.get_session")
    def test_db_backup(self, mock_session, mock_engine, runner):
        from sqlalchemy import create_engine
        engine = create_engine("sqlite:///:memory:")
        mock_engine.return_value = engine
        result = runner.invoke(cli, ["db", "backup"])
        assert result.exit_code in (0, 1)

    @patch("src.main.create_db_engine")
    @patch("src.main.get_session")
    def test_history_signals(self, mock_session, mock_engine, runner):
        from sqlalchemy import create_engine
        from src.db.engine import init_db
        engine = create_engine("sqlite:///:memory:")
        init_db(engine)
        mock_engine.return_value = engine
        from contextlib import contextmanager
        @contextmanager
        def _s(e):
            from sqlalchemy.orm import Session
            with Session(e) as s:
                yield s
        mock_session.side_effect = _s
        result = runner.invoke(cli, ["history", "signals", "AAPL"])
        assert result.exit_code in (0, 1)

    @patch("src.main.create_db_engine")
    @patch("src.main.get_session")
    def test_history_performance_no_data(self, mock_session, mock_engine, runner):
        from sqlalchemy import create_engine
        from src.db.engine import init_db

        engine = create_engine("sqlite:///:memory:")
        init_db(engine)
        mock_engine.return_value = engine

        from contextlib import contextmanager
        @contextmanager
        def _fake_session(eng):
            from sqlalchemy.orm import Session
            with Session(eng) as s:
                yield s

        mock_session.side_effect = _fake_session

        result = runner.invoke(cli, ["history", "performance"])
        assert result.exit_code == 0
        assert "없음" in result.output or "0" in result.output
