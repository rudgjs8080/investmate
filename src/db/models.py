"""Star Schema ORM 모델 정의 — Dimension / Fact / Bridge."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """선언적 베이스 클래스."""


class TimestampMixin:
    """created_at, updated_at 자동 관리 믹스인."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ──────────────────────────────────────────
# Dimension 테이블
# ──────────────────────────────────────────


class DimMarket(TimestampMixin, Base):
    """시장 구분 디멘션."""

    __tablename__ = "dim_markets"

    market_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), nullable=False)
    trading_hours: Mapped[str | None] = mapped_column(String(50), nullable=True)

    stocks: Mapped[list[DimStock]] = relationship(back_populates="market")


class DimSector(TimestampMixin, Base):
    """섹터/산업 분류 디멘션 (GICS 기반)."""

    __tablename__ = "dim_sectors"

    sector_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sector_name: Mapped[str] = mapped_column(String(100), nullable=False)
    industry_group: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)

    stocks: Mapped[list[DimStock]] = relationship(back_populates="sector")


class DimDate(Base):
    """날짜 디멘션 — QoQ/YoY 집계, 거래일 판별용."""

    __tablename__ = "dim_date"

    date_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # YYYYMMDD
    date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    quarter: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    week_of_year: Mapped[int] = mapped_column(Integer, nullable=False)
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    is_trading_day: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    fiscal_quarter: Mapped[str | None] = mapped_column(String(10), nullable=True)


class DimIndicatorType(Base):
    """기술적 지표 정의 디멘션 — EAV 패턴의 attribute 축."""

    __tablename__ = "dim_indicator_types"

    indicator_type_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class DimSignalType(Base):
    """시그널 종류 정의 디멘션."""

    __tablename__ = "dim_signal_types"

    signal_type_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    default_weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class DimStock(TimestampMixin, Base):
    """종목 마스터 디멘션."""

    __tablename__ = "dim_stocks"
    __table_args__ = (
        Index("idx_stocks_sp500", "is_sp500", "is_active"),
    )

    stock_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    name_kr: Mapped[str | None] = mapped_column(String(200), nullable=True)
    market_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_markets.market_id"), nullable=False
    )
    sector_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("dim_sectors.sector_id"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_sp500: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ipo_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    market: Mapped[DimMarket] = relationship(back_populates="stocks")
    sector: Mapped[DimSector | None] = relationship(back_populates="stocks")


# ──────────────────────────────────────────
# Fact 테이블
# ──────────────────────────────────────────


class FactDailyPrice(TimestampMixin, Base):
    """일봉 데이터 팩트 테이블."""

    __tablename__ = "fact_daily_prices"
    __table_args__ = (
        UniqueConstraint("stock_id", "date_id", name="uq_prices_stock_date"),
        Index("idx_prices_stock_date", "stock_id", "date_id"),
        Index("idx_prices_date", "date_id"),
    )

    price_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    open: Mapped[float] = mapped_column(Numeric, nullable=False)
    high: Mapped[float] = mapped_column(Numeric, nullable=False)
    low: Mapped[float] = mapped_column(Numeric, nullable=False)
    close: Mapped[float] = mapped_column(Numeric, nullable=False)
    adj_close: Mapped[float] = mapped_column(Numeric, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)


class FactIndicatorValue(TimestampMixin, Base):
    """기술적 지표 값 팩트 테이블 — EAV 패턴."""

    __tablename__ = "fact_indicator_values"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "date_id", "indicator_type_id",
            name="uq_indicator_stock_date_type",
        ),
        Index("idx_indicators_stock_date", "stock_id", "date_id"),
    )

    indicator_value_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    indicator_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_indicator_types.indicator_type_id"), nullable=False
    )
    value: Mapped[float] = mapped_column(Numeric, nullable=False)


class FactFinancial(TimestampMixin, Base):
    """원본 재무제표 팩트 테이블."""

    __tablename__ = "fact_financials"
    __table_args__ = (
        UniqueConstraint("stock_id", "period", name="uq_financials_stock_period"),
        Index("idx_financials_stock", "stock_id"),
    )

    financial_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    revenue: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    operating_income: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    net_income: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    total_assets: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    total_liabilities: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    total_equity: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    operating_cashflow: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class FactValuation(TimestampMixin, Base):
    """파생 밸류에이션 지표 팩트 테이블."""

    __tablename__ = "fact_valuations"
    __table_args__ = (
        UniqueConstraint("stock_id", "date_id", name="uq_valuations_stock_date"),
        Index("idx_valuations_stock_date", "stock_id", "date_id"),
    )

    valuation_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    market_cap: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    per: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    pbr: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    roe: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    debt_ratio: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ev_ebitda: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    short_ratio: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    short_pct_of_float: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class FactSignal(TimestampMixin, Base):
    """시그널 이력 팩트 테이블."""

    __tablename__ = "fact_signals"
    __table_args__ = (
        Index("idx_signals_stock_date", "stock_id", "date_id"),
    )

    signal_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    signal_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_signal_types.signal_type_id"), nullable=False
    )
    strength: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class FactMacroIndicator(TimestampMixin, Base):
    """매크로 지표 팩트 테이블."""

    __tablename__ = "fact_macro_indicators"
    __table_args__ = (
        UniqueConstraint("date_id", name="uq_macro_date"),
        Index("idx_macro_date", "date_id"),
    )

    macro_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    vix: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    us_10y_yield: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    us_13w_yield: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    dollar_index: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    sp500_close: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    sp500_sma20: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    market_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gold_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    oil_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    yield_spread: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class FactDailyRecommendation(TimestampMixin, Base):
    """데일리 추천 결과 팩트 테이블."""

    __tablename__ = "fact_daily_recommendations"
    __table_args__ = (
        Index("idx_recs_rundate", "run_date_id"),
        Index("idx_recs_stock", "stock_id"),
    )

    recommendation_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    run_date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    total_score: Mapped[float] = mapped_column(Numeric, nullable=False)
    technical_score: Mapped[float] = mapped_column(Numeric, nullable=False)
    fundamental_score: Mapped[float] = mapped_column(Numeric, nullable=False)
    external_score: Mapped[float] = mapped_column(Numeric, nullable=False)
    momentum_score: Mapped[float] = mapped_column(Numeric, nullable=False)
    recommendation_reason: Mapped[str] = mapped_column(Text, nullable=False)
    price_at_recommendation: Mapped[float] = mapped_column(Numeric, nullable=False)
    execution_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    smart_money_score: Mapped[float] = mapped_column(Numeric, nullable=False, default=5.0)
    return_1d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    return_5d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    return_10d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ai_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_target_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ai_stop_loss: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ai_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ai_entry_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_exit_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)


class FactAIFeedback(TimestampMixin, Base):
    """AI 예측 vs 실제 결과 피드백 테이블."""

    __tablename__ = "fact_ai_feedback"

    feedback_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fact_daily_recommendations.recommendation_id", ondelete="CASCADE"), nullable=False
    )
    run_date_id: Mapped[int] = mapped_column(Integer, ForeignKey("dim_date.date_id"), nullable=False)
    stock_id: Mapped[int] = mapped_column(Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ai_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    ai_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_target_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ai_stop_loss: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    price_at_rec: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    actual_price_5d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    actual_price_20d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    direction_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    target_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stop_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    target_error_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class FactNews(TimestampMixin, Base):
    """뉴스 팩트 테이블."""

    __tablename__ = "fact_news"

    news_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sentiment_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)

    stocks: Mapped[list[DimStock]] = relationship(
        secondary="bridge_news_stock", viewonly=True
    )


class FactCollectionLog(TimestampMixin, Base):
    """파이프라인 실행 이력 팩트 테이블."""

    __tablename__ = "fact_collection_logs"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    step: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    records_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


# ──────────────────────────────────────────
# 강화 데이터 Fact 테이블
# ──────────────────────────────────────────


class FactInsiderTrade(TimestampMixin, Base):
    """내부자 거래 팩트 테이블."""

    __tablename__ = "fact_insider_trades"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "date_id", "insider_name", "transaction_type",
            name="uq_insider_stock_date_name_type",
        ),
    )

    insider_trade_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    insider_name: Mapped[str] = mapped_column(String(200), nullable=False)
    insider_title: Mapped[str | None] = mapped_column(String(100), nullable=True)
    transaction_type: Mapped[str] = mapped_column(String(30), nullable=False)
    shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    shares_owned_after: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class FactInstitutionalHolding(TimestampMixin, Base):
    """기관 보유 팩트 테이블."""

    __tablename__ = "fact_institutional_holdings"

    holding_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    institution_name: Mapped[str] = mapped_column(String(300), nullable=False)
    shares: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    pct_of_shares: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class FactAnalystConsensus(TimestampMixin, Base):
    """애널리스트 컨센서스 팩트 테이블."""

    __tablename__ = "fact_analyst_consensus"
    __table_args__ = (
        UniqueConstraint("stock_id", "date_id", name="uq_analyst_stock_date"),
    )

    consensus_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    strong_buy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    buy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    hold: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    sell: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    strong_sell: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    target_mean: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    target_high: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    target_low: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    target_median: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class FactEarningsSurprise(TimestampMixin, Base):
    """실적 서프라이즈 팩트 테이블."""

    __tablename__ = "fact_earnings_surprises"
    __table_args__ = (
        UniqueConstraint("stock_id", "period", name="uq_earnings_stock_period"),
    )

    earnings_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    period: Mapped[str] = mapped_column(String(10), nullable=False)
    eps_estimate: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    eps_actual: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    surprise_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    revenue_estimate: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    revenue_actual: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    revenue_surprise_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)


# ──────────────────────────────────────────
# Bridge 테이블
# ──────────────────────────────────────────


class BridgeNewsStock(Base):
    """뉴스 ↔ 종목 다대다 브릿지 테이블."""

    __tablename__ = "bridge_news_stock"

    news_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fact_news.news_id", ondelete="CASCADE"), primary_key=True
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), primary_key=True
    )
    relevance: Mapped[float | None] = mapped_column(Numeric, nullable=True)
