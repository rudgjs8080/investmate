"""재무 품질 필터 테스트 — Piotroski F-Score, Altman Z-Score, Earnings Quality."""

from __future__ import annotations

import pytest

from src.analysis.quality import (
    AltmanResult,
    EarningsQuality,
    PiotroskiScore,
    calculate_altman_z,
    calculate_earnings_quality,
    calculate_piotroski,
    assess_quality,
    _safe_float,
)
from types import SimpleNamespace


# ──────────────────────────────────────────
# Helper: 간편 FactFinancial 생성
# ──────────────────────────────────────────


def _make_financial(
    stock_id: int = 1,
    period: str = "2025Q1",
    revenue: float | None = 100_000,
    operating_income: float | None = 20_000,
    net_income: float | None = 15_000,
    total_assets: float | None = 500_000,
    total_liabilities: float | None = 200_000,
    total_equity: float | None = 300_000,
    operating_cashflow: float | None = 18_000,
) -> SimpleNamespace:
    """테스트용 FactFinancial-like 객체를 생성한다 (DB 미사용)."""
    return SimpleNamespace(
        financial_id=None,
        stock_id=stock_id,
        period=period,
        revenue=revenue,
        operating_income=operating_income,
        net_income=net_income,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
        operating_cashflow=operating_cashflow,
    )


# ──────────────────────────────────────────
# Piotroski F-Score 테스트
# ──────────────────────────────────────────


class TestPiotroskiFScore:
    """Piotroski F-Score 계산 테스트."""

    def test_perfect_score(self):
        """모든 조건 충족 시 8점 (no_dilution=False 고정)."""
        current = _make_financial(
            period="2025Q1",
            net_income=20_000,
            operating_cashflow=25_000,  # > net_income
            total_assets=500_000,
            total_liabilities=180_000,  # 감소
            revenue=120_000,  # 증가
            operating_income=25_000,  # 증가
        )
        previous = _make_financial(
            period="2024Q4",
            net_income=15_000,
            operating_cashflow=18_000,
            total_assets=500_000,
            total_liabilities=200_000,
            revenue=100_000,
            operating_income=20_000,
        )
        result = calculate_piotroski(current, previous, None)

        assert isinstance(result, PiotroskiScore)
        assert result.score == 8  # no_dilution=False → max 8
        assert result.details["positive_net_income"] is True
        assert result.details["positive_cashflow"] is True
        assert result.details["earnings_quality"] is True
        assert result.details["no_dilution"] is False

    def test_worst_score_no_previous(self):
        """이전 분기 없이 적자 기업은 낮은 점수."""
        current = _make_financial(
            net_income=-5_000,
            operating_cashflow=-2_000,
        )
        result = calculate_piotroski(current, None, None)

        assert result.details["positive_net_income"] is False
        assert result.details["positive_cashflow"] is False
        assert result.details["roa_increased"] is False
        assert result.details["no_dilution"] is False
        # earnings_quality: OCF(-2000) > NI(-5000) = True
        assert result.details["earnings_quality"] is True
        # earnings_quality only = 1
        assert result.score == 1

    def test_roa_increased(self):
        """ROA 증가 여부 검증."""
        current = _make_financial(net_income=30_000, total_assets=500_000)
        previous = _make_financial(net_income=20_000, total_assets=500_000)
        result = calculate_piotroski(current, previous, None)
        assert result.details["roa_increased"] is True

    def test_roa_decreased(self):
        """ROA 감소 시 False."""
        current = _make_financial(net_income=10_000, total_assets=500_000)
        previous = _make_financial(net_income=20_000, total_assets=500_000)
        result = calculate_piotroski(current, previous, None)
        assert result.details["roa_increased"] is False

    def test_leverage_decreased(self):
        """TL/TA 감소 검증."""
        current = _make_financial(total_liabilities=150_000, total_assets=500_000)
        previous = _make_financial(total_liabilities=200_000, total_assets=500_000)
        result = calculate_piotroski(current, previous, None)
        assert result.details["leverage_decreased"] is True

    def test_none_fields_handled(self):
        """None 필드 안전 처리."""
        current = _make_financial(
            net_income=None, operating_cashflow=None,
            total_assets=None, total_liabilities=None,
            revenue=None, operating_income=None,
        )
        result = calculate_piotroski(current, None, None)
        assert isinstance(result.score, int)
        assert result.score >= 0
        # no_dilution=False 고정, 나머지 False
        assert result.details["no_dilution"] is False

    def test_gross_margin_increased(self):
        """매출총이익률 증가 검증 (OI/Revenue proxy)."""
        current = _make_financial(operating_income=30_000, revenue=100_000)
        previous = _make_financial(operating_income=20_000, revenue=100_000)
        result = calculate_piotroski(current, previous, None)
        assert result.details["gross_margin_increased"] is True

    def test_asset_turnover_increased(self):
        """자산회전율 증가 검증 (Revenue/TA)."""
        current = _make_financial(revenue=150_000, total_assets=500_000)
        previous = _make_financial(revenue=100_000, total_assets=500_000)
        result = calculate_piotroski(current, previous, None)
        assert result.details["asset_turnover_increased"] is True


# ──────────────────────────────────────────
# Altman Z-Score 테스트
# ──────────────────────────────────────────


class TestAltmanZScore:
    """Altman Z-Score 계산 테스트."""

    def test_safe_zone(self):
        """Z > 3.0이면 safe."""
        fin = _make_financial(
            total_equity=300_000,
            total_assets=500_000,
            operating_income=50_000,
            total_liabilities=200_000,
            revenue=400_000,
        )
        result = calculate_altman_z(fin, market_cap=1_000_000)
        assert isinstance(result, AltmanResult)
        assert result.zone == "safe"
        assert result.z_score > 3.0

    def test_distress_zone(self):
        """Z < 1.8이면 distress."""
        fin = _make_financial(
            total_equity=10_000,
            total_assets=500_000,
            operating_income=5_000,
            total_liabilities=490_000,
            revenue=50_000,
        )
        result = calculate_altman_z(fin, market_cap=50_000)
        assert result.zone == "distress"
        assert result.z_score < 1.8

    def test_gray_zone(self):
        """1.8 <= Z <= 3.0이면 gray."""
        fin = _make_financial(
            total_equity=200_000,
            total_assets=500_000,
            operating_income=40_000,
            total_liabilities=300_000,
            revenue=300_000,
        )
        result = calculate_altman_z(fin, market_cap=500_000)
        assert result.zone == "gray"
        assert 1.8 <= result.z_score <= 3.0

    def test_zero_total_assets(self):
        """total_assets=0이면 distress 반환."""
        fin = _make_financial(total_assets=0)
        result = calculate_altman_z(fin, market_cap=100_000)
        assert result.zone == "distress"
        assert result.z_score == 0.0

    def test_wc_proxy_uses_equity_minus_half_assets(self):
        """WC proxy: equity - 50% of total_assets (not equity/TA)."""
        fin = _make_financial(
            total_equity=300_000,
            total_assets=500_000,
            operating_income=0,
            total_liabilities=200_000,
            revenue=0,
            net_income=0,
        )
        result = calculate_altman_z(fin, market_cap=None)
        # wc = 300000 - 250000 = 50000; wc_ta = 0.1
        # re = 0 + 90000 = 90000; re_ta = 0.18
        # Z = 1.2*0.1 + 1.4*0.18 = 0.12 + 0.252 = 0.372
        assert result.z_score > 0.3
        assert result.z_score < 0.5

    def test_re_proxy_uses_annualized_ni_plus_equity(self):
        """RE proxy: 4*NI + 30%*equity (not equity/TA)."""
        fin = _make_financial(
            total_equity=100_000,
            total_assets=500_000,
            operating_income=0,
            total_liabilities=400_000,
            revenue=0,
            net_income=50_000,  # annualized = 200000
        )
        result = calculate_altman_z(fin, market_cap=None)
        # wc = 100000 - 250000 = -150000; wc_ta = -0.3
        # re = 200000 + 30000 = 230000; re_ta = 0.46
        # Z = 1.2*(-0.3) + 1.4*0.46 = -0.36 + 0.644 = 0.284
        assert result.z_score > 0.2
        assert result.z_score < 0.4

    def test_wc_and_re_not_double_counted(self):
        """WC와 RE가 서로 다른 proxy를 사용하여 double counting 방지."""
        # equity=0, ni=0 → wc=-250000, re=0 → 둘 다 다른 값
        fin = _make_financial(
            total_equity=0,
            total_assets=500_000,
            net_income=100_000,  # annualized = 400000
            operating_income=0,
            total_liabilities=500_000,
            revenue=0,
        )
        result = calculate_altman_z(fin, market_cap=None)
        # wc = 0 - 250000 = -250000; wc_ta = -0.5
        # re = 400000 + 0 = 400000; re_ta = 0.8
        # 1.2*(-0.5) + 1.4*0.8 = -0.6 + 1.12 = 0.52
        # Different from if both used te/ta (which would be 0)
        assert result.z_score > 0.4

    def test_none_market_cap(self):
        """market_cap=None이면 ME/TL=0으로 계산."""
        fin = _make_financial(
            total_equity=300_000,
            total_assets=500_000,
            operating_income=50_000,
            total_liabilities=200_000,
            revenue=400_000,
        )
        result = calculate_altman_z(fin, market_cap=None)
        assert isinstance(result.z_score, float)
        # ME/TL 항이 0이므로 market_cap 있을 때보다 점수가 낮음
        result_with_mc = calculate_altman_z(fin, market_cap=1_000_000)
        assert result.z_score < result_with_mc.z_score


# ──────────────────────────────────────────
# Earnings Quality 테스트
# ──────────────────────────────────────────


class TestEarningsQuality:
    """이익 품질 계산 테스트."""

    def test_high_quality(self):
        """accrual_ratio < 0.05이면 high."""
        fin = _make_financial(
            net_income=15_000,
            operating_cashflow=18_000,  # OCF > NI → 낮은 accrual
            total_assets=500_000,
        )
        result = calculate_earnings_quality(fin)
        assert result.quality == "high"
        assert result.accrual_ratio is not None
        assert result.accrual_ratio < 0.05

    def test_low_quality(self):
        """accrual_ratio > 0.10이면 low."""
        fin = _make_financial(
            net_income=100_000,
            operating_cashflow=10_000,  # NI >> OCF → 높은 accrual
            total_assets=500_000,
        )
        result = calculate_earnings_quality(fin)
        assert result.quality == "low"
        assert result.accrual_ratio is not None
        assert result.accrual_ratio > 0.10

    def test_medium_quality(self):
        """0.05 <= accrual_ratio <= 0.10이면 medium."""
        # (NI - OCF) / TA = (40000 - 5000) / 500000 = 0.07
        fin = _make_financial(
            net_income=40_000,
            operating_cashflow=5_000,
            total_assets=500_000,
        )
        result = calculate_earnings_quality(fin)
        assert result.quality == "medium"

    def test_none_fields_returns_medium(self):
        """데이터 없으면 medium 반환."""
        fin = _make_financial(
            net_income=None, operating_cashflow=None, total_assets=None,
        )
        result = calculate_earnings_quality(fin)
        assert result.quality == "medium"
        assert result.accrual_ratio is None

    def test_zero_total_assets_returns_medium(self):
        """total_assets=0이면 medium 반환."""
        fin = _make_financial(
            net_income=10_000, operating_cashflow=5_000, total_assets=0,
        )
        result = calculate_earnings_quality(fin)
        assert result.quality == "medium"
        assert result.accrual_ratio is None


# ──────────────────────────────────────────
# _safe_float 테스트
# ──────────────────────────────────────────


class TestSafeFloat:
    """_safe_float 유틸리티 테스트."""

    def test_none(self):
        assert _safe_float(None) is None

    def test_normal_float(self):
        assert _safe_float(3.14) == 3.14

    def test_int(self):
        assert _safe_float(42) == 42.0

    def test_string_invalid(self):
        assert _safe_float("abc") is None


# ──────────────────────────────────────────
# assess_quality 통합 테스트 (DB 필요)
# ──────────────────────────────────────────


class TestAssessQualityIntegration:
    """assess_quality DB 통합 테스트."""

    def test_assess_quality_with_db(self, seeded_session, us_market):
        """DB에 재무 데이터가 있으면 3종 점수 모두 반환."""
        from src.db.repository import StockRepository, FinancialRepository

        stock = StockRepository.add(
            seeded_session, "MSFT", "Microsoft Corp.", us_market, is_sp500=True,
        )
        seeded_session.commit()

        FinancialRepository.upsert(seeded_session, stock.stock_id, [
            {
                "period": "2024Q4",
                "revenue": 100_000, "operating_income": 20_000,
                "net_income": 15_000, "total_assets": 500_000,
                "total_liabilities": 200_000, "total_equity": 300_000,
                "operating_cashflow": 18_000,
            },
            {
                "period": "2025Q1",
                "revenue": 120_000, "operating_income": 25_000,
                "net_income": 20_000, "total_assets": 520_000,
                "total_liabilities": 190_000, "total_equity": 330_000,
                "operating_cashflow": 25_000,
            },
        ])
        seeded_session.commit()

        piotroski, altman, eq = assess_quality(seeded_session, stock.stock_id)

        assert isinstance(piotroski, PiotroskiScore)
        assert isinstance(altman, AltmanResult)
        assert isinstance(eq, EarningsQuality)
        assert 0 <= piotroski.score <= 9
        assert altman.zone in ("safe", "gray", "distress")
        assert eq.quality in ("high", "medium", "low")

    def test_assess_quality_no_data_raises(self, seeded_session, us_market):
        """재무 데이터 없으면 ValueError."""
        from src.db.repository import StockRepository

        stock = StockRepository.add(
            seeded_session, "NODATA", "No Data Corp.", us_market, is_sp500=True,
        )
        seeded_session.commit()

        with pytest.raises(ValueError, match="재무 데이터 없음"):
            assess_quality(seeded_session, stock.stock_id)
