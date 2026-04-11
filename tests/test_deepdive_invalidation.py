"""Phase 11a: Invalidation 자동 모니터링 — 파서 + 평가 테스트."""

from __future__ import annotations

import pytest

from src.deepdive.invalidation_parser import (
    LayerSnapshot,
    ParseResult,
    ParsedCondition,
    evaluate_condition,
    parse_conditions,
)


class TestParseRSI:
    def test_rsi_lower_bound_하회(self):
        result = parse_conditions(["RSI 40 하회"])
        assert len(result.parsed) == 1
        c = result.parsed[0]
        assert c.indicator == "rsi"
        assert c.op == "lt"
        assert c.value == 40.0
        assert c.raw == "RSI 40 하회"
        assert result.unparsed == ()

    def test_rsi_미만(self):
        result = parse_conditions(["RSI 30 미만"])
        assert result.parsed[0].indicator == "rsi"
        assert result.parsed[0].op == "lt"

    def test_rsi_이하(self):
        result = parse_conditions(["RSI 35 이하"])
        assert result.parsed[0].op == "le"

    def test_rsi_upper_bound_상회(self):
        result = parse_conditions(["RSI 70 상회"])
        assert result.parsed[0].indicator == "rsi"
        assert result.parsed[0].op == "gt"
        assert result.parsed[0].value == 70.0

    def test_rsi_돌파(self):
        result = parse_conditions(["RSI 75 돌파"])
        assert result.parsed[0].op == "gt"

    def test_rsi_lowercase(self):
        result = parse_conditions(["rsi 40 하회"])
        assert result.parsed[0].indicator == "rsi"


class TestParseSMA:
    def test_sma_200_이탈(self):
        result = parse_conditions(["200일 이평선 이탈"])
        assert len(result.parsed) == 1
        c = result.parsed[0]
        assert c.indicator == "sma_200"
        assert c.op == "below_close"
        assert c.value is None

    def test_sma_50_하회(self):
        result = parse_conditions(["50일 이평선 하회"])
        assert result.parsed[0].indicator == "sma_50"
        assert result.parsed[0].op == "below_close"

    def test_sma_20_돌파(self):
        result = parse_conditions(["20일 이평선 돌파"])
        assert result.parsed[0].indicator == "sma_20"
        assert result.parsed[0].op == "above_close"

    def test_sma_without_line_word(self):
        """이평선 → 이평 축약형."""
        result = parse_conditions(["50일 이평 이탈"])
        assert result.parsed[0].indicator == "sma_50"


class TestParseMACD:
    def test_dead_cross(self):
        result = parse_conditions(["MACD 데드크로스"])
        assert result.parsed[0].indicator == "macd_signal"
        assert result.parsed[0].op == "cross_down"

    def test_golden_cross(self):
        result = parse_conditions(["MACD 골든크로스"])
        assert result.parsed[0].indicator == "macd_signal"
        assert result.parsed[0].op == "cross_up"


class Test52Week:
    def test_52w_low(self):
        result = parse_conditions(["52주 신저가"])
        assert result.parsed[0].indicator == "low_52w"
        assert result.parsed[0].op == "below_close"

    def test_52w_high(self):
        result = parse_conditions(["52주 신고가"])
        assert result.parsed[0].indicator == "high_52w"
        assert result.parsed[0].op == "above_close"


class TestFScore:
    def test_f_score_미만(self):
        result = parse_conditions(["F-Score 6 미만"])
        c = result.parsed[0]
        assert c.indicator == "f_score"
        assert c.op == "lt"
        assert c.value == 6.0

    def test_f_score_no_dash(self):
        result = parse_conditions(["F Score 5 이하"])
        assert result.parsed[0].indicator == "f_score"
        assert result.parsed[0].op == "le"


class TestSectorPerPremium:
    def test_sector_per_premium(self):
        result = parse_conditions(["섹터 PER 프리미엄 30% 초과"])
        c = result.parsed[0]
        assert c.indicator == "sector_per_premium"
        assert c.op == "gt"
        assert c.value == 30.0


class TestUnparsed:
    def test_unknown_goes_to_unparsed(self):
        result = parse_conditions(["임의의 촉매 발생"])
        assert result.parsed == ()
        assert result.unparsed == ("임의의 촉매 발생",)

    def test_mixed(self):
        result = parse_conditions([
            "RSI 40 하회",
            "감히 알 수 없는 조건",
            "200일 이평선 이탈",
        ])
        assert len(result.parsed) == 2
        assert len(result.unparsed) == 1

    def test_empty_strings_ignored(self):
        result = parse_conditions(["", "  ", "RSI 40 하회"])
        assert len(result.parsed) == 1
        assert len(result.unparsed) == 0


class TestEvaluateRSI:
    def _snap(self, **kwargs):
        defaults = dict(
            rsi=None, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=None,
            high_52w=None, low_52w=None,
            f_score=None, sector_per_premium_pct=None, close=100.0,
        )
        defaults.update(kwargs)
        return LayerSnapshot(**defaults)

    def test_rsi_lt_fires(self):
        c = ParsedCondition(raw="RSI 40 하회", indicator="rsi", op="lt", value=40.0)
        assert evaluate_condition(c, self._snap(rsi=38.0)) is True

    def test_rsi_lt_not_fire(self):
        c = ParsedCondition(raw="RSI 40 하회", indicator="rsi", op="lt", value=40.0)
        assert evaluate_condition(c, self._snap(rsi=42.0)) is False

    def test_rsi_gt_fires(self):
        c = ParsedCondition(raw="RSI 70 상회", indicator="rsi", op="gt", value=70.0)
        assert evaluate_condition(c, self._snap(rsi=72.0)) is True

    def test_rsi_none_returns_false(self):
        c = ParsedCondition(raw="RSI 40 하회", indicator="rsi", op="lt", value=40.0)
        assert evaluate_condition(c, self._snap(rsi=None)) is False

    def test_rsi_le_boundary(self):
        c = ParsedCondition(raw="RSI 40 이하", indicator="rsi", op="le", value=40.0)
        assert evaluate_condition(c, self._snap(rsi=40.0)) is True


class TestEvaluateSMA:
    def _snap(self, close, **kwargs):
        defaults = dict(
            rsi=None, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=None,
            high_52w=None, low_52w=None,
            f_score=None, sector_per_premium_pct=None, close=close,
        )
        defaults.update(kwargs)
        return LayerSnapshot(**defaults)

    def test_sma_below_close_fires(self):
        c = ParsedCondition(
            raw="200일 이평선 이탈", indicator="sma_200",
            op="below_close", value=None,
        )
        assert evaluate_condition(c, self._snap(close=150.0, sma_200=155.0)) is True

    def test_sma_below_close_not_fire(self):
        c = ParsedCondition(
            raw="200일 이평선 이탈", indicator="sma_200",
            op="below_close", value=None,
        )
        assert evaluate_condition(c, self._snap(close=160.0, sma_200=155.0)) is False

    def test_sma_above_close_fires(self):
        c = ParsedCondition(
            raw="20일 이평선 돌파", indicator="sma_20",
            op="above_close", value=None,
        )
        assert evaluate_condition(c, self._snap(close=105.0, sma_20=100.0)) is True

    def test_sma_missing_returns_false(self):
        c = ParsedCondition(
            raw="200일 이평선 이탈", indicator="sma_200",
            op="below_close", value=None,
        )
        assert evaluate_condition(c, self._snap(close=100.0, sma_200=None)) is False


class TestEvaluateMACD:
    def _snap(self, **kwargs):
        defaults = dict(
            rsi=None, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=None,
            high_52w=None, low_52w=None,
            f_score=None, sector_per_premium_pct=None, close=100.0,
        )
        defaults.update(kwargs)
        return LayerSnapshot(**defaults)

    def test_dead_cross_fires(self):
        c = ParsedCondition(
            raw="MACD 데드크로스", indicator="macd_signal",
            op="cross_down", value=None,
        )
        assert evaluate_condition(c, self._snap(macd_hist_prev=0.5, macd_hist=-0.2)) is True

    def test_dead_cross_not_fire(self):
        c = ParsedCondition(
            raw="MACD 데드크로스", indicator="macd_signal",
            op="cross_down", value=None,
        )
        assert evaluate_condition(c, self._snap(macd_hist_prev=0.3, macd_hist=0.5)) is False

    def test_golden_cross_fires(self):
        c = ParsedCondition(
            raw="MACD 골든크로스", indicator="macd_signal",
            op="cross_up", value=None,
        )
        assert evaluate_condition(c, self._snap(macd_hist_prev=-0.5, macd_hist=0.1)) is True


class TestEvaluate52Week:
    def _snap(self, close, **kwargs):
        defaults = dict(
            rsi=None, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=None,
            high_52w=None, low_52w=None,
            f_score=None, sector_per_premium_pct=None, close=close,
        )
        defaults.update(kwargs)
        return LayerSnapshot(**defaults)

    def test_low_52w_fires(self):
        c = ParsedCondition(
            raw="52주 신저가", indicator="low_52w",
            op="below_close", value=None,
        )
        assert evaluate_condition(c, self._snap(close=80.0, low_52w=85.0)) is True

    def test_high_52w_fires(self):
        c = ParsedCondition(
            raw="52주 신고가", indicator="high_52w",
            op="above_close", value=None,
        )
        assert evaluate_condition(c, self._snap(close=120.0, high_52w=115.0)) is True


class TestEvaluateFScore:
    def _snap(self, **kwargs):
        defaults = dict(
            rsi=None, macd_hist=None, macd_hist_prev=None,
            sma_20=None, sma_50=None, sma_200=None,
            high_52w=None, low_52w=None,
            f_score=None, sector_per_premium_pct=None, close=100.0,
        )
        defaults.update(kwargs)
        return LayerSnapshot(**defaults)

    def test_f_score_lt_fires(self):
        c = ParsedCondition(raw="F-Score 6 미만", indicator="f_score", op="lt", value=6.0)
        assert evaluate_condition(c, self._snap(f_score=5)) is True

    def test_f_score_lt_not_fire(self):
        c = ParsedCondition(raw="F-Score 6 미만", indicator="f_score", op="lt", value=6.0)
        assert evaluate_condition(c, self._snap(f_score=7)) is False


class TestParseResultImmutability:
    def test_parse_result_is_frozen(self):
        result = parse_conditions(["RSI 40 하회"])
        with pytest.raises((AttributeError, TypeError)):
            result.parsed = ()  # type: ignore[misc]

    def test_parsed_condition_is_frozen(self):
        result = parse_conditions(["RSI 40 하회"])
        with pytest.raises((AttributeError, TypeError)):
            result.parsed[0].raw = "x"  # type: ignore[misc]
