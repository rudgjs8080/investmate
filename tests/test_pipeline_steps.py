"""파이프라인 개별 step 테스트 -- mock 기반."""

from datetime import date
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.pipeline import DailyPipeline


@pytest.fixture
def mock_engine():
    from sqlalchemy import create_engine
    from src.db.engine import init_db
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return engine


class TestPipelineInit:
    def test_default_date(self, mock_engine):
        pipeline = DailyPipeline(mock_engine)
        assert pipeline.target_date == date.today()
        assert pipeline.top_n == 10

    def test_custom_params(self, mock_engine):
        pipeline = DailyPipeline(mock_engine, target_date=date(2026, 3, 15), top_n=5, skip_notify=True)
        assert pipeline.target_date == date(2026, 3, 15)
        assert pipeline.top_n == 5
        assert pipeline.skip_notify is True

    def test_run_date_id(self, mock_engine):
        pipeline = DailyPipeline(mock_engine, target_date=date(2026, 3, 15))
        assert pipeline.run_date_id == 20260315


class TestStep3External:
    def test_returns_0_without_macro(self, mock_engine):
        pipeline = DailyPipeline(mock_engine, target_date=date(2026, 3, 15))
        # No macro data in empty DB
        result = pipeline.step3_external()
        assert result == 0


class TestStep6Notify:
    def test_skip_when_flagged(self, mock_engine):
        pipeline = DailyPipeline(mock_engine, skip_notify=True)
        result = pipeline.step6_notify()
        assert result == 0

    def test_skip_when_no_channels(self, mock_engine):
        pipeline = DailyPipeline(mock_engine)
        result = pipeline.step6_notify()
        assert result == 0


class TestLogStep:
    def test_logs_success(self, mock_engine):
        from datetime import datetime
        pipeline = DailyPipeline(mock_engine, target_date=date(2026, 3, 15))
        # Should not raise
        pipeline._log_step("test_step", "success", datetime.now(), records_count=5)

    def test_logs_failure(self, mock_engine):
        from datetime import datetime
        pipeline = DailyPipeline(mock_engine, target_date=date(2026, 3, 15))
        pipeline._log_step("test_step", "failed", datetime.now(), message="test error")
