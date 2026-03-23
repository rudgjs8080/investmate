"""추천 성과 분석 테스트."""

from datetime import date

import pytest

from src.analysis.performance import PerformanceReport, calculate_performance, _best_return
from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import FactDailyRecommendation
from src.db.repository import StockRepository


class TestBestReturn:
    def test_prefers_20d(self):
        rec = FactDailyRecommendation(
            run_date_id=20260301, stock_id=1, rank=1, total_score=7.0,
            technical_score=5.0, fundamental_score=5.0, external_score=5.0,
            momentum_score=5.0, smart_money_score=5.0,
            recommendation_reason="test", price_at_recommendation=100.0,
            return_1d=1.0, return_5d=3.0, return_20d=8.0,
        )
        assert _best_return(rec) == 8.0

    def test_falls_back_to_10d(self):
        rec = FactDailyRecommendation(
            run_date_id=20260301, stock_id=1, rank=1, total_score=7.0,
            technical_score=5.0, fundamental_score=5.0, external_score=5.0,
            momentum_score=5.0, smart_money_score=5.0,
            recommendation_reason="test", price_at_recommendation=100.0,
            return_1d=1.0, return_10d=5.0,
        )
        assert _best_return(rec) == 5.0

    def test_falls_back_to_1d(self):
        rec = FactDailyRecommendation(
            run_date_id=20260301, stock_id=1, rank=1, total_score=7.0,
            technical_score=5.0, fundamental_score=5.0, external_score=5.0,
            momentum_score=5.0, smart_money_score=5.0,
            recommendation_reason="test", price_at_recommendation=100.0,
            return_1d=2.0,
        )
        assert _best_return(rec) == 2.0

    def test_none_when_no_data(self):
        rec = FactDailyRecommendation(
            run_date_id=20260301, stock_id=1, rank=1, total_score=7.0,
            technical_score=5.0, fundamental_score=5.0, external_score=5.0,
            momentum_score=5.0, smart_money_score=5.0,
            recommendation_reason="test", price_at_recommendation=100.0,
        )
        assert _best_return(rec) is None


class TestCalculatePerformance:
    def test_empty_db(self, seeded_session):
        report = calculate_performance(seeded_session, days=90)
        assert report.total_recommendations == 0
        assert report.win_rate_1d is None

    def test_with_recommendations(self, seeded_session, sample_stock):
        ensure_date_ids(seeded_session, [date(2026, 3, 15), date(2026, 3, 16)])

        # 추천 2건 생성
        seeded_session.add(FactDailyRecommendation(
            run_date_id=20260315, stock_id=sample_stock["id"], rank=1,
            total_score=7.0, technical_score=6.0, fundamental_score=7.0,
            external_score=5.0, momentum_score=8.0, smart_money_score=5.0,
            recommendation_reason="test1", price_at_recommendation=100.0,
            return_1d=1.5, return_5d=3.0, return_10d=5.0, return_20d=8.0,
        ))
        seeded_session.add(FactDailyRecommendation(
            run_date_id=20260316, stock_id=sample_stock["id"], rank=1,
            total_score=6.0, technical_score=5.0, fundamental_score=6.0,
            external_score=4.0, momentum_score=7.0, smart_money_score=5.0,
            recommendation_reason="test2", price_at_recommendation=100.0,
            return_1d=-0.5, return_5d=-1.0, return_10d=-2.0, return_20d=-3.0,
        ))
        seeded_session.flush()

        report = calculate_performance(seeded_session, days=90)

        assert report.total_recommendations == 2
        assert report.with_return_data == 2
        assert report.win_rate_1d == 50.0  # 1/2
        assert report.win_rate_20d == 50.0
        assert report.avg_return_1d == 0.5  # (1.5 + -0.5) / 2
        assert report.avg_return_20d == 2.5  # (8.0 + -3.0) / 2

    def test_best_worst_picks(self, seeded_session, sample_stock):
        ensure_date_ids(seeded_session, [date(2026, 3, 15), date(2026, 3, 16)])

        seeded_session.add(FactDailyRecommendation(
            run_date_id=20260315, stock_id=sample_stock["id"], rank=1,
            total_score=7.0, technical_score=6.0, fundamental_score=7.0,
            external_score=5.0, momentum_score=8.0, smart_money_score=5.0,
            recommendation_reason="best", price_at_recommendation=100.0,
            return_20d=15.0,
        ))
        seeded_session.add(FactDailyRecommendation(
            run_date_id=20260316, stock_id=sample_stock["id"], rank=2,
            total_score=5.0, technical_score=5.0, fundamental_score=5.0,
            external_score=5.0, momentum_score=5.0, smart_money_score=5.0,
            recommendation_reason="worst", price_at_recommendation=100.0,
            return_20d=-8.0,
        ))
        seeded_session.flush()

        report = calculate_performance(seeded_session, days=90)

        assert report.best_pick is not None
        assert report.best_pick[1] == 15.0
        assert report.worst_pick is not None
        assert report.worst_pick[1] == -8.0

    def test_recent_picks(self, seeded_session, sample_stock):
        ensure_date_ids(seeded_session, [date(2026, 3, 15)])

        seeded_session.add(FactDailyRecommendation(
            run_date_id=20260315, stock_id=sample_stock["id"], rank=1,
            total_score=7.0, technical_score=6.0, fundamental_score=7.0,
            external_score=5.0, momentum_score=8.0, smart_money_score=5.0,
            recommendation_reason="test", price_at_recommendation=100.0,
            return_1d=2.0,
        ))
        seeded_session.flush()

        report = calculate_performance(seeded_session, days=90)
        assert len(report.recent_picks) == 1
        assert report.recent_picks[0]["ticker"] == sample_stock["ticker"]
        assert report.recent_picks[0]["return_1d"] == 2.0
