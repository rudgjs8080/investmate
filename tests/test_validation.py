"""데이터 검증 레이어 테스트."""

from __future__ import annotations

from datetime import date

import pytest

from src.data.schemas import DailyPriceData, MacroData
from src.data.validation import MacroValidator, PriceValidator, ValidationResult


class TestValidationResult:
    def test_empty_result(self):
        result = ValidationResult()
        assert not result.has_errors
        assert result.warning_count == 0
        assert result.error_count == 0

    def test_add_warning(self):
        result = ValidationResult()
        result.add_warning("field", "msg")
        assert result.warning_count == 1
        assert not result.has_errors

    def test_add_error(self):
        result = ValidationResult()
        result.add_error("field", "msg")
        assert result.error_count == 1
        assert result.has_errors


class TestPriceValidator:
    def _make_price(self, **kwargs) -> DailyPriceData:
        defaults = {
            "date": date(2026, 4, 1),
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "volume": 1000000,
            "adj_close": 102.0,
        }
        defaults.update(kwargs)
        return DailyPriceData(**defaults)

    def test_valid_prices(self):
        prices = [self._make_price()]
        result = PriceValidator().validate(prices)
        assert not result.has_errors
        assert result.warning_count == 0

    def test_detects_price_spike(self):
        prices = [
            self._make_price(close=100.0),
            self._make_price(close=200.0),  # 100% 상승
        ]
        result = PriceValidator().validate(prices, ticker="AAPL")
        assert result.warning_count >= 1
        assert any("변동" in i.message for i in result.issues)

    def test_detects_volume_spike(self):
        prices = [
            self._make_price(volume=1000),
            self._make_price(volume=200000),  # 200배
        ]
        result = PriceValidator().validate(prices, ticker="TEST")
        assert result.warning_count >= 1
        assert any("거래량" in i.message for i in result.issues)

    def test_empty_prices(self):
        result = PriceValidator().validate([])
        assert not result.has_errors

    def test_open_outside_range_warns(self):
        prices = [self._make_price(open=110.0, high=105.0, low=95.0)]
        result = PriceValidator().validate(prices)
        assert result.warning_count >= 1

    def test_high_less_than_low_errors(self):
        """Pydantic model_validator가 먼저 잡지만 검증기도 이중 체크."""
        with pytest.raises(ValueError, match="high.*low"):
            self._make_price(high=90.0, low=95.0)


class TestMacroValidator:
    def test_valid_macro(self):
        macro = MacroData(
            date=date(2026, 4, 1),
            vix=20.0,
            us_10y_yield=4.5,
            us_13w_yield=4.0,
            sp500_close=5000.0,
            yield_spread=0.5,
        )
        result = MacroValidator().validate(macro)
        assert not result.has_errors

    def test_vix_out_of_range(self):
        macro = MacroData(date=date(2026, 4, 1), vix=150.0)
        result = MacroValidator().validate(macro)
        assert result.has_errors
        assert any("vix" in i.field for i in result.issues)

    def test_missing_critical_fields(self):
        macro = MacroData(date=date(2026, 4, 1))
        result = MacroValidator().validate(macro)
        assert result.warning_count >= 2  # vix, sp500_close

    def test_yield_spread_inconsistency(self):
        macro = MacroData(
            date=date(2026, 4, 1),
            us_10y_yield=4.5,
            us_13w_yield=4.0,
            yield_spread=1.0,  # 실제로��� 0.5여야 함
        )
        result = MacroValidator().validate(macro)
        assert any("yield_spread" in i.field for i in result.issues)
