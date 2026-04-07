"""Pydantic 스키마 정의 — Star Schema 대응."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator, model_validator


class DailyPriceData(BaseModel):
    """일봉 데이터 스키마."""

    date: date
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    volume: int = Field(..., ge=0)
    adj_close: float = Field(..., gt=0)

    @model_validator(mode="after")
    def validate_price_consistency(self) -> DailyPriceData:
        if self.high < self.low:
            raise ValueError(f"high({self.high}) < low({self.low})")
        return self


class FinancialRecord(BaseModel):
    """원본 재무제표 스키마."""

    period: str = Field(..., min_length=1, max_length=10)
    revenue: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    total_equity: float | None = None
    operating_cashflow: float | None = None


class ValuationRecord(BaseModel):
    """파생 밸류에이션 스키마."""

    date: date
    market_cap: float | None = None
    per: float | None = None
    pbr: float | None = None
    roe: float | None = None
    debt_ratio: float | None = None
    dividend_yield: float | None = None
    ev_ebitda: float | None = None


class SignalData(BaseModel):
    """시그널 스키마."""

    date: date
    signal_type: str
    direction: str = Field(..., pattern="^(BUY|SELL|HOLD)$")
    strength: int = Field(..., ge=1, le=10)
    description: str


class MacroData(BaseModel):
    """매크로 지표 스키마."""

    date: date
    vix: float | None = None
    us_10y_yield: float | None = None
    us_13w_yield: float | None = None
    dollar_index: float | None = None
    sp500_close: float | None = None
    sp500_sma20: float | None = None
    market_score: int | None = None
    gold_price: float | None = None
    oil_price: float | None = None
    yield_spread: float | None = None
    fear_greed_index: float | None = None
    fear_greed_rating: str | None = None


class RecommendationData(BaseModel):
    """데일리 추천 스키마."""

    stock_id: int
    ticker: str
    name: str
    rank: int
    total_score: float
    technical_score: float
    fundamental_score: float
    smart_money_score: float = 5.0
    external_score: float
    momentum_score: float
    recommendation_reason: str
    price_at_recommendation: float


class NewsArticleData(BaseModel):
    """뉴스 기사 스키마."""

    title: str
    summary: str | None = None
    url: str
    source: str
    published_at: datetime
    sentiment_score: float | None = None


class StockInfo(BaseModel):
    """종목 기본 정보."""

    ticker: str
    name: str
    sector: str | None = None
    industry: str | None = None

    @field_validator("ticker")
    @classmethod
    def ticker_uppercase(cls, v: str) -> str:
        return v.upper()
