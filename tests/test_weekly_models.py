"""주간 리포트 데이터 모델 테스트."""

from __future__ import annotations

import pytest

from src.reports.weekly_models import (
    ConvictionPick,
    SectorRotationEntry,
    WeeklyAIAccuracy,
    WeeklyExecutiveSummary,
    WeeklyMacroSummary,
    WeeklyOutlook,
    WeeklyPerformanceReview,
    WeeklyPickPerformance,
    WeeklyReport,
    WeeklySignalTrend,
)


def test_executive_summary_frozen():
    es = WeeklyExecutiveSummary(
        market_oneliner="강세 지속",
        sp500_weekly_return_pct=1.5,
        vix_start=15.0, vix_end=14.5, vix_high=16.0, vix_low=14.0,
        regime_start="bull", regime_end="bull", regime_changed=False,
        weekly_win_rate_pct=60.0, weekly_avg_return_pct=1.2,
    )
    assert es.market_oneliner == "강세 지속"
    with pytest.raises(AttributeError):
        es.market_oneliner = "변경"


def test_weekly_pick_performance():
    p = WeeklyPickPerformance(
        ticker="AAPL", name="Apple", sector="Technology",
        days_recommended=4, avg_rank=2.5,
        weekly_return_pct=3.2,
        ai_approved_days=3, ai_rejected_days=0,
    )
    assert p.ticker == "AAPL"
    assert p.days_recommended == 4


def test_conviction_pick():
    c = ConvictionPick(
        ticker="MSFT", name="Microsoft", sector="Technology",
        days_recommended=5, consecutive_days=5,
        avg_rank=1.0, avg_total_score=8.5,
        weekly_return_pct=2.1, ai_consensus="추천",
    )
    assert c.ai_consensus == "추천"
    assert c.consecutive_days == 5


def test_sector_rotation_entry():
    e = SectorRotationEntry(
        sector="Technology", weekly_return_pct=2.5,
        volume_change_pct=10.3, momentum_delta="상승", pick_count=5,
    )
    assert e.momentum_delta == "상승"


def test_weekly_report_full():
    """WeeklyReport 전체 조립 테스트."""
    report = WeeklyReport(
        year=2026, week_number=13,
        week_start="2026-03-23", week_end="2026-03-27",
        trading_days=5, generated_at="2026-03-29 10:00:00",
        executive_summary=WeeklyExecutiveSummary(
            market_oneliner="테스트", sp500_weekly_return_pct=None,
            vix_start=None, vix_end=None, vix_high=None, vix_low=None,
            regime_start="range", regime_end="range", regime_changed=False,
            weekly_win_rate_pct=None, weekly_avg_return_pct=None,
        ),
        performance_review=WeeklyPerformanceReview(
            total_unique_picks=0, win_count=0, loss_count=0,
            win_rate_pct=None, avg_return_pct=None,
            best_pick=None, worst_pick=None,
            ai_approved_avg_return=None, ai_rejected_avg_return=None,
            all_picks=(),
        ),
        conviction_picks=(),
        sector_rotation=(),
        macro_summary=WeeklyMacroSummary(
            daily_scores=(), vix_series=(),
            us_10y_start=None, us_10y_end=None,
            us_13w_start=None, us_13w_end=None,
            spread_start=None, spread_end=None,
            dollar_start=None, dollar_end=None,
            gold_start=None, gold_end=None,
            oil_start=None, oil_end=None,
        ),
        signal_trend=WeeklySignalTrend(
            daily_buy_counts=(), daily_sell_counts=(),
            most_frequent_signal=None, avg_strength_change=None,
        ),
        ai_accuracy=WeeklyAIAccuracy(
            approval_rate_pct=None, direction_accuracy_pct=None,
            confidence_vs_return_corr=None, total_reviewed=0,
        ),
        outlook=WeeklyOutlook(
            regime_strategy="횡보 전략",
            watchlist_sectors=(), avoid_sectors=(),
            rebalancing_suggestion="현재 비중 유지",
        ),
    )
    assert report.year == 2026
    assert report.week_number == 13
    assert report.trading_days == 5
    with pytest.raises(AttributeError):
        report.year = 2025


def test_performance_review_empty():
    pr = WeeklyPerformanceReview(
        total_unique_picks=0, win_count=0, loss_count=0,
        win_rate_pct=None, avg_return_pct=None,
        best_pick=None, worst_pick=None,
        ai_approved_avg_return=None, ai_rejected_avg_return=None,
        all_picks=(),
    )
    assert pr.total_unique_picks == 0
    assert pr.all_picks == ()


def test_macro_summary_none_values():
    ms = WeeklyMacroSummary(
        daily_scores=(("2026-03-23", 6),),
        vix_series=(("2026-03-23", 15.5),),
        us_10y_start=4.25, us_10y_end=4.30,
        us_13w_start=None, us_13w_end=None,
        spread_start=None, spread_end=None,
        dollar_start=104.5, dollar_end=104.2,
        gold_start=None, gold_end=None,
        oil_start=None, oil_end=None,
    )
    assert ms.us_10y_start == 4.25
    assert ms.us_13w_start is None
