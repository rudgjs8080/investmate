"""LLM 감성 분석 모듈 테스트."""

from src.analysis.sentiment import (
    _build_sentiment_prompt,
    _parse_sentiment_response,
    analyze_sentiment_llm,
)


class TestBuildSentimentPrompt:
    def test_build_sentiment_prompt(self):
        articles = [
            {"title": "Apple beats earnings expectations"},
            {"title": "Tesla stock plunges after recall"},
        ]
        prompt = _build_sentiment_prompt(articles)
        assert "1. Apple beats earnings expectations" in prompt
        assert "2. Tesla stock plunges after recall" in prompt
        assert "sentiment" in prompt
        assert "JSON" in prompt


class TestParseSentimentResponse:
    def test_parse_sentiment_response_valid(self):
        response = '[{"index": 1, "sentiment": 0.8, "reason": "good"}, {"index": 2, "sentiment": -0.5, "reason": "bad"}]'
        articles = [{"title": "Good news"}, {"title": "Bad news"}]
        results = _parse_sentiment_response(response, articles)
        assert len(results) == 2
        assert results[0]["sentiment"] == 0.8
        assert results[0]["title"] == "Good news"
        assert results[1]["sentiment"] == -0.5

    def test_parse_sentiment_response_invalid(self):
        response = "This is not JSON at all"
        articles = [{"title": "Something"}]
        results = _parse_sentiment_response(response, articles)
        assert results == []

    def test_parse_sentiment_response_clamps_values(self):
        response = '[{"index": 1, "sentiment": 5.0, "reason": "extreme"}]'
        articles = [{"title": "Test"}]
        results = _parse_sentiment_response(response, articles)
        assert len(results) == 1
        assert results[0]["sentiment"] == 1.0  # clamped to max


class TestAnalyzeSentimentLLM:
    def test_analyze_sentiment_empty_articles(self):
        result = analyze_sentiment_llm([])
        assert result == []
