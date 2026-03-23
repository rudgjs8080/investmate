"""섹터 상대 밸류에이션 정규화 테스트."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.analysis.fundamental import (
    _score_per_relative,
    _score_pbr_relative,
    _score_roe_relative,
    analyze_fundamentals,
    build_sector_medians,
)
from src.data.schemas import FinancialRecord, ValuationRecord
from src.db.models import (
    Base,
    DimDate,
    DimMarket,
    DimSector,
    DimStock,
    FactValuation,
)


# ──────────────────────────────────────────
# _score_per_relative
# ──────────────────────────────────────────


class TestScorePerRelative:
    """섹터 상대 PER 점수 테스트."""

    def test_very_cheap(self):
        # ratio = 10 / 28 = 0.357 → < 0.5 → 9.0
        assert _score_per_relative(10.0, 28.0) == 9.0

    def test_cheap(self):
        # ratio = 18 / 28 = 0.643 → < 0.75 → 8.0
        assert _score_per_relative(18.0, 28.0) == 8.0

    def test_below_average(self):
        # ratio = 25 / 28 = 0.893 → < 1.0 → 7.0
        assert _score_per_relative(25.0, 28.0) == 7.0

    def test_above_average(self):
        # ratio = 32 / 28 = 1.143 → < 1.25 → 5.0
        assert _score_per_relative(32.0, 28.0) == 5.0

    def test_expensive(self):
        # ratio = 38 / 28 = 1.357 → < 1.5 → 4.0
        assert _score_per_relative(38.0, 28.0) == 4.0

    def test_very_expensive(self):
        # ratio = 50 / 28 = 1.786 → >= 1.5 → 3.0
        assert _score_per_relative(50.0, 28.0) == 3.0

    def test_negative_per(self):
        """적자 기업은 항상 2.0."""
        assert _score_per_relative(-5.0, 28.0) == 2.0

    def test_none_per_fallback(self):
        """PER None이면 절대 스코어링 fallback (3.5)."""
        assert _score_per_relative(None, 28.0) == 3.5

    def test_none_median_fallback(self):
        """섹터 중앙값 None이면 절대 스코어링 fallback."""
        assert _score_per_relative(12.0, None) == 8.0  # absolute: PER 12 → 8.0

    def test_zero_median_fallback(self):
        """섹터 중앙값 0이면 절대 스코어링 fallback."""
        assert _score_per_relative(12.0, 0.0) == 8.0


# ──────────────────────────────────────────
# _score_pbr_relative
# ──────────────────────────────────────────


class TestScorePbrRelative:
    """섹터 상대 PBR 점수 테스트."""

    def test_very_cheap(self):
        # ratio = 2.0 / 8.0 = 0.25 → < 0.5 → 9.0
        assert _score_pbr_relative(2.0, 8.0) == 9.0

    def test_below_average(self):
        # ratio = 7.0 / 8.0 = 0.875 → < 1.0 → 7.0
        assert _score_pbr_relative(7.0, 8.0) == 7.0

    def test_very_expensive(self):
        # ratio = 14.0 / 8.0 = 1.75 → >= 1.5 → 3.0
        assert _score_pbr_relative(14.0, 8.0) == 3.0

    def test_negative_pbr(self):
        assert _score_pbr_relative(-1.0, 8.0) == 2.0

    def test_none_fallback(self):
        assert _score_pbr_relative(None, 8.0) == 3.5


# ──────────────────────────────────────────
# _score_roe_relative
# ──────────────────────────────────────────


class TestScoreRoeRelative:
    """섹터 상대 ROE 점수 테스트 (높을수록 좋음 — 역비율)."""

    def test_much_higher_than_median(self):
        # ROE 0.40, median 0.15 → ratio = 0.15/0.40 = 0.375 → < 0.5 → 9.0
        assert _score_roe_relative(0.40, 0.15) == 9.0

    def test_slightly_above_median(self):
        # ROE 0.18, median 0.15 → ratio = 0.15/0.18 = 0.833 → < 1.0 → 7.0
        assert _score_roe_relative(0.18, 0.15) == 7.0

    def test_at_median(self):
        # ROE 0.15, median 0.15 → ratio = 1.0 → < 1.25 → 5.0
        assert _score_roe_relative(0.15, 0.15) == 5.0

    def test_below_median(self):
        # ROE 0.11, median 0.15 → ratio = 0.15/0.11 = 1.364 → < 1.5 → 4.0
        assert _score_roe_relative(0.11, 0.15) == 4.0

    def test_much_lower_than_median(self):
        # ROE 0.05, median 0.15 → ratio = 0.15/0.05 = 3.0 → >= 1.5 → 3.0
        assert _score_roe_relative(0.05, 0.15) == 3.0

    def test_negative_roe(self):
        assert _score_roe_relative(-0.10, 0.15) == 2.0

    def test_none_fallback(self):
        assert _score_roe_relative(None, 0.15) == 3.5

    def test_zero_roe(self):
        # roe == 0 → division guard → ratio = 999 → >= 1.5 → 3.0
        assert _score_roe_relative(0.0, 0.15) == 3.0


# ──────────────────────────────────────────
# analyze_fundamentals with sector_medians
# ──────────────────────────────────────────


class TestAnalyzeFundamentalsRelative:
    """sector_medians 파라미터 통합 테스트."""

    def _base_records(self):
        return [
            FinancialRecord(
                period="2024Q2", revenue=120000.0,
                total_assets=500000.0, total_liabilities=100000.0,
            ),
            FinancialRecord(period="2024Q1", revenue=100000.0),
        ]

    def test_without_sector_medians_unchanged(self):
        """sector_medians 미제공 → 기존 절대 스코어링."""
        records = self._base_records()
        val = ValuationRecord(date=date(2024, 6, 30), per=12.0, pbr=1.5, roe=0.20)
        result = analyze_fundamentals(records, val)
        assert result.per_score == 8.0  # absolute: PER 12 → 8.0
        assert result.pbr_score == 7.0  # absolute: PBR 1.5 → 7.0

    def test_with_sector_medians_it_sector(self):
        """IT 섹터 기준: PER 25는 평균 이하 → 높은 점수."""
        records = self._base_records()
        val = ValuationRecord(date=date(2024, 6, 30), per=25.0, pbr=7.0, roe=0.20)
        medians = {"per": 28.0, "pbr": 8.0, "roe": 0.18}
        result = analyze_fundamentals(records, val, sector_medians=medians)
        # PER 25/28 = 0.893 → 7.0 (below average, good)
        assert result.per_score == 7.0
        # PBR 7/8 = 0.875 → 7.0
        assert result.pbr_score == 7.0

    def test_with_sector_medians_utilities_expensive(self):
        """Utilities 섹터 기준: PER 25는 비쌈."""
        records = self._base_records()
        val = ValuationRecord(date=date(2024, 6, 30), per=25.0, pbr=3.0, roe=0.10)
        medians = {"per": 15.0, "pbr": 1.5, "roe": 0.12}
        result = analyze_fundamentals(records, val, sector_medians=medians)
        # PER 25/15 = 1.667 → >= 1.5 → 3.0
        assert result.per_score == 3.0
        # PBR 3/1.5 = 2.0 → >= 1.5 → 3.0
        assert result.pbr_score == 3.0

    def test_empty_financials_ignores_medians(self):
        """빈 재무 데이터 → 기본값 5.0 반환, sector_medians 무시."""
        result = analyze_fundamentals([], sector_medians={"per": 20.0})
        assert result.composite_score == 5.0

    def test_partial_sector_medians(self):
        """섹터 중앙값에 per만 있고 pbr/roe 없으면 해당 항목만 상대, 나머지 절대."""
        records = self._base_records()
        val = ValuationRecord(date=date(2024, 6, 30), per=25.0, pbr=1.5, roe=0.20)
        medians = {"per": 28.0}  # pbr, roe 없음
        result = analyze_fundamentals(records, val, sector_medians=medians)
        assert result.per_score == 7.0  # relative: 25/28 = 0.893
        assert result.pbr_score == 7.0  # fallback absolute: PBR 1.5 → 7.0
        assert result.roe_score >= 7.0  # fallback absolute: ROE 20% → 8.0


# ──────────────────────────────────────────
# build_sector_medians (in-memory SQLite)
# ──────────────────────────────────────────


@pytest.fixture
def sector_db_session():
    """섹터 밸류에이션 테스트용 in-memory DB 세션."""
    eng = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    sess = factory()

    # 시장
    market = DimMarket(
        code="US", name="미국", currency="USD", timezone="America/New_York",
    )
    sess.add(market)
    sess.flush()

    # 섹터
    it_sector = DimSector(sector_name="Information Technology")
    util_sector = DimSector(sector_name="Utilities")
    sess.add_all([it_sector, util_sector])
    sess.flush()

    # 날짜
    d1 = DimDate(
        date_id=20240601, date=date(2024, 6, 1),
        year=2024, quarter=2, month=6, week_of_year=22,
        day_of_week=5, is_trading_day=True,
    )
    d2 = DimDate(
        date_id=20240701, date=date(2024, 7, 1),
        year=2024, quarter=3, month=7, week_of_year=27,
        day_of_week=0, is_trading_day=True,
    )
    sess.add_all([d1, d2])
    sess.flush()

    # 종목 (IT 2개, Utilities 2개)
    aapl = DimStock(
        ticker="AAPL", name="Apple", market_id=market.market_id,
        sector_id=it_sector.sector_id,
    )
    msft = DimStock(
        ticker="MSFT", name="Microsoft", market_id=market.market_id,
        sector_id=it_sector.sector_id,
    )
    nee = DimStock(
        ticker="NEE", name="NextEra Energy", market_id=market.market_id,
        sector_id=util_sector.sector_id,
    )
    so = DimStock(
        ticker="SO", name="Southern Co", market_id=market.market_id,
        sector_id=util_sector.sector_id,
    )
    sess.add_all([aapl, msft, nee, so])
    sess.flush()

    # 밸류에이션 (각 종목 2개 날짜, 최신만 사용되어야 함)
    valuations = [
        # AAPL: 구 데이터
        FactValuation(
            stock_id=aapl.stock_id, date_id=20240601,
            per=28.0, pbr=9.0, roe=0.25,
        ),
        # AAPL: 최신
        FactValuation(
            stock_id=aapl.stock_id, date_id=20240701,
            per=30.0, pbr=10.0, roe=0.22,
        ),
        # MSFT: 최신만
        FactValuation(
            stock_id=msft.stock_id, date_id=20240701,
            per=26.0, pbr=8.0, roe=0.20,
        ),
        # NEE: 최신
        FactValuation(
            stock_id=nee.stock_id, date_id=20240701,
            per=14.0, pbr=2.0, roe=0.10,
        ),
        # SO: 최신
        FactValuation(
            stock_id=so.stock_id, date_id=20240701,
            per=16.0, pbr=1.8, roe=0.12,
        ),
    ]
    sess.add_all(valuations)
    sess.commit()

    yield sess
    sess.close()


class TestBuildSectorMedians:
    """build_sector_medians DB 통합 테스트."""

    def test_returns_correct_sectors(self, sector_db_session: Session):
        result = build_sector_medians(sector_db_session)
        assert "Information Technology" in result
        assert "Utilities" in result
        assert len(result) == 2

    def test_it_sector_medians(self, sector_db_session: Session):
        """IT 섹터: AAPL(30, 10, 0.22) + MSFT(26, 8, 0.20) → 중앙값."""
        result = build_sector_medians(sector_db_session)
        it = result["Information Technology"]
        # median of [30, 26] = 28.0
        assert it["per"] == 28.0
        # median of [10, 8] = 9.0
        assert it["pbr"] == 9.0
        # median of [0.22, 0.20] = 0.21
        assert it["roe"] == pytest.approx(0.21)

    def test_utilities_sector_medians(self, sector_db_session: Session):
        """Utilities 섹터: NEE(14, 2, 0.10) + SO(16, 1.8, 0.12)."""
        result = build_sector_medians(sector_db_session)
        util = result["Utilities"]
        assert util["per"] == 15.0  # median of [14, 16]
        assert util["pbr"] == 1.9   # median of [2.0, 1.8]
        assert util["roe"] == pytest.approx(0.11)

    def test_uses_latest_date_only(self, sector_db_session: Session):
        """AAPL의 구 데이터(date_id=20240601, PER=28)는 무시, 최신(PER=30)만 사용."""
        result = build_sector_medians(sector_db_session)
        it = result["Information Technology"]
        # 구 데이터 PER 28 포함되었다면 median([28, 30, 26]) = 28 이 아닌 28.0
        # 최신만 사용하면 median([30, 26]) = 28.0 — 이 경우 값이 같아서
        # 추가 검증: PBR은 구 데이터 9.0 vs 최신 10.0
        # 최신만 사용: median([10, 8]) = 9.0
        # 구 데이터 포함: median([9, 10, 8]) = 9.0 — 역시 같음
        # ROE로 검증: 구 0.25 vs 최신 0.22
        # 최신만: median([0.22, 0.20]) = 0.21
        # 구 포함: median([0.25, 0.22, 0.20]) = 0.22
        assert it["roe"] == pytest.approx(0.21)

    def test_empty_db(self):
        """빈 DB → 빈 딕셔너리."""
        eng = create_engine("sqlite:///:memory:", echo=False)
        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng, expire_on_commit=False)
        sess = factory()
        result = build_sector_medians(sess)
        assert result == {}
        sess.close()
