"""주간 리포트 조립기 테스트."""

from __future__ import annotations

from datetime import date

import pytest

from src.db.helpers import date_to_id
from src.db.models import (
    DimDate,
    DimMarket,
    DimSector,
    DimSignalType,
    DimStock,
    FactDailyPrice,
    FactDailyRecommendation,
    FactMacroIndicator,
    FactSignal,
)
from src.reports.weekly_assembler import (
    _build_ai_accuracy,
    _build_conviction_picks,
    _build_macro_summary,
    _build_outlook,
    _build_performance_review,
    _build_signal_trend,
    _calc_consecutive_days,
    _get_week_trading_days,
    assemble_weekly_report,
)


def _seed_week(session, year: int = 2026, week: int = 13):
    """테스트용 주간 데이터를 시딩한다."""
    # 날짜 디멘션 (월~금, 2026-W13: 03-23 ~ 03-27)
    dates = [
        date(2026, 3, 23),
        date(2026, 3, 24),
        date(2026, 3, 25),
        date(2026, 3, 26),
        date(2026, 3, 27),
    ]
    for i, d in enumerate(dates):
        session.add(DimDate(
            date_id=date_to_id(d), date=d,
            year=year, quarter=1, month=3,
            week_of_year=week, day_of_week=i,
            is_trading_day=True,
        ))

    # 시장 + 섹터 + 날짜 먼저 flush (FK 참조 대상)
    market = DimMarket(market_id=1, code="US", name="US Market", currency="USD", timezone="America/New_York")
    session.add(market)
    sector = DimSector(sector_id=1, sector_name="Technology")
    session.add(sector)
    session.flush()

    # 종목 2개
    stock1 = DimStock(
        stock_id=1, ticker="AAPL", name="Apple", market_id=1,
        sector_id=1, is_sp500=True, is_active=True,
    )
    stock2 = DimStock(
        stock_id=2, ticker="MSFT", name="Microsoft", market_id=1,
        sector_id=1, is_sp500=True, is_active=True,
    )
    session.add_all([stock1, stock2])
    session.flush()

    # 시그널 타입 (추천/시그널보다 먼저)
    buy_type = DimSignalType(
        signal_type_id=1, code="golden_cross", name="골든크로스",
        direction="BUY", default_weight=1.0,
    )
    sell_type = DimSignalType(
        signal_type_id=2, code="death_cross", name="데스크로스",
        direction="SELL", default_weight=1.0,
    )
    session.add_all([buy_type, sell_type])
    session.flush()

    # 추천: AAPL 5일, MSFT 2일
    for i, d in enumerate(dates):
        did = date_to_id(d)
        session.add(FactDailyRecommendation(
            run_date_id=did, stock_id=1, rank=1,
            total_score=8.0, technical_score=7.0,
            fundamental_score=8.0, external_score=6.0,
            momentum_score=7.0, smart_money_score=5.0,
            recommendation_reason="테스트",
            price_at_recommendation=150.0,
            ai_approved=True, ai_confidence=8,
        ))
        if i < 2:  # 월, 화만
            session.add(FactDailyRecommendation(
                run_date_id=did, stock_id=2, rank=2,
                total_score=7.0, technical_score=6.0,
                fundamental_score=7.0, external_score=5.0,
                momentum_score=6.0, smart_money_score=5.0,
                recommendation_reason="테스트",
                price_at_recommendation=300.0,
                ai_approved=False,
            ))

    # 가격 데이터
    for i, d in enumerate(dates):
        did = date_to_id(d)
        session.add(FactDailyPrice(
            stock_id=1, date_id=did,
            open=150.0 + i, high=152.0 + i, low=149.0 + i,
            close=151.0 + i, adj_close=151.0 + i, volume=1000000,
        ))
        session.add(FactDailyPrice(
            stock_id=2, date_id=did,
            open=300.0 + i, high=302.0 + i, low=299.0 + i,
            close=301.0 + i, adj_close=301.0 + i, volume=800000,
        ))

    # 매크로 데이터
    for i, d in enumerate(dates):
        did = date_to_id(d)
        session.add(FactMacroIndicator(
            date_id=did, vix=15.0 + i * 0.5,
            us_10y_yield=4.25 + i * 0.01,
            us_13w_yield=4.50,
            dollar_index=104.0 + i * 0.1,
            sp500_close=5200.0 + i * 20,
            sp500_sma20=5150.0,
            market_score=6 + (1 if i > 2 else 0),
            gold_price=2000.0, oil_price=75.0,
            yield_spread=-0.25 + i * 0.01,
        ))

    # 시그널
    for d in dates[:3]:
        did = date_to_id(d)
        session.add(FactSignal(
            stock_id=1, date_id=did, signal_type_id=1, strength=7,
        ))
    for d in dates[3:]:
        did = date_to_id(d)
        session.add(FactSignal(
            stock_id=2, date_id=did, signal_type_id=2, strength=5,
        ))

    session.commit()
    return dates


def test_get_week_trading_days(session):
    """해당 주의 거래일 목록을 반환한다."""
    _seed_week(session)
    date_ids, dates = _get_week_trading_days(session, 2026, 13)
    assert len(date_ids) == 5
    assert dates[0] == date(2026, 3, 23)


def test_get_week_trading_days_empty(session):
    """데이터 없는 주차는 빈 목록을 반환한다."""
    date_ids, dates = _get_week_trading_days(session, 2026, 99)
    assert date_ids == []
    assert dates == []


def test_build_performance_review(session):
    _seed_week(session)
    date_ids, _ = _get_week_trading_days(session, 2026, 13)
    pr = _build_performance_review(session, date_ids)
    assert pr.total_unique_picks == 2
    assert pr.win_count + pr.loss_count > 0  # 수익률 계산됨
    assert len(pr.all_picks) == 2


def test_build_performance_review_empty(session):
    pr = _build_performance_review(session, [])
    assert pr.total_unique_picks == 0


def test_build_conviction_picks(session):
    _seed_week(session)
    date_ids, _ = _get_week_trading_days(session, 2026, 13)
    picks = _build_conviction_picks(session, date_ids, 5)
    # AAPL: 5일 추천 → 확신, MSFT: 2일 → 미달
    assert len(picks) == 1
    assert picks[0].ticker == "AAPL"
    assert picks[0].days_recommended == 5
    assert picks[0].ai_consensus == "추천"


def test_build_conviction_picks_empty(session):
    picks = _build_conviction_picks(session, [], 5)
    assert picks == ()


def test_calc_consecutive_days():
    all_ids = [1, 2, 3, 4, 5]
    assert _calc_consecutive_days([1, 2, 3, 4, 5], all_ids) == 5
    assert _calc_consecutive_days([1, 2, 4, 5], all_ids) == 2
    assert _calc_consecutive_days([1, 3, 5], all_ids) == 1
    assert _calc_consecutive_days([], all_ids) == 0


def test_build_macro_summary(session):
    _seed_week(session)
    date_ids, _ = _get_week_trading_days(session, 2026, 13)
    ms = _build_macro_summary(session, date_ids)
    assert len(ms.daily_scores) == 5
    assert len(ms.vix_series) == 5
    assert ms.us_10y_start is not None
    assert ms.us_10y_end is not None


def test_build_macro_summary_empty(session):
    ms = _build_macro_summary(session, [])
    assert ms.daily_scores == ()


def test_build_signal_trend(session):
    _seed_week(session)
    date_ids, _ = _get_week_trading_days(session, 2026, 13)
    st = _build_signal_trend(session, date_ids, [])
    assert len(st.daily_buy_counts) == 5
    assert st.most_frequent_signal is not None


def test_build_ai_accuracy(session):
    _seed_week(session)
    date_ids, _ = _get_week_trading_days(session, 2026, 13)
    ai = _build_ai_accuracy(session, date_ids)
    assert ai.total_reviewed > 0
    assert ai.approval_rate_pct is not None


def test_build_ai_accuracy_empty(session):
    ai = _build_ai_accuracy(session, [])
    assert ai.total_reviewed == 0


def test_build_outlook():
    from src.analysis.regime import MarketRegime

    regime = MarketRegime(regime="bull", confidence=0.8, description="강세")
    rotation = ()
    ol = _build_outlook(regime, rotation)
    assert "강세" in ol.regime_strategy or "모멘텀" in ol.regime_strategy


def test_assemble_weekly_report(session):
    """전체 주간 리포트 조립 통합 테스트."""
    _seed_week(session)
    report = assemble_weekly_report(session, 2026, 13)
    assert report.year == 2026
    assert report.week_number == 13
    assert report.trading_days == 5
    assert report.performance_review.total_unique_picks == 2
    assert len(report.conviction_picks) == 1


def test_assemble_weekly_report_no_data(session):
    """데이터 없는 주차도 에러 없이 빈 리포트를 반환한다."""
    report = assemble_weekly_report(session, 2026, 99)
    assert report.trading_days == 0
    assert report.performance_review.total_unique_picks == 0
