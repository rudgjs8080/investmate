"""AI 캐시 테스트."""

from datetime import date

from src.ai.cache import get_cache_key, get_cached_response, save_cached_response


class TestCacheKey:
    def test_consistent_hash(self):
        """동일 프롬프트 → 동일 해시."""
        assert get_cache_key("test prompt") == get_cache_key("test prompt")

    def test_different_hash(self):
        """다른 프롬프트 → 다른 해시."""
        assert get_cache_key("prompt A") != get_cache_key("prompt B")

    def test_hash_length(self):
        assert len(get_cache_key("test")) == 16


class TestCacheReadWrite:
    def test_miss_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.ai.cache.CACHE_DIR", tmp_path / "cache")
        assert get_cached_response(date(2026, 3, 20), "test") is None

    def test_save_and_load(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.ai.cache.CACHE_DIR", tmp_path / "cache")
        save_cached_response(date(2026, 3, 20), "test prompt", "AI response text")
        result = get_cached_response(date(2026, 3, 20), "test prompt")
        assert result == "AI response text"

    def test_different_date_no_hit(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.ai.cache.CACHE_DIR", tmp_path / "cache")
        save_cached_response(date(2026, 3, 20), "test", "response")
        result = get_cached_response(date(2026, 3, 21), "test")
        assert result is None
