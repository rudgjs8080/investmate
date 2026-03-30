"""주간 확신 종목 초보자 설명 테스트."""

from __future__ import annotations

import pytest

from src.reports.weekly_explainer import (
    WeeklyBeginnerExplanation,
    explain_conviction_pick,
)
from src.reports.weekly_models import ConvictionPick, ConvictionTechnical


@pytest.fixture
def pick():
    return ConvictionPick(
        ticker="AAPL", name="Apple", sector="Technology",
        days_recommended=5, consecutive_days=5,
        avg_rank=1.0, avg_total_score=8.5,
        weekly_return_pct=2.5, ai_consensus="추천",
    )


@pytest.fixture
def tech():
    return ConvictionTechnical(
        ticker="AAPL", name="Apple",
        rsi_14=45.0, macd_signal="매수",
        sma_alignment="정배열", bb_position="중간",
        support_price=150.0, resistance_price=170.0,
    )


def test_explain_with_tech(pick, tech):
    result = explain_conviction_pick(pick, tech)
    assert isinstance(result, WeeklyBeginnerExplanation)
    assert result.ticker == "AAPL"
    assert "5일" in result.headline or "연속" in result.headline
    assert "추천" in result.why_recommended
    assert "RSI" in result.technical_summary
    assert "정배열" in result.technical_summary


def test_explain_without_tech(pick):
    result = explain_conviction_pick(pick, None)
    assert result.technical_summary == "기술적 데이터 없음"
    assert "분산 투자" in result.risk_simple


def test_explain_bearish_pick():
    pick = ConvictionPick(
        ticker="MSFT", name="Microsoft", sector="Technology",
        days_recommended=3, consecutive_days=2,
        avg_rank=5.0, avg_total_score=5.5,
        weekly_return_pct=-3.0, ai_consensus="제외",
    )
    tech = ConvictionTechnical(
        ticker="MSFT", name="Microsoft",
        rsi_14=75.0, macd_signal="매도",
        sma_alignment="역배열", bb_position="상단",
        support_price=None, resistance_price=None,
    )
    result = explain_conviction_pick(pick, tech)
    assert "과매수" in result.headline or "주의" in result.headline
    assert "제외" in result.why_recommended or "AI" in result.why_recommended
    assert "과매수" in result.technical_summary
    assert len(result.risk_simple) > 10  # 리스크가 있어야 함


def test_explain_oversold():
    pick = ConvictionPick(
        ticker="NVDA", name="NVIDIA", sector="Technology",
        days_recommended=4, consecutive_days=4,
        avg_rank=2.0, avg_total_score=7.0,
        weekly_return_pct=None, ai_consensus="혼재",
    )
    tech = ConvictionTechnical(
        ticker="NVDA", name="NVIDIA",
        rsi_14=25.0, macd_signal="매수",
        sma_alignment="혼조", bb_position="하단",
        support_price=100.0, resistance_price=120.0,
    )
    result = explain_conviction_pick(pick, tech)
    assert "과매도" in result.headline or "과매도" in result.technical_summary
    assert "혼재" in result.why_recommended or "엇갈" in result.risk_simple
