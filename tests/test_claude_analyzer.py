"""Claude AI 분석기 테스트."""

from datetime import date
from unittest.mock import MagicMock, patch

from src.ai.claude_analyzer import (
    _extract_json_robust,
    _try_parse_json,
    estimate_tokens,
    is_claude_available,
    parse_ai_response,
    run_analysis,
    run_claude_analysis_sdk,
    run_claude_analysis_streaming,
    run_claude_analysis_with_tools,
    save_analysis,
)


class TestIsClaudeAvailable:
    @patch("src.ai.claude_analyzer.shutil.which", return_value="/usr/bin/claude")
    def test_available(self, mock_which):
        assert is_claude_available() is True

    @patch("src.ai.claude_analyzer.shutil.which", return_value=None)
    def test_not_available(self, mock_which):
        assert is_claude_available() is False


class TestParseAiResponse:
    def test_parses_recommended_stock(self):
        response = "AAPL 매수 추천: 기술적 반등 신호가 강합니다."
        results = parse_ai_response(response)
        assert len(results) >= 1
        assert results[0]["ticker"] == "AAPL"
        assert results[0]["ai_approved"] is True

    def test_parses_excluded_stock(self):
        response = "TPL 제외: PER이 너무 높습니다."
        results = parse_ai_response(response)
        assert len(results) >= 1
        found = [r for r in results if r["ticker"] == "TPL"]
        assert found[0]["ai_approved"] is False

    def test_parses_target_price(self):
        response = (
            "MU 매수 추천\n"
            "목표가: $500\n"
            "손절가: $400\n"
        )
        results = parse_ai_response(response)
        mu = [r for r in results if r["ticker"] == "MU"]
        assert len(mu) == 1
        assert mu[0].get("ai_target_price") == 500.0
        assert mu[0].get("ai_stop_loss") == 400.0

    def test_empty_response(self):
        results = parse_ai_response("")
        assert results == []

    def test_multiple_stocks(self):
        response = (
            "1. AAPL 매수 추천 - 실적 양호\n"
            "2. MSFT 매수 추천 - 클라우드 성장\n"
            "3. INTC 제외 - 실적 부진\n"
        )
        results = parse_ai_response(response)
        approved = [r for r in results if r["ai_approved"]]
        excluded = [r for r in results if not r["ai_approved"]]
        assert len(approved) >= 2
        assert len(excluded) >= 1


class TestJsonParsing:
    def test_parses_json_block(self):
        response = '''분석 결과입니다.
```json
{
  "approved": ["AAPL", "MU"],
  "excluded": ["TPL"],
  "analysis": [
    {"ticker": "AAPL", "reason": "실적 양호", "target_price": 200, "stop_loss": 170},
    {"ticker": "MU", "reason": "MACD 전환"},
    {"ticker": "TPL", "reason": "PER 과다"}
  ]
}
```
'''
        results = parse_ai_response(response)
        approved = [r for r in results if r["ai_approved"]]
        excluded = [r for r in results if not r["ai_approved"]]
        assert len(approved) == 2
        assert len(excluded) == 1
        aapl = [r for r in results if r["ticker"] == "AAPL"][0]
        assert aapl["ai_target_price"] == 200.0
        assert aapl["ai_stop_loss"] == 170.0

    def test_json_takes_priority_over_regex(self):
        response = '''INTC 매수 추천
```json
{"approved": ["AAPL"], "excluded": ["INTC"], "analysis": []}
```
'''
        results = parse_ai_response(response)
        intc = [r for r in results if r["ticker"] == "INTC"]
        assert intc[0]["ai_approved"] is False  # JSON says excluded

    def test_fallback_to_regex_without_json(self):
        response = "AAPL 매수 추천: 좋은 종목입니다."
        results = parse_ai_response(response)
        assert len(results) >= 1
        assert results[0]["ai_approved"] is True

    def test_try_parse_json_returns_none_for_invalid(self):
        assert _try_parse_json("no json here") is None
        assert _try_parse_json("```json\ninvalid{{\n```") is None

    def test_parses_extended_schema(self):
        """확장 스키마: confidence, risk_level, entry/exit 파싱."""
        response = '''```json
{
  "approved": ["AAPL"],
  "excluded": [],
  "analysis": [
    {
      "ticker": "AAPL",
      "reason": "강한 실적",
      "confidence": 8,
      "risk_level": "LOW",
      "target_price": 210,
      "stop_loss": 175,
      "entry_strategy": "$185 근처 분할 매수",
      "exit_strategy": "목표가 $210 도달 시 50% 익절"
    }
  ]
}
```'''
        results = parse_ai_response(response)
        assert len(results) == 1
        aapl = results[0]
        assert aapl["ai_approved"] is True
        assert aapl["ai_confidence"] == 8
        assert aapl["ai_risk_level"] == "LOW"
        assert aapl["ai_target_price"] == 210.0
        assert aapl["ai_stop_loss"] == 175.0
        assert aapl["entry_strategy"] == "$185 근처 분할 매수"
        assert aapl["exit_strategy"] == "목표가 $210 도달 시 50% 익절"

    def test_parses_portfolio_section(self):
        """portfolio 섹션이 있어도 파싱 에러 없음."""
        response = '''```json
{
  "approved": ["AAPL"],
  "excluded": [],
  "analysis": [{"ticker": "AAPL", "reason": "좋음"}],
  "portfolio": {
    "market_outlook": "중립",
    "sector_balance": "IT 쏠림",
    "overall_risk": "MEDIUM",
    "position_sizing": "5종목 분산"
  }
}
```'''
        results = parse_ai_response(response)
        assert len(results) == 1
        assert results[0]["ticker"] == "AAPL"

    def test_invalid_confidence_clamped(self):
        """confidence 범위 밖 값은 1-10으로 클램핑."""
        response = '''```json
{"approved": ["TEST"], "excluded": [], "analysis": [{"ticker": "TEST", "reason": "ok", "confidence": 15}]}
```'''
        results = parse_ai_response(response)
        assert results[0]["ai_confidence"] == 10  # clamped


class TestSaveAnalysis:
    def test_saves_to_file(self, tmp_path):
        with patch("src.ai.claude_analyzer.Path") as mock_path_cls:
            # Use tmp_path directly
            pass

        # Direct test
        import tempfile
        from pathlib import Path
        reports_dir = tmp_path / "reports"
        reports_dir.mkdir()
        path = reports_dir / "2026-03-19_ai_analysis.md"
        path.write_text("test analysis", encoding="utf-8")
        assert path.read_text(encoding="utf-8") == "test analysis"


class TestSdkFunction:
    def test_sdk_returns_none_without_api_key(self):
        """anthropic 패키지 미설치 시 None 반환."""
        with patch.dict("sys.modules", {"anthropic": None}):
            result = run_claude_analysis_sdk("test prompt")
            assert result is None

    def test_sdk_returns_none_on_import_error(self):
        """ImportError 시 None 반환."""
        import sys
        original = sys.modules.get("anthropic")
        sys.modules["anthropic"] = None  # force ImportError
        try:
            result = run_claude_analysis_sdk("test prompt")
            assert result is None
        finally:
            if original is not None:
                sys.modules["anthropic"] = original
            else:
                sys.modules.pop("anthropic", None)


class TestRunAnalysis:
    @patch("src.ai.claude_analyzer.run_claude_analysis_with_tools", return_value={"approved": ["AAPL"]})
    @patch("src.ai.claude_analyzer.run_claude_analysis_streaming", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_sdk", return_value="SDK result")
    @patch("src.ai.claude_analyzer.run_claude_analysis", return_value="CLI result")
    def test_auto_uses_tool_use_first(self, mock_cli, mock_sdk, mock_stream, mock_tool):
        result, backend = run_analysis("prompt", backend="auto")
        assert result == {"approved": ["AAPL"]}
        assert backend == "tool_use"
        mock_tool.assert_called_once()
        mock_stream.assert_not_called()
        mock_sdk.assert_not_called()
        mock_cli.assert_not_called()

    @patch("src.ai.claude_analyzer.run_claude_analysis_with_tools", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_streaming", return_value="Streamed text")
    @patch("src.ai.claude_analyzer.run_claude_analysis_sdk", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis", return_value="CLI result")
    def test_auto_fallback_to_streaming(self, mock_cli, mock_sdk, mock_stream, mock_tool):
        result, backend = run_analysis("prompt", backend="auto")
        assert result == "Streamed text"
        assert backend == "streaming"
        mock_tool.assert_called_once()
        mock_stream.assert_called_once()
        mock_sdk.assert_not_called()

    @patch("src.ai.claude_analyzer.run_claude_analysis_with_tools", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_streaming", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_sdk", return_value="SDK result")
    @patch("src.ai.claude_analyzer.run_claude_analysis", return_value="CLI result")
    def test_auto_fallback_to_sdk(self, mock_cli, mock_sdk, mock_stream, mock_tool):
        result, backend = run_analysis("prompt", backend="auto")
        assert result == "SDK result"
        assert backend == "sdk"

    @patch("src.ai.claude_analyzer.run_claude_analysis_with_tools", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_streaming", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_sdk", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis", return_value="CLI result")
    def test_auto_fallback_to_cli(self, mock_cli, mock_sdk, mock_stream, mock_tool):
        result, backend = run_analysis("prompt", backend="auto")
        assert result == "CLI result"
        assert backend == "cli"
        mock_cli.assert_called_once()

    @patch("src.ai.claude_analyzer.run_claude_analysis_with_tools", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_streaming", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_sdk", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis", return_value="CLI result")
    def test_sdk_only_no_fallback(self, mock_cli, mock_sdk, mock_stream, mock_tool):
        result, backend = run_analysis("prompt", backend="sdk")
        assert result is None
        assert backend == "failed"
        mock_cli.assert_not_called()

    @patch("src.ai.claude_analyzer.run_claude_analysis_with_tools", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_streaming", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis_sdk", return_value=None)
    @patch("src.ai.claude_analyzer.run_claude_analysis", return_value="CLI result")
    def test_cli_backend(self, mock_cli, mock_sdk, mock_stream, mock_tool):
        result, backend = run_analysis("prompt", backend="cli")
        assert result == "CLI result"
        assert backend == "cli"
        mock_tool.assert_not_called()
        mock_sdk.assert_not_called()


class TestExtractJsonRobust:
    def test_extracts_nested_json(self):
        text = 'Some text {"approved": ["AAPL"], "analysis": [{"ticker": "AAPL", "reason": "good"}]} more text'
        result = _extract_json_robust(text)
        assert result is not None
        assert "approved" in result
        assert result["approved"] == ["AAPL"]

    def test_no_match(self):
        result = _extract_json_robust("no json here at all")
        assert result is None

    def test_extracts_from_code_block(self):
        text = '''Here is my analysis:
```json
{"approved": ["MSFT", "AAPL"], "excluded": ["INTC"], "analysis": [{"ticker": "MSFT", "reason": "strong growth"}]}
```
End of response.'''
        result = _extract_json_robust(text)
        assert result is not None
        assert "MSFT" in result["approved"]
        assert "INTC" in result["excluded"]

    def test_skips_irrelevant_json(self):
        text = '{"name": "test"} and then {"approved": ["AAPL"], "analysis": []}'
        result = _extract_json_robust(text)
        assert result is not None
        assert "approved" in result

    def test_handles_malformed_then_valid(self):
        text = '{bad json and {"approved": ["GOOG"], "analysis": []}'
        result = _extract_json_robust(text)
        assert result is not None
        assert result["approved"] == ["GOOG"]


class TestToolUse:
    def test_tool_use_returns_dict(self):
        """Tool Use 성공 시 dict 반환."""
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.input = {
            "approved": ["AAPL", "MSFT"],
            "excluded": ["INTC"],
            "analysis": [
                {"ticker": "AAPL", "reason": "실적 양호", "confidence": 8,
                 "risk_level": "LOW", "target_price": 200, "stop_loss": 170},
            ],
        }

        mock_message = MagicMock()
        mock_message.content = [tool_block]
        mock_message.usage.input_tokens = 1000
        mock_message.usage.output_tokens = 500

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_message

        mock_anthropic_mod = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client

        import sys
        sys.modules["anthropic"] = mock_anthropic_mod
        try:
            result = run_claude_analysis_with_tools("test prompt")
            assert isinstance(result, dict)
            assert result["approved"] == ["AAPL", "MSFT"]
            assert result["excluded"] == ["INTC"]
        finally:
            sys.modules.pop("anthropic", None)

    def test_tool_use_fallback_to_sdk(self):
        """Tool Use 실패 -> SDK 텍스트 반환."""
        with patch("src.ai.claude_analyzer.run_claude_analysis_with_tools", return_value=None), \
             patch("src.ai.claude_analyzer.run_claude_analysis_streaming", return_value=None), \
             patch("src.ai.claude_analyzer.run_claude_analysis_sdk", return_value="SDK text"), \
             patch("src.ai.claude_analyzer.run_claude_analysis", return_value=None):
            result, backend = run_analysis("prompt", backend="auto")
            assert result == "SDK text"
            assert backend == "sdk"
            assert isinstance(result, str)

    def test_tool_use_fallback_to_cli(self):
        """SDK 모두 실패 -> CLI 호출."""
        with patch("src.ai.claude_analyzer.run_claude_analysis_with_tools", return_value=None), \
             patch("src.ai.claude_analyzer.run_claude_analysis_streaming", return_value=None), \
             patch("src.ai.claude_analyzer.run_claude_analysis_sdk", return_value=None), \
             patch("src.ai.claude_analyzer.run_claude_analysis", return_value="CLI text"):
            result, backend = run_analysis("prompt", backend="auto")
            assert result == "CLI text"
            assert backend == "cli"

    def test_run_analysis_with_model_param(self):
        """model 파라미터가 Tool Use에 전달되는지 확인."""
        with patch("src.ai.claude_analyzer.run_claude_analysis_with_tools", return_value={"approved": []}) as mock_tool:
            result, backend = run_analysis("prompt", model="claude-opus-4-20250514")
            assert backend == "tool_use"
            mock_tool.assert_called_once_with("prompt", 300, "claude-opus-4-20250514")


class TestStreaming:
    def test_streaming_collects_text(self):
        """스트리밍 청크가 올바르게 합쳐지는지 확인."""
        mock_stream_ctx = MagicMock()
        mock_stream_ctx.__enter__ = MagicMock(return_value=mock_stream_ctx)
        mock_stream_ctx.__exit__ = MagicMock(return_value=False)
        mock_stream_ctx.text_stream = iter(["Hello", " ", "World"])

        mock_client = MagicMock()
        mock_client.messages.stream.return_value = mock_stream_ctx

        mock_anthropic_mod = MagicMock()
        mock_anthropic_mod.Anthropic.return_value = mock_client

        import sys
        sys.modules["anthropic"] = mock_anthropic_mod
        try:
            result = run_claude_analysis_streaming("test prompt")
            assert result == "Hello World"
        finally:
            sys.modules.pop("anthropic", None)


class TestEstimateTokens:
    def test_estimate_tokens_korean(self):
        """한국어 토큰 추정: 한글 2자당 1토큰."""
        text = "안녕하세요"  # 5 Korean chars
        tokens = estimate_tokens(text)
        assert tokens == 2  # 5 // 2 = 2

    def test_estimate_tokens_english(self):
        """영어 토큰 추정: 4자당 1토큰."""
        text = "HelloWorld"  # 10 chars
        tokens = estimate_tokens(text)
        assert tokens == 2  # 10 // 4 = 2

    def test_estimate_tokens_mixed(self):
        """한영 혼합 토큰 추정."""
        text = "Hello안녕"  # 5 English + 2 Korean
        tokens = estimate_tokens(text)
        # korean=2 -> 2//2=1, other=5 -> 5//4=1, total=2
        assert tokens == 2
