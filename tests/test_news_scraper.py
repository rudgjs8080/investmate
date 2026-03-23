"""뉴스 스크래퍼 테스트."""

from unittest.mock import MagicMock, patch

from src.data.news_scraper import scrape_news, scrape_market_news


def _mock_news_v2():
    return [
        {
            "content": {
                "title": "Test News Title",
                "summary": "Test summary",
                "pubDate": "2026-03-19T10:00:00Z",
                "canonicalUrl": {"url": "https://example.com/1"},
                "provider": {"displayName": "TestSource"},
            }
        },
        {
            "content": {
                "title": "Another News",
                "summary": None,
                "pubDate": "2026-03-19T09:00:00Z",
                "canonicalUrl": {"url": "https://example.com/2"},
                "provider": {"displayName": "OtherSource"},
            }
        },
    ]


class TestScrapeNews:
    @patch("src.data.news_scraper.yf")
    def test_parses_v2_format(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.news = _mock_news_v2()
        mock_yf.Ticker.return_value = mock_ticker

        articles = scrape_news("AAPL", count=5)
        assert len(articles) == 2
        assert articles[0].title == "Test News Title"
        assert articles[0].source == "TestSource"
        assert articles[0].url == "https://example.com/1"

    @patch("src.data.news_scraper.yf")
    def test_deduplicates_urls(self, mock_yf):
        dup_news = [
            {"content": {"title": "A", "canonicalUrl": {"url": "https://dup.com"}, "provider": {"displayName": "S"}, "pubDate": "2026-03-20T10:00:00Z"}},
            {"content": {"title": "B", "canonicalUrl": {"url": "https://dup.com"}, "provider": {"displayName": "S"}, "pubDate": "2026-03-20T11:00:00Z"}},
        ]
        mock_ticker = MagicMock()
        mock_ticker.news = dup_news
        mock_yf.Ticker.return_value = mock_ticker

        articles = scrape_news("AAPL")
        assert len(articles) == 1

    @patch("src.data.news_scraper.yf")
    def test_empty_news(self, mock_yf):
        mock_ticker = MagicMock()
        mock_ticker.news = []
        mock_yf.Ticker.return_value = mock_ticker

        articles = scrape_news("AAPL")
        assert articles == []

    @patch("src.data.news_scraper.yf")
    def test_handles_exception(self, mock_yf):
        mock_yf.Ticker.side_effect = Exception("API error")
        articles = scrape_news("AAPL")
        assert articles == []


class TestScrapeMarketNews:
    @patch("src.data.news_scraper.scrape_news")
    def test_tries_gspc_first(self, mock_scrape):
        mock_scrape.return_value = [MagicMock()]
        result = scrape_market_news(count=5)
        mock_scrape.assert_called_once_with("^GSPC", count=5)
        assert len(result) == 1

    @patch("src.data.news_scraper.scrape_news")
    def test_fallback_to_spy(self, mock_scrape):
        mock_scrape.side_effect = [[], [MagicMock()]]
        result = scrape_market_news(count=5)
        assert mock_scrape.call_count == 2
        assert len(result) == 1
