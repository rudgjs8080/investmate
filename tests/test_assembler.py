"""리포트 조립기 순수 함수 테스트."""

from src.reports.assembler import (
    _build_macro,
    _calc_sma_alignment,
    _derive_risk_factors,
    _fmt_large_num,
)
from src.reports.report_models import (
    EarningsDetail,
    FundamentalDetail,
    SmartMoneyDetail,
    TechnicalDetail,
)
from unittest.mock import MagicMock


class TestBuildMacro:
    def test_none_input(self):
        result = _build_macro(None)
        assert result.mood == "미정"
        assert result.vix is None

    def test_bullish(self):
        row = MagicMock()
        row.vix = 15.0
        row.sp500_close = 5500.0
        row.sp500_sma20 = 5400.0
        row.us_10y_yield = 4.0
        row.us_13w_yield = 3.5
        row.dollar_index = 99.0
        row.market_score = 8
        result = _build_macro(row)
        assert result.mood == "강세"
        assert result.vix_status == "안정"
        assert result.sp500_trend == "상승"
        assert result.yield_spread == 0.5

    def test_bearish(self):
        row = MagicMock()
        row.vix = 35.0
        row.sp500_close = 5000.0
        row.sp500_sma20 = 5200.0
        row.us_10y_yield = 5.0
        row.us_13w_yield = 4.8
        row.dollar_index = 105.0
        row.market_score = 2
        result = _build_macro(row)
        assert result.mood == "약세"
        assert result.vix_status == "위험"
        assert result.sp500_trend == "하락"


class TestCalcSmaAlignment:
    def test_bullish_alignment(self):
        assert _calc_sma_alignment(110.0, 105.0, 100.0) == "정배열"

    def test_bearish_alignment(self):
        assert _calc_sma_alignment(90.0, 95.0, 100.0) == "역배열"

    def test_mixed(self):
        assert _calc_sma_alignment(100.0, 110.0, 95.0) == "혼조"

    def test_none_values(self):
        assert _calc_sma_alignment(None, 100.0, 95.0) == "혼조"


class TestDeriveRiskFactors:
    def test_no_risks(self):
        tech = TechnicalDetail(rsi=50.0, sma_alignment="정배열")
        fund = FundamentalDetail(per=15.0, debt_ratio=0.3)
        smart = SmartMoneyDetail()
        earnings = EarningsDetail()
        risks = _derive_risk_factors(tech, fund, smart, earnings)
        assert "특별한 리스크 요인 없음" in risks

    def test_high_rsi_risk(self):
        tech = TechnicalDetail(rsi=72.0)
        fund = FundamentalDetail()
        smart = SmartMoneyDetail()
        earnings = EarningsDetail()
        risks = _derive_risk_factors(tech, fund, smart, earnings)
        assert any("과매수" in r for r in risks)

    def test_high_per_risk(self):
        tech = TechnicalDetail()
        fund = FundamentalDetail(per=40.0)
        smart = SmartMoneyDetail()
        earnings = EarningsDetail()
        risks = _derive_risk_factors(tech, fund, smart, earnings)
        assert any("고평가" in r for r in risks)

    def test_high_debt_risk(self):
        tech = TechnicalDetail()
        fund = FundamentalDetail(debt_ratio=0.75)
        smart = SmartMoneyDetail()
        earnings = EarningsDetail()
        risks = _derive_risk_factors(tech, fund, smart, earnings)
        assert any("부채비율" in r for r in risks)

    def test_reverse_alignment_risk(self):
        tech = TechnicalDetail(sma_alignment="역배열")
        fund = FundamentalDetail()
        smart = SmartMoneyDetail()
        earnings = EarningsDetail()
        risks = _derive_risk_factors(tech, fund, smart, earnings)
        assert any("역배열" in r for r in risks)

    def test_short_interest_risk(self):
        tech = TechnicalDetail()
        fund = FundamentalDetail()
        smart = SmartMoneyDetail(short_pct=8.0)
        earnings = EarningsDetail()
        risks = _derive_risk_factors(tech, fund, smart, earnings)
        assert any("공매도" in r for r in risks)

    def test_target_below_risk(self):
        tech = TechnicalDetail()
        fund = FundamentalDetail()
        smart = SmartMoneyDetail(upside_pct=-10.0)
        earnings = EarningsDetail()
        risks = _derive_risk_factors(tech, fund, smart, earnings)
        assert any("목표가" in r for r in risks)

    def test_earnings_miss_risk(self):
        tech = TechnicalDetail()
        fund = FundamentalDetail()
        smart = SmartMoneyDetail()
        earnings = EarningsDetail(latest_period="2025Q4", beat_streak=0)
        risks = _derive_risk_factors(tech, fund, smart, earnings)
        assert any("실적 미달" in r for r in risks)


class TestFmtLargeNum:
    def test_billions(self):
        assert _fmt_large_num(5_000_000_000) == "5.0B"

    def test_millions(self):
        assert _fmt_large_num(50_000_000) == "50.0M"

    def test_thousands(self):
        assert _fmt_large_num(50_000) == "50K"

    def test_small(self):
        assert _fmt_large_num(500) == "500"
