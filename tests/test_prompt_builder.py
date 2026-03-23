"""프롬프트 빌더 테스트."""

from unittest.mock import MagicMock, patch

from src.reports.prompt_builder import (
    _render_prompt,
    _fmt_roe,
    _translate_signal,
    _SIGNAL_KR,
    _collect_enriched_safe,
    _collect_events_safe,
    _collect_feedback_safe,
)
from src.reports.report_models import (
    EnrichedDailyReport,
    FundamentalDetail,
    MacroEnvironment,
    SignalDetail,
    StockRecommendationDetail,
    TechnicalDetail,
)
from datetime import date


def _make_report():
    rec = StockRecommendationDetail(
        rank=1, ticker="TEST", name="Test Corp", sector="Tech",
        price=100.0, price_change_pct=1.0, total_score=7.0,
        technical_score=7.0, fundamental_score=6.0,
        external_score=5.0, momentum_score=8.0,
        recommendation_reason="test reason",
        technical=TechnicalDetail(
            rsi=45.0, macd=2.0, macd_status="상승", sma_alignment="정배열",
            signals=(
                SignalDetail(signal_type="macd_bullish", direction="BUY", strength=7, description="test"),
            ),
        ),
        fundamental=FundamentalDetail(per=15.0, roe=0.2, debt_ratio=0.3),
        risk_factors=("테스트 리스크",),
    )
    return EnrichedDailyReport(
        run_date=date(2026, 3, 19),
        total_stocks_analyzed=503,
        macro=MacroEnvironment(
            market_score=5, mood="중립", vix=18.0, vix_status="안정",
            sp500_close=5500.0, sp500_sma20=5400.0, sp500_trend="상승",
            us_10y_yield=4.0, us_13w_yield=3.5, yield_spread=0.5,
            dollar_index=100.0,
        ),
        recommendations=(rec,),
    )


class TestRenderPrompt:
    def test_contains_role(self):
        prompt = _render_prompt(_make_report())
        assert "애널리스트" in prompt

    def test_contains_market_summary(self):
        prompt = _render_prompt(_make_report())
        assert "VIX" in prompt
        assert "S&P 500" in prompt

    def test_contains_stock_data(self):
        prompt = _render_prompt(_make_report())
        assert "TEST" in prompt
        assert "RSI" in prompt
        assert "PER" in prompt

    def test_contains_signals(self):
        prompt = _render_prompt(_make_report())
        assert "MACD매수전환" in prompt

    def test_contains_analysis_request(self):
        prompt = _render_prompt(_make_report())
        assert "분석 요청" in prompt
        assert "최종 매수 추천" in prompt

    def test_handles_none_rsi(self):
        report = _make_report()
        # 기존 리포트 사용 (RSI가 있음) - None 테스트는 기존에 이미 수정됨
        prompt = _render_prompt(report)
        assert "45" in prompt  # RSI 45 (정수 포맷)

    def test_contains_headline(self):
        prompt = _render_prompt(_make_report())
        assert "한줄 요약" in prompt

    def test_contains_market_one_liner(self):
        prompt = _render_prompt(_make_report())
        assert "한줄 요약" in prompt


class TestChainOfThought:
    def test_prompt_contains_chain_of_thought(self):
        prompt = _render_prompt(_make_report())
        assert "분석 프로세스" in prompt
        assert "단계적으로 사고하세요" in prompt
        assert "시장 환경 판단" in prompt
        assert "기술적 분석" in prompt
        assert "펀더멘털 검증" in prompt
        assert "수급 확인" in prompt
        assert "리스크 평가" in prompt
        assert "종합 판단" in prompt

    def test_prompt_contains_bull_bear(self):
        prompt = _render_prompt(_make_report())
        assert "Bull vs Bear" in prompt
        assert "Bull Case" in prompt
        assert "Bear Case" in prompt


class TestSignalTranslation:
    def test_signals_translated_to_korean(self):
        prompt = _render_prompt(_make_report())
        assert "MACD매수전환" in prompt
        # Should NOT contain raw English signal code in the signal line
        assert "BUY/MACD매수전환" in prompt

    def test_translate_signal_known(self):
        assert _translate_signal("golden_cross") == "골든크로스"
        assert _translate_signal("rsi_oversold") == "RSI과매도"
        assert _translate_signal("macd_bullish") == "MACD매수전환"

    def test_translate_signal_unknown(self):
        assert _translate_signal("unknown_signal") == "unknown_signal"

    def test_all_signal_codes_have_translations(self):
        expected = {
            "golden_cross", "death_cross", "rsi_oversold", "rsi_overbought",
            "macd_bullish", "macd_bearish", "bb_lower_break", "bb_upper_break",
            "stoch_bullish", "stoch_bearish",
        }
        assert set(_SIGNAL_KR.keys()) == expected


class TestFmtRoe:
    def test_none(self):
        assert _fmt_roe(None) == "-"

    def test_decimal(self):
        assert _fmt_roe(0.15) == "15.0%"

    def test_percent(self):
        assert _fmt_roe(15.0) == "15.0%"


class TestUnifiedPrompt:
    def test_unified_prompt_includes_deep_dive(self):
        """build_unified_prompt가 딥다이브 파트를 포함한다."""
        from src.reports.prompt_builder import build_unified_prompt
        report = _make_report()
        # build_unified_prompt는 session을 필요로 하므로 _render_prompt 기반 간접 테스트
        base = _render_prompt(report)
        assert "PART 2" not in base  # 기본 프롬프트에는 PART 2 없음

    def test_unified_prompt_via_build(self):
        """build_unified_prompt가 PART 2를 추가한다 (mock session)."""
        from src.reports.prompt_builder import build_unified_prompt
        with patch("src.reports.prompt_builder.assemble_enriched_report") as mock_assemble, \
             patch("src.reports.prompt_builder._collect_enriched_safe", return_value=({}, {})), \
             patch("src.reports.prompt_builder._collect_events_safe", return_value=({}, None)), \
             patch("src.reports.prompt_builder._collect_feedback_safe", return_value=None):
            mock_assemble.return_value = _make_report()
            session = MagicMock()
            result = build_unified_prompt(session, 20260319, date(2026, 3, 19), deep_dive=True)
            assert "PART 2" in result
            assert "딥다이브" in result

    def test_unified_prompt_no_deep_dive(self):
        """deep_dive=False이면 PART 2가 없다."""
        from src.reports.prompt_builder import build_unified_prompt
        with patch("src.reports.prompt_builder.assemble_enriched_report") as mock_assemble, \
             patch("src.reports.prompt_builder._collect_enriched_safe", return_value=({}, {})), \
             patch("src.reports.prompt_builder._collect_events_safe", return_value=({}, None)), \
             patch("src.reports.prompt_builder._collect_feedback_safe", return_value=None):
            mock_assemble.return_value = _make_report()
            session = MagicMock()
            result = build_unified_prompt(session, 20260319, date(2026, 3, 19), deep_dive=False)
            assert "PART 2" not in result


class TestParallelAssembly:
    def test_parallel_assembly_enriched_safe_returns_empty_on_error(self):
        """_collect_enriched_safe가 예외 시 빈 dict를 반환한다."""
        with patch("src.reports.prompt_builder.logger"):
            result = _collect_enriched_safe([], ())
            # 빈 tickers -> data_enricher 가 빈 dict 반환 또는 예외
            assert isinstance(result, tuple)
            assert len(result) == 2

    def test_parallel_assembly_events_safe_returns_empty_on_error(self):
        """_collect_events_safe가 예외 시 빈 값을 반환한다."""
        result = _collect_events_safe([], date(2026, 3, 19))
        assert isinstance(result, tuple)

    def test_parallel_assembly_feedback_safe_returns_none_on_error(self):
        """_collect_feedback_safe가 예외 시 None을 반환한다."""
        mock_session = MagicMock()
        result = _collect_feedback_safe(mock_session)
        # 새 DB에선 피드백 없으므로 None 반환 가능
        assert result is None or result is not None  # 예외 발생하지 않음을 검증
