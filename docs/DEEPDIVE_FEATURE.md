# Deep Dive 개인 분석 — 기능 명세서

> Phase 1 ~ Phase 12 전체 구현 완료 · Phase 12에서 89개 테스트 신규 추가
> 최종 업데이트: 2026-04-11 (Phase 12: 사용자 행동 가능성 레이어)

---

## 1. 기능 개요

사용자가 직접 관리하는 워치리스트(~12종목)를 매일 자동으로 **심층 분석 → 정량 매매 가이드 → 알림**까지 이어지는 개인 투자자 전용 파이프라인. 기존 DailyPipeline(S&P 500 전체 스캔)과 **완전히 독립된 별도 프로세스**로 동작한다.

### 핵심 가치

| 가치 | 설명 |
|------|------|
| **개인화 분석** | 워치리스트 종목만 6레이어 심층 분석 |
| **AI 토론** | Bull / Bear / Synthesizer 3라운드 멀티 에이전트 토론 |
| **시나리오 예측** | 3 호라이즌(1M/3M/6M) × 3 시나리오(BASE/BULL/BEAR) = 9개 |
| **실행 가이드** | Buy zone / Stop loss / Targets / EV / R/R / 제안 포지션 비중 (Phase 5) |
| **근거 추적** | AI 판단 근거를 "layer3.rsi=72" 형태로 수집·UI 노출 (Phase 4) |
| **포트폴리오 적합도** | 섹터/종목 상한 여유를 AI 프롬프트에 주입 + 경고 (Phase 10) |
| **정확도 보정** | 과거 horizon별 hit_rate로 EV 자동 디스카운트 (Phase 9) |
| **장중 알림** | Buy zone 진입 / Stop 근접 / Target 도달 자동 푸시 (Phase 8) |
| **일일 변경 감지** | 전일 대비 액션/확신도/확률/리스크/트리거 변경 추적 |
| **페어 비교** | 동종 섹터+시총+코사인 유사도 기반 top 5 페어 자동 선정 |
| **예측 정확도** | 만기 도래 예측의 적중률·방향 정확도 자동 측정 |
| **히스토리 추적** | 모든 분석 결과를 날짜별로 DB에 영구 보존 |
| **웹 보유 관리** | 워치리스트/보유를 모달·CSV로 직접 관리 (Phase 12a) |
| **인앱 알림 센터** | 모든 알림을 DB에 영구 저장, 웹에서 필터·확인·헤더 뱃지 (Phase 12b) |
| **What-if 시뮬레이터** | 보유 변경을 가상 적용해 리밸런싱·섹터 분포를 즉시 미리보기 (Phase 12c) |
| **종목 직접 비교** | 2~4개 종목의 6레이어/AI/EV를 사이드바이사이드 + 레이더 오버레이 (Phase 12d) |

### 설계 원칙

- **DailyPipeline과 분리**: watchlist 전용 사이클. 서로의 실패가 전파되지 않는다.
- **결정론 우선**: AI 판단 위에 Layer 데이터 기반 결정론적 계산을 덧입혀 재현 가능성·신뢰성 확보.
- **근거 추적성**: AI 주장 → 구체 데이터 포인트 연결. Synthesizer의 구조화 출력을 UI까지 무손실 전달.
- **장애 격리**: 한 종목 실패가 나머지 종목의 분석·가이드·알림을 막지 않는다.
- **점진적 개선**: Phase 4~10은 기존 Phase 1~3 위에 **추가**된 레이어다. 구버전 리포트도 안전하게 렌더된다.

---

## 2. 9단계 파이프라인

```
Step 1: dd_s1_load        — 워치리스트 로드 + 자동 등록
Step 2: dd_s2_collect     — 비 S&P500 / 오늘 데이터 없는 종목 수집
Step 3: dd_s3_compute     — 6개 레이어 계산
Step 4: dd_s4_pairs       — 페어 자동 선정 (7일 staleness)
Step 5: dd_s5_ai          — 3라운드 CLI 토론 + 포트폴리오 컨텍스트 주입
Step 5.5: dd_s5_5_guide   — 실행 가이드 계산 + hit_rate 디스카운트 [Phase 5/9]
Step 6: dd_s6_diff        — 전일 대비 변경 감지
Step 7: dd_s7_persist     — DB 저장 + 만기 예측 평가
Step 8: dd_s8_notify      — 알림 엔진 + Telegram 요약 [Phase 8]
```

**특성:**
- **Resilient**: 개별 종목 실패 격리. 전체 파이프라인은 중단되지 않는다.
- **Step Checkpointing**: `FactCollectionLog`에 step별 success 기록. `--force` 없으면 재실행 스킵.
- **Graceful Shutdown**: SIGTERM/SIGINT → 현재 스텝 완료 후 종료.
- **Summary JSON**: `logs/{date}_deepdive_summary.json`에 각 스텝 결과 저장.

---

## 3. 6레이어 분석 엔진

워치리스트 각 종목을 6개 축으로 독립 평가. 각 레이어는 pydantic `frozen=True` DTO로 반환되어 불변성 보장.

### Layer 1: 펀더멘털 (`layers_fundamental.py`)
- **Grade**: A / B / C / D / F
- **지표**: F-Score(0-9), Altman Z-Score, gross/operating/net margin, ROE, 부채비율, 실적 beat streak
- **출처**: `FactFinancial`, `FactValuation`, `FactEarningsSurprise`

### Layer 2: 밸류에이션 (`layers_valuation.py`)
- **Grade**: Cheap / Fair / Rich / Extreme
- **지표**: PER 5년 백분위, PBR 5년 백분위, EV/EBITDA 백분위, 섹터 PER/PBR 프리미엄, DCF 내재 성장률, PEG, FCF Yield

### Layer 3: 기술적 (`layers_technical.py`)
- **Grade**: Bullish / Neutral / Bearish
- **지표**: trend_alignment(aligned_up/down/mixed), 52주 위치, RSI(14), MACD 시그널, 최근접 S/R, atr_regime, **atr_14 원시값** (Phase 5에서 execution_guide가 손절 계산에 사용), **current_close**

### Layer 4: 수급 (`layers_flow.py`)
- **Grade**: Accumulation / Neutral / Distribution
- **지표**: 내부자 90일 순매수, short float %, 애널리스트 매수 %, 목표가 업사이드, 기관 변동

### Layer 5: 내러티브 (`layers_narrative.py`)
- **Grade**: Positive / Neutral / Negative
- **지표**: 30/60/90일 감성, sentiment_trend, 임박 촉매, 리스크 이벤트, 임원 변동

### Layer 6: 매크로 (`layers_macro.py`)
- **Grade**: Favorable / Neutral / Headwind
- **지표**: VIX/10Y/Dollar 베타, 섹터 모멘텀 순위, 현재 레짐, 레짐 평균 수익률

### Layer 실행 안정성 (Phase 4)

- **`_safe_call`** 헬퍼: 각 레이어 계산을 try/except로 감싸고 실패 시 `logger.error` + 결과 dict에서 해당 키 **누락**.
- **ImportError 명시화**: 선택 레이어(2/5/6) 모듈이 없으면 `logger.error("layer2 모듈 import 실패: ...")` — 이전엔 silent drop이었음.
- **관측성**: logs/ 디렉토리에 실패 기록 남으므로 개발 중 이슈를 조기 발견.

---

## 4. AI 토론 엔진 (Phase 4 강화)

### 3라운드 구조 (`ai_debate_cli.py`)

```
R1: Bull 독립 분석 → Bull R1 JSON
R1: Bear 독립 분석 → Bear R1 JSON
R2: Bull 반박 (Bear R1 읽고 재분석) → Bull R2 JSON
R2: Bear 반박 (Bull R1 읽고 재분석) → Bear R2 JSON
R3: Synthesizer 종합 (Bull R2 + Bear R2) → 최종 JSON
```

- 한 종목당 **5회 순차 Claude CLI 호출**. 종목 간 순차(병렬화는 플래그 기반 향후 과제).
- R1 실패 시 R2 스킵. R3 실패 시 `run_deepdive_simple()` 폴백 (consensus_strength = low).

### CLI 호출 안정화 (Phase 4)

`run_deepdive_cli(..., max_attempts=2)`:

1. 첫 응답이 비거나 JSON 파싱 불가면 **1회 재시도**
2. `_has_parseable_json()` — 응답에 `action_grade` 또는 `action` 필드가 있는 JSON 객체가 있는지 확인
3. 재시도도 실패하면 마지막 raw output 반환 (regex fallback 기회 유지)

### Synthesizer 구조화 출력 (Phase 4)

Synthesizer가 반드시 내놓아야 하는 JSON 필드:

```json
{
  "action_grade": "HOLD|ADD|TRIM|EXIT",
  "conviction": 1-10,
  "uncertainty": "low|medium|high",
  "reasoning": "300~600자 구체 수치 인용",
  "scenarios": {"1M": {"base":..., "bull":..., "bear":...}, "3M":..., "6M":...},
  "consensus_strength": "high|medium|low",
  "what_missing": "반대 의견",
  "key_levels": {"support": 가격, "resistance": 가격, "stop_loss": 가격},
  "next_review_trigger": "재검토 조건",
  "evidence_refs": ["layer3.rsi=72", "layer1.f_score=8", ...],
  "invalidation_conditions": ["RSI 40 하회", ...]
}
```

이전 버전은 `key_levels`를 요구했지만 `AIResult` 스키마에 필드가 없어 **파싱 즉시 버려졌다**. Phase 4는 이 구조를 완전히 복구한다.

### AIResult 확장 필드

```python
class AIResult(BaseModel, frozen=True):
    action_grade: str
    conviction: int
    uncertainty: str
    reasoning: str                                 # 2000자 (기존 500자 → 완화)
    what_missing: str | None = None
    # Phase 4: 근거 추적
    support_price: float | None = None
    resistance_price: float | None = None
    stop_loss: float | None = None
    next_review_trigger: str | None = None
    evidence_refs: tuple[str, ...] = ()            # frozen 호환 tuple
    invalidation_conditions: tuple[str, ...] = ()
```

### 포트폴리오 컨텍스트 주입 (Phase 10)

`build_stock_context`는 `portfolio_context` 인자를 받아 `<portfolio_context>` XML 블록을 프롬프트에 삽입한다:

```xml
<portfolio_context>
보유 종목 수: 8
섹터 분포: Technology=35%, Healthcare=20%, Financials=15%, ...
이 종목 섹터(Technology) 기존 비중: 35.0% (상한 30%, 여유 -5.0%p)
이 종목 기존 비중: 0.0% (상한 10%, 여유 +10.0%p)
</portfolio_context>
```

Synthesizer 프롬프트에는 **"포트폴리오 적합도 — 섹터/종목 여유를 고려하여 conviction 하향 조정"** 지침이 추가되어, AI가 이미 포화된 섹터에는 신규 진입을 자제하도록 유도한다.

파이프라인은 Step 5 시작 시 `_compute_existing_weights()`를 1회 호출해 모든 토론에 동일 컨텍스트를 주입한다.

---

## 5. 실행 가이드 엔진 (Phase 5)

**역할**: AI의 추상적 판단(`ADD / conviction 8`)을 개인 투자자가 **지금 얼마에 사고 얼마에 자를 것인가**로 변환.

모듈: `src/deepdive/execution_guide.py`
DTO: `ExecutionGuide` (frozen dataclass)

### 계산 필드

| 필드 | 의미 | 계산 방식 |
|------|------|----------|
| `buy_zone_low/high` | 진입 존 | `max(현재가×0.97, support×1.01) ~ min(현재가×1.01, resistance×0.98)` |
| `buy_zone_status` | in_zone / wait / above_zone / below_zone | RSI≥70이면 wait |
| `stop_loss` | 손절가 | AI / support-ATR×0.5 / trailing / ATR×N 중 **가장 보수적**(= 가장 높은 값) |
| `stop_loss_source` | ai / support / trailing / atr | 선택된 근거 |
| `target_1m/3m/6m` | 호라이즌별 목표가 | `Σ(prob × midpoint)` 확률 가중 평균 |
| `expected_value_pct` | EV % | `(target - current) / current × 100` |
| `risk_reward_ratio` | R/R (3M 기준) | `(target_3m - current) / (current - stop)` |
| `risk_reward_label` | favorable / neutral / unfavorable | ≥2.5 / 1.5~2.5 / <1.5 |
| `suggested_position_pct` | 제안 비중 % | `max_stock_pct × (conviction/10)² × sigmoid_tilt × rr_boost`, 상한 클립 |
| `position_rationale` | 산출 근거 한 줄 | "conviction 8/10 · sigmoid tilt ×1.42 · R/R 2.8 우호 · 상한 10%" |
| `portfolio_fit_warnings` | 포트폴리오 제약 경고 | 섹터/종목 상한 초과 시 |
| `action_hint` | now / wait_pullback / avoid / sell / hold | 최종 UI 조언 |

### 입력 우선순위

- **AI key_levels**가 있으면 `layer3.nearest_support/resistance`보다 우선
- **AI stop_loss**는 후보 중 하나로 참여 (가장 보수적이지 않으면 채택 안 됨)
- `layer3.metrics.atr_14`로 ATR 기반 stop 계산 (Phase 5에서 technical layer에 추가)

### 결정론 보장

execution_guide의 **모든 계산은 순수 함수**. 동일 입력 → 동일 출력. AI 없이도 재현 가능하며 35개 단위 테스트로 경계값·오류 케이스 검증.

### 과거 정확도 기반 EV 디스카운트 (Phase 9)

파이프라인 Step 5.5에서 `get_historical_hit_rates(session, ticker, min_samples=10)`으로 horizon별 과거 적중률을 조회한 뒤, `apply_hit_rate_discount()`로 EV를 보정:

```
discounted_ev = ev × max(hit_rate, 0.30)
```

- **min_samples=10** 미만이면 해당 horizon 생략 (원본 유지)
- **floor=0.30**으로 과도한 디스카운트 방지
- 자체 캘리브레이션 루프 — 예측이 빗나가는 종목/호라이즌의 EV가 자동으로 낮아진다

---

## 6. 알림 엔진 (Phase 8)

모듈: `src/deepdive/alert_engine.py` — 순수 함수, DB 직접 접근 없음

### 트리거 종류

| trigger_type | 조건 | severity |
|--------------|------|----------|
| `buy_zone_entered` | 현재가 ∈ [bz_low, bz_high] 이고 직전가 바깥 | info |
| `stop_proximity` | \|현재가 - stop\| / stop ≤ 2% | warning (위) / critical (아래) |
| `target_1m_hit` / `target_3m_hit` / `target_6m_hit` | 현재가 ≥ target × 0.995 이고 직전가 미달 | info |

### 중복 방지

각 트리거는 **이전 거래일 종가**를 참조해 "신규 발생"만 감지. 같은 조건이 계속 지속되면 다음 날 재알림하지 않는다.

### 파이프라인 통합

`step8_notify`가 워치리스트 각 종목의 `ExecutionGuide` + `_get_current_and_previous_price()`를 `evaluate_alerts_batch()`에 넘기고, critical/warning severity의 알림은 Telegram 요약의 `new_risks` 섹션에 병합.

`format_alerts_summary()`는 이모지 기반 텍스트 블록을 생성 — 🔴 critical > 🟡 warning > 🔵 info 정렬, 상위 20건까지.

---

## 7. 데이터 변경 감지 (Phase 3)

`diff_detector.py`가 당일 결과 vs 전일 리포트를 비교해 5종 변경을 추출:

| change_type | 감지 조건 |
|-------------|----------|
| `action_changed` | HOLD → ADD 등 action_grade 변동 |
| `conviction_shift` | conviction ±2 이상 변동 |
| `probability_shift` | 시나리오 확률 ±15%p 이상 |
| `new_risk` | invalidation_conditions 신규 항목 발견 |
| `trigger_hit` | 이전 리포트의 `next_review_trigger` 조건 성립 |

변경은 `FactDeepDiveChange` 테이블에 저장되며 심각도별(critical/warning/info)로 UI에 뱃지 표시된다.

---

## 7.5. 사용자 행동 가능성 레이어 (Phase 12)

Phase 1~11이 "분석 엔진"을 완성했다면, Phase 12는 **"사용자가 그 분석을 가지고 실제 의사결정을 내리기까지의 마지막 1마일"**을 채운다. 분석 깊이가 아니라 UX/행동가능성을 높이는 레이어.

### Phase 12a — 보유/워치리스트 웹 CRUD

**목표**: 웹에서 워치리스트 종목을 추가/제거하고 보유 정보(주수·평단가·매수일)를 직접 입력/수정하게 한다. 기존 CLI(`investmate watchlist add/hold`) 전용 체계가 웹까지 확장됐다.

**백엔드 (FastAPI 라우트, `src/web/routes/personal.py`)**
```
POST   /personal/watchlist               {ticker, note?}
DELETE /personal/watchlist/{ticker}      (보유정보 함께 cascade 삭제)
POST   /personal/holdings                {ticker, shares, avg_cost, opened_at?}
DELETE /personal/holdings/{ticker}       (워치리스트는 유지)
GET    /personal/holdings/csv-template   CSV 템플릿 다운로드
POST   /personal/holdings/import         CSV 일괄 UPSERT (행별 검증 리포트)
```

모든 라우트는 **`{success, data?, error?}` JSON envelope** 반환 — 프론트엔드는 fetch + 토스트/모달 갱신.

- **자동 등록**: DimStock에 없는 종목은 `ensure_stock_registered()` (watchlist_manager)가 yfinance `.info`로 메타를 fetch해 자동 등록 (비 S&P 500 종목 지원).
- **CSV import**: UTF-8 BOM 허용, 최대 500행/256KB, 행별 실패를 `errors[]`에 수집하고 성공 행만 commit.
- **검증**: ticker 10자 이내 영문/숫자/`-`/`.`, shares 1~1e7, avg_cost 0~1e6, opened_at ISO8601.
- **신규 repository 함수**: `WatchlistRepository.delete_holding()`.

**프론트엔드 (`src/web/templates/personal.html`)**
- 대시보드 상단 `+ 종목 추가` / `CSV 가져오기` 버튼 + 모달(`dd_modal_add_ticker`, `dd_modal_import`)
- 카드 우상단 **햄버거 메뉴**: "보유 편집 / 보유 삭제 / 워치리스트 제거"
- 보유 편집 모달은 shares·avg_cost·opened_at 입력 유효성 검증 (숫자/날짜 포맷)
- 빈 상태 CTA가 CLI 명령 안내 → "+ 종목 추가" 버튼으로 교체

### Phase 12b — 인앱 알림 센터 & 영구 저장

**목표**: alert_engine이 발화한 알림을 Telegram으로 한 번 푸시하고 증발시키지 않는다. 모든 알림을 DB에 영구 저장하고 웹에서 히스토리·필터·확인(acknowledge)을 지원한다.

**신규 DB 테이블 (`FactDeepDiveAlert`)**
```
alert_id PK
date_id, stock_id, ticker
trigger_type     -- buy_zone_entered/stop_proximity/target_{1m,3m,6m}_hit/
                 --  invalidation_hit/review_trigger_hit/earnings_imminent/
                 --  fomc_imminent/ex_dividend_imminent
severity         -- critical|warning|info
message          Text
current_price    Float
reference_price  Float  (손절/목표/buy_zone_low 등 비교 기준)
context_json     Text   (트리거 시점 스냅샷 확장용, 현재는 nullable)
acknowledged     Boolean
acknowledged_at  DateTime

UniqueConstraint(ticker, trigger_type, date_id)  -- 일일 dedup
Index(date_id), (ticker, date_id), (severity, acknowledged)
```

`migrate.py`가 신규 테이블을 자동 CREATE.

**AlertRepository (`src/db/repository.py`)**
- `persist_batch(date_id, stock_id_lookup, alerts)` — sqlite_insert `ON CONFLICT DO NOTHING`로 dedup. 반환값은 신규 INSERT 건수.
- `get_recent(days, severity_min, ack_filter, ticker, limit)` — 필터 지원 + `_SEVERITY_RANK`로 severity_min 이상만 조회.
- `acknowledge(alert_id)` / `acknowledge_all(date_id?)` — 단일/일괄 확인.
- `count_unread(days)` — 헤더 뱃지용 count 집계.

**파이프라인 통합 (`deepdive_pipeline.py::step8_notify`)**
- `evaluate_alerts_batch()` 결과를 Telegram으로 푸시하기 **직전**에 `AlertRepository.persist_batch()`로 저장.
- 저장 실패가 Telegram 전송을 막지 않도록 try/except 격리 (`pragma: no cover`).

**웹 라우트**
```
GET  /personal/alerts                 필터(days/severity/ack/ticker) + 페이지네이션 200건
GET  /personal/alerts/unread-count    {count: n} 헤더 뱃지 폴링용
POST /personal/alerts/{alert_id}/ack  단일 확인
POST /personal/alerts/ack-all         전체 또는 date_id 지정 일괄 확인
```

**UI (`personal_alerts.html`)**
- 타임라인 그룹화: **오늘 / 어제 / 지난 7일 / 이전** (date_id → 오늘과의 delta로 분류)
- 심각도별 색상: critical=red, warning=amber, info=blue
- 확인된 알림은 `opacity-60`으로 dimmed, "확인" 버튼 대신 "확인됨" 표기
- 메인 대시보드 헤더에 🔔 뱃지 (60초 폴링 + DOMContentLoaded 1회 fetch)

### Phase 12c — What-if 포트폴리오 시뮬레이터

**목표**: 보유 변경(가상) → 리밸런싱 플랜, 섹터 분포, 제약 위반이 즉시 before/after로 보여지게 한다. DB를 절대 수정하지 않는 **순수 계산 레이어**.

**핵심 통찰**: `rebalance_advisor.build_rebalance_plan`이 이미 순수 함수이므로 **holdings 입력만 바꿔서 두 번 호출**하면 그게 곧 시뮬레이션이다.

**신규 모듈 (`src/deepdive/whatif_simulator.py`)**

DTO (모두 `@dataclass(frozen=True)`):
```python
class Modification:
    ticker: str
    shares: int | None       # 절대값 (0 = 전량 매도)
    shares_delta: int | None # 증감 (음수 = 매도)
    # __post_init__에서 정확히 하나만 채워졌는지 강제

class StockInfo:
    current_price: float
    sector: str | None

class SimulationResult:
    before_plan: RebalancePlan
    after_plan: RebalancePlan
    before_sector_weights: tuple[tuple[str, float], ...]
    after_sector_weights: tuple[tuple[str, float], ...]
    before_total_value: float
    after_total_value: float
    modified_tickers: tuple[str, ...]
    warnings: tuple[str, ...]
    violations: tuple[str, ...]
```

메인 함수:
```python
def simulate_holdings_change(
    current: Sequence[Holding],
    modifications: Sequence[Modification],
    guides: Mapping[str, _GuideLike],
    universe: Mapping[str, StockInfo] | None = None,
    *, max_sector_weight, max_single_stock_pct, max_daily_turnover_pct, tx_cost_bps,
) -> SimulationResult
```

로직:
1. `current` → dict 불변 복사
2. `universe`에 current 종목 정보 자동 포함 (중복 제거)
3. modifications 순회하며 새 dict 구성
   - shares_delta: 기존 shares + delta (미보유는 0부터)
   - shares=0: 전량 매도 (dict에서 제거)
   - 결과 shares < 0: `violations`에 "매도 수량 초과" 기록
   - 신규 종목이지만 `universe`에 가격/섹터 없음: `violations`에 "가격 정보 없음" 기록
4. `build_rebalance_plan()` 두 번 호출 (before/after)
5. 섹터 분포 계산 + 제약 위반 검증 (after 섹터 상한 초과 / 단일 종목 상한 초과)

**불변성 보장**: 입력 sequence를 절대 수정하지 않음. `test_does_not_mutate_input`로 before/after snapshot 동일성 검증 (30년 트레이더의 핵심 원칙).

**라우트 (`POST /personal/simulate`)**
```json
Request:
{ "modifications": [
    {"ticker": "AAPL", "shares_delta": 50},
    {"ticker": "MSFT", "shares": 0}
] }

Response:
{ "success": true, "data": {
    "before": {"suggestions":[...], "total_turnover_pct":..., "cash_weight_pct":..., ...},
    "after":  {...},
    "before_sector_weights":[{"sector":"Technology","pct":35.0},...],
    "after_sector_weights":[...],
    "before_total_value": 125000, "after_total_value": 133000,
    "modified_tickers": ["AAPL","MSFT"],
    "warnings":[...], "violations":[...]
} }
```

라우트가 DB에서 현재 보유/워치리스트/가격/섹터/최신 보고서의 execution_guide를 모두 배치 로드한 뒤 순수 함수에 주입. 단, **DB는 수정하지 않는다**.

**UI (접이식 패널, personal.html 내부)**
- 대시보드 상단에 `🔮 What-if 시뮬레이터` 섹션 (클릭 시 펼침)
- 수정 행: ticker 드롭다운(워치리스트만) · 증감/절대 모드 토글 · 숫자 입력 · 삭제 ✕
- "시뮬레이션 실행" 버튼 클릭 시 fetch → before/after 결과 2단 그리드 렌더
- 섹터 분포 progress bar · 제약 위반 빨간 블록 · 경고 앰버 블록 · After 리밸런싱 제안 테이블

### Phase 12d — 종목 직접 비교 페이지

**목표**: 사용자가 임의로 선택한 2~4개 종목의 6레이어/AI/EV를 사이드바이사이드로 비교. 기존 페어 탭은 자동 선정 5개에 한정됐으나 이건 능동 선택이 가능하다.

**라우트 (`GET /personal/compare?tickers=AAPL,MSFT,NVDA`)**
- 최대 4개 cap + dedup + `_validate_ticker`로 sanitize
- 각 종목의 `get_latest_report()`에서 report_json 파싱 → metrics + radar 추출
- `_compute_best_worst()`: 각 행(지표)별로 best/worst 컬럼 인덱스 계산해 셀 색상 강조
  - pct/ratio/int10: 수치 최대/최소
  - grade: `_GRADE_SCORES` 매핑 (A=9, Cheap=9, Bullish=9, ...)
  - action_grade: `{ADD:3, HOLD:2, TRIM:1, EXIT:0}`
  - entry_distance: 0에 가까울수록 best (절대값 반전)

**UI (`personal_compare.html`)**
- 상단: 종목 선택 체크박스 (워치리스트 전체), 최대 4개 체크 후 "비교" 버튼
- 본문: 13행 × N열 테이블
  - 행: AI 액션, 확신도, 불확실도, 3M EV, R/R, 진입존 거리, 제안 비중, Layer1~6 그레이드
  - 셀: best=녹색 배경+볼드, worst=빨간 배경
- 하단: ECharts 6축 레이더 overlay (종목별 컬러 `#6366f1 / #10b981 / #f59e0b / #ec4899`)
- 테이블 헤더 클릭 시 `/personal/{ticker}` 상세로 이동

### Phase 12 테스트 (89 케이스)

| 파일 | 케이스 | 범위 |
|------|-------|------|
| `test_web_personal_holdings.py` | 29 | CRUD 6 라우트 + CSV import + BOM + 검증 |
| `test_alert_persistence.py` | 18 | AlertRepository persist_batch dedup / filter / ack / count |
| `test_web_personal_alerts.py` | 10 | /personal/alerts 페이지 + unread-count + ack 라우트 |
| `test_whatif_simulator.py` | 17 | 순수 함수: delta/absolute/신규 ticker/섹터 violation/immutability |
| `test_web_personal_simulate.py` | 7 | /personal/simulate 라우트 통합 |
| `test_web_personal_compare.py` | 8 | /personal/compare 렌더 + 필터 + best/worst |

**통과**: 89/89 (회귀 0). 기존 스위트와 합쳐 1507 passing.

### Phase 12 신규/수정 파일 맵

```
src/
├── db/
│   ├── models.py              (+ FactDeepDiveAlert)
│   └── repository.py          (+ WatchlistRepository.delete_holding, + AlertRepository)
├── deepdive/
│   └── whatif_simulator.py    NEW
├── deepdive_pipeline.py       (+ step8_notify에 AlertRepository.persist_batch 호출)
└── web/
    ├── routes/personal.py     (+ CRUD/alerts/simulate/compare 라우트 ~500 LOC)
    └── templates/
        ├── personal.html      (+ 헤더 버튼, 카드 햄버거, What-if 패널, 모달 3종, JS)
        ├── personal_alerts.html   NEW
        └── personal_compare.html  NEW

pyproject.toml                 (+ python-multipart>=0.0.20)
```

---

## 8. DB 스키마

### FactDeepDiveReport
```sql
report_id, date_id, stock_id, ticker,
action_grade, conviction, uncertainty,
report_json,                            -- 전체 JSON (layers + ai_result + execution_guide + pair_comparisons)
layer1_summary ~ layer6_summary,        -- 등급 문자열
ai_bull_text, ai_bear_text, ai_synthesis,
consensus_strength, what_missing
```

**`report_json` 구조** (Phase 5 이후):
```json
{
  "layers": {"layer1": {...}, "layer3": {...}, ...},
  "ai_result": {
    "action_grade": "ADD",
    "conviction": 8,
    "reasoning": "...",
    "support_price": 172.5,
    "resistance_price": 196.0,
    "stop_loss": 158.5,
    "evidence_refs": ["layer3.rsi=62", ...],
    "invalidation_conditions": ["RSI 40 하회", ...],
    "next_review_trigger": "Q4 실적발표"
  },
  "execution_guide": {
    "buy_zone_low": 174.6, "buy_zone_high": 181.8,
    "stop_loss": 168.25, "stop_loss_source": "support",
    "target_1m": 184.0, "target_3m": 193.5, "target_6m": 201.2,
    "expected_value_pct": {"1M": 2.2, "3M": 7.5, "6M": 11.8},
    "risk_reward_ratio": 1.9, "risk_reward_label": "neutral",
    "suggested_position_pct": 6.4,
    "position_rationale": "conviction 8/10 · sizer ...",
    "portfolio_fit_warnings": [],
    "action_hint": "now"
  },
  "pair_comparisons": [{"peer_ticker": "...", ...}]
}
```

기존 컬럼 변경 없음 → **마이그레이션 불필요**. 구버전 리포트는 `execution_guide` 키가 없어도 UI가 안전하게 렌더 (`get("execution_guide") or None`).

### 기타 테이블

- `FactDeepDiveForecast`: 9개 시나리오 예측, 만기 시 actual_price/hit_range 업데이트
- `FactDeepDiveAction`: 액션 히스토리 (prev_action_grade, prev_conviction 포함)
- `FactDeepDiveChange`: 일일 변경 감지 결과
- `FactDeepDiveAlert` **[Phase 12b 신규]**: 알림 히스토리 영구 저장 — `UniqueConstraint(ticker, trigger_type, date_id)`로 일일 dedup, `acknowledged`/`acknowledged_at` 필드로 확인 상태 추적

---

## 9. 웹 대시보드

### 라우트

| Path | Method | 설명 |
|------|--------|------|
| `/personal` | GET | 워치리스트 카드 그리드 + 리밸런싱 제안 + **What-if 패널** (Phase 11c/12c) |
| `/personal/{ticker}` | GET | 종목 상세 — Hero + **실행 가이드 카드** + 6탭 |
| `/personal/{ticker}/history` | GET | 과거 분석 타임라인 (conviction/action 변화) |
| `/personal/forecasts` | GET | 예측 정확도 리더보드 (종목별 hit_rate / direction_accuracy) |
| `/personal/compare` | GET | **종목 직접 비교 페이지 (2~4개, Phase 12d)** |
| `/personal/alerts` | GET | **알림 센터 — 타임라인 그룹·필터·확인 (Phase 12b)** |
| `/personal/alerts/unread-count` | GET | 헤더 뱃지 폴링 (60초) |
| `/personal/alerts/{id}/ack` | POST | 단일 알림 확인 |
| `/personal/alerts/ack-all` | POST | 일괄 확인 |
| `/personal/watchlist` | POST | **종목 추가 (자동 DimStock 등록, Phase 12a)** |
| `/personal/watchlist/{ticker}` | DELETE | 워치리스트 제거 (보유 cascade) |
| `/personal/holdings` | POST | **보유 UPSERT (shares/avg_cost/opened_at, Phase 12a)** |
| `/personal/holdings/{ticker}` | DELETE | 보유 삭제 (워치리스트 유지) |
| `/personal/holdings/csv-template` | GET | CSV 템플릿 다운로드 |
| `/personal/holdings/import` | POST | **CSV 일괄 UPSERT (Phase 12a)** |
| `/personal/simulate` | POST | **What-if 시뮬레이션 (Phase 12c)** |

### `/personal` 카드 (Phase 6)

```
┌─────────────────────┐
│ AAPL          [ADD] │
│ Apple Inc.          │
│ $182.45  +1.23%     │
│ ━━━━━━━━━ 8/10      │
│ [EV +7.5%] [R/R 2.1] [Zone 내] [NOW] │
│ 보유: 15주 @ $165.2 │
│ P&L: +10.4%         │
└─────────────────────┘
```

### `/personal/{ticker}` 상세 (Phase 6)

**Hero 카드 (Above-the-fold)**
- 종목명 · 현재가 · 일간변화
- Action 배지 · 확신도 · **EV 3M %** · **R/R 비율**
- 보유 정보 (있으면)

**실행 가이드 카드 (Phase 5 핵심)**
- 4칼럼 그리드: Buy Zone / Stop Loss / Targets 1M·3M·6M / 제안 비중
- 각 수치에 상태 뱃지 (in_zone, RSI 과매수 대기, ai/atr/support/trailing 출처)
- `action_hint`에 따른 카드 테두리 색상: 초록(now), 노랑(wait_pullback), 빨강(avoid/sell)
- 포트폴리오 적합도 경고 배너 (있으면)

**탭**
1. **요약** — AI 종합 판단 + what_missing + next_review_trigger + invalidation_conditions + 오늘의 변경
2. **근거 추적** (Phase 6 신규) — `evidence_refs` 각 항목을 "layer3 | rsi=72" 배지+코드로 렌더, 클릭 시 해당 레이어 앵커로 이동
3. **6레이어** — 레이더 차트 + 레이어별 details 접이식 (`id="layer1"` 앵커)
4. **시나리오** — 9개 예측 테이블 + 차트 (Phase 5에서 buy zone/stop/target 오버레이 지원)
5. **페어** (Phase 6 신규) — 동종 페어 테이블, 클릭 시 해당 종목 상세로 이동
6. **토론** — Bull/Bear 논거 전문 + 합의 강도

### 차트 재사용

- `layerRadarChart()` — 6축 레이더 (`charts.js`)
- `scenarioRangeChart()` — 호라이즌별 범위 차트
- `buildMarkLine()` — 수평 참조선 (buy zone/stop/target 오버레이)
- 다크모드 토글 시 `reinitAllCharts()`로 재생성

---

## 10. CLI

```bash
investmate deepdive run [--date YYYY-MM-DD] [--ticker T] [--force] [--skip-notify]
investmate deepdive latest [--ticker T]   # 콘솔에 최신 리포트 + 실행 가이드 출력
investmate deepdive status                # 최근 5일 파이프라인 스텝 현황
```

**`deepdive latest` 출력 예시:**

```
AAPL — ADD (conviction 8/10, consensus high)
  RSI 62, F-Score 8/9, 섹터 PER 프리미엄 -3%로 밸류 컴포트에 진입...
  Buy Zone: $174.60~$181.80  Stop: $168.25  Target 3M: $193.50
  EV 3M: +7.5%  R/R: 1.9  Size: 6.4%
  ⚠ 추가 후 Technology 비중 31.2% > 상한 30%
```

### Cron (`scripts/run_deepdive.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/Scripts/activate
mkdir -p logs
python -m src.main deepdive run --date "$(date +%Y-%m-%d)" \
    2>&1 | tee -a "logs/deepdive_$(date +%Y%m%d).log"
```

권장 cron 스케줄: **일일 07:00 KST** (DailyPipeline 06:30 종료 이후).

---

## 11. 전체 데이터 흐름

```
┌─────────────────┐
│ Watchlist 12종목 │
└────────┬────────┘
         ↓
┌─────────────────────────────────────────────┐
│ Step 1-2: load + collect (yfinance)         │
└────────┬────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────┐
│ Step 3: 6 Layers                            │
│  ├─ Layer1 Fundamental (F-Score, Z, margins)│
│  ├─ Layer2 Valuation (PER percentile, DCF)  │
│  ├─ Layer3 Technical (RSI, S/R, ATR, trend) │
│  ├─ Layer4 Flow (insider, short, analyst)   │
│  ├─ Layer5 Narrative (sentiment, catalysts) │
│  └─ Layer6 Macro (VIX/10Y/$ beta, regime)   │
└────────┬────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────┐
│ Step 4: Pair Selection                      │
│  같은 섹터 + 시총 + 60일 수익률 코사인 유사도   │
└────────┬────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────┐
│ Step 5: 3-Round Debate (Claude CLI ×5)      │
│  + <portfolio_context> 주입 [Phase 10]      │
│  Synthesizer → AIResult with                │
│  key_levels, evidence_refs, invalidation    │
└────────┬────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────┐
│ Step 5.5: Execution Guide [Phase 5]         │
│  buy_zone / stop / targets / EV / R/R       │
│  suggested_position_pct + fit_warnings      │
│  × historical hit_rate discount [Phase 9]   │
└────────┬────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────┐
│ Step 6: Diff Detection vs 전일              │
└────────┬────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────┐
│ Step 7: Persist (Report/Forecast/Action/    │
│         Change) + 만기 예측 평가             │
└────────┬────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────┐
│ Step 8: Alert Engine [Phase 8]              │
│  buy_zone_entered / stop_proximity /        │
│  target_hit → Telegram / Slack / Email     │
└────────┬────────────────────────────────────┘
         ↓
┌─────────────────────────────────────────────┐
│ 웹 대시보드 `/personal/{ticker}`             │
│ Hero + 실행 가이드 카드 + 6 탭               │
└─────────────────────────────────────────────┘
```

---

## 12. Phase 이력

| Phase | 범위 | 핵심 산출 |
|-------|------|----------|
| **Phase 1** | 단일 AI 호출 + 6레이어 | `run_deepdive_simple()` + `layers.py` |
| **Phase 2** | 3라운드 토론 + 페어 선정 | `ai_debate_cli.py` + `pair_analysis.py` |
| **Phase 3** | 시나리오 저장 + 만기 평가 + 변경 감지 | `forecast_evaluator.py` + `diff_detector.py` |
| **Phase 4** | AI 근거 추적 + CLI 안정화 | AIResult 확장, SYNTH 프롬프트 강화, 재시도, `_has_parseable_json()`, layers.py 명시적 오류 로깅 |
| **Phase 5** | 실행 가이드 엔진 | `execution_guide.py`, `ExecutionGuide` DTO, `step5_5_execution_guide`, ATR raw 값 추가 |
| **Phase 6** | UI 실행형 대시보드 | `personal_detail.html` 전면 재구성, 카드 배지, 근거 추적 탭, 페어 비교 탭 |
| **Phase 7** | CLI 확장 | `investmate deepdive latest/status`, `scripts/run_deepdive.sh` |
| **Phase 8** | 알림 엔진 | `alert_engine.py`, 중복 방지, Telegram 통합 |
| **Phase 9** | 정확도 피드백 루프 | `get_historical_hit_rates()`, `apply_hit_rate_discount()` |
| **Phase 10** | 포트폴리오 컨텍스트 주입 | `build_stock_context(portfolio_context=...)`, SYNTH 적합도 기준 추가 |
| **Phase 11a** | Invalidation 자동 모니터링 | `invalidation_parser.py` (룰 파서), `LayerSnapshot`, `invalidation_hit`/`review_trigger_hit` 트리거, 일일 dedup |
| **Phase 11b** | 촉매 캘린더 알림 | `UpcomingCatalyst` DTO, Layer 5 구조화 필드, `earnings_imminent`/`fomc_imminent`/`ex_dividend_imminent` 트리거, `format_catalyst_block()` |
| **Phase 11c** | 포트폴리오 리밸런싱 제안 | `rebalance_advisor.py` (순수 함수), 섹터/단일종목/턴오버 상한 + 거래비용 차감 EV 필터, `/personal` 상단 섹션 |
| **Phase 11d** | AI 토론 병렬화 1단계 | `run_deepdive_debate_async()` + `run_debate_smart()` dispatcher, 라운드 내 Bull/Bear asyncio.gather, 환경변수 3종(`INVESTMATE_DEEPDIVE_PARALLEL/BACKEND/PARALLEL_MAX`), SDK 경로는 `NotImplementedError` 예약 |
| **Phase 12a** | 보유/워치리스트 웹 CRUD | POST/DELETE 6개 라우트 (JSON envelope), `WatchlistRepository.delete_holding`, personal.html 모달 3종 + 카드 햄버거 메뉴, CSV import (UTF-8 BOM/500행/256KB 제한), `python-multipart` 의존성 추가 |
| **Phase 12b** | 인앱 알림 센터 & 영구 저장 | `FactDeepDiveAlert` 테이블 (UniqueConstraint dedup), `AlertRepository` (persist_batch/get_recent/acknowledge*/count_unread), `step8_notify` DB 저장 통합, `/personal/alerts` 페이지 + 헤더 뱃지 폴링 |
| **Phase 12c** | What-if 포트폴리오 시뮬레이터 | `whatif_simulator.py` 순수 함수 + frozen DTO 3종, `build_rebalance_plan` 재사용해 before/after 2회 호출, `/personal/simulate` 라우트, 접이식 UI 패널 + 섹터 분포 바 |
| **Phase 12d** | 종목 직접 비교 페이지 | `/personal/compare?tickers=`, `personal_compare.html`, `_compute_best_worst` (grade/pct/ratio/action 우열 계산), ECharts 레이더 오버레이 (최대 4종목) |

---

## 13. 주요 파일 맵

### 소스 코드
```
src/deepdive/
├── schemas.py                — 모든 DTO (AIResult, ExecutionGuide, UpcomingCatalyst[11b] 등)
├── layers.py                 — 6레이어 오케스트레이션 + _safe_call
├── layers_fundamental.py     — Layer 1
├── layers_valuation.py       — Layer 2
├── layers_technical.py       — Layer 3 (+ atr_14 추가)
├── layers_flow.py            — Layer 4
├── layers_narrative.py       — Layer 5 (+ upcoming_catalysts_structured [11b])
├── layers_macro.py           — Layer 6
├── ai_prompts.py             — build_stock_context + run_deepdive_cli
├── ai_debate_cli.py          — 3라운드 토론 + 프롬프트 + async/dispatcher [11d]
├── execution_guide.py        — 실행 가이드 계산 [Phase 5]
├── alert_engine.py           — 알림 트리거 + LayerSnapshot + 촉매 블록 [8/11a/11b]
├── invalidation_parser.py    — 자연어 → ParsedCondition AST [Phase 11a]
├── rebalance_advisor.py      — 포트폴리오 리밸런싱 제안 순수 함수 [Phase 11c]
├── whatif_simulator.py       — What-if 보유 변경 시뮬레이터 순수 함수 [Phase 12c]
├── forecast_evaluator.py     — 정확도 평가 + hit_rate discount [Phase 9]
├── diff_detector.py          — 변경 감지
├── pair_analysis.py          — 페어 자동 선정
├── scenarios.py              — 시나리오 파싱
└── watchlist_manager.py      — 워치리스트 I/O

src/deepdive_pipeline.py      — 9단계 오케스트레이터 (+ Phase 12b 알림 persist 통합)
src/web/routes/personal.py    — 웹 라우트 (+ Phase 12a/b/c/d 16개 추가 라우트)
src/web/templates/personal.html          — 카드 그리드 + 모달/햄버거/What-if 패널 [12a/b/c]
src/web/templates/personal_detail.html   — 상세 페이지
src/web/templates/personal_history.html  — 히스토리
src/web/templates/personal_forecasts.html — 정확도 리더보드
src/web/templates/personal_alerts.html   — 알림 센터 [Phase 12b]
src/web/templates/personal_compare.html  — 종목 직접 비교 [Phase 12d]

src/db/models.py              — (+ FactDeepDiveAlert [Phase 12b])
src/db/repository.py          — (+ delete_holding, + AlertRepository [Phase 12b])
```

### 테스트 (339 Deep Dive 케이스 / 전체 스위트 1507 passing)
```
tests/test_deepdive_watchlist.py           — 워치리스트 로드
tests/test_deepdive_layers.py              — 6레이어 계산
tests/test_deepdive_ai.py                  — 3라운드 토론
tests/test_deepdive_ai_extended.py         — [Phase 4] 신규 필드 + 재시도
tests/test_deepdive_execution_guide.py     — [Phase 5] 35 케이스
tests/test_deepdive_feedback_loop.py       — [Phase 9] hit_rate discount
tests/test_deepdive_alerts.py              — [Phase 8/11a/11b] 트리거 25 케이스
tests/test_deepdive_invalidation.py        — [Phase 11a] 파서 + 평가 38 케이스
tests/test_deepdive_catalysts.py           — [Phase 11b] 촉매 트리거 15 케이스
tests/test_deepdive_rebalance.py           — [Phase 11c] 리밸런싱 advisor 10 케이스
tests/test_deepdive_debate_parallel.py     — [Phase 11d] async dispatcher 6 케이스
tests/test_deepdive_pipeline.py            — 파이프라인 통합
tests/test_deepdive_web.py                 — 웹 라우트
tests/test_deepdive_diff.py                — 변경 감지
tests/test_deepdive_forecast.py            — 시나리오 평가
tests/test_deepdive_pairs.py               — 페어 선정
tests/test_web_personal_holdings.py        — [Phase 12a] CRUD 6 라우트 + CSV import (29 케이스)
tests/test_alert_persistence.py            — [Phase 12b] AlertRepository dedup/filter/ack (18 케이스)
tests/test_web_personal_alerts.py          — [Phase 12b] /personal/alerts 페이지 (10 케이스)
tests/test_whatif_simulator.py             — [Phase 12c] 순수 함수 + immutability (17 케이스)
tests/test_web_personal_simulate.py        — [Phase 12c] /personal/simulate 통합 (7 케이스)
tests/test_web_personal_compare.py         — [Phase 12d] /personal/compare 페이지 (8 케이스)
```

---

## 14. 운영 가이드

### 첫 실행

**방법 A: CLI (배치/스크립트 친화적)**
```bash
investmate watchlist add AAPL
investmate watchlist add MSFT
investmate watchlist hold AAPL 15 165.20
investmate deepdive run --force
investmate deepdive latest
investmate web  # → http://localhost:8000/personal
```

**방법 B: 웹 UI (Phase 12a, 실사용자용 권장)**
```bash
investmate web                          # 먼저 서버 기동
# 브라우저 → http://localhost:8000/personal
# 1) 우상단 "+ 종목 추가" 버튼 → 모달로 ticker 입력
# 2) 카드 햄버거 메뉴 → "보유 편집" → shares/avg_cost 입력
# 3) 또는 "CSV 가져오기"로 일괄 import
# 4) 터미널에서:  investmate deepdive run --force
# 5) 새로고침하면 실행 가이드 + 리밸런싱 제안이 자동 표시
```

### 일일 운영
```bash
# cron으로 자동 실행 (scripts/run_deepdive.sh)
0 7 * * 2-6 /path/to/investmate/scripts/run_deepdive.sh

# 문제 발생 시 수동 재실행
investmate deepdive run --date 2026-04-11 --force
investmate deepdive status  # 스텝별 성공/실패 확인
```

### 특정 종목 재분석
```bash
investmate deepdive run --ticker AAPL --force
```

### 알림 설정
```bash
# .env
INVESTMATE_TELEGRAM_TOKEN=...
INVESTMATE_TELEGRAM_CHAT_ID=...
# 또는
INVESTMATE_SLACK_WEBHOOK=...
```

---

## 15. 알려진 한계 및 향후 과제

**Phase 11에서 해소된 한계**
- ✅ `invalidation_conditions` / `next_review_trigger` 자동 모니터링 — 룰 파서로 RSI/SMA/MACD/52주/F-Score/섹터 PER 프리미엄 9종 조건 자동 감지 (Phase 11a)
- ✅ 촉매 캘린더 알림 — Layer 5 `upcoming_catalysts_structured` 기반 `earnings_imminent`(D-1/D-3), `fomc_imminent`, `ex_dividend_imminent` 트리거 (Phase 11b)
- ✅ 리밸런싱 제안 자동화 — `rebalance_advisor.py`가 섹터/단일종목/턴오버 상한 + 거래비용 차감 EV 필터를 적용해 `/personal` 상단에 즉시 노출 (Phase 11c)
- ✅ AI 토론 병렬화 1단계 — `INVESTMATE_DEEPDIVE_PARALLEL=true`로 라운드 내 Bull/Bear가 asyncio.gather 병렬 실행, 종목당 5콜 순차 → 3단계 파이프라인으로 압축 (Phase 11d)

**Phase 12에서 해소된 한계**
- ✅ 웹에서 워치리스트/보유 관리 불가 — CLI 전용 체계가 모달·CSV import까지 확장, `WatchlistRepository` 재사용 (Phase 12a)
- ✅ 알림이 Telegram 1회성으로 휘발 — `FactDeepDiveAlert` 영구 저장 + 타임라인 그룹·필터·확인·헤더 뱃지 (Phase 12b)
- ✅ 보유 변경 영향을 즉시 미리볼 수 없음 — `whatif_simulator.py` 순수 함수로 before/after 즉시 계산, DB 수정 없음 (Phase 12c)
- ✅ 능동적 종목 직접 비교 불가 — `/personal/compare` 최대 4종목 사이드바이사이드 + 레이더 오버레이 (Phase 12d)

**남은 한계**
- **SDK Tool Use 전환 (Phase 11d 2단계)** — 현재는 Claude CLI subprocess. `INVESTMATE_DEEPDIVE_BACKEND=sdk` 경로는 `NotImplementedError`로 예약되어 있음. 전환 시 `ai_prompts.py`의 3개 JSON 프롬프트를 3개의 Tool Use 스키마(Bull/Bear/Synth)로 재작성 필요 — 별도 세션 권장
- **Deep Dive 주간 PDF 리포트** — `src/weekly_pipeline.py` 패턴을 본뜬 `investmate deepdive weekly` 미구현. 7일치 action 변경, 발화 알림, 만기 도래 예측 적중률, 포트폴리오 P&L을 PDF로 발송하는 기능은 데이터 축적이 충분히 쌓인 뒤 실시가 적절하여 deferred
- **거래 이력 기반 포지션 추적** — 현재는 `DimWatchlistHolding`의 shares×현재가를 비중으로 사용. FIFO/tax lot 기반의 정확한 포지션 추적(`FactTradeLot` 같은 신규 테이블)은 별도 과제
- **옵션 체인 기반 implied move** — 데이터 소스 부재로 out of scope
- **파서가 커버하지 않는 자연어 조건** — `ParseResult.unparsed`로 분류되고 UI에 "⚠ 자동 모니터링 불가" 뱃지로 안내됨. §16 룰 문법 참조
- **WebSocket/SSE 실시간 푸시** — Phase 12b는 60초 폴링으로 시작, 실시간 알림은 deferred

---

## 16. 자동 모니터링 룰 문법 (Phase 11a)

`src/deepdive/invalidation_parser.py`는 AI가 생성한 자연어 `invalidation_conditions`와 `next_review_trigger`를 룰 기반 AST(`ParsedCondition`)로 변환해 `alert_engine`이 현재 스냅샷(`LayerSnapshot`)과 비교한다. 파싱 실패 시 **silent drop 금지** — `ParseResult.unparsed`에 원문이 남고 `logger.warning`이 기록된다.

### 지원 패턴 표

| 문법(한국어) | indicator | op | 예 |
|---|---|---|---|
| `RSI {숫자} 하회` / `미만` / `아래` / `<` | `rsi` | `lt` | "RSI 40 하회" |
| `RSI {숫자} 이하` | `rsi` | `le` | "RSI 40 이하" |
| `RSI {숫자} 상회` / `초과` / `돌파` / `>` | `rsi` | `gt` | "RSI 70 상회" |
| `RSI {숫자} 이상` | `rsi` | `ge` | "RSI 70 이상" |
| `{20\|50\|200}일 이평(선) 이탈` / `하회` / `아래` | `sma_{N}` | `below_close` | "200일 이평선 이탈" |
| `{20\|50\|200}일 이평(선) 돌파` / `상회` | `sma_{N}` | `above_close` | "50일 이평선 돌파" |
| `MACD 데드크로스` | `macd_signal` | `cross_down` | "MACD 데드크로스" |
| `MACD 골든크로스` | `macd_signal` | `cross_up` | "MACD 골든크로스" |
| `52주 신고(가)` | `high_52w` | `above_close` | "52주 신고가" |
| `52주 신저(가)` | `low_52w` | `below_close` | "52주 신저가" |
| `F-Score {숫자} 미만` / `<` | `f_score` | `lt` | "F-Score 6 미만" |
| `F-Score {숫자} 이하` | `f_score` | `le` | "F-Score 5 이하" |
| `섹터 PER 프리미엄 {숫자}% 초과` / `>` / `상회` | `sector_per_premium` | `gt` | "섹터 PER 프리미엄 30% 초과" |
| `섹터 PER 프리미엄 {숫자}% 이상` | `sector_per_premium` | `ge` | "섹터 PER 프리미엄 30% 이상" |

### 평가 규칙
- **RSI / F-Score / 섹터 PER 프리미엄**: 숫자 비교(`lt`/`le`/`gt`/`ge`).
- **SMA 20/50/200**: `below_close` = 현재 종가 < SMA, `above_close` = 현재 종가 > SMA. 값은 `close_history`(최근 220개 종가)로 즉석 계산.
- **MACD**: 히스토그램 부호 반전. `cross_down`은 `prev > 0 ≥ now`, `cross_up`은 `prev < 0 ≤ now`. 12/26/9 EMA로 즉석 계산(최근 35개 이상 필요).
- **52주 신고/저**: 현재 종가가 Layer 3 metrics의 `high_52w` / `low_52w`를 돌파/이탈.

### 중복 방지
- `alert_engine.evaluate_alerts_batch()`는 `dedup_keys: set[str]`을 공유 인자로 받는다.
- 키 형식: `"{trigger_type}:{ticker}:{raw}"` — 같은 조건은 한 run 내에서 1회만 발화.

### 파싱 실패 처리
- 지원하지 않는 표현은 `ParseResult.unparsed`에 담겨 반환 + `logger.warning`.
- 웹 UI는 `⚠ 자동 모니터링 불가` 뱃지로 사용자에게 명시적으로 알리는 것을 원칙으로 한다.

### 관련 환경변수 (Phase 11d)
| 변수 | 기본값 | 설명 |
|---|---|---|
| `INVESTMATE_DEEPDIVE_PARALLEL` | `false` | `true` 시 라운드 내 Bull/Bear를 asyncio로 병렬 실행 |
| `INVESTMATE_DEEPDIVE_BACKEND` | `cli` | `cli` / `auto` 지원. `sdk`는 Phase 11d 2단계 예약(NotImplementedError) |
| `INVESTMATE_DEEPDIVE_PARALLEL_MAX` | `2` | 동시 실행 CLI 상한(라운드당) |

---

## 17. 면책

본 시스템은 **투자 참고용**이며 투자 권유가 아니다. 모든 분석·추천·가이드는 과거 데이터와 AI 모델의 예측에 기반하며 실제 투자 결과를 보장하지 않는다. 최종 투자 결정과 그에 따른 책임은 전적으로 사용자에게 있다.
