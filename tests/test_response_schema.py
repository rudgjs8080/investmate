"""AI 응답 스키마 테스트."""

from src.ai.response_schema import AIAnalysisResult, AIPortfolioSummary, AIStockAnalysis


class TestAIStockAnalysis:
    def test_frozen(self):
        a = AIStockAnalysis(ticker="AAPL", approved=True, confidence=8)
        assert a.ticker == "AAPL"
        assert a.approved is True
        assert a.confidence == 8
        assert a.risk_level == "MEDIUM"  # default

    def test_defaults(self):
        a = AIStockAnalysis(ticker="TEST", approved=False)
        assert a.confidence == 5
        assert a.reason == ""
        assert a.target_price is None
        assert a.key_catalysts == ()


class TestAIPortfolioSummary:
    def test_defaults(self):
        p = AIPortfolioSummary()
        assert p.market_outlook == ""
        assert p.overall_risk == "MEDIUM"


class TestAIAnalysisResult:
    def test_empty(self):
        r = AIAnalysisResult()
        assert r.stocks == ()
        assert r.raw_response == ""

    def test_with_stocks(self):
        s = AIStockAnalysis(ticker="AAPL", approved=True)
        r = AIAnalysisResult(stocks=(s,))
        assert len(r.stocks) == 1
        assert r.stocks[0].ticker == "AAPL"
