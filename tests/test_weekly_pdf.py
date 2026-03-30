"""주간 리포트 PDF 생성기 테스트."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
            daily_scores=(("2026-03-23", 6), ("2026-03-27", 7)),
            vix_series=(("2026-03-23", 15.0), ("2026-03-27", 14.5)),
            us_10y_start=4.25, us_10y_end=4.30,
            us_13w_start=4.50, us_13w_end=4.50,
            spread_start=-0.25, spread_end=-0.20,
            dollar_start=104.0, dollar_end=103.5,
            gold_start=2000.0, gold_end=2010.0,
            oil_start=75.0, oil_end=76.0,
        ),
        signal_trend=WeeklySignalTrend(
            daily_buy_counts=(("2026-03-23", 10), ("2026-03-27", 12)),
            daily_sell_counts=(("2026-03-23", 5), ("2026-03-27", 3)),
            most_frequent_signal="golden_cross", avg_strength_change=0.5,
        ),
        ai_accuracy=WeeklyAIAccuracy(
            approval_rate_pct=80.0, direction_accuracy_pct=65.0,
            confidence_vs_return_corr=0.45, total_reviewed=10,
        ),
        outlook=WeeklyOutlook(
            regime_strategy="강세장 지속", watchlist_sectors=("Technology",),
            avoid_sectors=(), rebalancing_suggestion="유지",
        ),
    )


def test_pdf_generation(sample_report, tmp_path):
    """PDF 생성 + 파일 존재 검증."""
    from src.reports.weekly_pdf import WeeklyReportPDF, _find_font, _FONT_PATHS

    font = _find_font(_FONT_PATHS)
    if not font:
        pytest.skip("한글 폰트 없음 — PDF 테스트 스킵")

    builder = WeeklyReportPDF(sample_report, commentary="테스트 AI 코멘터리입니다.")
    pdf_bytes = builder.build()
    assert len(pdf_bytes) > 0

    path = tmp_path / "test.pdf"
    path.write_bytes(pdf_bytes)
    assert path.exists()
    assert path.stat().st_size > 1000  # 최소 1KB


def test_pdf_no_commentary(sample_report, tmp_path):
    """코멘터리 없이도 PDF 생성 성공."""
    from src.reports.weekly_pdf import WeeklyReportPDF, _find_font, _FONT_PATHS

    font = _find_font(_FONT_PATHS)
    if not font:
        pytest.skip("한글 폰트 없음 — PDF 테스트 스킵")

    builder = WeeklyReportPDF(sample_report, commentary=None)
    pdf_bytes = builder.build()
    assert len(pdf_bytes) > 0


def test_pdf_empty_report(tmp_path):
    """빈 리포트도 에러 없이 PDF 생성."""
    from src.reports.weekly_pdf import WeeklyReportPDF, _find_font, _FONT_PATHS

    font = _find_font(_FONT_PATHS)
    if not font:
        pytest.skip("한글 폰트 없음 — PDF 테스트 스킵")

    empty = WeeklyReport(
        year=2026, week_number=99,
        week_start="", week_end="", trading_days=0,
        generated_at="2026-03-29",
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
    builder = WeeklyReportPDF(empty)
    pdf_bytes = builder.build()
    assert len(pdf_bytes) > 0


def test_find_font():
    """폰트 탐색 함수 테스트."""
    from src.reports.weekly_pdf import _find_font

    # 존재하지 않는 경로만 전달
    result = _find_font([Path("/nonexistent/font.ttf")])
    assert result is None

    # Windows에서 malgun.ttf가 있으면 찾아야 함
    import platform
    if platform.system() == "Windows":
        result = _find_font([Path("C:/Windows/Fonts/malgun.ttf")])
        # 존재할 수도, 안 할 수도 — None이 아니면 Path
        if result:
            assert result.exists()
