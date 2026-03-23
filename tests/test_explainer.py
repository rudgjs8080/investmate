"""초보자 친화 설명 생성기 테스트."""

from src.reports.explainer import (
    BeginnerExplanation,
    explain_stock,
    market_investment_opinion,
    summarize_market,
    summarize_recommendations_oneliner,
)
from src.reports.report_models import (
    FundamentalDetail,
    MacroEnvironment,
    SignalDetail,
    StockRecommendationDetail,
    TechnicalDetail,
)


def _make_rec(**overrides) -> StockRecommendationDetail:
    """테스트용 추천 종목 생성."""
    defaults = dict(
        rank=1, ticker="AAPL", name="Apple", sector="Technology",
        price=180.0, price_change_pct=1.5, total_score=7.0,
        technical_score=7.0, fundamental_score=7.0,
        external_score=5.0, momentum_score=8.0,
        recommendation_reason="테스트 추천",
    )
    defaults.update(overrides)
    return StockRecommendationDetail(**defaults)


class TestExplainStock:
    def test_returns_beginner_explanation(self):
        rec = _make_rec(
            technical=TechnicalDetail(
                rsi=31.0, rsi_status="과매도",
                macd_status="상승", sma_alignment="정배열",
            ),
            fundamental=FundamentalDetail(per=14.0, roe=0.2, composite_score=7.0, summary="우수"),
        )
        result = explain_stock(rec)
        assert isinstance(result, BeginnerExplanation)
        assert result.headline
        assert result.why_recommended
        assert result.numbers_backing
        assert result.risk_simple

    def test_headline_includes_trend(self):
        rec = _make_rec(
            technical=TechnicalDetail(sma_alignment="정배열"),
        )
        result = explain_stock(rec)
        assert "상승 추세" in result.headline

    def test_headline_includes_oversold(self):
        rec = _make_rec(
            technical=TechnicalDetail(rsi=25.0, rsi_status="과매도"),
        )
        result = explain_stock(rec)
        assert "과매도" in result.headline

    def test_why_explains_rsi_low(self):
        rec = _make_rec(
            technical=TechnicalDetail(rsi=28.0, rsi_status="과매도"),
        )
        result = explain_stock(rec)
        assert "반등" in result.why_recommended

    def test_why_explains_per(self):
        rec = _make_rec(
            fundamental=FundamentalDetail(per=12.0),
        )
        result = explain_stock(rec)
        assert "저렴" in result.why_recommended or "PER" in result.why_recommended

    def test_why_explains_roe(self):
        rec = _make_rec(
            fundamental=FundamentalDetail(roe=0.2),
        )
        result = explain_stock(rec)
        assert "ROE" in result.why_recommended or "효율" in result.why_recommended

    def test_numbers_backing_contains_key_metrics(self):
        rec = _make_rec(
            technical=TechnicalDetail(rsi=50.0, macd_status="상승", sma_alignment="정배열"),
            fundamental=FundamentalDetail(per=18.0, roe=0.15, debt_ratio=0.3),
        )
        result = explain_stock(rec)
        assert "RSI" in result.numbers_backing
        assert "PER" in result.numbers_backing
        assert "종합점수" in result.numbers_backing

    def test_risk_warns_high_rsi(self):
        rec = _make_rec(
            technical=TechnicalDetail(rsi=72.0),
        )
        result = explain_stock(rec)
        assert "높은 편" in result.risk_simple or "조정" in result.risk_simple

    def test_risk_warns_sell_signals(self):
        rec = _make_rec(
            technical=TechnicalDetail(
                signals=(
                    SignalDetail(signal_type="rsi_overbought", direction="SELL", strength=5, description="test"),
                ),
            ),
        )
        result = explain_stock(rec)
        assert "매도 시그널" in result.risk_simple

    def test_risk_default_message(self):
        rec = _make_rec(
            technical=TechnicalDetail(rsi=50.0, sma_alignment="정배열"),
            fundamental=FundamentalDetail(per=15.0, debt_ratio=0.2),
        )
        result = explain_stock(rec)
        assert "분산 투자" in result.risk_simple or "위험 신호" in result.risk_simple


class TestAIRiskExplanation:
    """AI 리스크 관련 설명 테스트."""

    def test_ai_excluded_shows_in_risk(self):
        """AI 제외 종목은 리스크에 표시."""
        rec = StockRecommendationDetail(
            rank=1, ticker="TEST", name="Test", sector="Tech",
            price=100.0, total_score=6.0,
            technical=TechnicalDetail(rsi=50.0, rsi_status="중립"),
            fundamental=FundamentalDetail(per=15.0, debt_ratio=0.3),
            ai_approved=False, ai_reason="과대평가",
        )
        result = explain_stock(rec)
        assert "제외" in result.risk_simple or "과대평가" in result.risk_simple

    def test_ai_high_risk_shows_warning(self):
        """AI 리스크 HIGH 경고."""
        rec = StockRecommendationDetail(
            rank=1, ticker="TEST", name="Test", sector="Tech",
            price=100.0, total_score=6.0,
            technical=TechnicalDetail(rsi=50.0, rsi_status="중립"),
            fundamental=FundamentalDetail(per=15.0, debt_ratio=0.3),
            ai_approved=True, ai_risk_level="HIGH",
        )
        result = explain_stock(rec)
        assert "높음" in result.risk_simple or "소액" in result.risk_simple


class TestSummarizeMarket:
    def test_bearish_market(self):
        macro = MacroEnvironment(mood="약세", vix=26.0, sp500_trend="하락")
        result = summarize_market(macro)
        assert "불안" in result
        assert "경계" in result

    def test_bullish_market(self):
        macro = MacroEnvironment(mood="강세", vix=15.0, sp500_trend="상승")
        result = summarize_market(macro)
        assert "좋습니다" in result

    def test_high_vix(self):
        macro = MacroEnvironment(mood="약세", vix=35.0)
        result = summarize_market(macro)
        assert "불안감" in result


class TestMarketInvestmentOpinion:
    def test_bullish(self):
        macro = MacroEnvironment(market_score=8, mood="강세")
        result = market_investment_opinion(macro, 10)
        assert "분할 매수" in result

    def test_neutral(self):
        macro = MacroEnvironment(market_score=5, mood="중립")
        result = market_investment_opinion(macro, 10)
        assert "신중" in result

    def test_bearish(self):
        macro = MacroEnvironment(market_score=2, mood="약세")
        result = market_investment_opinion(macro, 10)
        assert "현금 비중" in result


class TestSummarizeRecommendations:
    def test_empty_recs(self):
        result = summarize_recommendations_oneliner(())
        assert "없습니다" in result

    def test_formats_tickers(self):
        recs = (_make_rec(ticker="AAPL"), _make_rec(ticker="MSFT", rank=2))
        result = summarize_recommendations_oneliner(recs)
        assert "AAPL" in result
        assert "MSFT" in result
