"""데일리 리포트 생성 테스트."""

import json
from datetime import date
from pathlib import Path

import pytest

from src.reports.daily_report import _save_json, _save_markdown, _log_report_quality
from src.reports.report_models import (
    EnrichedDailyReport,
    FundamentalDetail,
    MacroEnvironment,
    SignalDetail,
    StockRecommendationDetail,
    TechnicalDetail,
    NewsItem,
    EarningsDetail,
)


def _make_report(**overrides) -> EnrichedDailyReport:
    rec = StockRecommendationDetail(
        rank=1, ticker="AAPL", name="Apple", sector="Technology",
        price=180.0, price_change_pct=1.5, total_score=7.2,
        technical_score=7.0, fundamental_score=7.5,
        external_score=5.0, momentum_score=8.0,
        recommendation_reason="AAPL: 상승추세, 우수 재무",
        technical=TechnicalDetail(
            rsi=45.0, rsi_status="중립",
            macd=2.5, macd_hist=0.5, macd_status="상승",
            sma_5=181.0, sma_20=178.0, sma_60=170.0,
            sma_alignment="정배열",
            bb_upper=190.0, bb_middle=178.0, bb_lower=166.0,
            bb_position="중단",
            stoch_k=65.0, stoch_d=60.0,
            volume_ratio=1.2,
            signals=(
                SignalDetail(signal_type="macd_bullish", direction="BUY", strength=7, description="test"),
            ),
        ),
        fundamental=FundamentalDetail(
            per=18.0, per_score=7.0, roe=0.25, roe_score=8.0,
            debt_ratio=0.3, debt_score=7.0,
            composite_score=7.5, summary="우수",
            market_cap=3e12,
        ),
        news=(
            NewsItem(title="Apple beats earnings", source="Reuters", published_at="2026-03-19", sentiment_score=0.5),
        ),
        earnings=EarningsDetail(latest_period="2025Q4", eps_surprise_pct=5.2, beat_streak=4),
        risk_factors=("특별한 리스크 요인 없음",),
    )

    defaults = dict(
        run_date=date(2026, 3, 19),
        total_stocks_analyzed=503,
        stocks_passed_filter=10,
        pipeline_duration_sec=600.0,
        macro=MacroEnvironment(
            market_score=7, mood="강세", vix=15.0, vix_status="안정",
            sp500_close=5500.0, sp500_sma20=5400.0, sp500_trend="상승",
            us_10y_yield=4.0, us_13w_yield=3.5, dollar_index=99.0,
            yield_spread=0.5,
        ),
        recommendations=(rec,),
    )
    defaults.update(overrides)
    return EnrichedDailyReport(**defaults)


class TestSaveJson:
    def test_creates_valid_json(self, tmp_path):
        report = _make_report()
        path = tmp_path / "test.json"
        _save_json(report, path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["run_date"] == "2026-03-19"
        assert len(data["recommendations"]) == 1
        assert data["recommendations"][0]["ticker"] == "AAPL"
        assert data["disclaimer"]

    def test_json_has_technical_data(self, tmp_path):
        report = _make_report()
        path = tmp_path / "test.json"
        _save_json(report, path)
        data = json.loads(path.read_text(encoding="utf-8"))
        tech = data["recommendations"][0]["technical"]
        assert tech["rsi"] == 45.0
        assert tech["macd_status"] == "상승"


class TestSaveMarkdown:
    def test_creates_markdown(self, tmp_path):
        report = _make_report()
        path = tmp_path / "test.md"
        _save_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "핵심 요약" in content
        assert "AAPL" in content

    def test_markdown_has_inverted_pyramid(self, tmp_path):
        report = _make_report()
        path = tmp_path / "test.md"
        _save_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        # 핵심 요약이 종목 상세보다 먼저 나와야 함
        summary_pos = content.find("핵심 요약")
        detail_pos = content.find("추천 종목 상세")
        assert summary_pos < detail_pos

    def test_markdown_has_beginner_explanation(self, tmp_path):
        report = _make_report()
        path = tmp_path / "test.md"
        _save_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "왜 추천하나요?" in content
        assert "숫자로 보면" in content
        assert "주의할 점" in content

    def test_markdown_has_news(self, tmp_path):
        report = _make_report()
        path = tmp_path / "test.md"
        _save_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "관련 뉴스" in content
        assert "Apple beats earnings" in content

    def test_markdown_has_details_tag(self, tmp_path):
        report = _make_report()
        path = tmp_path / "test.md"
        _save_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "<details>" in content
        assert "상세 데이터 펼치기" in content

    def test_markdown_has_disclaimer(self, tmp_path):
        report = _make_report()
        path = tmp_path / "test.md"
        _save_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "투자 참고용" in content

    def test_markdown_has_market_section(self, tmp_path):
        report = _make_report()
        path = tmp_path / "test.md"
        _save_markdown(report, path)
        content = path.read_text(encoding="utf-8")
        assert "시장 환경 상세" in content
        assert "VIX" in content
