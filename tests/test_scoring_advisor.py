"""적응형 스코어링 어드바이저 테스트 (Phase 5)."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.ai.scoring_advisor import (
    AdaptiveWeights,
    _pearson_correlation,
    compute_adaptive_weights,
    compute_feature_importance,
)
from src.db.helpers import date_to_id
from src.db.models import (
    Base,
    DimDate,
    DimMarket,
    DimSector,
    DimStock,
    FactDailyRecommendation,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.add(DimMarket(market_id=1, code="US", name="미국", currency="USD", timezone="America/New_York"))
    session.add(DimSector(sector_id=1, sector_name="Technology"))
    session.add(DimStock(stock_id=1, ticker="AAPL", name="Apple", market_id=1, sector_id=1, is_sp500=True))

    # 날짜 디멘션: 120일치
    base_date = date(2026, 1, 1)
    for i in range(120):
        d = base_date + timedelta(days=i)
        did = date_to_id(d)
        session.add(DimDate(
            date_id=did, date=d, year=d.year, quarter=(d.month - 1) // 3 + 1,
            month=d.month, week_of_year=d.isocalendar()[1],
            day_of_week=d.weekday(), is_trading_day=d.weekday() < 5,
        ))
    session.flush()
    session.commit()
    return session


class TestPearsonCorrelation:
    def test_perfect_positive(self):
        assert _pearson_correlation([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]) == 1.0

    def test_perfect_negative(self):
        assert _pearson_correlation([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]) == -1.0

    def test_no_correlation(self):
        corr = _pearson_correlation([1, 2, 3, 4, 5], [5, 1, 4, 2, 3])
        assert abs(corr) < 0.5

    def test_insufficient_data(self):
        assert _pearson_correlation([1, 2], [3, 4]) == 0.0

    def test_constant_series(self):
        assert _pearson_correlation([5, 5, 5, 5], [1, 2, 3, 4]) == 0.0


class TestAdaptiveWeights:
    def test_frozen_dataclass(self):
        w = AdaptiveWeights(0.2, 0.2, 0.2, 0.2, 0.2, 100, 0.5)
        assert w.technical == 0.2
        try:
            w.technical = 0.3  # type: ignore
            assert False, "should raise"
        except AttributeError:
            pass

    def test_to_dict(self):
        w = AdaptiveWeights(0.25, 0.25, 0.15, 0.15, 0.20, 50, 0.3)
        d = w.to_dict()
        assert d["technical"] == 0.25
        assert sum(d.values()) > 0.99


class TestComputeAdaptiveWeights:
    def test_returns_none_when_insufficient_data(self):
        session = _make_session()
        result = compute_adaptive_weights(session, min_samples=30)
        assert result is None
        session.close()

    def test_computes_weights_with_enough_data(self):
        session = _make_session()

        # 35개 추천 데이터 삽입 (min_samples=30 충족)
        base_date = date(2026, 1, 5)
        for i in range(35):
            d = base_date + timedelta(days=i)
            did = date_to_id(d)
            # 기술적 점수가 높을수록 수익률 양수 → 기술적 가중치 높아야 함
            tech_score = 5.0 + i * 0.1
            ret = (tech_score - 5.0) * 2 + (i % 3 - 1)
            session.add(FactDailyRecommendation(
                run_date_id=did, stock_id=1, rank=1,
                total_score=7.0, technical_score=tech_score,
                fundamental_score=5.0, smart_money_score=5.0,
                external_score=5.0, momentum_score=5.0,
                recommendation_reason="test",
                price_at_recommendation=100.0,
                return_20d=ret,
            ))
        session.commit()

        result = compute_adaptive_weights(session, min_samples=30)
        assert result is not None
        assert result.sample_size == 35
        assert result.correlation_quality >= 0

        d = result.to_dict()
        total = sum(d.values())
        assert abs(total - 1.0) < 0.01  # 합계 = 1.0

        # 기술적 가중치가 가장 높아야 함 (상관이 가장 강함)
        assert d["technical"] >= d["fundamental"]
        session.close()

    def test_weights_sum_to_one(self):
        session = _make_session()
        base_date = date(2026, 1, 5)
        for i in range(40):
            d = base_date + timedelta(days=i)
            did = date_to_id(d)
            session.add(FactDailyRecommendation(
                run_date_id=did, stock_id=1, rank=1,
                total_score=7.0, technical_score=5.0 + i * 0.05,
                fundamental_score=6.0 - i * 0.02,
                smart_money_score=5.0, external_score=5.0,
                momentum_score=5.0 + i * 0.03,
                recommendation_reason="test",
                price_at_recommendation=100.0,
                return_20d=i * 0.5 - 5.0,
            ))
        session.commit()

        result = compute_adaptive_weights(session, min_samples=30)
        assert result is not None
        d = result.to_dict()
        assert abs(sum(d.values()) - 1.0) < 0.01
        session.close()


class TestComputeFeatureImportance:
    def test_returns_empty_when_no_model(self):
        result = compute_feature_importance("/nonexistent/path.pkl")
        assert result == {}

    def test_returns_empty_when_no_models_dir(self):
        result = compute_feature_importance(None)
        # data/models 없으면 빈 dict
        assert isinstance(result, dict)
