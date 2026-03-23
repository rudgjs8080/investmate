"""외부 요인 분석 테스트."""

from __future__ import annotations

from datetime import date

from src.analysis.external import (
    analyze_macro,
    analyze_news_sentiment,
    calculate_sector_momentum,
)
from src.data.schemas import MacroData, NewsArticleData


class TestAnalyzeMacro:
    def test_stable_market(self):
        macro = MacroData(date=date.today(), vix=15.0, sp500_close=5100.0, sp500_sma20=5000.0, us_10y_yield=3.5, dollar_index=100.0)
        score = analyze_macro(macro)
        assert score >= 7

    def test_volatile_market(self):
        macro = MacroData(date=date.today(), vix=35.0, sp500_close=4800.0, sp500_sma20=5000.0, us_10y_yield=5.5, dollar_index=105.0)
        score = analyze_macro(macro)
        assert score <= 4

    def test_neutral_market(self):
        macro = MacroData(date=date.today())
        score = analyze_macro(macro)
        assert score == 5

    def test_high_yield_penalty(self):
        macro = MacroData(date=date.today(), us_10y_yield=5.5)
        score = analyze_macro(macro)
        assert score <= 5

    def test_strong_dollar_penalty(self):
        """달러 인덱스 > 105 → 감점."""
        macro = MacroData(date=date.today(), vix=18.0, sp500_close=5000.0, sp500_sma20=4900.0, us_10y_yield=4.0, dollar_index=108.0)
        score = analyze_macro(macro)
        score_no_dollar = analyze_macro(MacroData(date=date.today(), vix=18.0, sp500_close=5000.0, sp500_sma20=4900.0, us_10y_yield=4.0, dollar_index=100.0))
        assert score < score_no_dollar

    def test_weak_dollar_bonus(self):
        """달러 인덱스 < 95 → 가점."""
        macro = MacroData(date=date.today(), vix=18.0, sp500_close=5000.0, sp500_sma20=4900.0, us_10y_yield=4.0, dollar_index=92.0)
        score = analyze_macro(macro)
        score_neutral = analyze_macro(MacroData(date=date.today(), vix=18.0, sp500_close=5000.0, sp500_sma20=4900.0, us_10y_yield=4.0, dollar_index=100.0))
        assert score > score_neutral

    def test_incomplete_macro_returns_neutral(self):
        """유효 지표 <3이면 중립 5."""
        macro = MacroData(date=date.today(), vix=15.0)
        assert analyze_macro(macro) == 5


class TestNewsSentiment:
    def test_positive_news(self):
        from datetime import datetime
        articles = [
            NewsArticleData(
                title="Market rally continues with strong gains",
                url="https://ex.com/1", source="Test",
                published_at=datetime.now(),
            ),
        ]
        score = analyze_news_sentiment(articles)
        assert score > 0

    def test_negative_news(self):
        from datetime import datetime
        articles = [
            NewsArticleData(
                title="Market crash fears as recession looms",
                url="https://ex.com/2", source="Test",
                published_at=datetime.now(),
            ),
        ]
        score = analyze_news_sentiment(articles)
        assert score < 0

    def test_empty_news(self):
        assert analyze_news_sentiment([]) == 0.0

    def test_word_boundary_no_false_positive(self):
        """'bulletin' → 'bull' 미감지 (단어 경계)."""
        from datetime import datetime
        articles = [
            NewsArticleData(
                title="Company bulletin released",
                url="https://ex.com/3", source="Test",
                published_at=datetime.now(),
            ),
        ]
        score = analyze_news_sentiment(articles)
        assert score == 0.0  # "bulletin" is NOT "bull"


class TestSectorMomentum:
    def test_momentum_scores(self):
        returns = {"Tech": 5.0, "Health": -2.0, "Energy": 10.0}
        scores = calculate_sector_momentum(returns)
        assert scores["Energy"] > scores["Tech"] > scores["Health"]

    def test_empty_returns(self):
        assert calculate_sector_momentum({}) == {}

    def test_all_equal_returns(self):
        """모든 섹터 수익률 동일 → 전부 5.0."""
        result = calculate_sector_momentum({"Tech": 2.5, "Finance": 2.5, "Energy": 2.5})
        for v in result.values():
            assert v == 5.0

    def test_flat_market_dampening(self):
        """spread < 1% → 점수가 정규화 대비 5.0 방향 압축."""
        normal = calculate_sector_momentum({"Tech": 1.0, "Finance": 10.0})  # spread 9 → 정상
        flat = calculate_sector_momentum({"Tech": 1.0, "Finance": 1.5})  # spread 0.5 → 감쇄
        # flat에서 Tech-Finance 차이가 normal보다 작아야 함
        normal_diff = abs(normal["Finance"] - normal["Tech"])
        flat_diff = abs(flat["Finance"] - flat["Tech"])
        assert flat_diff < normal_diff
