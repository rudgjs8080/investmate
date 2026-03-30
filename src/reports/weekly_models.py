"""주간 리포트 데이터 모델 — 8개 섹션 frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WeeklyExecutiveSummary:
    """1분 브리핑 — 주간 시장 총평."""

    market_oneliner: str
    sp500_weekly_return_pct: float | None
    vix_start: float | None
    vix_end: float | None
    vix_high: float | None
    vix_low: float | None
    regime_start: str  # bull/bear/range/crisis
    regime_end: str
    regime_changed: bool
    weekly_win_rate_pct: float | None
    weekly_avg_return_pct: float | None


@dataclass(frozen=True)
class WeeklyPickPerformance:
    """주간 추천 종목별 성과."""

    ticker: str
    name: str
    sector: str | None
    days_recommended: int
    avg_rank: float
    weekly_return_pct: float | None
    ai_approved_days: int
    ai_rejected_days: int


@dataclass(frozen=True)
class WeeklyPerformanceReview:
    """주간 추천 성과 리뷰."""

    total_unique_picks: int
    win_count: int
    loss_count: int
    win_rate_pct: float | None
    avg_return_pct: float | None
    best_pick: WeeklyPickPerformance | None
    worst_pick: WeeklyPickPerformance | None
    ai_approved_avg_return: float | None
    ai_rejected_avg_return: float | None
    all_picks: tuple[WeeklyPickPerformance, ...]


@dataclass(frozen=True)
class ConvictionPick:
    """확신 종목 — 주중 3일 이상 추천된 종목."""

    ticker: str
    name: str
    sector: str | None
    days_recommended: int
    consecutive_days: int
    avg_rank: float
    avg_total_score: float
    weekly_return_pct: float | None
    ai_consensus: str  # "추천" / "혼재" / "제외"


@dataclass(frozen=True)
class SectorRotationEntry:
    """섹터 로테이션 분석 항목."""

    sector: str
    weekly_return_pct: float | None
    volume_change_pct: float | None  # 이번 주 vs 지난 주
    momentum_delta: str  # 상승/하락/유지
    pick_count: int


@dataclass(frozen=True)
class WeeklyMacroSummary:
    """매크로 환경 주간 변화."""

    daily_scores: tuple[tuple[str, int | None], ...]  # (YYYY-MM-DD, score)
    vix_series: tuple[tuple[str, float | None], ...]
    us_10y_start: float | None
    us_10y_end: float | None
    us_13w_start: float | None
    us_13w_end: float | None
    spread_start: float | None
    spread_end: float | None
    dollar_start: float | None
    dollar_end: float | None
    gold_start: float | None
    gold_end: float | None
    oil_start: float | None
    oil_end: float | None


@dataclass(frozen=True)
class WeeklySignalTrend:
    """시그널 트렌드 분석."""

    daily_buy_counts: tuple[tuple[str, int], ...]  # (YYYY-MM-DD, count)
    daily_sell_counts: tuple[tuple[str, int], ...]
    most_frequent_signal: str | None
    avg_strength_change: float | None  # 이번 주 vs 지난 주


@dataclass(frozen=True)
class WeeklyAIAccuracy:
    """AI 예측 정확도 주간 리뷰."""

    approval_rate_pct: float | None
    direction_accuracy_pct: float | None
    confidence_vs_return_corr: float | None
    total_reviewed: int


@dataclass(frozen=True)
class WeeklyOutlook:
    """다음 주 전망 & 체크포인트."""

    regime_strategy: str
    watchlist_sectors: tuple[str, ...]
    avoid_sectors: tuple[str, ...]
    rebalancing_suggestion: str


@dataclass(frozen=True)
class WeeklyBestWorstDetail:
    """주간 베스트/워스트 종목 상세 분석."""

    ticker: str
    name: str
    weekly_return_pct: float | None
    rsi_14: float | None
    macd_histogram: float | None
    sma_alignment: str  # 정배열/역배열/혼조
    volume_vs_avg_pct: float | None
    sector: str | None
    catalyst_note: str


@dataclass(frozen=True)
class RiskDashboard:
    """리스크 대시보드 — 포트폴리오 수준 리스크."""

    portfolio_beta: float | None
    max_sector_concentration_pct: float | None
    top_sector: str | None
    vix_exposure: str  # 낮음/보통/높음
    avg_correlation: float | None
    drawdown_from_peak_pct: float | None


@dataclass(frozen=True)
class WinRateTrend:
    """추천 적중률 4주 롤링 트렌드."""

    weekly_rates: tuple[tuple[str, float | None], ...]  # (week_id, rate_pct)
    trend_direction: str  # 개선/악화/유지
    four_week_avg_pct: float | None


@dataclass(frozen=True)
class ConvictionTechnical:
    """확신 종목 기술적 상황."""

    ticker: str
    name: str
    rsi_14: float | None
    macd_signal: str  # 매수/매도/중립
    sma_alignment: str  # 정배열/역배열/혼조
    bb_position: str  # 상단/중간/하단
    support_price: float | None
    resistance_price: float | None


@dataclass(frozen=True)
class WeekOverWeekChange:
    """이전 주 대비 변화."""

    prev_win_rate_pct: float | None
    curr_win_rate_pct: float | None
    win_rate_delta: float | None
    prev_avg_return_pct: float | None
    curr_avg_return_pct: float | None
    return_delta: float | None
    regime_changed: bool
    new_sectors_in: tuple[str, ...]
    sectors_out: tuple[str, ...]


@dataclass(frozen=True)
class WeeklyReport:
    """주간 리포트 최상위 모델."""

    year: int
    week_number: int
    week_start: str  # YYYY-MM-DD
    week_end: str
    trading_days: int
    generated_at: str
    executive_summary: WeeklyExecutiveSummary
    performance_review: WeeklyPerformanceReview
    conviction_picks: tuple[ConvictionPick, ...]
    sector_rotation: tuple[SectorRotationEntry, ...]
    macro_summary: WeeklyMacroSummary
    signal_trend: WeeklySignalTrend
    ai_accuracy: WeeklyAIAccuracy
    outlook: WeeklyOutlook
    # 고도화 섹션 (optional, backward compatible)
    ai_commentary: str | None = None
    best_worst_detail: tuple[WeeklyBestWorstDetail, ...] = ()
    risk_dashboard: RiskDashboard | None = None
    win_rate_trend: WinRateTrend | None = None
    conviction_technicals: tuple[ConvictionTechnical, ...] = ()
    week_over_week: WeekOverWeekChange | None = None
