"""AI 시스템 공용 상수 — 모델명, VIX 임계값, 비티커 필터 등.

시스템 전체에서 동일한 값을 사용하도록 여기에만 정의한다.
"""

from __future__ import annotations


def get_analysis_model() -> str:
    """분석용 AI 모델명을 반환한다. config에서 읽되 import 순환 방지를 위해 lazy."""
    from src.config import get_settings
    return get_settings().ai_model_analysis


def get_chat_model() -> str:
    """채팅용 AI 모델명을 반환한다."""
    from src.config import get_settings
    return get_settings().ai_model_chat


# ---------------------------------------------------------------------------
# VIX 기반 시장 레짐 임계값
# ---------------------------------------------------------------------------
VIX_CRISIS: float = 30.0
VIX_HIGH_VOL: float = 25.0
VIX_NORMAL: float = 20.0

# ---------------------------------------------------------------------------
# 레짐별 최대 추천 수
# ---------------------------------------------------------------------------
MAX_RECS_BY_REGIME: dict[str, int] = {
    "crisis": 3,
    "bear": 5,
    "range": 7,
    "bull": 10,
}

# ---------------------------------------------------------------------------
# AI 응답 파싱 시 비티커 필터
# ---------------------------------------------------------------------------
NON_TICKERS: frozenset[str] = frozenset({
    "TOP", "BUY", "SELL", "HOLD", "NYSE", "NASDAQ", "S&P",
    "ETF", "USD", "AI", "CEO", "IPO", "EPS", "PE", "RSI",
    "MACD", "SMA", "EMA", "ATR", "VIX", "GDP", "CPI",
    "FOMC", "SEC", "FCF", "ROE", "PER", "PBR", "VOL",
    "YOY", "QOQ", "YTD", "Q1", "Q2", "Q3", "Q4",
    "LOW", "HIGH", "RISK", "STOP", "USA", "FED",
    "BULL", "BEAR", "PUT", "CALL", "OTC", "IPO",
    "ADD", "TRIM", "EXIT",
})
