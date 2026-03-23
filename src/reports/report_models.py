"""리포트 데이터 모델 — 풍부한 상세 분석용 frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class SignalDetail:
    """개별 시그널 상세."""

    signal_type: str
    direction: str  # BUY / SELL
    strength: int
    description: str


@dataclass(frozen=True)
class TechnicalDetail:
    """기술적 분석 상세."""

    rsi: float | None = None
    rsi_status: str = "중립"  # 과매수/과매도/중립
    macd: float | None = None
    macd_signal: float | None = None
    macd_hist: float | None = None
    macd_status: str = "중립"  # 상승/하락/중립
    sma_5: float | None = None
    sma_20: float | None = None
    sma_60: float | None = None
    sma_120: float | None = None
    sma_alignment: str = "혼조"  # 정배열/역배열/혼조
    bb_upper: float | None = None
    bb_middle: float | None = None
    bb_lower: float | None = None
    bb_position: str = "중단"  # 상단근접/중단/하단근접
    stoch_k: float | None = None
    stoch_d: float | None = None
    volume_ratio: float | None = None  # 현재 거래량 / SMA20 거래량
    signals: tuple[SignalDetail, ...] = ()


@dataclass(frozen=True)
class FundamentalDetail:
    """기본적 분석 상세."""

    per: float | None = None
    per_score: float = 5.0
    pbr: float | None = None
    pbr_score: float = 5.0
    roe: float | None = None
    roe_score: float = 5.0
    debt_ratio: float | None = None
    debt_score: float = 5.0
    growth_score: float = 5.0
    composite_score: float = 5.0
    summary: str = "보통"  # 우수/보통/주의
    market_cap: float | None = None
    dividend_yield: float | None = None
    ev_ebitda: float | None = None


@dataclass(frozen=True)
class SmartMoneyDetail:
    """수급/스마트머니 상세."""

    # 애널리스트 컨센서스
    analyst_strong_buy: int = 0
    analyst_buy: int = 0
    analyst_hold: int = 0
    analyst_sell: int = 0
    analyst_strong_sell: int = 0
    target_mean: float | None = None
    target_high: float | None = None
    target_low: float | None = None
    upside_pct: float | None = None  # 목표가 대비 상승여력 %
    # 내부자
    insider_net_value: float | None = None
    insider_summary: str = "데이터 없음"
    # 기관
    top_institutions: tuple[tuple[str, float], ...] = ()  # (이름, 보유가치)
    # 공매도
    short_ratio: float | None = None
    short_pct: float | None = None


@dataclass(frozen=True)
class EarningsDetail:
    """실적 서프라이즈 상세."""

    latest_period: str | None = None
    eps_surprise_pct: float | None = None
    revenue_surprise_pct: float | None = None
    beat_streak: int = 0  # 연속 실적 상회 분기 수


@dataclass(frozen=True)
class NewsItem:
    """뉴스 항목."""

    title: str
    source: str
    published_at: str | None = None
    sentiment_score: float | None = None


@dataclass(frozen=True)
class StockRecommendationDetail:
    """추천 종목 상세 — 모든 분석 데이터 통합."""

    rank: int
    ticker: str
    name: str
    sector: str | None
    price: float
    price_change_pct: float | None = None
    total_score: float = 0.0
    technical_score: float = 0.0
    fundamental_score: float = 0.0
    smart_money_score: float = 5.0
    external_score: float = 0.0
    momentum_score: float = 0.0
    recommendation_reason: str = ""
    technical: TechnicalDetail = field(default_factory=TechnicalDetail)
    fundamental: FundamentalDetail = field(default_factory=FundamentalDetail)
    smart_money: SmartMoneyDetail = field(default_factory=SmartMoneyDetail)
    earnings: EarningsDetail = field(default_factory=EarningsDetail)
    news: tuple[NewsItem, ...] = ()
    risk_factors: tuple[str, ...] = ()
    ai_approved: bool | None = None
    ai_reason: str | None = None
    ai_target_price: float | None = None
    ai_stop_loss: float | None = None
    ai_confidence: int | None = None
    ai_risk_level: str | None = None
    ai_entry_strategy: str | None = None
    ai_exit_strategy: str | None = None
    # 보강 데이터 (프롬프트 표시용)
    pct_from_52w_high: float | None = None
    beta: float | None = None
    forward_per: float | None = None
    is_pre_earnings: bool = False


@dataclass(frozen=True)
class SignalSummaryItem:
    """시그널 발생 종목 요약."""

    ticker: str
    name: str
    signal_type: str
    direction: str
    strength: int
    description: str


@dataclass(frozen=True)
class MacroEnvironment:
    """시장 환경 상세."""

    market_score: int | None = None
    mood: str = "미정"  # 강세/중립/약세
    vix: float | None = None
    vix_status: str = "미정"  # 안정/주의/위험
    sp500_close: float | None = None
    sp500_sma20: float | None = None
    sp500_trend: str = "미정"  # 상승/하락
    us_10y_yield: float | None = None
    us_13w_yield: float | None = None
    dollar_index: float | None = None
    yield_spread: float | None = None  # 10y - 13w


@dataclass(frozen=True)
class EnrichedDailyReport:
    """풍부한 데일리 리포트 최상위 모델."""

    run_date: date
    total_stocks_analyzed: int = 0
    stocks_passed_filter: int = 0
    pipeline_duration_sec: float | None = None
    macro: MacroEnvironment = field(default_factory=MacroEnvironment)
    recommendations: tuple[StockRecommendationDetail, ...] = ()
    all_signals: tuple[SignalSummaryItem, ...] = ()
    buy_signal_count: int = 0
    sell_signal_count: int = 0
