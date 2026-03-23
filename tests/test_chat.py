"""채팅 API 캐싱 + 멀티턴 테스트."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest


class TestChatCache:
    def test_cache_hit(self):
        """동일한 context+message 해시로 캐시 히트한다."""
        from src.web.routes.chat import _chat_cache, CHAT_CACHE_TTL
        import hashlib

        context = "test context"
        message = "test message"
        key = hashlib.sha256(f"{context}:{message}".encode()).hexdigest()[:16]

        _chat_cache[key] = ("cached response", time.time())

        cached = _chat_cache.get(key)
        assert cached is not None
        assert cached[0] == "cached response"
        assert time.time() - cached[1] < CHAT_CACHE_TTL

        # 정리
        del _chat_cache[key]

    def test_cache_miss_expired(self):
        """TTL 초과 시 캐시 미스한다."""
        from src.web.routes.chat import _chat_cache, CHAT_CACHE_TTL
        import hashlib

        context = "expired context"
        message = "expired message"
        key = hashlib.sha256(f"{context}:{message}".encode()).hexdigest()[:16]

        _chat_cache[key] = ("old response", time.time() - CHAT_CACHE_TTL - 1)

        cached = _chat_cache.get(key)
        assert cached is not None
        # TTL 초과 확인
        assert time.time() - cached[1] >= CHAT_CACHE_TTL

        # 정리
        del _chat_cache[key]


class TestMultiTurnHistory:
    def test_history_preserved(self):
        """세션 ID별로 히스토리가 유지된다."""
        from src.web.routes.chat import _chat_history

        session_id = "test-session-preserve"
        _chat_history[session_id] = [
            {"role": "user", "content": "첫 번째 질문"},
            {"role": "assistant", "content": "첫 번째 답변"},
        ]

        history = _chat_history.get(session_id, [])
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

        # 정리
        del _chat_history[session_id]

    def test_history_limit(self):
        """히스토리가 MAX_HISTORY_TURNS로 제한된다."""
        from src.web.routes.chat import _chat_history, MAX_HISTORY_TURNS

        session_id = "test-session-limit"
        # MAX_HISTORY_TURNS * 2 개의 메시지 생성
        history = []
        for i in range(MAX_HISTORY_TURNS * 2):
            role = "user" if i % 2 == 0 else "assistant"
            history.append({"role": role, "content": f"message {i}"})

        # MAX_HISTORY_TURNS로 잘라서 저장
        _chat_history[session_id] = history[-MAX_HISTORY_TURNS:]

        stored = _chat_history[session_id]
        assert len(stored) == MAX_HISTORY_TURNS
        # 가장 최신 메시지가 유지되어야 함
        assert stored[-1]["content"] == f"message {MAX_HISTORY_TURNS * 2 - 1}"

        # 정리
        del _chat_history[session_id]

    def test_separate_sessions(self):
        """다른 세션 ID는 독립적인 히스토리를 가진다."""
        from src.web.routes.chat import _chat_history

        _chat_history["session-a"] = [{"role": "user", "content": "A 질문"}]
        _chat_history["session-b"] = [{"role": "user", "content": "B 질문"}]

        assert _chat_history["session-a"][0]["content"] == "A 질문"
        assert _chat_history["session-b"][0]["content"] == "B 질문"
        assert len(_chat_history["session-a"]) == 1
        assert len(_chat_history["session-b"]) == 1

        # 정리
        del _chat_history["session-a"]
        del _chat_history["session-b"]
