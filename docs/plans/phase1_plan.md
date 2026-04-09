# Deep Dive Phase 1 — 상세 구현 계획서

> 이 문서의 내용을 구현 시작 시 `docs/plans/phase1_plan.md`로 저장할 것.

---

## 1. Phase 1 범위

### 포함

| # | 항목 | 설명 |
|---|------|------|
| 1 | DB 7개 테이블 | dim_watchlist, dim_watchlist_holdings, dim_watchlist_pairs, fact_deepdive_reports, fact_deepdive_forecasts, fact_deepdive_actions, fact_deepdive_changes |
| 2 | Repository 2개 | WatchlistRepository (CRUD), DeepDiveRepository (보고서 INSERT/조회) |
| 3 | Watchlist Manager | CRUD + 비S&P500 dim_stocks 자동 등록 |
| 4 | CLI | `watchlist add/remove/list/set-holding` + `deepdive run` |
| 5 | Seed Script | 12종목 초기 시드 (INSERT OR IGNORE) |
| 6 | Pipeline 뼈대 | DeepDivePipeline (Step 1/2/3/5/7/8) — checkpointing, signal handling |
| 7 | Layer 1 | 펀더멘털 헬스체크 (F-Score, Z-Score, 마진, ROE, 실적 beat) |
| 8 | Layer 3 | 멀티TF 기술적 (추세 정렬, 52주 위치, RSI, S/R, 상대강도) |
| 9 | Layer 4 | 수급/포지셔닝 (내부자, 공매도, 애널리스트) |
| 10 | Simple AI | 단일 CLI 호출 (--model opus), debate 없음 |
| 11 | Web | `/personal` 카드 그리드 페이지 |
| 12 | Notification | `send_deepdive_summary()` 텔레그램 1줄 |
| 13 | Config | `ai_model_deepdive`, `deepdive_timeout` 2개 설정 |
| 14 | Tests | 4개 테스트 파일 (~25개 케이스) |

### 제외 (Phase 2/3로 이관)

| 항목 | Phase |
|------|-------|
| Layer 2 (Valuation — DCF, PEG, 5년 백분위) | 2 |
| Layer 5 (Narrative — 뉴스 감성, 촉매 캘린더) | 2 |
| Layer 6 (Macro — 베타 회귀, 레짐별 행동) | 2 |
| 3라운드 토론 (Bull/Bear/Synthesizer) | 2 |
| 시나리오 예측 (1M/3M/6M × Base/Bull/Bear) | 2 |
| `/personal/{ticker}` 상세 페이지 | 2 |
| `scenarioRangeChart`, `layerRadarChart` | 2 |
| 페어 자동 선정 (코사인 유사도) | 3 |
| Diff 감지 (전일 대비 변경점 추출) | 3 |
| `/personal/{ticker}/history` | 3 |
| `/personal/forecasts` 정확도 리더보드 | 3 |
| `actionTimelineChart` | 3 |
| cron 스크립트 (`scripts/run_deepdive.sh`) | 3 |

---

## 2. Task 분할 (10개, 각 1-3시간)

### Task 1: DB Schema — 7개 ORM 테이블 (2h)

**파일:** `src/db/models.py` (기존 789줄 하단에 ~180줄 추가)

**작업:**
- 7개 ORM 클래스 추가 (기존 `TimestampMixin + Base` 패턴)
- Phase 1에서 채우지 않는 테이블(pairs, forecasts, changes)도 DDL은 생성

**핵심 모델:**

```python
class DimWatchlist(TimestampMixin, Base):
    __tablename__ = "dim_watchlist"
    watchlist_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

class DimWatchlistHolding(TimestampMixin, Base):
    __tablename__ = "dim_watchlist_holdings"
    holding_id: Mapped[int]  # PK
    ticker: Mapped[str]       # String(20), unique
    avg_cost: Mapped[float]   # Numeric
    shares: Mapped[int]       # Integer
    opened_at: Mapped[date | None]  # Date

class DimWatchlistPair(TimestampMixin, Base):
    __tablename__ = "dim_watchlist_pairs"
    pair_id: Mapped[int]
    ticker: Mapped[str]          # String(20)
    peer_ticker: Mapped[str]     # String(20)
    similarity_score: Mapped[float | None]
    # UniqueConstraint("ticker", "peer_ticker")

class FactDeepDiveReport(TimestampMixin, Base):
    __tablename__ = "fact_deepdive_reports"
    report_id: Mapped[int]       # PK
    date_id: Mapped[int]         # FK dim_date
    stock_id: Mapped[int]        # FK dim_stocks, CASCADE
    ticker: Mapped[str]          # String(20), 비정규화 — 빠른 조회용
    action_grade: Mapped[str]    # String(4): HOLD/ADD/TRIM/EXIT
    conviction: Mapped[int]      # 1-10
    uncertainty: Mapped[str]     # String(6): low/medium/high
    report_json: Mapped[str]     # Text, 전체 분석 JSON
    layer1_summary: Mapped[str | None]
    layer2_summary: Mapped[str | None]  # Phase 2
    layer3_summary: Mapped[str | None]
    layer4_summary: Mapped[str | None]
    layer5_summary: Mapped[str | None]  # Phase 2
    layer6_summary: Mapped[str | None]  # Phase 2
    ai_bull_text: Mapped[str | None]    # Phase 2
    ai_bear_text: Mapped[str | None]    # Phase 2
    ai_synthesis: Mapped[str | None]
    consensus_strength: Mapped[str | None]  # Phase 2
    what_missing: Mapped[str | None]
    # UniqueConstraint("stock_id", "date_id")

class FactDeepDiveForecast(TimestampMixin, Base):
    __tablename__ = "fact_deepdive_forecasts"
    # Phase 1: 테이블만 생성, 데이터 미적재
    forecast_id, report_id(FK), date_id, stock_id, ticker,
    horizon(String(2)), scenario(String(4)), probability,
    price_low, price_high, trigger_condition,
    actual_price, actual_date, hit_range

class FactDeepDiveAction(TimestampMixin, Base):
    __tablename__ = "fact_deepdive_actions"
    action_id: Mapped[int]
    date_id: Mapped[int]
    stock_id: Mapped[int]
    ticker: Mapped[str]
    action_grade: Mapped[str]     # String(4)
    conviction: Mapped[int]
    prev_action_grade: Mapped[str | None]
    prev_conviction: Mapped[int | None]

class FactDeepDiveChange(TimestampMixin, Base):
    __tablename__ = "fact_deepdive_changes"
    # Phase 1: 테이블만 생성, Phase 3에서 적재
    change_id, date_id, stock_id, ticker,
    change_type(String(30)), description(Text), severity(String(10))
```

**인덱스:**
- `dim_watchlist`: `idx_watchlist_active(active)`
- `dim_watchlist_pairs`: `idx_pairs_ticker(ticker)`, `UniqueConstraint(ticker, peer_ticker)`
- `fact_deepdive_reports`: `UniqueConstraint(stock_id, date_id)`, `idx_dd_reports_ticker_date(ticker, date_id)`
- `fact_deepdive_forecasts`: `idx_dd_forecasts_report(report_id)`, `idx_dd_forecasts_ticker(ticker, horizon)`
- `fact_deepdive_actions`: `idx_dd_actions_ticker(ticker, date_id)`
- `fact_deepdive_changes`: `idx_dd_changes_date(date_id)`, `idx_dd_changes_ticker(ticker, date_id)`

**테스트:**
- `test_tables_created` — `Base.metadata.create_all()` 후 7개 테이블 존재
- `test_watchlist_unique_ticker` — 중복 ticker INSERT 시 IntegrityError
- `test_report_unique_stock_date` — 같은 stock+date INSERT 시 IntegrityError

**완료 정의:** in-memory SQLite에서 7개 테이블 DDL 정상 생성, `ensure_schema()` 통과

---

### Task 2: WatchlistRepository + DeepDiveRepository (2h)

**파일:** `src/db/repository.py` (하단에 ~130줄 추가)

**핵심 시그니처:**

```python
class WatchlistRepository:
    @staticmethod
    def add_ticker(session: Session, ticker: str, note: str | None = None) -> DimWatchlist:
        """워치리스트에 종목 추가. 이미 존재하면 재활성화(active=True)."""

    @staticmethod
    def remove_ticker(session: Session, ticker: str) -> bool:
        """soft delete (active=False). 존재하지 않으면 False."""

    @staticmethod
    def get_active(session: Session) -> list[DimWatchlist]:
        """active=True인 종목 리스트 (ticker 정렬)."""

    @staticmethod
    def set_holding(session: Session, ticker: str, shares: int, avg_cost: float,
                    opened_at: date | None = None) -> DimWatchlistHolding:
        """보유정보 UPSERT."""

    @staticmethod
    def get_holding(session: Session, ticker: str) -> DimWatchlistHolding | None:
        """종목별 보유정보 조회."""

    @staticmethod
    def get_all_holdings(session: Session) -> dict[str, DimWatchlistHolding]:
        """{ticker: holding} 매핑."""


class DeepDiveRepository:
    @staticmethod
    def insert_report(session: Session, **kwargs) -> FactDeepDiveReport:
        """보고서 INSERT (절대 UPDATE 아님). 반환: 생성된 row."""

    @staticmethod
    def insert_action(session: Session, **kwargs) -> FactDeepDiveAction:
        """액션 이력 INSERT."""

    @staticmethod
    def get_latest_report(session: Session, stock_id: int) -> FactDeepDiveReport | None:
        """종목의 최신 보고서 (diff 감지용, Phase 2)."""

    @staticmethod
    def get_latest_reports_all(session: Session) -> list[FactDeepDiveReport]:
        """전 종목 최신 보고서 (카드 그리드용). subquery로 종목별 max(date_id) 조회."""

    @staticmethod
    def get_reports_by_ticker(session: Session, ticker: str, limit: int = 30) -> list[FactDeepDiveReport]:
        """종목별 보고서 이력 (Phase 2 상세 페이지)."""
```

**재사용:** 기존 `StockRepository`, `DailyPriceRepository` 패턴 (staticmethod, Session 파라미터, flush 후 반환)

**테스트:**
- `test_add_watchlist_idempotent` — add → remove → add 재활성화
- `test_remove_nonexistent` — 없는 ticker → False
- `test_get_active_only` — active=True만 필터
- `test_set_holding_upsert` — 두 번째 호출 시 shares/cost 갱신
- `test_insert_report_immutable` — INSERT 후 row 확인, UPDATE 없음
- `test_get_latest_reports_all` — 종목별 최신 1건만 반환

**완료 정의:** 6개 테스트 통과, 기존 repository 패턴 준수

---

### Task 3: Watchlist Manager + Auto-register (2h)

**파일 생성:**
- `src/deepdive/__init__.py` (빈 패키지)
- `src/deepdive/watchlist_manager.py` (~130줄)

**핵심 시그니처:**

```python
@dataclass(frozen=True)
class HoldingInfo:
    shares: int
    avg_cost: float
    opened_at: date | None

@dataclass(frozen=True)
class WatchlistEntry:
    """불변 DTO — 워치리스트 종목 + 보유정보."""
    ticker: str
    stock_id: int
    name: str
    name_kr: str | None
    sector: str | None
    is_sp500: bool
    holding: HoldingInfo | None  # 비보유면 None

def load_watchlist(session: Session) -> list[WatchlistEntry]:
    """active 워치리스트 로드 + holdings 매핑 + 자동 등록.
    입력: Session
    출력: WatchlistEntry 리스트 (ticker 정렬)"""

def ensure_stock_registered(session: Session, ticker: str) -> DimStock:
    """dim_stocks에 없으면 yfinance .info로 자동 등록.
    입력: Session, ticker
    출력: DimStock row (기존 또는 신규)"""

def _fetch_stock_info(ticker: str) -> dict:
    """yfinance .info에서 기본 정보 추출.
    출력: {"name": str, "sector": str, "industry": str}
    방어: .get() + 기본값 fallback"""
```

**재사용:**
- `StockRepository.get_by_ticker()` — dim_stocks 조회
- `StockRepository` add 패턴 — 신규 등록
- `WatchlistRepository.get_active()`, `get_all_holdings()`

**테스트:**
- `test_load_watchlist_with_holdings` — HoldingInfo 채워짐
- `test_load_watchlist_no_holdings` — holding=None
- `test_auto_register_non_sp500` — yfinance mock, dim_stocks에 is_sp500=False로 등록
- `test_auto_register_existing` — 이미 존재하면 중복 생성 없음
- `test_fetch_stock_info_partial` — yfinance 필드 일부 누락 시 기본값 사용

**완료 정의:** `load_watchlist()`가 정확한 `WatchlistEntry` 리스트 반환, 비S&P500 자동 등록 동작

---

### Task 4: Seed Script — 12종목 (1h)

**파일 생성:** `scripts/seed_watchlist.py` (~60줄)

**핵심:**

```python
INITIAL_WATCHLIST = [
    "NVDA", "UNH", "TSLA", "PLTR", "SMR", "GOOG",
    "AMZN", "MSFT", "AVGO", "META", "NFLX", "AAPL",
]

def seed_watchlist(engine: Engine) -> int:
    """초기 12종목 시드. 멱등성 보장 (INSERT OR IGNORE 패턴).
    반환: 신규 추가 수"""

if __name__ == "__main__":
    # standalone 실행
```

**처리 흐름:**
1. 각 ticker → `WatchlistRepository.add_ticker()` (중복은 재활성화)
2. 각 ticker → `ensure_stock_registered()` (비S&P500 자동 등록)
3. 신규 추가 수 카운트 + Rich 출력

**테스트:**
- `test_seed_creates_12` — 12개 dim_watchlist row
- `test_seed_idempotent` — 2회 실행 시 중복 없음
- `test_non_sp500_registered` — SMR이 dim_stocks에 is_sp500=False로 존재

**완료 정의:** `python scripts/seed_watchlist.py` 정상 실행, 재실행 안전

---

### Task 5: CLI — `watchlist` + `deepdive` 명령 (2h)

**파일 수정:** `src/main.py` (~120줄 추가)

**명령 구조:**

```python
@cli.group(help="개인 워치리스트 관리")
def watchlist(): ...

@watchlist.command("add")    # investmate watchlist add AAPL [--shares 100 --avg-cost 150]
@watchlist.command("remove") # investmate watchlist remove AAPL
@watchlist.command("list")   # investmate watchlist list (Rich Table 출력)
@watchlist.command("set-holding")  # investmate watchlist set-holding AAPL --shares 100 --avg-cost 150

@cli.group(help="Deep Dive 개인 분석")
def deepdive(): ...

@deepdive.command("run")    # investmate deepdive run [--date] [--ticker AAPL] [--force] [--skip-notify]
```

**`watchlist list` 출력 (Rich Table):**
| 티커 | 종목명 | 상태 | 보유 | 추가일 |
| NVDA | NVIDIA | active | 100주 @ $130.50 | 2026-04-07 |

**`deepdive run` 연동:**
- `_parse_date()` 재사용
- `create_db_engine()`, `ensure_schema()`, `init_db()` 기존 패턴
- `DeepDivePipeline(engine, target_date, ticker, force, skip_notify).run()`

**테스트:**
- `test_watchlist_add_cli` — CliRunner, exit_code 0
- `test_watchlist_list_empty` — "워치리스트가 비어 있습니다"
- `test_watchlist_remove_cli` — 제거 확인
- `test_deepdive_run_help` — `--help` 정상 출력

**완료 정의:** 4개 CLI 명령 + deepdive run 정상 동작, 한국어 UI

---

### Task 6: 3개 분석 레이어 + Schemas (3h)

**파일 생성:**
- `src/deepdive/schemas.py` (~100줄) — Pydantic DTO
- `src/deepdive/layers.py` (~350줄) — 3개 레이어 계산 로직

**schemas.py:**

```python
class FundamentalHealth(BaseModel, frozen=True):
    """Layer 1 출력"""
    health_grade: str   # A/B/C/D/F
    f_score: int        # 0-9
    z_score: float | None
    margin_trend: str   # improving/declining/stable
    gross_margin: float | None
    operating_margin: float | None
    net_margin: float | None
    roe: float | None
    debt_ratio: float | None
    earnings_beat_streak: int
    metrics: dict       # 상세 수치

class TechnicalProfile(BaseModel, frozen=True):
    """Layer 3 출력"""
    technical_grade: str    # Bullish/Neutral/Bearish
    trend_alignment: str    # aligned_up/aligned_down/mixed
    position_52w_pct: float # 52주 고저 대비 위치 (0-100%)
    rsi: float | None
    macd_signal: str | None # bullish/bearish/neutral
    nearest_support: float | None
    nearest_resistance: float | None
    relative_strength_pct: float | None
    atr_regime: str         # High/Normal/Low
    metrics: dict

class FlowProfile(BaseModel, frozen=True):
    """Layer 4 출력"""
    flow_grade: str          # Accumulation/Neutral/Distribution
    insider_net_90d: float   # 순매수/매도 금액
    insider_signal: str      # net_buy/net_sell/neutral
    short_ratio: float | None
    short_pct_float: float | None
    analyst_buy_pct: float | None
    analyst_target_upside: float | None
    institutional_change: str | None  # increasing/decreasing/stable
    metrics: dict

class AIResult(BaseModel, frozen=True):
    """AI 분석 결과"""
    action_grade: str   # HOLD/ADD/TRIM/EXIT
    conviction: int     # 1-10
    uncertainty: str    # low/medium/high
    reasoning: str      # 한국어 종합 판단
    what_missing: str | None

class DeepDiveResult(BaseModel):
    """종목별 통합 결과"""
    ticker: str
    stock_id: int
    current_price: float
    daily_change_pct: float
    layer1: FundamentalHealth | None
    layer3: TechnicalProfile | None
    layer4: FlowProfile | None
    ai_result: AIResult | None  # Step 5에서 세팅
```

**layers.py 핵심 함수:**

```python
def compute_layer1_fundamental(session: Session, stock_id: int) -> FundamentalHealth | None:
    """Layer 1: 펀더멘털 헬스체크
    재사용: calculate_piotroski() (src/analysis/quality.py)
           calculate_altman_z() (src/analysis/quality.py)
    데이터: FactFinancial 최근 8분기, FactEarningsSurprise 최근 4분기
    그레이드: A(F>=7,Z>=3) B(F>=5) C(F>=3) D(F<3|Z<1.8) F(둘 다 나쁨)"""

def compute_layer3_technical(session: Session, stock_id: int, date_id: int) -> TechnicalProfile | None:
    """Layer 3: 멀티타임프레임 기술적
    재사용: calculate_indicators() (src/analysis/technical.py)
           detect_signals() (src/analysis/signals.py)
           find_support_resistance() (src/analysis/support_resistance.py)
    신규: 52주 위치, 추세 정렬(일/주/월), ATR 레짐
    데이터: FactDailyPrice 최근 252거래일, FactIndicatorValue"""

def compute_layer4_flow(session: Session, stock_id: int) -> FlowProfile | None:
    """Layer 4: 수급/포지셔닝
    데이터: FactInsiderTrade 최근 90일, FactInstitutionalHolding,
           FactAnalystConsensus, FactValuation(short_ratio)
    신규: C-suite 가중치 2x, 순매수/매도 금액, 옵션은 Phase 1 제외"""

def compute_all_layers(session: Session, stock_id: int, date_id: int) -> dict:
    """3개 레이어 통합 계산. 각 레이어 try/except 독립 실행.
    반환: {"layer1": FundamentalHealth|None, "layer3": ..., "layer4": ...}"""
```

**Layer 1 상세 로직:**
1. `FactFinancial` 최근 8분기 로드 → gross/operating/net margin QoQ 추세
2. `calculate_piotroski()` 직접 호출 → F-Score (0-9)
3. `calculate_altman_z()` 직접 호출 → Z-Score
4. ROE = `net_income / total_equity`, 부채비율 = `total_liabilities / total_assets`
5. `FactEarningsSurprise` 최근 4분기 → beat 연속 횟수
6. 그레이드 판정: A(F>=7 & Z>=3), B(F>=5), C(F>=3), D(F<3 | Z<1.8), F(F<3 & Z<1.8)

**Layer 3 상세 로직:**
1. `FactDailyPrice` 최근 252일 → DataFrame 구성
2. `calculate_indicators()` 호출 → RSI, MACD, SMA 등
3. 52주 위치: `(close - 52w_low) / (52w_high - 52w_low) * 100`
4. 추세 정렬: close vs SMA20 vs SMA50 정배열/역배열/혼합
5. `find_support_resistance()` → 가장 가까운 S/R
6. ATR 레짐: `14d_ATR / close * 100` vs 20d SMA → High/Normal/Low
7. 그레이드: Bullish(3+ 매수 시그널 + 추세 상승), Bearish(3+), Neutral

**Layer 4 상세 로직:**
1. `FactInsiderTrade` 최근 90일 → C-suite 2x 가중 순매수/매도 금액
2. `FactValuation` 최신 → short_ratio, short_pct_of_float
3. `FactAnalystConsensus` 최신 → buy_pct = (strong_buy + buy) / total
4. `FactInstitutionalHolding` → 순증/순감 방향
5. 그레이드: Accumulation(내부자 매수 + 낮은 공매도), Distribution(매도 + 높은 공매도)

**테스트:**
- `test_layer1_good_financials` — F-Score 7+, grade A
- `test_layer1_no_data` — None 반환 (크래시 없음)
- `test_layer1_margin_trend` — 4분기 추세 판정
- `test_layer3_52w_position` — 정확한 % 계산
- `test_layer3_trend_alignment` — 정배열/역배열 감지
- `test_layer4_insider_net_buy` — 순매수 = Accumulation
- `test_layer4_no_data` — None 반환
- `test_compute_all_layers` — 3개 모두 dict에 포함

**완료 정의:** 8개 테스트 통과, 레이어별 독립 실패 격리, 파일 350줄 이내

---

### Task 7: DeepDivePipeline 뼈대 (3h)

**파일 생성:** `src/deepdive_pipeline.py` (~300줄)

**클래스 구조:**

```python
class DeepDivePipeline:
    """Deep Dive 전용 파이프라인. DailyPipeline 패턴 복제."""

    def __init__(self, engine: Engine, target_date: date | None = None,
                 ticker: str | None = None, force: bool = False,
                 skip_notify: bool = False):
        self.engine = engine
        self.target_date = target_date or date.today()
        self.ticker = ticker.upper() if ticker else None
        self.force = force
        self.skip_notify = skip_notify
        self.run_date_id = date_to_id(self.target_date)
        self._interrupted = False
        # SIGTERM/SIGINT 핸들링 (기존 패턴)
        # step 간 데이터 전달
        self._watchlist_entries: list[WatchlistEntry] = []
        self._layer_results: dict[str, dict] = {}
        self._ai_results: dict[str, AIResult] = {}

    # --- 인프라 메서드 (DailyPipeline에서 복제) ---
    def _handle_signal(self, signum, frame): ...
    def _is_step_done(self, step_name: str) -> bool: ...
    def _log_step(self, step_name: str, status: str, started: datetime,
                  records_count: int = 0, message: str | None = None): ...

    # --- 실행 ---
    def run(self) -> None:
        """전체 파이프라인 실행."""

    # --- Step 구현 ---
    def step1_load_watchlist(self) -> int:    # dd_s1_load
    def step2_collect_extras(self) -> int:    # dd_s2_collect
    def step3_compute_layers(self) -> int:    # dd_s3_compute
    def step5_ai_analysis(self) -> int:       # dd_s5_ai
    def step7_persist(self) -> int:           # dd_s7_persist
    def step8_notify(self) -> int:            # dd_s8_notify
```

**run() 메서드 흐름:**
```python
def run(self) -> None:
    ensure_date_ids(session, [self.target_date])

    steps = [
        ("dd_s1_load",    self.step1_load_watchlist),
        ("dd_s2_collect", self.step2_collect_extras),
        ("dd_s3_compute", self.step3_compute_layers),
        ("dd_s5_ai",      self.step5_ai_analysis),
        ("dd_s7_persist", self.step7_persist),
        ("dd_s8_notify",  self.step8_notify),
    ]

    for step_name, step_fn in steps:
        if self._interrupted: break
        if not self.force and self._is_step_done(step_name):
            console.print(f"  {step_name} 이미 완료, 스킵")
            continue
        started = datetime.now()
        try:
            count = step_fn()
            self._log_step(step_name, "success", started, count)
        except Exception as e:
            self._log_step(step_name, "failed", started, message=str(e))
```

**Step 상세:**

| Step | 입력 | 처리 | 출력 |
|------|------|------|------|
| `dd_s1_load` | DB | `load_watchlist()` + ticker 필터 | `self._watchlist_entries` |
| `dd_s2_collect` | entries | 비S&P500이거나 오늘 데이터 없는 종목만 수집. `batch_download_prices()`, `fetch_financial_data()` 재사용 | 수집 레코드 수 |
| `dd_s3_compute` | DB | 종목별 `compute_all_layers()`. 개별 실패 격리 | `self._layer_results` |
| `dd_s5_ai` | layers + entries | 종목별 `run_deepdive_simple()`. 개별 실패 격리 | `self._ai_results` |
| `dd_s7_persist` | 전체 결과 | `DeepDiveRepository.insert_report()` + `insert_action()`. force 시 기존 row 삭제 후 INSERT | INSERT 수 |
| `dd_s8_notify` | 요약 | `send_deepdive_summary()` 호출 | 1 or 0 |

**force 재실행 전략:**
- `step7_persist`에서 force=True일 때 해당 date_id의 기존 reports/actions DELETE 후 INSERT
- 이렇게 하면 UniqueConstraint(stock_id, date_id) 충돌 방지 + "절대 덮어쓰지 않음" 원칙과 양립

**테스트:**
- `test_pipeline_init` — 속성 확인
- `test_checkpointing_skips` — _is_step_done True → 스킵
- `test_force_reruns` — force=True → 재실행
- `test_single_ticker_filter` — ticker 지정 시 해당 종목만
- `test_per_ticker_isolation` — 1개 실패해도 나머지 계속
- `test_step7_inserts` — fact_deepdive_reports row 생성 확인
- `test_graceful_shutdown` — _interrupted=True → 즉시 종료

**완료 정의:** 파이프라인 end-to-end 실행 (mock 외부), checkpointing 동작, 300줄 이내

---

### Task 8: Simple AI — 단일 CLI 호출 (2h)

**파일 생성:** `src/deepdive/ai_prompts.py` (~180줄)

**핵심 함수:**

```python
DEEPDIVE_SYSTEM_PROMPT: str  # Synthesizer 페르소나 (한국어)

def build_stock_context(entry: WatchlistEntry, layers: dict,
                        current_price: float, daily_change: float) -> str:
    """<stock_context> XML 블록 빌드.
    입력: WatchlistEntry, layers dict, 현재가, 일간변화
    출력: XML 문자열
    보유정보 있으면 <holding_context> 삽입, 없으면 생략"""

def build_deepdive_prompt(stock_context: str) -> str:
    """유저 프롬프트 빌드.
    출력: 전체 프롬프트 문자열"""

def run_deepdive_simple(entry: WatchlistEntry, layers: dict,
                        current_price: float, daily_change: float,
                        timeout: int = 600, model: str = "opus") -> AIResult | None:
    """Phase 1 단일 CLI 호출.
    1. build_stock_context + build_deepdive_prompt
    2. run_deepdive_cli(prompt, DEEPDIVE_SYSTEM_PROMPT, timeout, model)
    3. _parse_ai_response → AIResult
    4. 실패 시 None"""

def run_deepdive_cli(prompt: str, system_prompt: str | None = None,
                     timeout: int = 600, model: str = "opus") -> str | None:
    """Claude CLI 호출 (master_plan 섹션 8과 동일).
    subprocess.run([claude_path, "-p", "--model", model, "--system-prompt", ...])
    실패/타임아웃 시 None"""

def _parse_ai_response(raw: str) -> AIResult | None:
    """JSON 파싱 + regex fallback.
    재사용: _try_parse_json 패턴 (src/ai/claude_analyzer.py)"""
```

**시스템 프롬프트 핵심:**
```
너는 30년 경력 수석 CIO다. 제공된 데이터를 기반으로 종목을 분석하고 최종 판단을 내려라.

판단 기준:
1. 펀더멘털 건전성 (Layer 1)
2. 기술적 추세와 모멘텀 (Layer 3)
3. 수급 흐름 (Layer 4)
4. 리스크/보상 비대칭성

보유자 관점 (보유 종목만):
- HOLD = 현 포지션 유지
- ADD = 추가 매수 (확신 높을 때)
- TRIM = 일부 매도
- EXIT = 전량 매도 (확신 높을 때)

출력 (JSON만):
{"action_grade":"HOLD", "conviction":7, "uncertainty":"medium",
 "reasoning":"200자 이내 종합 판단", "what_missing":"반대 의견"}
```

**보유정보 주입:**
```xml
<holding_context>
보유 수량: {shares}주 | 평단가: ${avg_cost}
보유 수익률: {pnl_pct}% (${pnl_amount})
보유 기간: {holding_days}일
포지션 가치: ${position_value}
</holding_context>
```

**테스트:**
- `test_build_context_with_holding` — `<holding_context>` 포함
- `test_build_context_no_holding` — `<holding_context>` 미포함
- `test_cli_model_flag` — subprocess mock, `--model opus` 확인
- `test_cli_system_prompt` — `--system-prompt` args 확인
- `test_parse_valid_json` — 정상 JSON → AIResult
- `test_parse_malformed` — regex fallback 동작
- `test_cli_not_available` — claude 미설치 → None
- `test_cli_timeout` — TimeoutExpired → None

**완료 정의:** CLI 호출 패턴 정확, 보유정보 주입 동작, Anthropic SDK import 없음, 180줄 이내

---

### Task 9: Web — `/personal` 카드 그리드 (2.5h)

**파일 생성:**
- `src/web/routes/personal.py` (~100줄)
- `src/web/templates/personal.html` (~180줄)

**파일 수정:**
- `src/web/app.py` — `from src.web.routes.personal import router as personal_router` + `app.include_router(personal_router)` 추가
- `src/web/templates/base.html` — 네비게이션에 "개인 분석" (`/personal`) 링크 추가

**라우트:**

```python
router = APIRouter(tags=["personal"])

@router.get("/personal")
def personal_dashboard(request: Request, db: Session = Depends(get_db)):
    """워치리스트 카드 그리드.
    쿼리:
    1. WatchlistRepository.get_active(db)
    2. WatchlistRepository.get_all_holdings(db)
    3. DeepDiveRepository.get_latest_reports_all(db)
    4. 종목별 최신 가격 (FactDailyPrice)
    조합: 카드 데이터 리스트
    반환: TemplateResponse("personal.html", {...})"""
```

**카드 구성 (각 종목):**
- 티커 (bold) + 종목명 (smaller)
- 현재가 + 일간 수익률 (녹/적)
- 액션 배지: HOLD(gray), ADD(green), TRIM(orange), EXIT(red) — 색상 pill
- Conviction 바: 10단계 수평 바
- 보유 종목: "보유: N주 @ $X.XX | P&L: +Y.Y% ($Z)" (녹/적)
- 분석 미완료: "분석 대기중" (dimmed)
- 하단: "투자 참고용이며 투자 권유가 아닙니다"

**템플릿 레이아웃:**
- extends `base.html`
- Tailwind 반응형 그리드: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4`
- 다크모드: `dark:` prefix
- 빈 워치리스트: "워치리스트가 비어 있습니다" 안내

**테스트:**
- `test_personal_page_200` — GET /personal → 200
- `test_personal_empty` — 빈 워치리스트 → 안내 메시지
- `test_personal_with_data` — 시드 + 리포트 → 카드 HTML 확인
- `test_personal_holding_pnl` — 보유정보 → P&L 렌더링

**완료 정의:** 페이지 정상 렌더링, 다크모드, 반응형, 빈 상태 처리

---

### Task 10: Notification + Config + Glue (2h)

**파일 수정:**
- `src/alerts/notifier.py` — `send_deepdive_summary()` 추가 (~30줄)
- `src/config.py` — `ai_model_deepdive`, `deepdive_timeout` 설정 추가 (~6줄)
- `src/ai/constants.py` — `NON_TICKERS`에 "ADD", "TRIM", "EXIT" 추가

**Notification 함수:**

```python
def send_deepdive_summary(
    run_date: date,
    stock_count: int,
    action_summary: dict[str, int],  # {"ADD": 2, "TRIM": 1, "HOLD": 9}
    failed_count: int = 0,
    channel: str | None = None,
) -> bool:
    """Deep dive 완료 알림 1줄.
    메시지: '[Investmate] Deep dive 완료: 12종목 분석, ADD 2건, TRIM 1건'
    패턴: 기존 send_daily_summary/send_weekly_summary와 동일"""
```

**Config 추가:**

```python
ai_model_deepdive: str = Field(default="opus", alias="INVESTMATE_AI_MODEL_DEEPDIVE")
deepdive_timeout: int = Field(default=600, alias="INVESTMATE_DEEPDIVE_TIMEOUT")
```

**테스트 파일 생성 (4개):**
- `tests/test_deepdive_watchlist.py` — Task 2/3/4 검증 (~80줄)
- `tests/test_deepdive_layers.py` — Task 6 검증 (~120줄)
- `tests/test_deepdive_pipeline.py` — Task 7 검증 (~100줄)
- `tests/test_deepdive_web.py` — Task 9 검증 (~60줄)

**완료 정의:** 전체 테스트 통과, 알림/설정 동작

---

## 3. Task 간 의존성 그래프

```
Task 1 (DB Schema)
  │
  ▼
Task 2 (Repositories)
  │
  ├──────────┬──────────┐
  ▼          ▼          ▼
Task 3     Task 4     Task 5 (CLI watchlist 부분만)
(Manager)  (Seed)
  │          │
  ▼          │
Task 6 ◄────┘
(Layers)
  │
  ├──────────┐
  ▼          ▼
Task 8     Task 9
(AI)       (Web)
  │          │
  ▼          │
Task 7 ◄────┘
(Pipeline)
  │
  ▼
Task 10 (Glue + Tests)
  │
  ▼
Task 5 완성 (deepdive run 연동)
```

**권장 실행 순서:** 1 → 2 → 3 → 4 → 6 → 8 → 7 → 9 → 5 → 10

**병렬 가능 구간:**
- Task 3, 4는 Task 2 완료 후 병렬 가능
- Task 8, 9는 Task 6 완료 후 병렬 가능

---

## 4. 기존 모듈 재사용 매핑

| Deep Dive 기능 | 재사용 함수 | 위치 |
|---|---|---|
| 가격 수집 | `batch_download_prices()` | `src/data/providers/yfinance_provider.py` |
| 재무 수집 | `fetch_financial_data()` | `src/data/providers/yfinance_provider.py` |
| 내부자/기관/애널리스트 | `collect_all_enhanced()` | `src/data/enhanced_collector.py` |
| F-Score | `calculate_piotroski()` | `src/analysis/quality.py` |
| Z-Score | `calculate_altman_z()` | `src/analysis/quality.py` |
| 기술 지표 | `calculate_indicators()` | `src/analysis/technical.py` |
| 시그널 감지 | `detect_signals()` | `src/analysis/signals.py` |
| S/R 레벨 | `find_support_resistance()` | `src/analysis/support_resistance.py` |
| 상대강도 | `calculate_rs_ranks()` | `src/analysis/relative_strength.py` |
| 체크포인팅 | `_is_step_done()`, `_log_step()` 패턴 | `src/pipeline.py` |
| DB 세션 | `get_session()` | `src/db/engine.py` |
| 날짜 유틸 | `date_to_id()`, `ensure_date_ids()` | `src/db/helpers.py` |
| 웹 의존성 | `get_db()` | `src/web/deps.py` |
| 텔레그램 알림 | `_send_telegram()` | `src/alerts/notifier.py` |
| JSON 파싱 | `_try_parse_json()` 패턴 | `src/ai/claude_analyzer.py` |

---

## 5. Phase 1 통합 테스트 시나리오

### 시나리오 A: 첫 실행 Happy Path
1. `seed_watchlist.py` 실행 → 12종목 dim_watchlist + 비S&P500 자동 등록
2. `investmate deepdive run --force` (CLI mock) → 12 reports + 12 actions INSERT
3. GET `/personal` → 12개 카드 렌더링 (action 배지, conviction 바)

### 시나리오 B: 단일 종목 모드
1. `investmate deepdive run --ticker AAPL --force`
2. fact_deepdive_reports에 AAPL row 1개만 생성
3. 다른 종목 미영향

### 시나리오 C: 체크포인팅
1. 첫 실행 성공 완료
2. 두 번째 실행 (no --force) → 모든 step "이미 완료, 스킵"
3. 두 번째 실행 (--force) → 기존 row 삭제 후 새 row 생성

### 시나리오 D: CRUD 흐름
1. `watchlist add COIN` → dim_watchlist + dim_stocks 자동 등록
2. `watchlist set-holding NVDA --shares 100 --avg-cost 130.50`
3. `watchlist remove TSLA` → active=False
4. `deepdive run` → COIN 포함, TSLA 제외 (11종목 + COIN = 12종목)

### 시나리오 E: 보유정보 + Web
1. NVDA 보유정보 설정 (100주 @ $130.50)
2. deepdive 실행 (AI 결과: ADD, conviction 8)
3. `/personal` → NVDA 카드에 녹색 ADD 배지 + "보유: 100주 @ $130.50 | P&L: +X.X%"

### 시나리오 F: 부분 실패 복원력
1. 1개 종목 layer 계산에서 예외 발생 (mock)
2. 나머지 11종목 정상 처리
3. 파이프라인 overall status: "success" (11/12)
4. 실패 종목 로깅

---

## 6. Phase 1 완료 검증 방법

- [ ] **DB**: `investmate db status` → 7개 신규 테이블 존재
- [ ] **시드**: `python scripts/seed_watchlist.py` → 12종목, 재실행 안전
- [ ] **CLI CRUD**: add/remove/list/set-holding 4개 명령 정상
- [ ] **자동등록**: 비S&P500 ticker → dim_stocks(is_sp500=False) 생성
- [ ] **파이프라인**: `investmate deepdive run --ticker AAPL --force` 완료
- [ ] **레이어**: Layer 1/3/4 크래시 없이 계산 (실제 데이터)
- [ ] **AI**: CLI 호출 `--model opus --system-prompt` 사용, JSON 파싱
- [ ] **보유정보**: NVDA(보유) → holding_context 포함, GOOG(비보유) → 미포함
- [ ] **영속성**: reports INSERT only (UPDATE 없음), actions INSERT
- [ ] **Web**: `/personal` 카드 그리드, 다크모드, 빈 상태 처리
- [ ] **Nav**: base.html "개인 분석" 링크 (데스크톱 + 모바일)
- [ ] **알림**: `send_deepdive_summary()` 텔레그램 1줄
- [ ] **테스트**: `pytest tests/test_deepdive_*.py -v` 전체 통과
- [ ] **커버리지**: `--cov=src/deepdive --cov=src/deepdive_pipeline` 80%+
- [ ] **파일 크기**: 신규 파일 400줄 미만
- [ ] **한국어**: 모든 사용자 대면 텍스트 한국어
- [ ] **면책**: "투자 참고용이며 투자 권유가 아닙니다"

---

## 7. Phase 2로 넘어가기 전 정리할 기술 부채

| # | 항목 | 설명 | 영향 |
|---|------|------|------|
| 1 | **report_json 스키마** | Phase 1은 자유 JSON blob. Phase 2 상세페이지가 안정적으로 파싱하려면 Pydantic 모델로 구조 정의 필요 | Phase 2 상세 페이지 |
| 2 | **AI 비용 추적** | Phase 1은 deepdive AI 비용 미추적. `src/ai/cost_tracker.py` 연동 필요 | 운영 비용 관리 |
| 3 | **force 재실행 전략** | Phase 1은 DELETE+INSERT. 이력 보존이 중요해지면 report_version 컬럼 추가 검토 | 데이터 정합성 |
| 4 | **나머지 3개 레이어** | Layer 2(DCF, 5년 백분위 — FactValuation 히스토리 필요), Layer 5(뉴스 감성 — FactNews 집계), Layer 6(베타 회귀 — scipy 의존) | Phase 2 필수 |
| 5 | **3라운드 토론** | `run_deepdive_simple()` → `run_deepdive_debate()` 리팩토링. 기존 `debate.py` 패턴 참고하되 CLI 기반으로 전환 | Phase 2 AI 고도화 |
| 6 | **시나리오 예측 적재** | fact_deepdive_forecasts 빈 테이블. 9개 row/종목/일 파싱 + actual_price 업데이트 로직 | Phase 2 예측 기능 |
| 7 | **FactCollectionLog step 이름** | 현재 `dd_s1_load` 등 25자 이내. Phase 2에서 `dd_s5_ai_bull_r1` 같은 서브스텝 추가 시 30자 제한 주의 | 컬럼 길이 |
| 8 | **비S&P500 데이터 완전성** | SMR 등 enhanced data(기관/애널리스트) 부실 가능. Layer 4 N/A 케이스 처리는 했으나 데이터 품질 모니터링 필요 | 분석 정확도 |
| 9 | **5년 밸류에이션 히스토리** | 신규 등록 종목은 DB 히스토리 부족. Layer 2에서 "데이터 부족" 표시 로직 필요 | Phase 2 Layer 2 |
| 10 | **cards.js 커스텀 차트** | Phase 1은 기존 차트만 사용. Phase 2에서 `scenarioRangeChart`, `layerRadarChart` 추가 시 charts.js 확장 | Phase 2 UI |
