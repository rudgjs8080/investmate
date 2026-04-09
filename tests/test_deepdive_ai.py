"""Deep Dive AI debate + scenario 테스트."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from datetime import date

from src.db.helpers import date_to_id
from src.db.repository import StockRepository
from src.deepdive.schemas import AIResult, ScenarioForecast
from src.deepdive.watchlist_manager import HoldingInfo, WatchlistEntry

_MOCK_ENTRY = WatchlistEntry(
    ticker="AAPL", stock_id=1, name="Apple",
    name_kr=None, sector="Technology", is_sp500=True, holding=None,
)

_SYNTH_JSON = json.dumps({
    "action_grade": "HOLD", "conviction": 7, "uncertainty": "medium",
    "reasoning": "성장 모멘텀 유지", "consensus_strength": "medium",
    "what_missing": "옵션 데이터 부재",
    "scenarios": {
        "1M": {
            "base": {"prob": 0.5, "low": 175, "high": 185},
            "bull": {"prob": 0.3, "low": 185, "high": 200},
            "bear": {"prob": 0.2, "low": 160, "high": 175},
        },
        "3M": {
            "base": {"prob": 0.45, "low": 170, "high": 195},
            "bull": {"prob": 0.3, "low": 195, "high": 220},
            "bear": {"prob": 0.25, "low": 150, "high": 170},
        },
        "6M": {
            "base": {"prob": 0.4, "low": 165, "high": 210},
            "bull": {"prob": 0.35, "low": 210, "high": 250},
            "bear": {"prob": 0.25, "low": 140, "high": 165},
        },
    },
    "key_levels": {"support": 170, "resistance": 195, "stop_loss": 155},
})

_BULL_JSON = json.dumps({"action": "ADD", "conviction": 8, "bull_case": ["Strong growth"]})
_BEAR_JSON = json.dumps({"action": "HOLD", "conviction": 4, "bear_case": ["Valuation concern"]})


class TestCLIDebate:
    """CLI 기반 3라운드 토론."""

    @patch("src.deepdive.ai_debate_cli.run_deepdive_cli")
    def test_debate_5_calls(self, mock_cli):
        from src.deepdive.ai_debate_cli import run_deepdive_debate

        mock_cli.side_effect = [_BULL_JSON, _BEAR_JSON, _BULL_JSON, _BEAR_JSON, _SYNTH_JSON]
        result = run_deepdive_debate(_MOCK_ENTRY, {}, 180.0, 1.5)
        assert mock_cli.call_count == 5
        assert result is not None
        assert result.final_result is not None
        assert result.final_result.action_grade == "HOLD"
        assert result.consensus_strength == "medium"

    @patch("src.deepdive.ai_debate_cli.run_deepdive_cli")
    def test_debate_r1_bull_failure(self, mock_cli):
        """R1 Bull 실패 → R2 Bull 스킵."""
        from src.deepdive.ai_debate_cli import run_deepdive_debate

        mock_cli.side_effect = [None, _BEAR_JSON, None, _BEAR_JSON, _SYNTH_JSON]
        result = run_deepdive_debate(_MOCK_ENTRY, {}, 180.0, 1.5)
        assert result is not None
        assert result.final_result is not None

    @patch("src.deepdive.ai_debate_cli.run_deepdive_simple")
    @patch("src.deepdive.ai_debate_cli.run_deepdive_cli")
    def test_debate_all_fail_simple_fallback(self, mock_cli, mock_simple):
        """5회 모두 실패 → simple 폴백."""
        from src.deepdive.ai_debate_cli import run_deepdive_debate

        mock_cli.return_value = None
        # mock_simple is the outer decorator (run_deepdive_simple)
        mock_simple.return_value = AIResult(
            action_grade="HOLD", conviction=5, uncertainty="high",
            reasoning="Fallback", what_missing=None,
        )
        result = run_deepdive_debate(_MOCK_ENTRY, {}, 180.0, 1.5, timeout=5)
        assert result is not None
        assert result.final_result.action_grade == "HOLD"
        assert result.consensus_strength == "low"

    @patch("src.deepdive.ai_debate_cli.run_deepdive_cli")
    def test_debate_has_scenarios(self, mock_cli):
        """Synthesizer가 시나리오를 반환하는지 확인."""
        from src.deepdive.ai_debate_cli import run_deepdive_debate

        mock_cli.side_effect = [_BULL_JSON, _BEAR_JSON, _BULL_JSON, _BEAR_JSON, _SYNTH_JSON]
        result = run_deepdive_debate(_MOCK_ENTRY, {}, 180.0, 1.5)
        assert result.scenarios is not None
        assert "1M" in result.scenarios


class TestScenarioParsing:
    """시나리오 예측 파싱."""

    def test_parse_valid(self):
        from src.deepdive.scenarios import parse_scenarios

        data = json.loads(_SYNTH_JSON)
        result = parse_scenarios(data, 180.0)
        assert len(result) == 9
        assert all(isinstance(s, ScenarioForecast) for s in result)
        base_1m = next(s for s in result if s.horizon == "1M" and s.scenario == "BASE")
        assert base_1m.probability == 0.5

    def test_parse_missing_scenarios(self):
        from src.deepdive.scenarios import parse_scenarios

        result = parse_scenarios({}, 180.0)
        assert result == []

    def test_parse_sanity_check(self):
        """가격 범위 현재가 +-80% 초과 시 필터링."""
        from src.deepdive.scenarios import parse_scenarios

        data = {
            "scenarios": {
                "1M": {
                    "base": {"prob": 0.5, "low": 10, "high": 20},  # $180 대비 너무 낮음
                    "bull": {"prob": 0.3, "low": 500, "high": 600},  # 너무 높음
                    "bear": {"prob": 0.2, "low": 160, "high": 175},  # 유효
                },
            },
        }
        result = parse_scenarios(data, 180.0)
        assert len(result) == 1
        assert result[0].scenario == "BEAR"


class TestForecastRepository:
    """시나리오 예측 DB 저장/조회."""

    def test_insert_and_get(self, seeded_session, us_market):
        from src.db.helpers import ensure_date_ids
        from src.db.repository import DeepDiveRepository

        stock = StockRepository.add(seeded_session, "AAPL", "Apple", us_market, is_sp500=True)
        today = date.today()
        ensure_date_ids(seeded_session, [today])
        did = date_to_id(today)

        report = DeepDiveRepository.insert_report(
            seeded_session, date_id=did, stock_id=stock.stock_id,
            ticker="AAPL", action_grade="HOLD", conviction=7,
            uncertainty="medium", report_json="{}",
        )

        forecasts = [
            ScenarioForecast(horizon="1M", scenario="BASE", probability=0.5, price_low=175, price_high=185, trigger_condition="test"),
            ScenarioForecast(horizon="1M", scenario="BULL", probability=0.3, price_low=185, price_high=200, trigger_condition=None),
        ]
        count = DeepDiveRepository.insert_forecasts_batch(
            seeded_session, report.report_id, did, stock.stock_id, "AAPL", forecasts,
        )
        assert count == 2

        loaded = DeepDiveRepository.get_forecasts_by_report(seeded_session, report.report_id)
        assert len(loaded) == 2
        assert loaded[0].horizon == "1M"
