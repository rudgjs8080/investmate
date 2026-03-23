"""AI 분석 응답 스키마 — 구조화된 AI 출력 모델."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AIStockAnalysis:
    """개별 종목 AI 분석 결과."""

    ticker: str
    approved: bool
    confidence: int = 5  # 1-10
    reason: str = ""
    risk_level: str = "MEDIUM"  # LOW / MEDIUM / HIGH
    target_price: float | None = None
    stop_loss: float | None = None
    entry_strategy: str = ""
    exit_strategy: str = ""
    key_catalysts: tuple[str, ...] = ()
    key_risks: tuple[str, ...] = ()


@dataclass(frozen=True)
class AIPortfolioSummary:
    """포트폴리오 레벨 AI 분석."""

    market_outlook: str = ""
    sector_balance: str = ""
    overall_risk: str = "MEDIUM"
    position_sizing: str = ""


@dataclass(frozen=True)
class AIAnalysisResult:
    """전체 AI 분석 결과."""

    stocks: tuple[AIStockAnalysis, ...] = ()
    portfolio: AIPortfolioSummary = field(default_factory=AIPortfolioSummary)
    raw_response: str = ""
