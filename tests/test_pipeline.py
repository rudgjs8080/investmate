"""파이프라인 테스트."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.pipeline import DailyPipeline


class TestDailyPipeline:
    """DailyPipeline 기본 동작 테스트."""

    def test_pipeline_init(self, engine):
        pipeline = DailyPipeline(engine, target_date=date(2024, 3, 1))
        assert pipeline.target_date == date(2024, 3, 1)
        assert pipeline.top_n == 10
        assert pipeline.run_date_id == 20240301
        assert pipeline._interrupted is False

    def test_pipeline_creates_date(self, engine):
        """파이프라인 실행 시 dim_date에 날짜가 생성된다."""
        from src.db.seed import seed_dimensions

        seed_dimensions(engine)

        pipeline = DailyPipeline(engine, target_date=date(2024, 3, 1))
        # run()은 내부에서 ensure_date_ids 호출
        # step 실행은 외부 의존성이 필요하므로 개별 테스트에서 mock

    @patch("src.pipeline.DailyPipeline.step1_collect", return_value=0)
    @patch("src.pipeline.DailyPipeline.step2_analyze", return_value=0)
    @patch("src.pipeline.DailyPipeline.step3_external", return_value=0)
    @patch("src.pipeline.DailyPipeline.step4_screen", return_value=0)
    @patch("src.pipeline.DailyPipeline.step5_report", return_value=0)
    @patch("src.pipeline.DailyPipeline.step6_notify", return_value=0)
    def test_run_calls_all_steps(self, m6, m5, m4, m3, m2, m1, engine):
        """run()이 6단계를 모두 호출한다."""
        from src.db.seed import seed_dimensions

        seed_dimensions(engine)

        pipeline = DailyPipeline(engine, target_date=date(2024, 3, 1))
        pipeline.run(force=True)

        m1.assert_called_once()
        m2.assert_called_once()
        m3.assert_called_once()
        m4.assert_called_once()
        m5.assert_called_once()
        m6.assert_called_once()

    @patch("src.pipeline.DailyPipeline.step2_analyze", return_value=0)
    def test_run_specific_step(self, m2, engine):
        """특정 단계만 실행할 수 있다."""
        from src.db.seed import seed_dimensions

        seed_dimensions(engine)

        pipeline = DailyPipeline(engine, target_date=date(2024, 3, 1))
        pipeline.run(step=2, force=True)

        m2.assert_called_once()

    def test_per_stock_error_isolation(self, engine):
        """개별 종목 저장 실패 시 다른 종목은 정상 처리된다."""
        from src.db.seed import seed_dimensions

        seed_dimensions(engine)

        pipeline = DailyPipeline(engine, target_date=date(2024, 3, 1))

        # Mock: batch_download_prices returns 3 tickers
        mock_price = MagicMock()
        mock_price.model_dump.return_value = {
            "date": date(2024, 3, 1), "open": 100, "high": 105,
            "low": 95, "close": 102, "adj_close": 102, "volume": 1000000,
        }

        prices_data = {
            "GOOD1": [mock_price],
            "FAIL": [mock_price],
            "GOOD2": [mock_price],
        }

        mock_stock_good1 = MagicMock(stock_id=1, ticker="GOOD1")
        mock_stock_fail = MagicMock(stock_id=2, ticker="FAIL")
        mock_stock_good2 = MagicMock(stock_id=3, ticker="GOOD2")
        stocks = [mock_stock_good1, mock_stock_fail, mock_stock_good2]

        call_count = 0
        def mock_upsert(session, stock_id, price_dicts):
            nonlocal call_count
            call_count += 1
            if stock_id == 2:
                raise RuntimeError("DB write error for FAIL ticker")
            return len(price_dicts)

        # Import target module to ensure it's loaded
        import src.data.yahoo_client  # noqa: F401

        with patch("src.pipeline.StockRepository.get_sp500_active", return_value=stocks), \
             patch("src.pipeline.DailyPriceRepository.get_last_date", return_value=None), \
             patch("src.pipeline.DailyPriceRepository.upsert_prices_batch", side_effect=mock_upsert), \
             patch("src.data.yahoo_client.batch_download_prices", return_value=(prices_data, [])), \
             patch("src.data.yahoo_client.fetch_financial_data", return_value=([], None)):
            result = pipeline.step1_collect()

        # GOOD1 + GOOD2 succeed (1 each), FAIL raises
        assert result >= 2
        assert call_count == 3  # all 3 attempted

    @patch("src.pipeline.DailyPipeline.step1_collect", return_value=0)
    @patch("src.pipeline.DailyPipeline.step2_analyze", return_value=0)
    @patch("src.pipeline.DailyPipeline.step3_external", return_value=0)
    @patch("src.pipeline.DailyPipeline.step4_screen", return_value=0)
    @patch("src.pipeline.DailyPipeline.step5_report", return_value=0)
    @patch("src.pipeline.DailyPipeline.step6_notify", return_value=0)
    def test_graceful_shutdown_flag(self, m6, m5, m4, m3, m2, m1, engine):
        """_interrupted=True 시 남은 스텝을 실행하지 않는다."""
        from src.db.seed import seed_dimensions

        seed_dimensions(engine)

        pipeline = DailyPipeline(engine, target_date=date(2024, 3, 1))
        # step1 호출 시 인터럽트 플래그 설정
        def interrupt_after_step1():
            pipeline._interrupted = True
            return 0

        m1.side_effect = interrupt_after_step1

        pipeline.run(force=True)

        m1.assert_called_once()
        # step2 이후는 호출되지 않아야 함
        m2.assert_not_called()
        m3.assert_not_called()
        m4.assert_not_called()
        m5.assert_not_called()
        m6.assert_not_called()

    @patch("src.pipeline.DailyPipeline.step1_collect", return_value=0)
    @patch("src.pipeline.DailyPipeline.step2_analyze", return_value=0)
    @patch("src.pipeline.DailyPipeline.step3_external", return_value=0)
    @patch("src.pipeline.DailyPipeline.step4_screen", return_value=0)
    @patch("src.pipeline.DailyPipeline.step5_report", return_value=0)
    @patch("src.pipeline.DailyPipeline.step6_notify", return_value=0)
    def test_step_checkpoint_skip(self, m6, m5, m4, m3, m2, m1, engine):
        """이미 성공한 스텝은 force=False일 때 스킵된다."""
        from src.db.seed import seed_dimensions
        from src.db.engine import get_session
        from src.db.helpers import ensure_date_ids
        from src.db.models import FactCollectionLog

        seed_dimensions(engine)

        target = date(2024, 3, 1)
        run_date_id = 20240301

        # 날짜 시딩 + step1 성공 로그 삽입
        with get_session(engine) as session:
            ensure_date_ids(session, [target])
            log = FactCollectionLog(
                run_date_id=run_date_id,
                step="step1_collect",
                status="success",
                started_at=datetime(2024, 3, 1, 10, 0, 0),
                finished_at=datetime(2024, 3, 1, 10, 5, 0),
                records_count=100,
            )
            session.add(log)
            session.commit()

        pipeline = DailyPipeline(engine, target_date=target)
        pipeline.run(force=False)

        # step1 은 이미 완료 -> 스킵
        m1.assert_not_called()
        # 나머지는 호출 (force=False이지만 완료 로그 없음)
        m2.assert_called_once()

    def test_is_step_done_false(self, engine):
        """완료 로그가 없으면 False를 반환한다."""
        from src.db.seed import seed_dimensions
        from src.db.engine import get_session
        from src.db.helpers import ensure_date_ids

        seed_dimensions(engine)

        target = date(2024, 3, 1)
        with get_session(engine) as session:
            ensure_date_ids(session, [target])

        pipeline = DailyPipeline(engine, target_date=target)
        assert pipeline._is_step_done("step1_collect") is False

    def test_is_step_done_true(self, engine):
        """성공 로그가 있으면 True를 반환한다."""
        from src.db.seed import seed_dimensions
        from src.db.engine import get_session
        from src.db.helpers import ensure_date_ids
        from src.db.models import FactCollectionLog

        seed_dimensions(engine)

        target = date(2024, 3, 1)
        with get_session(engine) as session:
            ensure_date_ids(session, [target])
            log = FactCollectionLog(
                run_date_id=20240301,
                step="step1_collect",
                status="success",
                started_at=datetime(2024, 3, 1, 10, 0, 0),
                finished_at=datetime(2024, 3, 1, 10, 5, 0),
            )
            session.add(log)
            session.commit()

        pipeline = DailyPipeline(engine, target_date=target)
        assert pipeline._is_step_done("step1_collect") is True
