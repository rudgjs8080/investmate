"""멀티 호라이즌 피드백 시스템 테스트 (Phase 1)."""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.ai.feedback import (
    AIPerformanceSummary,
    ConstraintRules,
    calculate_ai_performance,
    collect_multi_horizon_feedback,
    compute_feedback_weight,
    generate_constraint_rules,
)
from src.db.helpers import date_to_id
from src.db.models import (
    Base,
    DimDate,
    DimMarket,
    DimSector,
    DimStock,
    FactAIFeedback,
    FactDailyPrice,
    FactDailyRecommendation,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    session.add(DimMarket(market_id=1, code="US", name="미국", currency="USD", timezone="America/New_York"))
    session.add(DimSector(sector_id=1, sector_name="Technology"))
    session.add(DimSector(sector_id=2, sector_name="Healthcare"))
    session.add(DimStock(stock_id=1, ticker="AAPL", name="Apple", market_id=1, sector_id=1, is_sp500=True))
    session.add(DimStock(stock_id=2, ticker="MSFT", name="Microsoft", market_id=1, sector_id=1, is_sp500=True))
    session.add(DimStock(stock_id=3, ticker="JNJ", name="J&J", market_id=1, sector_id=2, is_sp500=True))

    # 날짜 디멘션: 90일치
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


def _add_price_data(session, stock_id: int, start: date, prices: list[float]):
    """주어진 가격 리스트로 일봉 데이터를 추가한다."""
    for i, price in enumerate(prices):
        d = start + timedelta(days=i)
        did = date_to_id(d)
        session.add(FactDailyPrice(
            stock_id=stock_id, date_id=did,
            open=price, high=price * 1.01, low=price * 0.99,
            close=price, adj_close=price, volume=1000000,
        ))
    session.flush()


class TestComputeFeedbackWeight:
    """시간 감쇠 가중치 테스트."""

    def test_today_weight_is_one(self):
        """오늘 추천의 가중치는 1.0."""
        weight = compute_feedback_weight(
            rec_date=date(2026, 3, 1),
            eval_date=date(2026, 3, 1),
            halflife_days=30,
        )
        assert weight == 1.0

    def test_halflife_weight(self):
        """반감기(30일) 경과 시 가중치 ≈ 0.5."""
        weight = compute_feedback_weight(
            rec_date=date(2026, 2, 1),
            eval_date=date(2026, 3, 3),
            halflife_days=30,
        )
        assert abs(weight - 0.5) < 0.05

    def test_old_weight_near_zero(self):
        """120일 전 추천은 가중치가 매우 낮다."""
        weight = compute_feedback_weight(
            rec_date=date(2025, 11, 1),
            eval_date=date(2026, 3, 1),
            halflife_days=30,
        )
        assert weight < 0.1

    def test_weight_never_negative(self):
        """가중치는 음수가 될 수 없다."""
        weight = compute_feedback_weight(
            rec_date=date(2020, 1, 1),
            eval_date=date(2026, 3, 1),
            halflife_days=30,
        )
        assert weight >= 0.0


class TestCollectMultiHorizonFeedback:
    """멀티 호라이즌 피드백 수집 테스트."""

    def test_collects_all_horizons(self):
        """5d/10d/20d/60d 수익률을 모두 수집한다."""
        session = _make_session()
        rec_date = date(2026, 1, 5)
        did = date_to_id(rec_date)
        rec_price = 100.0

        session.add(FactDailyRecommendation(
            run_date_id=did, stock_id=1, rank=1,
            total_score=7.0, technical_score=7.0, fundamental_score=7.0,
            smart_money_score=6.0, external_score=6.0, momentum_score=7.0,
            recommendation_reason="test", price_at_recommendation=rec_price,
            ai_approved=True, ai_confidence=8,
            return_5d=3.0, return_10d=5.0, return_20d=8.0,
        ))
        session.commit()

        # 60일치 가격 데이터 (5일후 +3%, 10일후 +5%, 20일후 +8%, 60일후 +15%)
        prices = [rec_price]
        for i in range(1, 80):
            if i <= 5:
                prices.append(rec_price * (1 + 0.03 * i / 5))
            elif i <= 10:
                prices.append(rec_price * (1 + 0.05 * i / 10))
            elif i <= 20:
                prices.append(rec_price * (1 + 0.08 * i / 20))
            else:
                prices.append(rec_price * (1 + 0.15 * i / 60))

        _add_price_data(session, 1, rec_date, prices)

        count = collect_multi_horizon_feedback(session, horizons=[5, 10, 20, 60])
        assert count >= 1

        fb = session.query(FactAIFeedback).first()
        assert fb is not None
        # 5d, 10d는 FactDailyRecommendation에서 읽음
        assert fb.return_5d is not None
        assert fb.return_10d is not None
        assert fb.return_20d is not None
        # 60d는 가격 데이터에서 직접 계산
        assert fb.return_60d is not None
        session.close()

    def test_direction_correct_per_horizon(self):
        """각 호라이즌별로 방향 정확도를 독립 평가한다."""
        session = _make_session()
        rec_date = date(2026, 1, 5)
        did = date_to_id(rec_date)

        # AI가 추천(approved)했고, 5d +3%, 20d -2%
        session.add(FactDailyRecommendation(
            run_date_id=did, stock_id=1, rank=1,
            total_score=7.0, technical_score=7.0, fundamental_score=7.0,
            smart_money_score=6.0, external_score=6.0, momentum_score=7.0,
            recommendation_reason="test", price_at_recommendation=100.0,
            ai_approved=True, ai_confidence=7,
            return_5d=3.0, return_10d=1.0, return_20d=-2.0,
        ))
        session.commit()

        count = collect_multi_horizon_feedback(session, horizons=[5, 10, 20])
        assert count == 1

        fb = session.query(FactAIFeedback).first()
        # 추천 + 5d 양수 = 맞음
        assert fb.direction_correct_5d is True
        # 추천 + 10d 양수 = 맞음
        assert fb.direction_correct_10d is True
        # 추천 + 20d 음수 = 틀림
        assert fb.direction_correct is False
        session.close()

    def test_feedback_weight_stored(self):
        """시간 감쇠 가중치가 저장된다."""
        session = _make_session()
        rec_date = date(2026, 1, 5)
        did = date_to_id(rec_date)

        session.add(FactDailyRecommendation(
            run_date_id=did, stock_id=1, rank=1,
            total_score=7.0, technical_score=7.0, fundamental_score=7.0,
            smart_money_score=6.0, external_score=6.0, momentum_score=7.0,
            recommendation_reason="test", price_at_recommendation=100.0,
            ai_approved=True, return_5d=2.0, return_20d=5.0,
        ))
        session.commit()

        count = collect_multi_horizon_feedback(session, horizons=[5, 20])
        assert count == 1

        fb = session.query(FactAIFeedback).first()
        assert fb.feedback_weight is not None
        assert 0.0 < float(fb.feedback_weight) <= 1.0
        session.close()

    def test_skips_already_collected(self):
        """이미 수집된 추천은 건너뛴다."""
        session = _make_session()
        d = date(2026, 1, 5)
        did = date_to_id(d)

        session.add(FactDailyRecommendation(
            run_date_id=did, stock_id=1, rank=1,
            total_score=7.0, technical_score=7.0, fundamental_score=7.0,
            smart_money_score=6.0, external_score=6.0, momentum_score=7.0,
            recommendation_reason="test", price_at_recommendation=100.0,
            ai_approved=True, return_20d=5.0,
        ))
        session.commit()

        count1 = collect_multi_horizon_feedback(session, horizons=[20])
        assert count1 == 1
        count2 = collect_multi_horizon_feedback(session, horizons=[20])
        assert count2 == 0
        session.close()


class TestGraduatedPenalty:
    """점진적 패널티 공식 테스트."""

    def test_no_penalty_when_approved_better(self):
        """AI 추천 평균이 제외보다 좋으면 패널티 0."""
        session = _make_session()
        d = date(2026, 1, 5)
        did = date_to_id(d)

        # 추천(approved=True) → 양수 수익
        session.add(FactAIFeedback(
            recommendation_id=1, run_date_id=did, stock_id=1,
            ticker="AAPL", sector="Technology",
            ai_approved=True, ai_confidence=7,
            price_at_rec=100.0, return_20d=8.0, direction_correct=True,
            feedback_weight=1.0,
        ))
        # 제외(approved=False) → 양수 수익 (AI가 틀렸음)
        session.add(FactAIFeedback(
            recommendation_id=2, run_date_id=did, stock_id=2,
            ticker="MSFT", sector="Technology",
            ai_approved=False, ai_confidence=3,
            price_at_rec=300.0, return_20d=2.0, direction_correct=False,
            feedback_weight=1.0,
        ))
        session.commit()

        rules = generate_constraint_rules(session)
        # 추천 avg(8.0) > 제외 avg(2.0) → 패널티 없음
        assert rules.confidence_penalty == 0
        session.close()

    def test_graduated_penalty_formula(self):
        """제외 평균이 추천보다 나을 때 점진적 패널티."""
        session = _make_session()
        d = date(2026, 1, 5)
        did = date_to_id(d)

        # 추천 평균: -5.0%
        session.add(FactAIFeedback(
            recommendation_id=1, run_date_id=did, stock_id=1,
            ticker="AAPL", sector="Technology",
            ai_approved=True, ai_confidence=7,
            price_at_rec=100.0, return_20d=-5.0, direction_correct=False,
            feedback_weight=1.0,
        ))
        # 제외 평균: +5.0%
        session.add(FactAIFeedback(
            recommendation_id=2, run_date_id=did, stock_id=2,
            ticker="MSFT", sector="Technology",
            ai_approved=False, ai_confidence=3,
            price_at_rec=300.0, return_20d=5.0, direction_correct=True,
            feedback_weight=1.0,
        ))
        session.commit()

        rules = generate_constraint_rules(session)
        # penalty = max(0, int(2 * (5.0 - (-5.0)) / 5)) = max(0, int(4.0)) = 4
        assert rules.confidence_penalty == 4
        session.close()

    def test_penalty_clamped_at_four(self):
        """패널티는 최대 4로 클램프."""
        session = _make_session()
        d = date(2026, 1, 5)
        did = date_to_id(d)

        # 극단적 차이: 추천 -20%, 제외 +20%
        session.add(FactAIFeedback(
            recommendation_id=1, run_date_id=did, stock_id=1,
            ticker="AAPL", ai_approved=True, ai_confidence=7,
            price_at_rec=100.0, return_20d=-20.0, direction_correct=False,
            feedback_weight=1.0,
        ))
        session.add(FactAIFeedback(
            recommendation_id=2, run_date_id=did, stock_id=2,
            ticker="MSFT", ai_approved=False, ai_confidence=3,
            price_at_rec=300.0, return_20d=20.0, direction_correct=True,
            feedback_weight=1.0,
        ))
        session.commit()

        rules = generate_constraint_rules(session)
        assert rules.confidence_penalty <= 4
        session.close()


class TestMultiHorizonPerformance:
    """호라이즌별 성과 계산 테스트."""

    def test_performance_includes_horizon_metrics(self):
        """AIPerformanceSummary에 호라이즌별 승률이 포함된다."""
        session = _make_session()
        d = date(2026, 1, 5)
        did = date_to_id(d)

        session.add(FactAIFeedback(
            recommendation_id=1, run_date_id=did, stock_id=1,
            ticker="AAPL", sector="Technology",
            ai_approved=True, ai_confidence=8,
            price_at_rec=100.0,
            return_5d=3.0, return_10d=5.0, return_20d=8.0, return_60d=15.0,
            direction_correct=True,
            direction_correct_5d=True, direction_correct_10d=True, direction_correct_60d=True,
            feedback_weight=1.0,
        ))
        session.add(FactAIFeedback(
            recommendation_id=2, run_date_id=did, stock_id=2,
            ticker="MSFT", sector="Technology",
            ai_approved=True, ai_confidence=6,
            price_at_rec=300.0,
            return_5d=-1.0, return_10d=-2.0, return_20d=-3.0, return_60d=-5.0,
            direction_correct=False,
            direction_correct_5d=False, direction_correct_10d=False, direction_correct_60d=False,
            feedback_weight=1.0,
        ))
        session.commit()

        result = calculate_ai_performance(session)
        assert result.total_predictions == 2
        assert result.win_rate_approved == 50.0

        # 호라이즌별 승률 확인
        assert result.horizon_win_rates is not None
        assert "5d" in result.horizon_win_rates
        assert "20d" in result.horizon_win_rates
        assert result.horizon_win_rates["5d"] == 50.0
        assert result.horizon_win_rates["20d"] == 50.0
        session.close()

    def test_performance_with_temporal_decay(self):
        """시간 감쇠 가중치가 성과 계산에 반영된다."""
        session = _make_session()
        d1 = date(2026, 1, 5)
        d2 = date(2026, 3, 1)
        did1 = date_to_id(d1)
        did2 = date_to_id(d2)

        # 오래된 추천 (가중치 낮음): 실패
        session.add(FactAIFeedback(
            recommendation_id=1, run_date_id=did1, stock_id=1,
            ticker="AAPL", ai_approved=True, ai_confidence=7,
            price_at_rec=100.0, return_20d=-5.0, direction_correct=False,
            feedback_weight=0.2,
        ))
        # 최근 추천 (가중치 높음): 성공
        session.add(FactAIFeedback(
            recommendation_id=2, run_date_id=did2, stock_id=2,
            ticker="MSFT", ai_approved=True, ai_confidence=7,
            price_at_rec=300.0, return_20d=10.0, direction_correct=True,
            feedback_weight=0.9,
        ))
        session.commit()

        result = calculate_ai_performance(session)
        # 가중 승률: 최근 성공이 더 큰 영향
        assert result.weighted_win_rate_approved is not None
        # 가중평균: (0.2*0 + 0.9*1) / (0.2+0.9) ≈ 0.818 → 81.8%
        assert result.weighted_win_rate_approved > 60.0
        session.close()
