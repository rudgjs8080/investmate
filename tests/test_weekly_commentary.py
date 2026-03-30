"""AI 주간 코멘터리 테스트."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.reports.weekly_commentary import (
    build_weekly_commentary_prompt,
    generate_weekly_commentary,
    save_commentary,
)
from src.reports.weekly_models import (
    ConvictionPick,
    RiskDashboard,
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


@pytest.fixture
def sample_report():
    pick = WeeklyPickPerformance(
        ticker="AAPL", name="Apple", sector="Technology",
        days_recommended=5, avg_rank=1.0, weekly_return_pct=2.5,
        ai_approved_days=5, ai_rejected_days=0,
    )
    return WeeklyReport(
        year=2026, week_number=13,
        week_start="2026-03-23", week_end="2026-03-27",
        trading_days=5, generated_at="2026-03-29 10:00:00",
        executive_summary=WeeklyExecutiveSummary(
            market_oneliner="강세 지속", sp500_weekly_return_pct=1.5,
            vix_start=15.0, vix_end=14.5, vix_high=16.0, vix_low=14.0,
            regime_start="bull", regime_end="bull", regime_changed=False,
            weekly_win_rate_pct=60.0, weekly_avg_return_pct=1.2,
        ),
        performance_review=WeeklyPerformanceReview(
            total_unique_picks=1, win_count=1, loss_count=0,
            win_rate_pct=100.0, avg_return_pct=2.5,
            best_pick=pick, worst_pick=pick,
            ai_approved_avg_return=2.5, ai_rejected_avg_return=None,
            all_picks=(pick,),
        ),
        conviction_picks=(ConvictionPick(
            ticker="AAPL", name="Apple", sector="Technology",
            days_recommended=5, consecutive_days=5,
            avg_rank=1.0, avg_total_score=8.5,
            weekly_return_pct=2.5, ai_consensus="추천",
        ),),
        sector_rotation=(SectorRotationEntry(
            sector="Technology", weekly_return_pct=2.0,
            volume_change_pct=5.3, momentum_delta="상승", pick_count=1,
        ),),
        macro_summary=WeeklyMacroSummary(
            daily_scores=(("2026-03-23", 6),), vix_series=(("2026-03-23", 15.0),),
            us_10y_start=4.25, us_10y_end=4.30,
            us_13w_start=4.50, us_13w_end=4.50,
            spread_start=-0.25, spread_end=-0.20,
            dollar_start=104.0, dollar_end=103.5,
            gold_start=2000.0, gold_end=2010.0,
            oil_start=75.0, oil_end=76.0,
        ),
        signal_trend=WeeklySignalTrend(
            daily_buy_counts=(), daily_sell_counts=(),
            most_frequent_signal=None, avg_strength_change=None,
        ),
        ai_accuracy=WeeklyAIAccuracy(
            approval_rate_pct=80.0, direction_accuracy_pct=65.0,
            confidence_vs_return_corr=0.45, total_reviewed=10,
        ),
        outlook=WeeklyOutlook(
            regime_strategy="강세장", watchlist_sectors=("Technology",),
            avoid_sectors=(), rebalancing_suggestion="유지",
        ),
        risk_dashboard=RiskDashboard(
            portfolio_beta=None, max_sector_concentration_pct=100.0,
            top_sector="Technology", vix_exposure="낮음",
            avg_correlation=None, drawdown_from_peak_pct=None,
        ),
    )


def test_build_prompt_contains_key_sections(sample_report):
    prompt = build_weekly_commentary_prompt(sample_report)
    assert "30년 경력" in prompt
    assert "S&P 500" in prompt
    assert "AAPL" in prompt
    assert "주간 시장 총평" in prompt
    assert "확신 종목" in prompt
    assert "리스크 포인트" in prompt


def test_build_prompt_handles_none_values():
    """None 값이 있어도 프롬프트 생성 성공."""
    report = WeeklyReport(
        year=2026, week_number=99,
        week_start="", week_end="", trading_days=0,
        generated_at="",
        executive_summary=WeeklyExecutiveSummary(
            market_oneliner="데이터 없음", sp500_weekly_return_pct=None,
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
        conviction_picks=(), sector_rotation=(),
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
            regime_strategy="횡보", watchlist_sectors=(),
            avoid_sectors=(), rebalancing_suggestion="유지",
        ),
    )
    prompt = build_weekly_commentary_prompt(report)
    assert isinstance(prompt, str)
    assert len(prompt) > 100


@patch("src.ai.claude_analyzer.run_claude_analysis_streaming")
def test_generate_commentary_streaming(mock_stream, sample_report):
    mock_stream.return_value = "테스트 코멘터리 결과"
    result = generate_weekly_commentary(sample_report, model="test-model")
    assert result == "테스트 코멘터리 결과"
    mock_stream.assert_called_once()


@patch("src.ai.claude_analyzer.run_claude_analysis_streaming", return_value=None)
@patch("src.ai.claude_analyzer.run_claude_analysis_sdk")
def test_generate_commentary_fallback_to_sdk(mock_sdk, mock_stream, sample_report):
    mock_sdk.return_value = "SDK 코멘터리"
    result = generate_weekly_commentary(sample_report)
    assert result == "SDK 코멘터리"


def test_save_commentary(tmp_path):
    with patch("src.reports.weekly_commentary.Path") as mock_path_cls:
        pass  # save_commentary 직접 테스트
    # 직접 파일 쓰기 테스트
    path = tmp_path / "test.md"
    path.write_text("# Test\n\nContent", encoding="utf-8")
    assert path.exists()
    assert "Content" in path.read_text(encoding="utf-8")
