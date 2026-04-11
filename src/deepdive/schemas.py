"""Deep Dive 분석 결과 스키마 — 불변 Pydantic DTO."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date

from pydantic import BaseModel


class UpcomingCatalyst(BaseModel, frozen=True):
    """Phase 11b: 임박 촉매 (earnings/ex_dividend/fomc) 구조화 표현."""

    kind: str              # earnings | ex_dividend | fomc
    event_date: _date
    days_until: int
    label: str             # "실적 발표 3일 후" 등 표시용 요약


class FundamentalHealth(BaseModel, frozen=True):
    """Layer 1: 펀더멘털 헬스체크 결과."""

    health_grade: str       # A/B/C/D/F
    f_score: int            # 0-9
    z_score: float | None
    margin_trend: str       # improving/declining/stable
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None
    roe: float | None
    debt_ratio: float | None
    earnings_beat_streak: int
    metrics: dict


class ValuationContext(BaseModel, frozen=True):
    """Layer 2: 밸류에이션 컨텍스트."""

    valuation_grade: str            # Cheap/Fair/Rich/Extreme
    per_5y_percentile: float | None
    pbr_5y_percentile: float | None
    ev_ebitda_5y_percentile: float | None
    sector_per_premium: float | None
    sector_pbr_premium: float | None
    dcf_implied_growth: float | None
    peg_ratio: float | None
    fcf_yield: float | None
    metrics: dict


class TechnicalProfile(BaseModel, frozen=True):
    """Layer 3: 멀티타임프레임 기술적 분석 결과."""

    technical_grade: str        # Bullish/Neutral/Bearish
    trend_alignment: str        # aligned_up/aligned_down/mixed
    position_52w_pct: float     # 0-100
    rsi: float | None
    macd_signal: str | None     # bullish/bearish/neutral
    nearest_support: float | None
    nearest_resistance: float | None
    relative_strength_pct: float | None
    atr_regime: str             # High/Normal/Low
    metrics: dict


class FlowProfile(BaseModel, frozen=True):
    """Layer 4: 수급/포지셔닝 분석 결과."""

    flow_grade: str             # Accumulation/Neutral/Distribution
    insider_net_90d: float
    insider_signal: str         # net_buy/net_sell/neutral
    short_ratio: float | None
    short_pct_float: float | None
    analyst_buy_pct: float | None
    analyst_target_upside: float | None
    institutional_change: str | None
    metrics: dict


class NarrativeProfile(BaseModel, frozen=True):
    """Layer 5: 내러티브 + 촉매."""

    narrative_grade: str            # Positive/Neutral/Negative
    sentiment_30d: float | None
    sentiment_60d: float | None
    sentiment_90d: float | None
    sentiment_trend: str            # improving/declining/stable
    upcoming_catalysts: list[str]
    risk_events: list[str]
    exec_changes: list[str]
    metrics: dict
    # Phase 11b: 구조화된 촉매 — UI 표시용 legacy 필드(upcoming_catalysts)는 유지
    upcoming_catalysts_structured: tuple[UpcomingCatalyst, ...] = ()


class MacroSensitivity(BaseModel, frozen=True):
    """Layer 6: 거시 민감도."""

    macro_grade: str                # Favorable/Neutral/Headwind
    beta_vix: float | None
    beta_10y: float | None
    beta_dollar: float | None
    sector_momentum_rank: int | None
    sector_momentum_total: int | None
    current_regime: str | None
    regime_avg_return: float | None
    metrics: dict


class AIResult(BaseModel, frozen=True):
    """AI 분석 결과."""

    action_grade: str                            # HOLD/ADD/TRIM/EXIT
    conviction: int                              # 1-10
    uncertainty: str                             # low/medium/high
    reasoning: str
    what_missing: str | None = None

    # Phase 4: 근거 추적 + 실행 가이드 힌트 (Synthesizer 구조화 출력 복구)
    support_price: float | None = None           # 기술적 지지선 (AI 관찰)
    resistance_price: float | None = None        # 기술적 저항선 (AI 관찰)
    stop_loss: float | None = None               # AI 제시 손절가
    next_review_trigger: str | None = None       # 재검토 트리거 조건
    evidence_refs: tuple[str, ...] = ()          # ["layer3.rsi=72", ...]
    invalidation_conditions: tuple[str, ...] = ()  # 논지가 깨지는 조건


class ScenarioForecast(BaseModel, frozen=True):
    """단일 시나리오 예측."""

    horizon: str          # 1M/3M/6M
    scenario: str         # BASE/BULL/BEAR
    probability: float    # 0.0-1.0
    price_low: float
    price_high: float
    trigger_condition: str | None


@dataclass(frozen=True)
class DebateRound:
    """단일 토론 라운드 결과."""

    round_num: int
    role: str
    raw_text: str = ""
    parsed: dict | None = None


@dataclass(frozen=True)
class CLIDebateResult:
    """CLI 기반 3라운드 토론 결과."""

    rounds: tuple[DebateRound, ...] = ()
    final_result: AIResult | None = None
    scenarios: dict | None = None
    consensus_strength: str = "low"
    bull_summary: str | None = None
    bear_summary: str | None = None


@dataclass(frozen=True)
class PeerComparison:
    """단일 페어 비교 결과."""

    peer_ticker: str
    peer_name: str
    similarity_score: float       # 코사인 유사도 0.0-1.0
    market_cap_ratio: float       # peer/target 시총 비율
    return_60d_peer: float        # 페어 60일 수익률 %
    return_60d_target: float      # 대상 60일 수익률 %
    per_peer: float | None        # 페어 PER
    per_target: float | None      # 대상 PER


@dataclass(frozen=True)
class ChangeRecord:
    """단일 변경 감지 결과."""

    change_type: str              # action_changed/conviction_shift/probability_shift/new_risk/trigger_hit
    description: str              # 사람이 읽을 수 있는 설명
    severity: str                 # critical/warning/info


@dataclass(frozen=True)
class ForecastAccuracy:
    """종목별 예측 정확도 요약."""

    ticker: str
    total_evaluated: int          # 평가 완료된 예측 수
    hit_count: int                # 적중 수
    hit_rate: float               # hit_count / total_evaluated
    direction_correct: int        # 방향 정확 수
    direction_accuracy: float     # direction_correct / total_evaluated
    overall_score: float          # hit_rate * 0.6 + direction_accuracy * 0.4
    by_horizon: dict = field(default_factory=dict)   # {"1M": {"hit_rate": 0.6, "count": 5}, ...}
    by_scenario: dict = field(default_factory=dict)  # {"BASE": {"hit_rate": 0.7, "count": 5}, ...}


class DeepDiveResult(BaseModel):
    """종목별 통합 분석 결과."""

    ticker: str
    stock_id: int
    current_price: float
    daily_change_pct: float
    layer1: FundamentalHealth | None = None
    layer2: ValuationContext | None = None
    layer3: TechnicalProfile | None = None
    layer4: FlowProfile | None = None
    layer5: NarrativeProfile | None = None
    layer6: MacroSensitivity | None = None
    ai_result: AIResult | None = None
