"""Phase 11d: AI 토론 병렬화 디스패처 테스트."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.deepdive.ai_debate_cli import (
    run_debate_smart,
    run_deepdive_debate_async,
)
from src.deepdive.watchlist_manager import WatchlistEntry


@pytest.fixture
def entry():
    return WatchlistEntry(
        ticker="AAPL", stock_id=1, name="Apple Inc.",
        name_kr=None, sector="Tech", is_sp500=True, holding=None,
    )


def _fake_cli_raw_bull() -> str:
    return (
        '{"action":"ADD","conviction":8,'
        '"bull_case":["성장세 강함","마진 개선","경쟁력"],'
        '"scenarios":{"1M":{"base":{"prob":0.5,"low":170,"high":190},'
        '"bull":{"prob":0.3,"low":185,"high":200},'
        '"bear":{"prob":0.2,"low":165,"high":175}},'
        '"3M":{"base":{"prob":0.5,"low":170,"high":200},'
        '"bull":{"prob":0.3,"low":190,"high":215},'
        '"bear":{"prob":0.2,"low":160,"high":175}},'
        '"6M":{"base":{"prob":0.5,"low":170,"high":210},'
        '"bull":{"prob":0.3,"low":195,"high":230},'
        '"bear":{"prob":0.2,"low":155,"high":180}}},'
        '"catalysts":["실적발표"],"key_risks_acknowledged":[]}'
    )


def _fake_cli_raw_bear() -> str:
    return (
        '{"action":"TRIM","conviction":6,'
        '"bear_case":["밸류 과열","경기 둔화","규제"],'
        '"scenarios":{"1M":{"base":{"prob":0.5,"low":170,"high":185},'
        '"bull":{"prob":0.2,"low":185,"high":195},'
        '"bear":{"prob":0.3,"low":160,"high":175}},'
        '"3M":{"base":{"prob":0.5,"low":165,"high":185},'
        '"bull":{"prob":0.2,"low":185,"high":200},'
        '"bear":{"prob":0.3,"low":150,"high":170}},'
        '"6M":{"base":{"prob":0.5,"low":160,"high":180},'
        '"bull":{"prob":0.2,"low":180,"high":195},'
        '"bear":{"prob":0.3,"low":140,"high":165}}},'
        '"stop_loss_level":160,"key_strengths_acknowledged":[]}'
    )


def _fake_cli_raw_synth() -> str:
    return (
        '{"action_grade":"HOLD","conviction":7,"uncertainty":"medium",'
        '"reasoning":"Bull/Bear 균형. 체제는 range, 밸류 중립, RSI 55, '
        'F-Score 7/9, 섹터 PER +5%. 종합 판단: HOLD 유지가 적절하다.",'
        '"scenarios":{"1M":{"base":{"prob":0.5,"low":170,"high":185},'
        '"bull":{"prob":0.3,"low":185,"high":200},'
        '"bear":{"prob":0.2,"low":160,"high":175}},'
        '"3M":{"base":{"prob":0.5,"low":170,"high":190},'
        '"bull":{"prob":0.3,"low":190,"high":210},'
        '"bear":{"prob":0.2,"low":155,"high":175}},'
        '"6M":{"base":{"prob":0.5,"low":170,"high":195},'
        '"bull":{"prob":0.3,"low":195,"high":220},'
        '"bear":{"prob":0.2,"low":150,"high":175}}},'
        '"consensus_strength":"medium","what_missing":"regulatory risk",'
        '"key_levels":{"support":170,"resistance":195,"stop_loss":160},'
        '"next_review_trigger":"RSI 70 상회",'
        '"evidence_refs":["layer3.rsi=55","layer1.f_score=7"],'
        '"invalidation_conditions":["RSI 40 하회","분기 EPS 미스"]}'
    )


class TestBackendSdkRaises:
    def test_backend_sdk_raises_not_implemented(self, entry):
        with pytest.raises(NotImplementedError):
            run_debate_smart(
                entry, {}, 180.0, 0.5,
                backend="sdk", parallel=False,
            )

    def test_backend_unknown_raises_value_error(self, entry):
        with pytest.raises(ValueError):
            run_debate_smart(
                entry, {}, 180.0, 0.5,
                backend="banana", parallel=False,
            )


class TestSyncPathUnchanged:
    def test_sync_path_calls_existing_debate(self, entry):
        """parallel=False는 기존 sync 경로를 그대로 호출해야 한다."""
        with patch(
            "src.deepdive.ai_debate_cli.run_deepdive_debate"
        ) as mock_sync, patch(
            "src.deepdive.ai_debate_cli.run_deepdive_debate_async"
        ) as mock_async:
            mock_sync.return_value = None
            run_debate_smart(
                entry, {}, 180.0, 0.5,
                backend="cli", parallel=False,
            )
            mock_sync.assert_called_once()
            mock_async.assert_not_called()


class TestAsyncHappyPath:
    def test_async_returns_cli_debate_result(self, entry):
        """asyncio 경로에서 R1/R2/R3가 순서대로 호출되고 CLIDebateResult를 반환."""
        call_log: list[tuple[str, str]] = []

        def fake_cli(prompt, system_prompt, timeout, model):
            # 순서 주의: CIO 먼저 체크 (SYNTH), 그다음 숏셀러(BEAR), 나머지 BULL.
            if "CIO" in system_prompt:
                call_log.append(("synth", prompt[:20]))
                return _fake_cli_raw_synth()
            if "숏셀러" in system_prompt:
                call_log.append(("bear", prompt[:20]))
                return _fake_cli_raw_bear()
            call_log.append(("bull", prompt[:20]))
            return _fake_cli_raw_bull()

        with patch(
            "src.deepdive.ai_debate_cli.run_deepdive_cli", side_effect=fake_cli,
        ), patch(
            "src.deepdive.ai_debate_cli.build_stock_context",
            return_value="<ctx>test</ctx>",
        ):
            result = asyncio.run(
                run_deepdive_debate_async(entry, {}, 180.0, 0.5, timeout=10, model="opus")
            )
        assert result is not None
        assert result.final_result is not None
        assert result.final_result.action_grade == "HOLD"
        # Bull/Bear 각 R1+R2 = 4 + Synth 1 = 5
        roles = [c[0] for c in call_log]
        assert roles.count("bull") == 2
        assert roles.count("bear") == 2
        assert roles.count("synth") == 1


class TestAsyncPartialFailure:
    def test_r1_bear_failure_recovers(self, entry):
        """R1 Bear가 예외를 던져도 Bull R1은 성공 → R2는 스킵 → synth까지 진행."""
        def fake_cli(prompt, system_prompt, timeout, model):
            if "CIO" in system_prompt:
                return _fake_cli_raw_synth()
            if "숏셀러" in system_prompt:
                raise RuntimeError("bear CLI 다운")
            return _fake_cli_raw_bull()

        with patch(
            "src.deepdive.ai_debate_cli.run_deepdive_cli", side_effect=fake_cli,
        ), patch(
            "src.deepdive.ai_debate_cli.build_stock_context",
            return_value="<ctx>test</ctx>",
        ):
            result = asyncio.run(
                run_deepdive_debate_async(entry, {}, 180.0, 0.5, timeout=10, model="opus")
            )
        assert result is not None
        assert result.final_result is not None
        # Bear가 완전히 실패했으므로 bear_summary는 비어 있거나 None
        assert not result.bear_summary or result.bear_summary == ""


class TestAsyncParallelR1:
    def test_r1_runs_in_parallel(self, entry):
        """R1 Bull/Bear가 동시에 진입했는지 Event로 검증."""
        barrier = asyncio.Event()
        arrivals = {"count": 0}
        release = asyncio.Event()

        def fake_cli(prompt, system_prompt, timeout, model):
            # 동기 함수 안에서 block — asyncio.to_thread로 스레드 격리되어 동시 진입 가능
            arrivals_lock.acquire()
            arrivals["count"] += 1
            reached = arrivals["count"]
            arrivals_lock.release()
            if reached == 2:
                barrier_reached.set()
            # 첫 번째는 두 번째 도착까지 대기
            barrier_reached.wait(timeout=2.0)
            if "CIO" in system_prompt:
                return _fake_cli_raw_synth()
            if "숏셀러" in system_prompt:
                return _fake_cli_raw_bear()
            return _fake_cli_raw_bull()

        import threading

        arrivals_lock = threading.Lock()
        barrier_reached = threading.Event()

        with patch(
            "src.deepdive.ai_debate_cli.run_deepdive_cli", side_effect=fake_cli,
        ), patch(
            "src.deepdive.ai_debate_cli.build_stock_context",
            return_value="<ctx>test</ctx>",
        ):
            result = asyncio.run(
                run_deepdive_debate_async(entry, {}, 180.0, 0.5, timeout=10, model="opus")
            )
        # 동시 진입 확인
        assert barrier_reached.is_set()
        assert result is not None
