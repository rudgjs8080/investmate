"""터미널 출력 테스트 -- render 함수들이 에러 없이 실행되는지 확인."""

from datetime import date
from io import StringIO
from unittest.mock import patch

from rich.console import Console

from src.reports.report_models import (
    EnrichedDailyReport,
    FundamentalDetail,
    MacroEnvironment,
    SignalDetail,
    SmartMoneyDetail,
    StockRecommendationDetail,
    TechnicalDetail,
    EarningsDetail,
    SignalSummaryItem,
)
from src.reports.terminal import render_daily_report


def _make_report():
    rec = StockRecommendationDetail(
        rank=1, ticker="AAPL", name="Apple", sector="Technology",
        price=180.0, price_change_pct=1.5, total_score=7.0,
        technical_score=7.0, fundamental_score=7.0,
        external_score=5.0, momentum_score=8.0,
        recommendation_reason="test",
        technical=TechnicalDetail(
            rsi=45.0, macd=2.0, macd_status="상승", sma_alignment="정배열",
            volume_ratio=1.2,
            signals=(SignalDetail(signal_type="macd_bullish", direction="BUY", strength=7, description="test"),),
        ),
        fundamental=FundamentalDetail(per=15.0, composite_score=7.0, summary="우수"),
        smart_money=SmartMoneyDetail(analyst_strong_buy=5, analyst_buy=10, analyst_hold=3),
        earnings=EarningsDetail(latest_period="2025Q4", eps_surprise_pct=5.0, beat_streak=3),
        risk_factors=("테스트 리스크",),
    )
    return EnrichedDailyReport(
        run_date=date(2026, 3, 19),
        total_stocks_analyzed=503,
        stocks_passed_filter=10,
        pipeline_duration_sec=600.0,
        macro=MacroEnvironment(
            market_score=5, mood="중립", vix=18.0, vix_status="안정",
            sp500_close=5500.0, sp500_sma20=5400.0, sp500_trend="상승",
        ),
        recommendations=(rec,),
        all_signals=(
            SignalSummaryItem(ticker="AAPL", name="Apple", signal_type="macd_bullish", direction="BUY", strength=7, description="test"),
        ),
        buy_signal_count=1,
        sell_signal_count=0,
    )


class TestRenderDailyReport:
    def test_no_error(self):
        """render_daily_report가 에러 없이 실행되는지 확인."""
        report = _make_report()
        # Rich Console을 StringIO로 리다이렉트하여 실제 출력 안 함
        with patch("src.reports.terminal.console", Console(file=StringIO(), force_terminal=True)):
            render_daily_report(report)

    def test_empty_recommendations(self):
        report = EnrichedDailyReport(
            run_date=date(2026, 3, 19),
            macro=MacroEnvironment(),
        )
        with patch("src.reports.terminal.console", Console(file=StringIO(), force_terminal=True)):
            render_daily_report(report)
