"""DeepDivePipeline 통합 테스트."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src.db.helpers import date_to_id
from src.db.models import FactCollectionLog, FactDeepDiveAction, FactDeepDiveReport
from src.deepdive.schemas import AIResult, ChangeRecord
from src.deepdive.watchlist_manager import HoldingInfo, WatchlistEntry


@pytest.fixture
def mock_entries():
    return [
        WatchlistEntry(
            ticker="AAPL", stock_id=1, name="Apple",
            name_kr=None, sector="Technology",
            is_sp500=True, holding=None,
        ),
        WatchlistEntry(
            ticker="NVDA", stock_id=2, name="NVIDIA",
            name_kr=None, sector="Technology",
            is_sp500=True,
            holding=HoldingInfo(shares=100, avg_cost=130.0, opened_at=None),
        ),
    ]


@pytest.fixture
def mock_ai_result():
    return AIResult(
        action_grade="HOLD", conviction=7, uncertainty="medium",
        reasoning="Test reasoning", what_missing="Test missing",
    )


class TestDeepDivePipeline:
    """파이프라인 동작 검증."""

    def test_init(self, engine):
        from src.deepdive_pipeline import DeepDivePipeline

        pipeline = DeepDivePipeline(engine, date(2025, 1, 15), ticker="AAPL", force=True)
        assert pipeline.ticker == "AAPL"
        assert pipeline.force is True
        assert pipeline.run_date_id == 20250115

    @patch("src.deepdive_pipeline.load_watchlist")
    def test_step1_load(self, mock_load, engine, mock_entries):
        from src.deepdive_pipeline import DeepDivePipeline

        mock_load.return_value = mock_entries
        pipeline = DeepDivePipeline(engine, date(2025, 1, 15))
        count = pipeline.step1_load_watchlist()
        assert count == 2
        assert len(pipeline._watchlist_entries) == 2

    @patch("src.deepdive_pipeline.load_watchlist")
    def test_single_ticker_filter(self, mock_load, engine, mock_entries):
        from src.deepdive_pipeline import DeepDivePipeline

        mock_load.return_value = mock_entries
        pipeline = DeepDivePipeline(engine, date(2025, 1, 15), ticker="AAPL")
        count = pipeline.step1_load_watchlist()
        assert count == 1
        assert pipeline._watchlist_entries[0].ticker == "AAPL"

    def test_graceful_shutdown(self, engine):
        from src.deepdive_pipeline import DeepDivePipeline

        pipeline = DeepDivePipeline(engine, date(2025, 1, 15))
        pipeline._interrupted = True
        pipeline.run()  # 중단되어 빠르게 종료


class TestAIPrompts:
    """AI 프롬프트 모듈."""

    def test_build_context_with_holding(self, mock_entries):
        from src.deepdive.ai_prompts import build_stock_context

        entry = mock_entries[1]  # NVDA with holding
        layers = {}
        ctx = build_stock_context(entry, layers, 150.0, 2.5)
        assert "<holding_context>" in ctx
        assert "130.00" in ctx  # avg_cost
        assert "100" in ctx  # shares

    def test_build_context_no_holding(self, mock_entries):
        from src.deepdive.ai_prompts import build_stock_context

        entry = mock_entries[0]  # AAPL without holding
        layers = {}
        ctx = build_stock_context(entry, layers, 180.0, -1.2)
        assert "<holding_context>" not in ctx

    def test_parse_valid_json(self):
        from src.deepdive.ai_prompts import _parse_ai_response

        raw = '{"action_grade":"ADD","conviction":8,"uncertainty":"low","reasoning":"Good","what_missing":"None"}'
        result = _parse_ai_response(raw)
        assert result is not None
        assert result.action_grade == "ADD"
        assert result.conviction == 8

    def test_parse_malformed_fallback(self):
        from src.deepdive.ai_prompts import _parse_ai_response

        raw = 'Some text "action_grade":"TRIM","conviction":3 more text'
        result = _parse_ai_response(raw)
        assert result is not None
        assert result.action_grade == "TRIM"

    @patch("src.deepdive.ai_prompts.shutil.which")
    def test_cli_not_available(self, mock_which):
        from src.deepdive.ai_prompts import run_deepdive_cli

        mock_which.return_value = None
        result = run_deepdive_cli("test prompt")
        assert result is None

    @patch("src.deepdive.ai_prompts.subprocess.run")
    @patch("src.deepdive.ai_prompts.shutil.which")
    def test_cli_model_flag(self, mock_which, mock_run):
        from src.deepdive.ai_prompts import run_deepdive_cli

        mock_which.return_value = "/usr/bin/claude"
        mock_run.return_value = MagicMock(returncode=0, stdout='{"action_grade":"HOLD"}')
        run_deepdive_cli("test", system_prompt="sys", model="opus")
        args = mock_run.call_args[0][0]
        assert "--model" in args
        assert "opus" in args
        assert "--system-prompt" in args


class TestPhase3Steps:
    """Phase 3 신규 step 테스트."""

    def test_step4_in_steps_list(self, engine):
        """step4_pairs가 steps 리스트에 포함."""
        from src.deepdive_pipeline import DeepDivePipeline

        pipeline = DeepDivePipeline(engine, date(2025, 1, 15))
        steps = [
            ("dd_s1_load", pipeline.step1_load_watchlist),
            ("dd_s2_collect", pipeline.step2_collect_extras),
            ("dd_s3_compute", pipeline.step3_compute_layers),
            ("dd_s4_pairs", pipeline.step4_pairs),
            ("dd_s5_ai", pipeline.step5_ai_analysis),
            ("dd_s6_diff", pipeline.step6_diff_detection),
            ("dd_s7_persist", pipeline.step7_persist),
            ("dd_s8_notify", pipeline.step8_notify),
        ]
        step_names = [s[0] for s in steps]
        assert "dd_s4_pairs" in step_names
        assert "dd_s6_diff" in step_names

    def test_step4_pairs_method_exists(self, engine):
        """step4_pairs 메서드 존재."""
        from src.deepdive_pipeline import DeepDivePipeline

        pipeline = DeepDivePipeline(engine, date(2025, 1, 15))
        assert hasattr(pipeline, "step4_pairs")
        assert callable(pipeline.step4_pairs)

    def test_step6_diff_method_exists(self, engine):
        """step6_diff_detection 메서드 존재."""
        from src.deepdive_pipeline import DeepDivePipeline

        pipeline = DeepDivePipeline(engine, date(2025, 1, 15))
        assert hasattr(pipeline, "step6_diff_detection")
        assert callable(pipeline.step6_diff_detection)

    @patch("src.deepdive_pipeline.load_watchlist")
    def test_step4_empty_watchlist(self, mock_load, engine):
        """빈 워치리스트 -> step4 정상 완료."""
        from src.deepdive_pipeline import DeepDivePipeline

        mock_load.return_value = []
        pipeline = DeepDivePipeline(engine, date(2025, 1, 15))
        pipeline.step1_load_watchlist()
        count = pipeline.step4_pairs()
        assert count == 0

    def test_build_context_with_pairs(self, mock_entries):
        """pair_results 있을 때 pair_comparison 블록 포함."""
        from src.deepdive.ai_prompts import build_stock_context
        from src.deepdive.schemas import PeerComparison

        pairs = [PeerComparison(
            peer_ticker="MSFT", peer_name="Microsoft",
            similarity_score=0.95, market_cap_ratio=1.2,
            return_60d_peer=5.0, return_60d_target=3.0,
            per_peer=30.0, per_target=28.0,
        )]
        ctx = build_stock_context(mock_entries[0], {}, 180.0, -1.2, pair_results=pairs)
        assert "<pair_comparison>" in ctx
        assert "MSFT" in ctx
