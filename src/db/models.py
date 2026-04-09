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
    fear_greed_index: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    fear_greed_rating: Mapped[str | None] = mapped_column(String(20), nullable=True)


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
    return_60d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ai_approved: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=None)
    ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_target_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ai_stop_loss: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ai_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_risk_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ai_entry_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_exit_strategy: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 포지션 사이징 (Step 4.6)
    position_weight: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    trailing_stop: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    atr_stop: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    sizing_strategy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 실행 비용 분해 (영역 3)
    spread_cost_bps: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    impact_cost_bps: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    total_cost_bps: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    daily_turnover: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    # 감사 추적 (Phase 3)
    ai_confidence_raw: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_confidence_adjustments: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON


class FactDataQualityLog(TimestampMixin, Base):
    """데이터 품질 검증 로그."""

    __tablename__ = "fact_data_quality_log"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    check_type: Mapped[str] = mapped_column(String(30), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(10), nullable=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="info")
    description: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    actual_value: Mapped[str | None] = mapped_column(String(100), nullable=True)


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
    return_5d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    return_10d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    return_20d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    return_60d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    direction_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    direction_correct_5d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    direction_correct_10d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    direction_correct_60d: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    target_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    stop_hit: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    target_error_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    feedback_weight: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class FactAILesson(TimestampMixin, Base):
    """AI 자기학습 교훈 누적 저장소."""

    __tablename__ = "fact_ai_lessons"

    lesson_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    lesson_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    source_recommendation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fact_daily_recommendations.recommendation_id", ondelete="CASCADE"),
        nullable=False,
    )
    source_ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    source_sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_regime: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_vix_level: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    source_return_20d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    times_applied: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    effectiveness_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class FactAIRetrospective(TimestampMixin, Base):
    """AI 예측 복기 기록."""

    __tablename__ = "fact_ai_retrospectives"

    retrospective_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    run_date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    recommendation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("fact_daily_recommendations.recommendation_id", ondelete="CASCADE"),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    original_ai_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_return_20d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    max_gain_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    max_loss_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    price_path_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    retrospective_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    lesson_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("fact_ai_lessons.lesson_id"), nullable=True
    )


class FactCalibrationCell(TimestampMixin, Base):
    """조건별 캘리브레이션 셀 (regime x sector x confidence x event x horizon)."""

    __tablename__ = "fact_calibration_cells"
    __table_args__ = (
        UniqueConstraint(
            "regime", "sector", "confidence_range", "has_event", "horizon",
            name="uq_calibration_cell_v2",
        ),
    )

    cell_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    regime: Mapped[str] = mapped_column(String(20), nullable=False)
    sector: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence_range: Mapped[str] = mapped_column(String(10), nullable=False)
    has_event: Mapped[bool] = mapped_column(Boolean, nullable=False)
    horizon: Mapped[str] = mapped_column(String(5), nullable=False, default="20d")
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    win_rate: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    avg_return: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    last_updated_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=True
    )


class FactAIDebate(TimestampMixin, Base):
    """멀티 에이전트 토론 라운드별 기록."""

    __tablename__ = "fact_ai_debate"
    __table_args__ = (
        Index("idx_debate_rec", "recommendation_id"),
        Index("idx_debate_date", "run_date_id"),
    )

    debate_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    recommendation_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("fact_daily_recommendations.recommendation_id", ondelete="CASCADE"),
        nullable=True,
    )
    agent_role: Mapped[str] = mapped_column(String(12), nullable=False)
    round_num: Mapped[int] = mapped_column(Integer, nullable=False)
    analysis_text: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    key_arguments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    consensus_strength: Mapped[str | None] = mapped_column(String(10), nullable=True)


class FactAgentAccuracy(TimestampMixin, Base):
    """에이전트별(Bull/Bear) 예측 정확도 추적 테이블."""

    __tablename__ = "fact_agent_accuracy"
    __table_args__ = (
        Index("idx_agent_acc_date", "run_date_id"),
    )

    accuracy_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    agent_role: Mapped[str] = mapped_column(String(12), nullable=False)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    predicted_direction: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    actual_return_20d: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    was_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class FactCounterfactual(TimestampMixin, Base):
    """반사실 시뮬레이션 결과."""

    __tablename__ = "fact_counterfactuals"

    counterfactual_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    run_date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    recommendation_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey(
            "fact_daily_recommendations.recommendation_id", ondelete="CASCADE"
        ),
        nullable=False,
    )
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    original_decision: Mapped[str] = mapped_column(String(10), nullable=False)
    original_return: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    counterfactual_return: Mapped[float | None] = mapped_column(
        Numeric, nullable=True
    )
    delta: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    lesson_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class FactMLModelLog(TimestampMixin, Base):
    """ML 모델 학습 이력."""

    __tablename__ = "fact_ml_model_log"

    model_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trained_date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    model_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "binary" | "regression"
    train_auc: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    train_rmse: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    feature_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FactMLDriftCheck(TimestampMixin, Base):
    """모델 드리프트 검사 이력."""

    __tablename__ = "fact_ml_drift_check"

    check_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    accuracy_current: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    accuracy_baseline: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    is_drifted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    action_taken: Mapped[str | None] = mapped_column(String(20), nullable=True)


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


class FactFactorReturn(TimestampMixin, Base):
    """팩터 수익률 팩트 테이블 — 일별 롱숏 스프레드."""

    __tablename__ = "fact_factor_returns"
    __table_args__ = (
        UniqueConstraint("date_id", "factor_name", name="uq_factor_return_date_name"),
        Index("idx_factor_returns_date", "date_id"),
    )

    factor_return_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    factor_name: Mapped[str] = mapped_column(String(30), nullable=False)
    long_return: Mapped[float] = mapped_column(Numeric, nullable=False)
    short_return: Mapped[float] = mapped_column(Numeric, nullable=False)
    spread: Mapped[float] = mapped_column(Numeric, nullable=False)


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


# ──────────────────────────────────────────
# Deep Dive 테이블 (워치리스트 + 개인 분석)
# ──────────────────────────────────────────


class DimWatchlist(TimestampMixin, Base):
    """워치리스트 종목 관리 디멘션."""

    __tablename__ = "dim_watchlist"
    __table_args__ = (
        Index("idx_watchlist_active", "active"),
    )

    watchlist_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class DimWatchlistHolding(TimestampMixin, Base):
    """워치리스트 보유 정보 디멘션."""

    __tablename__ = "dim_watchlist_holdings"

    holding_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    avg_cost: Mapped[float] = mapped_column(Numeric, nullable=False)
    shares: Mapped[int] = mapped_column(Integer, nullable=False)
    opened_at: Mapped[date | None] = mapped_column(Date, nullable=True)


class DimWatchlistPair(TimestampMixin, Base):
    """워치리스트 페어 종목 디멘션."""

    __tablename__ = "dim_watchlist_pairs"
    __table_args__ = (
        UniqueConstraint("ticker", "peer_ticker", name="uq_watchlist_pair"),
        Index("idx_pairs_ticker", "ticker"),
    )

    pair_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    peer_ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    similarity_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class FactDeepDiveReport(TimestampMixin, Base):
    """Deep Dive 일별 분석 리포트 팩트 — 매일 새 row로 누적 (절대 덮어쓰지 않음)."""

    __tablename__ = "fact_deepdive_reports"
    __table_args__ = (
        UniqueConstraint("stock_id", "date_id", name="uq_dd_reports_stock_date"),
        Index("idx_dd_reports_ticker_date", "ticker", "date_id"),
    )

    report_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    date_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_date.date_id"), nullable=False
    )
    stock_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("dim_stocks.stock_id", ondelete="CASCADE"), nullable=False
    )
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    action_grade: Mapped[str] = mapped_column(String(4), nullable=False)
    conviction: Mapped[int] = mapped_column(Integer, nullable=False)
    uncertainty: Mapped[str] = mapped_column(String(6), nullable=False)
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
    # 레이어별 요약 (검색/필터용)
    layer1_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    layer2_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    layer3_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    layer4_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    layer5_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    layer6_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # AI 토론 결과
    ai_bull_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_bear_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_synthesis: Mapped[str | None] = mapped_column(Text, nullable=True)
    consensus_strength: Mapped[str | None] = mapped_column(String(10), nullable=True)
    what_missing: Mapped[str | None] = mapped_column(Text, nullable=True)


class FactDeepDiveForecast(TimestampMixin, Base):
    """Deep Dive 시나리오 예측 팩트 — 정확도 측정용."""

    __tablename__ = "fact_deepdive_forecasts"
    __table_args__ = (
        Index("idx_dd_forecasts_report", "report_id"),
        Index("idx_dd_forecasts_ticker", "ticker", "horizon"),
    )

    forecast_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    report_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fact_deepdive_reports.report_id", ondelete="CASCADE"),
        nullable=False,
    )
    date_id: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    horizon: Mapped[str] = mapped_column(String(2), nullable=False)
    scenario: Mapped[str] = mapped_column(String(4), nullable=False)
    probability: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    price_low: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    price_high: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    trigger_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_price: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    actual_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    hit_range: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class FactDeepDiveAction(TimestampMixin, Base):
    """Deep Dive 액션 등급 히스토리 팩트."""

    __tablename__ = "fact_deepdive_actions"
    __table_args__ = (
        Index("idx_dd_actions_ticker", "ticker", "date_id"),
    )

    action_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    date_id: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    action_grade: Mapped[str] = mapped_column(String(4), nullable=False)
    conviction: Mapped[int] = mapped_column(Integer, nullable=False)
    prev_action_grade: Mapped[str | None] = mapped_column(String(4), nullable=True)
    prev_conviction: Mapped[int | None] = mapped_column(Integer, nullable=True)


class FactDeepDiveChange(TimestampMixin, Base):
    """Deep Dive 일일 변경 감지 결과 팩트."""

    __tablename__ = "fact_deepdive_changes"
    __table_args__ = (
        Index("idx_dd_changes_date", "date_id"),
        Index("idx_dd_changes_ticker", "ticker", "date_id"),
    )

    change_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    date_id: Mapped[int] = mapped_column(Integer, nullable=False)
    stock_id: Mapped[int] = mapped_column(Integer, nullable=False)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    change_type: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False, default="info"
    )
