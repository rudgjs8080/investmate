"""기본적 분석 테스트."""

from __future__ import annotations

from datetime import date

from src.analysis.fundamental import FundamentalScore, analyze_fundamentals
from src.data.schemas import FinancialRecord, ValuationRecord


class TestAnalyzeFundamentals:
    """analyze_fundamentals 테스트."""

    def test_empty_financials(self):
        result = analyze_fundamentals([])
        assert result.composite_score == 5.0

    def test_good_fundamentals(self):
        records = [
            FinancialRecord(
                period="2024Q2", revenue=100000.0,
                total_assets=500000.0, total_liabilities=100000.0,
            ),
            FinancialRecord(period="2024Q1", revenue=80000.0),
        ]
        val = ValuationRecord(date=date(2024, 6, 30), per=12.0, pbr=1.5, roe=0.20)
        result = analyze_fundamentals(records, val)
        assert result.per_score >= 7.0
        assert result.roe_score >= 7.0
        assert result.composite_score > 5.0
        assert result.summary == "우수"

    def test_poor_fundamentals(self):
        records = [
            FinancialRecord(
                period="2024Q2", revenue=50000.0,
                total_assets=100000.0, total_liabilities=80000.0,
            ),
            FinancialRecord(period="2024Q1", revenue=60000.0),
        ]
        val = ValuationRecord(date=date(2024, 6, 30), per=45.0, pbr=15.0, roe=-0.05)
        result = analyze_fundamentals(records, val)
        assert result.per_score <= 4.0
        assert result.composite_score < 5.0
        assert result.summary == "주의"

    def test_none_valuation(self):
        records = [FinancialRecord(period="2024Q2")]
        result = analyze_fundamentals(records)
        assert result.per_score == 3.5  # 데이터 누락 감점
        assert result.pbr_score == 3.5  # 데이터 누락 감점

    def test_negative_per(self):
        records = [FinancialRecord(period="2024Q2")]
        val = ValuationRecord(date=date(2024, 6, 30), per=-5.0)
        result = analyze_fundamentals(records, val)
        assert result.per_score == 2.0

    def test_high_roe(self):
        records = [FinancialRecord(period="2024Q2")]
        val = ValuationRecord(date=date(2024, 6, 30), roe=0.30)
        result = analyze_fundamentals(records, val)
        assert result.roe_score >= 8.0

    def test_low_debt_ratio(self):
        records = [
            FinancialRecord(
                period="2024Q2",
                total_assets=1000000.0, total_liabilities=100000.0,
            ),
        ]
        result = analyze_fundamentals(records)
        assert result.debt_score >= 8.0

    def test_growth_score_increasing(self):
        records = [
            FinancialRecord(period="2024Q2", revenue=120000.0),
            FinancialRecord(period="2024Q1", revenue=100000.0),
        ]
        result = analyze_fundamentals(records)
        assert result.growth_score >= 7.0

    def test_growth_score_decreasing(self):
        records = [
            FinancialRecord(period="2024Q2", revenue=80000.0),
            FinancialRecord(period="2024Q1", revenue=100000.0),
        ]
        result = analyze_fundamentals(records)
        assert result.growth_score <= 4.0

    def test_frozen_dataclass(self):
        score = FundamentalScore(
            per_score=7.0, pbr_score=5.0, roe_score=8.0,
            debt_score=6.0, growth_score=7.0, composite_score=6.8,
        )
        try:
            score.per_score = 10.0  # type: ignore
            assert False, "should raise"
        except AttributeError:
            pass


class TestFundamentalScoreSummary:
    def test_excellent(self):
        score = FundamentalScore(7, 7, 7, 7, 7, 7.5)
        assert score.summary == "우수"

    def test_average(self):
        score = FundamentalScore(5, 5, 5, 5, 5, 5.5)
        assert score.summary == "보통"

    def test_caution(self):
        score = FundamentalScore(3, 3, 3, 3, 3, 3.0)
        assert score.summary == "주의"


class TestFundamentalEdgeCases:
    def test_extreme_per(self):
        from src.analysis.fundamental import _score_per
        assert _score_per(None) == 3.5  # 데이터 누락 감점
        assert _score_per(5.0) >= 8.0  # Very low PER
        assert _score_per(50.0) <= 4.0  # Very high PER

    def test_extreme_roe(self):
        from src.analysis.fundamental import _score_roe
        assert _score_roe(None) == 3.5  # 데이터 누락 감점
        assert _score_roe(0.3) >= 8.0  # 30% ROE
        assert _score_roe(0.01) <= 4.0  # 1% ROE

    def test_extreme_debt(self):
        from src.analysis.fundamental import _score_debt
        assert _score_debt(None, None) == 3.5  # 데이터 누락 감점
        assert _score_debt(1000, 100) >= 8.0  # 10% debt
        assert _score_debt(1000, 900) <= 3.0  # 90% debt

    def test_growth_with_data(self):
        from src.analysis.fundamental import _score_growth
        revenues = [120000, 110000, 100000, 90000]
        score = _score_growth(revenues)
        assert score >= 6.0  # Growing revenue

    def test_growth_no_data(self):
        from src.analysis.fundamental import _score_growth
        assert _score_growth([]) == 5.0
        assert _score_growth([None, None]) == 5.0

    def test_per_ranges(self):
        from src.analysis.fundamental import _score_per
        # PER ranges: <10 → 9, 10-15 → 8, 15-20 → 7, 20-25 → 6, 25-30 → 5, 30-40 → 4, >40 → 3
        assert _score_per(8.0) >= 8.0
        assert _score_per(12.0) >= 7.0
        assert _score_per(22.0) >= 5.0
        assert _score_per(35.0) <= 5.0
        assert _score_per(45.0) <= 4.0

    def test_pbr_ranges(self):
        from src.analysis.fundamental import _score_pbr
        assert _score_pbr(None) == 3.5  # 데이터 누락 감점
        assert _score_pbr(0.8) >= 8.0   # PBR < 1
        assert _score_pbr(1.5) >= 7.0   # PBR 1-2
        assert _score_pbr(3.0) >= 5.0   # PBR 2-5
        assert _score_pbr(8.0) <= 5.0   # PBR > 5

    def test_roe_ranges(self):
        from src.analysis.fundamental import _score_roe
        assert _score_roe(0.25) >= 8.0  # 25% ROE
        assert _score_roe(0.15) >= 6.0  # 15% ROE
        assert _score_roe(0.05) <= 5.0  # 5% ROE

    def test_debt_ranges(self):
        from src.analysis.fundamental import _score_debt
        assert _score_debt(100, 20) >= 8.0   # 20% debt
        assert _score_debt(100, 40) >= 6.0   # 40%
        assert _score_debt(100, 70) <= 4.0   # 70%

    def test_growth_declining(self):
        from src.analysis.fundamental import _score_growth
        revenues = [80000, 90000, 100000, 110000]  # declining when read latest-first
        score = _score_growth(revenues)
        assert 1.0 <= score <= 10.0

    def test_dividend_yield_ranges(self):
        from src.analysis.fundamental import _score_dividend_yield
        assert _score_dividend_yield(None) == 5.0  # 중립 (성장주도 많으므로)
        assert _score_dividend_yield(0.06) == 8.0   # 6% 고배당
        assert _score_dividend_yield(0.035) == 7.0   # 3.5%
        assert _score_dividend_yield(0.02) == 6.0    # 2%
        assert _score_dividend_yield(0.005) == 5.0   # 0.5%
        assert _score_dividend_yield(0.0) == 4.0     # 무배당

    def test_none_penalty_consistency(self):
        """모든 스코어 함수가 None에 대해 3.5를 반환한다."""
        from src.analysis.fundamental import _score_per, _score_pbr, _score_roe, _score_debt
        assert _score_per(None) == 3.5
        assert _score_pbr(None) == 3.5
        assert _score_roe(None) == 3.5
        assert _score_debt(None, None) == 3.5


class TestScoreEvEbitda:
    """_score_ev_ebitda 테스트."""

    def test_none_returns_neutral(self):
        from src.analysis.fundamental import _score_ev_ebitda
        assert _score_ev_ebitda(None) == 5.0

    def test_negative_ebitda(self):
        from src.analysis.fundamental import _score_ev_ebitda
        assert _score_ev_ebitda(-5.0) == 3.0

    def test_very_cheap(self):
        from src.analysis.fundamental import _score_ev_ebitda
        assert _score_ev_ebitda(6.0) == 9.0

    def test_cheap(self):
        from src.analysis.fundamental import _score_ev_ebitda
        assert _score_ev_ebitda(10.0) == 8.0

    def test_fair(self):
        from src.analysis.fundamental import _score_ev_ebitda
        assert _score_ev_ebitda(14.0) == 7.0

    def test_normal(self):
        from src.analysis.fundamental import _score_ev_ebitda
        assert _score_ev_ebitda(18.0) == 5.0

    def test_expensive(self):
        from src.analysis.fundamental import _score_ev_ebitda
        assert _score_ev_ebitda(25.0) == 4.0

    def test_very_expensive(self):
        from src.analysis.fundamental import _score_ev_ebitda
        assert _score_ev_ebitda(35.0) == 3.0

    def test_ev_ebitda_in_composite(self):
        """EV/EBITDA가 composite에 반영된다."""
        records = [FinancialRecord(period="2024Q2")]
        val_cheap = ValuationRecord(date=date(2024, 6, 30), ev_ebitda=6.0)
        val_expensive = ValuationRecord(date=date(2024, 6, 30), ev_ebitda=35.0)
        score_cheap = analyze_fundamentals(records, val_cheap)
        score_expensive = analyze_fundamentals(records, val_expensive)
        assert score_cheap.composite_score > score_expensive.composite_score


class TestScoreFcf:
    """_score_fcf 테스트."""

    def test_none_returns_neutral(self):
        from src.analysis.fundamental import _score_fcf
        assert _score_fcf(None, None, None) == 5.0
        assert _score_fcf(100.0, 50.0, None) == 5.0
        assert _score_fcf(None, 50.0, 1000.0) == 5.0

    def test_zero_total_assets(self):
        from src.analysis.fundamental import _score_fcf
        assert _score_fcf(100.0, 50.0, 0) == 5.0

    def test_high_quality_fcf(self):
        """양수 CF > NI, 높은 FCF 마진 → 고득점."""
        from src.analysis.fundamental import _score_fcf
        # operating_cashflow=150, net_income=100, total_assets=1000
        # +1.5 (positive CF) +1.0 (CF>NI) +1.5 (margin 15% > 10%)
        score = _score_fcf(150.0, 100.0, 1000.0)
        assert score == 9.0

    def test_negative_cashflow(self):
        """음수 CF → 감점."""
        from src.analysis.fundamental import _score_fcf
        # operating_cashflow=-50, net_income=10, total_assets=1000
        # margin = -0.05 < 0 → -1.5
        score = _score_fcf(-50.0, 10.0, 1000.0)
        assert score == 3.5  # 5.0 - 1.5


class TestScorePeg:
    """_score_peg 테스트."""

    def test_none_per_returns_neutral(self):
        from src.analysis.fundamental import _score_peg
        assert _score_peg(None, 20.0) == 5.0

    def test_negative_growth_returns_neutral(self):
        from src.analysis.fundamental import _score_peg
        assert _score_peg(15.0, -5.0) == 5.0

    def test_zero_growth_returns_neutral(self):
        from src.analysis.fundamental import _score_peg
        assert _score_peg(15.0, 0.0) == 5.0

    def test_negative_per_returns_neutral(self):
        from src.analysis.fundamental import _score_peg
        assert _score_peg(-5.0, 20.0) == 5.0

    def test_peg_below_0_5(self):
        """PEG < 0.5 → 9.0."""
        from src.analysis.fundamental import _score_peg
        assert _score_peg(5.0, 20.0) == 9.0  # PEG = 0.25

    def test_peg_below_1(self):
        """PEG < 1.0 → 8.0."""
        from src.analysis.fundamental import _score_peg
        assert _score_peg(15.0, 20.0) == 8.0  # PEG = 0.75

    def test_peg_below_1_5(self):
        """PEG < 1.5 → 7.0."""
        from src.analysis.fundamental import _score_peg
        assert _score_peg(25.0, 20.0) == 7.0  # PEG = 1.25

    def test_peg_below_2(self):
        """PEG < 2.0 → 5.0."""
        from src.analysis.fundamental import _score_peg
        assert _score_peg(30.0, 20.0) == 5.0  # PEG = 1.5

    def test_peg_above_2(self):
        """PEG >= 2.0 → 3.0."""
        from src.analysis.fundamental import _score_peg
        assert _score_peg(50.0, 20.0) == 3.0  # PEG = 2.5


class TestGrowthYoY:
    """YoY 성장률 우선순위 테스트."""

    def test_yoy_used_when_5_quarters(self):
        """5분기 이상이면 YoY (index 0 vs 4)."""
        from src.analysis.fundamental import _score_growth
        # valid[0]=150, valid[4]=100 → YoY = 50%
        revenues = [150000, 140000, 130000, 120000, 100000]
        score = _score_growth(revenues)
        assert score >= 9.0  # 50% growth → 9.0

    def test_qoq_fallback_when_2_quarters(self):
        """2분기만 있으면 QoQ fallback."""
        from src.analysis.fundamental import _score_growth
        revenues = [120000, 100000]
        score = _score_growth(revenues)
        assert score >= 7.0  # 20% QoQ → 8.0

    def test_yoy_vs_qoq_different_result(self):
        """YoY와 QoQ가 다른 결과를 줄 수 있다."""
        from src.analysis.fundamental import _score_growth
        # QoQ: 110/105 = 4.76% → 5.0
        # YoY: 110/100 = 10% → 7.0
        revenues = [110000, 105000, 103000, 101000, 100000]
        score = _score_growth(revenues)
        assert score >= 7.0  # YoY 10% → 7.0
