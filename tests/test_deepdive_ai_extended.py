"""Phase 4: AIResult 확장 필드 파싱 + CLI 재시도 테스트."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.deepdive.ai_prompts import (
    _dict_to_result,
    _has_parseable_json,
    _parse_ai_response,
    _regex_fallback,
    _safe_float,
    run_deepdive_cli,
)
from src.deepdive.schemas import AIResult


_FULL_SYNTH_JSON = json.dumps(
    {
        "action_grade": "ADD",
        "conviction": 8,
        "uncertainty": "low",
        "reasoning": (
            "RSI 62, F-Score 8/9, 섹터 PER 프리미엄 -3%로 "
            "밸류 컴포트에 진입. 마진 개선 4분기 연속."
        ),
        "what_missing": "중국 매출 비중 공개 부재",
        "consensus_strength": "high",
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
        "key_levels": {"support": 172.5, "resistance": 196.0, "stop_loss": 158.5},
        "next_review_trigger": "Q4 실적발표 또는 RSI 70 돌파",
        "evidence_refs": [
            "layer3.rsi=62",
            "layer1.f_score=8",
            "layer2.per_5y_percentile=35",
            "layer4.insider_net_90d=+1.2M",
        ],
        "invalidation_conditions": [
            "RSI 40 하회",
            "다음 분기 EPS 미스",
            "섹터 모멘텀 순위 하위 30% 진입",
        ],
    }
)


class TestKeyLevelsParsing:
    """key_levels 필드가 AIResult 스키마로 복구되는지."""

    def test_support_resistance_stop(self):
        data = json.loads(_FULL_SYNTH_JSON)
        result = _dict_to_result(data)
        assert result is not None
        assert result.support_price == 172.5
        assert result.resistance_price == 196.0
        assert result.stop_loss == 158.5

    def test_missing_key_levels(self):
        data = {"action_grade": "HOLD", "conviction": 5, "uncertainty": "medium"}
        result = _dict_to_result(data)
        assert result is not None
        assert result.support_price is None
        assert result.resistance_price is None
        assert result.stop_loss is None

    def test_string_prices(self):
        """숫자가 문자열로 와도 float 변환."""
        data = {
            "action_grade": "HOLD",
            "conviction": 5,
            "uncertainty": "medium",
            "key_levels": {"support": "170.5", "resistance": "190", "stop_loss": "155.2"},
        }
        result = _dict_to_result(data)
        assert result.support_price == 170.5
        assert result.resistance_price == 190.0
        assert result.stop_loss == 155.2


class TestEvidenceRefs:
    """evidence_refs는 tuple로 저장되어 frozen 호환."""

    def test_evidence_captured(self):
        data = json.loads(_FULL_SYNTH_JSON)
        result = _dict_to_result(data)
        assert len(result.evidence_refs) == 4
        assert "layer3.rsi=62" in result.evidence_refs
        # tuple이어야 frozen BaseModel 호환
        assert isinstance(result.evidence_refs, tuple)

    def test_invalidation_captured(self):
        data = json.loads(_FULL_SYNTH_JSON)
        result = _dict_to_result(data)
        assert len(result.invalidation_conditions) == 3
        assert "RSI 40 하회" in result.invalidation_conditions

    def test_evidence_defaults_empty(self):
        data = {"action_grade": "HOLD", "conviction": 5, "uncertainty": "medium"}
        result = _dict_to_result(data)
        assert result.evidence_refs == ()
        assert result.invalidation_conditions == ()

    def test_evidence_non_list_ignored(self):
        """dict이나 str로 오면 빈 tuple로 폴백."""
        data = {
            "action_grade": "HOLD",
            "conviction": 5,
            "uncertainty": "medium",
            "evidence_refs": "not-a-list",
            "invalidation_conditions": {"oops": "dict"},
        }
        result = _dict_to_result(data)
        assert result.evidence_refs == ()
        assert result.invalidation_conditions == ()

    def test_evidence_truncated(self):
        """길이 200자 제한."""
        long = "x" * 500
        data = {
            "action_grade": "HOLD",
            "conviction": 5,
            "uncertainty": "medium",
            "evidence_refs": [long],
        }
        result = _dict_to_result(data)
        assert len(result.evidence_refs[0]) == 200


class TestReasoningLengthRelaxed:
    """reasoning이 500 → 2000자로 완화되었는지."""

    def test_long_reasoning_preserved(self):
        long_text = "x" * 1500
        data = {
            "action_grade": "HOLD",
            "conviction": 5,
            "uncertainty": "medium",
            "reasoning": long_text,
        }
        result = _dict_to_result(data)
        assert len(result.reasoning) == 1500

    def test_very_long_reasoning_truncated_at_2000(self):
        long_text = "x" * 3000
        data = {
            "action_grade": "HOLD",
            "conviction": 5,
            "uncertainty": "medium",
            "reasoning": long_text,
        }
        result = _dict_to_result(data)
        assert len(result.reasoning) == 2000


class TestSafeFloat:
    def test_numeric(self):
        assert _safe_float(123.45) == 123.45
        assert _safe_float(100) == 100.0

    def test_string(self):
        assert _safe_float("50.5") == 50.5

    def test_invalid(self):
        assert _safe_float(None) is None
        assert _safe_float("abc") is None
        assert _safe_float(-5) is None  # non-positive
        assert _safe_float(0) is None


class TestParseableJsonDetection:
    def test_valid_json_with_action_grade(self):
        raw = '{"action_grade": "HOLD", "conviction": 5}'
        assert _has_parseable_json(raw) is True

    def test_valid_json_with_action_only(self):
        """Bull/Bear agent uses "action" (no "_grade")."""
        raw = '{"action": "ADD", "conviction": 8}'
        assert _has_parseable_json(raw) is True

    def test_prose_no_json(self):
        assert _has_parseable_json("I think this is a good stock.") is False

    def test_json_without_action(self):
        raw = '{"hello": "world"}'
        assert _has_parseable_json(raw) is False

    def test_json_in_markdown(self):
        raw = '```json\n{"action_grade": "HOLD", "conviction": 5}\n```'
        assert _has_parseable_json(raw) is True


class TestCLIRetryOnUnparseable:
    """JSON 파싱 불가 응답 시 1회 재시도."""

    @patch("src.deepdive.ai_prompts.subprocess.run")
    @patch("src.deepdive.ai_prompts.shutil.which")
    def test_retry_on_unparseable_then_success(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/claude"
        ok = MagicMock(returncode=0, stdout='{"action_grade": "HOLD", "conviction": 6}', stderr="")
        bad = MagicMock(returncode=0, stdout="nope, no JSON here", stderr="")
        mock_run.side_effect = [bad, ok]

        out = run_deepdive_cli("prompt", "sys", timeout=5, model="opus", max_attempts=2)
        assert out is not None
        assert '"action_grade"' in out
        assert mock_run.call_count == 2

    @patch("src.deepdive.ai_prompts.subprocess.run")
    @patch("src.deepdive.ai_prompts.shutil.which")
    def test_no_retry_when_first_attempt_parseable(self, mock_which, mock_run):
        mock_which.return_value = "/usr/bin/claude"
        ok = MagicMock(returncode=0, stdout='{"action_grade": "ADD", "conviction": 9}', stderr="")
        mock_run.return_value = ok

        out = run_deepdive_cli("prompt", "sys", timeout=5, model="opus", max_attempts=2)
        assert out is not None
        assert mock_run.call_count == 1

    @patch("src.deepdive.ai_prompts.subprocess.run")
    @patch("src.deepdive.ai_prompts.shutil.which")
    def test_both_fail_returns_last_raw(self, mock_which, mock_run):
        """둘 다 unparseable이어도 마지막 raw는 regex fallback을 위해 반환."""
        mock_which.return_value = "/usr/bin/claude"
        bad = MagicMock(returncode=0, stdout="garbage 1", stderr="")
        bad2 = MagicMock(returncode=0, stdout="garbage 2", stderr="")
        mock_run.side_effect = [bad, bad2]

        out = run_deepdive_cli("prompt", "sys", timeout=5, model="opus", max_attempts=2)
        assert out == "garbage 2"
        assert mock_run.call_count == 2


class TestRegexFallbackExtended:
    """regex fallback이 key_levels도 복구하는지."""

    def test_regex_extracts_stop_loss(self):
        raw = (
            "The model says: "
            '"action_grade": "TRIM", "conviction": 6, '
            '"support": 150.5, "resistance": 175.0, "stop_loss": 142.0'
        )
        result = _regex_fallback(raw)
        assert result is not None
        assert result.action_grade == "TRIM"
        assert result.conviction == 6
        assert result.support_price == 150.5
        assert result.resistance_price == 175.0
        assert result.stop_loss == 142.0

    def test_regex_no_action_returns_none(self):
        raw = "no action_grade here"
        assert _regex_fallback(raw) is None


class TestParseFullResponse:
    """end-to-end: _parse_ai_response가 모든 신규 필드 관통."""

    def test_full_synth_response(self):
        result = _parse_ai_response(_FULL_SYNTH_JSON)
        assert result is not None
        assert result.action_grade == "ADD"
        assert result.conviction == 8
        assert result.support_price == 172.5
        assert result.resistance_price == 196.0
        assert result.stop_loss == 158.5
        assert result.next_review_trigger is not None
        assert len(result.evidence_refs) == 4
        assert len(result.invalidation_conditions) == 3


class TestAIResultBackwardCompat:
    """기존 테스트에서 AIResult를 5개 필드로 생성하던 것이 여전히 동작."""

    def test_minimal_construction(self):
        r = AIResult(
            action_grade="HOLD",
            conviction=5,
            uncertainty="medium",
            reasoning="test",
            what_missing=None,
        )
        assert r.support_price is None
        assert r.evidence_refs == ()

    def test_frozen(self):
        r = AIResult(
            action_grade="HOLD", conviction=5, uncertainty="medium",
            reasoning="test", what_missing=None,
        )
        with pytest.raises(Exception):
            r.conviction = 9  # pydantic frozen blocks mutation
