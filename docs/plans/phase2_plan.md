# Deep Dive Phase 2 — 상세 구현 계획서

> Phase 1 완료 (30개 테스트 통과) 기반. Layer 2/5/6 + 3라운드 토론 + 시나리오 예측 + 상세 페이지.

---

## 1. Phase 2 범위 재확인

### 포함

| # | 항목 | 설명 |
|---|------|------|
| 1 | Layer 2: 밸류에이션 | 5년 PER/PBR 백분위, 섹터 대비, DCF implied growth, PEG, FCF yield |
| 2 | Layer 5: 내러티브 | 뉴스 감성 30/60/90일 추이, 임박 촉매(실적/FOMC), 리스크 이벤트, 경영진 변화 |
| 3 | Layer 6: 거시 민감도 | 베타 회귀(VIX/10Y/Dollar/Oil), 섹터 모멘텀 순위, 레짐별 행동 |
| 4 | layers.py 파일 분할 | 현재 549줄 → 레이어별 별도 파일 (400줄 제약 준수) |
| 5 | compute_all_layers() 확장 | 6개 레이어 전체 반환 |
| 6 | 3라운드 CLI 토론 | Bull/Bear/Synthesizer 순차 CLI 호출 (5회/종목), SDK 미사용 |
| 7 | 시나리오 예측 | 1M/3M/6M x Base/Bull/Bear → fact_deepdive_forecasts INSERT (9개/종목) |
| 8 | /personal/{ticker} 상세 페이지 | 4탭: 요약, 6레이어, 시나리오, 토론 기록 |
| 9 | scenarioRangeChart | 1M/3M/6M 가격 범위 수평 바 차트 (charts.js 추가) |
| 10 | layerRadarChart | 6축 레이더 차트 (charts.js 추가) |
| 11 | DeepDiveResult 확장 | layer2, layer5, layer6, debate_result, scenarios 필드 추가 |
| 12 | schemas.py 확장 | ValuationContext, NarrativeProfile, MacroSensitivity, CLIDebateResult 추가 |
| 13 | step7_persist 확장 | layer2/5/6_summary, ai_bull_text, ai_bear_text, consensus_strength, forecasts 저장 |
| 14 | DeepDiveRepository 확장 | insert_forecast(), insert_forecasts_batch(), get_forecasts_by_report() 추가 |
| 15 | 테스트 확장 | ~18개 추가 → 총 48개 |

### 제외 (Phase 3)

| 항목 | 비고 |
|------|------|
| 페어 분석 (dim_watchlist_pairs 채움) | 코사인 유사도 자동 선정 |
| Diff 감지 (fact_deepdive_changes 채움) | 전일 대비 변경점 추출 |
| /personal/{ticker}/history | 히스토리 타임라인 |
| /personal/forecasts | 예측 정확도 리더보드 |
| actionTimelineChart | 액션 등급 변경 타임라인 차트 |
| cron 스크립트 (scripts/run_deepdive.sh) | 자동 실행 래퍼 |
| 예측 actual_price 백테스트 | hit_range 추적 |
| 페어 비교 탭 (상세 페이지) | 페어 데이터 Phase 3 생성 |
| 변경 배지 (카드 그리드) | diff 감지 Phase 3 구현 |

---

## 2. Phase 1 → Phase 2 인터페이스 매핑

### 2.1 함수/클래스 확장 지점

| Phase 1 산출물 | Phase 2 확장 | 파일 |
|---------|-------------|------|
| `compute_all_layers()` → dict{layer1,3,4} | dict{layer1,2,3,4,5,6} 반환 | `src/deepdive/layers.py` |
| `DeepDiveResult` → layer1,3,4,ai_result | +layer2,5,6,debate_result,scenarios | `src/deepdive/schemas.py` |
| `build_stock_context()` → XML layer1,3,4 | +`<layer2_valuation>`,`<layer5_narrative>`,`<layer6_macro>` | `src/deepdive/ai_prompts.py` |
| `run_deepdive_simple()` → 단일 CLI 호출 | `run_deepdive_debate()` → 5회 CLI 호출 | `src/deepdive/ai_debate_cli.py` (신규) |
| `step5_ai_analysis()` → simple 모드 | debate 모드로 교체 | `src/deepdive_pipeline.py` |
| `step7_persist()` → layer1/3/4_summary | +layer2/5/6_summary, bull/bear text, forecasts | `src/deepdive_pipeline.py` |
| `DeepDiveRepository.insert_report()` | +insert_forecasts_batch(), get_forecasts_by_report() | `src/db/repository.py` |
| `/personal` 카드 → 클릭 비활성 | 카드 클릭 → `/personal/{ticker}` 링크 | `src/web/templates/personal.html` |

### 2.2 DB 테이블 매핑

| 테이블 | Phase 1 사용 | Phase 2 추가 사용 |
|--------|-------------|-----------------|
| fact_deepdive_reports | layer1/3/4_summary, ai_synthesis, what_missing | **layer2/5/6_summary, ai_bull_text, ai_bear_text, consensus_strength** |
| fact_deepdive_forecasts | 미사용 (스키마만 존재) | **9개 row/종목 INSERT (3 horizon x 3 scenario)** |
| fact_deepdive_actions | action_grade, conviction | 변경 없음 |
| fact_deepdive_changes | 미사용 | Phase 3 |
| dim_watchlist_pairs | 미사용 | Phase 3 |

> DB 마이그레이션 불필요 — Phase 1에서 모든 컬럼 이미 정의됨.

### 2.3 기존 분석 함수 재사용 (Phase 2 신규 레이어)

| 함수 | 위치 | Phase 2 사용 |
|------|------|-------------|
| `calculate_sector_momentum()` | `src/analysis/external.py` | Layer 6: 섹터 모멘텀 순위 |
| `detect_regime()` | `src/ai/regime.py` | Layer 6: 현재 레짐 판단 |
| `collect_earnings_calendar()` | `src/data/event_collector.py` | Layer 5: 임박 실적 발표 |
| `get_next_fomc_date()` | `src/data/event_collector.py` | Layer 5: 다음 FOMC |
| `run_deepdive_cli()` | `src/deepdive/ai_prompts.py` | debate 5회 호출 재사용 |
| `_parse_ai_response()` | `src/deepdive/ai_prompts.py` | Synthesizer 응답 파싱 |

---

## 3. Task 분할 (15개, 각 1-3시간)

| Task | 이름 | 예상 시간 | 의존 |
|------|------|----------|------|
| T0 | layers.py 파일 분할 (리팩터링) | 1.5h | - |
| T1 | Layer 2 스키마 + 계산 | 3h | T0 |
| T2 | Layer 5 스키마 + 계산 | 2.5h | T0 |
| T3 | Layer 6 스키마 + 계산 | 3h | T0 |
| T4 | compute_all_layers() + DeepDiveResult 확장 | 1h | T1,T2,T3 |
| T5 | build_stock_context() XML 블록 확장 | 1.5h | T4 |
| T6 | Bull/Bear/Synth 프롬프트 상수 정의 | 1h | - |
| T7 | ai_debate_cli.py 토론 오케스트레이터 | 3h | T5,T6 |
| T8 | 시나리오 예측 파싱 + 스키마 | 2h | T7 |
| T9 | DeepDiveRepository 확장 (insert_forecast) | 1h | T8 |
| T10 | 파이프라인 step3/step5/step7 확장 | 2.5h | T7,T8,T9 |
| T11 | /personal/{ticker} 라우트 | 2h | T9 |
| T12 | personal_detail.html 템플릿 (4탭) | 3h | T11 |
| T13 | charts.js: scenarioRangeChart + layerRadarChart | 2h | - |
| T14 | 테스트 확장 (~18개 추가) | 3h | T0-T12 |

**총 예상**: ~31.5시간

---

## 4. Task 의존성 그래프

```
T0 (layers.py 분할) ──┐
                       ├── T1 (Layer 2) ──┐
                       ├── T2 (Layer 5) ──┼── T4 (all_layers 확장) ── T5 (context XML) ──┐
                       └── T3 (Layer 6) ──┘                                               │
                                                                                           │
T6 (프롬프트 상수) ────────────────────────────────────────────────────────────────────────┤
                                                                                           │
                                                                                           └── T7 (debate CLI) ── T8 (scenario 파싱)
                                                                                                                       │
T13 (charts.js) ────────────────────────────────────────────────────────────────────┐      T9 (repo 확장)
                                                                                    │         │
                                                                                    ├── T10 (pipeline 확장)
                                                                                    │         │
                                                                                    ├── T11 (route) ── T12 (template)
                                                                                    │
                                                                                    └── T14 (tests) ← 모든 Task 완료 후
```

**권장 실행 순서**: T0 → [T1,T2,T3,T6,T13 병렬] → T4 → T5 → T7 → [T8,T9] → [T10,T11] → T12 → T14

---

## 5. 각 Task 상세

### Task 0: layers.py 파일 분할 (1.5h)

**목적**: 현재 `layers.py` 549줄 → 레이어별 별도 파일 분리 (400줄 제약 준수)

**파일 변경**:
- `src/deepdive/layers.py` (549줄 → ~50줄, 통합 import + compute_all_layers만 유지)
- `src/deepdive/layers_fundamental.py` (신규, ~200줄 — Layer 1 이동)
- `src/deepdive/layers_technical.py` (신규, ~200줄 — Layer 3 이동)
- `src/deepdive/layers_flow.py` (신규, ~150줄 — Layer 4 이동)
- 공용 유틸(`_sf`, `_calc_ratio`, `_round_or_none`)은 `layers.py`에 유지하거나 별도 모듈로

**리팩터링 결과**:
```python
# src/deepdive/layers.py (통합 모듈)
from src.deepdive.layers_fundamental import compute_layer1_fundamental
from src.deepdive.layers_technical import compute_layer3_technical
from src.deepdive.layers_flow import compute_layer4_flow

def compute_all_layers(session, stock_id, date_id, ...) -> dict:
    return {
        "layer1": compute_layer1_fundamental(session, stock_id),
        "layer3": compute_layer3_technical(session, stock_id, date_id),
        "layer4": compute_layer4_flow(session, stock_id),
    }
```

**테스트**: 기존 30개 테스트 전부 통과 (동작 변경 없음)

**DoD**: 기존 테스트 30개 통과, 각 파일 400줄 미만, import 경로 호환

---

### Task 1: Layer 2 — 밸류에이션 컨텍스트 (3h)

**파일 생성**: `src/deepdive/layers_valuation.py` (~150줄)
**파일 수정**: `src/deepdive/schemas.py` (+25줄)

**schemas.py 추가**:
```python
class ValuationContext(BaseModel, frozen=True):
    """Layer 2: 밸류에이션 컨텍스트."""
    valuation_grade: str           # Cheap/Fair/Rich/Extreme
    per_5y_percentile: float | None     # 0-100
    pbr_5y_percentile: float | None
    ev_ebitda_5y_percentile: float | None
    sector_per_premium: float | None    # % vs sector median
    sector_pbr_premium: float | None
    dcf_implied_growth: float | None    # 연간 FCF 성장률 %
    peg_ratio: float | None
    fcf_yield: float | None             # FCF/market_cap %
    metrics: dict
```

**핵심 함수**:
```python
def compute_layer2_valuation(
    session: Session, stock_id: int, sector_id: int | None,
) -> ValuationContext | None:
```

**로직**:
1. `FactValuation` 최근 1260거래일(~5년) 히스토리 → PER/PBR/EV_EBITDA 백분위
2. 동일 `sector_id` 전 종목 최신 PER/PBR 중앙값 → premium/discount %
3. DCF implied growth: `operating_cashflow` TTM → 할인율 10% 역산
4. PEG: `PER / EPS_growth_rate` (최근 4Q vs 전년 4Q)
5. FCF yield: `FCF_TTM / market_cap * 100`
6. 그레이드: Cheap(PER <20p & PBR <30p), Rich(>80p & >70p), Extreme(>95p), Fair(나머지)

**입력 DB**: `FactValuation`, `FactFinancial`, `DimStock`(sector_id)

**Phase 1 의존**: `_sf()`, `_round_or_none()` 유틸 (T0에서 공용화)

**테스트**:
- `test_layer2_percentile` — 5년 PER 백분위 정확성
- `test_layer2_dcf_implied_growth` — DCF 역산
- `test_layer2_sector_premium` — 섹터 대비 premium 계산
- `test_layer2_insufficient_history` — 1년 미만 데이터 → 가용 기간 사용

**DoD**: 4개 테스트 통과, 5년 미만 데이터 시 가용 기간 계산, None 안전 처리

---

### Task 2: Layer 5 — 내러티브 + 촉매 (2.5h)

**파일 생성**: `src/deepdive/layers_narrative.py` (~120줄)
**파일 수정**: `src/deepdive/schemas.py` (+20줄)

**schemas.py 추가**:
```python
class NarrativeProfile(BaseModel, frozen=True):
    """Layer 5: 내러티브 + 촉매."""
    narrative_grade: str            # Positive/Neutral/Negative
    sentiment_30d: float | None     # -1.0 ~ 1.0
    sentiment_60d: float | None
    sentiment_90d: float | None
    sentiment_trend: str            # improving/declining/stable
    upcoming_catalysts: list[str]   # ["실적 발표 14일 후", "FOMC 7일 후"]
    risk_events: list[str]          # 최근 7일 부정 뉴스 급증
    exec_changes: list[str]         # CEO/CFO 관련 뉴스 제목
    metrics: dict
```

**핵심 함수**:
```python
def compute_layer5_narrative(
    session: Session, stock_id: int, ticker: str, reference_date: date,
) -> NarrativeProfile | None:
```

**로직**:
1. `FactNews` + `BridgeNewsStock` JOIN → 90일 뉴스, 30/60/90일 윈도우별 sentiment_score 평균
2. 추이: 30d > 60d > 90d → improving 등
3. `collect_earnings_calendar()` + `get_next_fomc_date()` → 임박 촉매
4. 최근 7일 부정 뉴스 5건 이상 → 리스크 이벤트 플래그
5. 뉴스 제목 "CEO|CFO|resign|appoint|임명|사임" → 경영진 변화
6. 그레이드: Positive(30d > 0.2 & improving), Negative(< -0.2 & declining), Neutral

**입력 DB**: `FactNews`, `BridgeNewsStock`
**외부 함수**: `collect_earnings_calendar()`, `get_next_fomc_date()` (src/data/event_collector.py)

**테스트**:
- `test_layer5_sentiment_trend` — 30/60/90일 추이 판정
- `test_layer5_catalysts` — 실적/FOMC 감지
- `test_layer5_no_news` — 뉴스 0건 → Neutral + 빈 리스트

**DoD**: 3개 테스트 통과, 뉴스 0건 시 정상 동작

---

### Task 3: Layer 6 — 거시 민감도 (3h)

**파일 생성**: `src/deepdive/layers_macro.py` (~140줄)
**파일 수정**: `src/deepdive/schemas.py` (+20줄)

**schemas.py 추가**:
```python
class MacroSensitivity(BaseModel, frozen=True):
    """Layer 6: 거시 민감도."""
    macro_grade: str                # Favorable/Neutral/Headwind
    beta_vix: float | None          # VIX 변화 1% 시 종목 수익률 변화
    beta_10y: float | None          # 10년물 금리 베타
    beta_dollar: float | None       # 달러 인덱스 베타
    sector_momentum_rank: int | None    # 전체 섹터 중 순위
    sector_momentum_total: int | None   # 총 섹터 수
    current_regime: str | None      # bull/bear/range/crisis
    regime_avg_return: float | None # 현 레짐에서 과거 평균 수익률
    metrics: dict
```

**핵심 함수**:
```python
def compute_layer6_macro(
    session: Session, stock_id: int, sector_id: int | None, date_id: int,
) -> MacroSensitivity | None:
```

**로직**:
1. `FactDailyPrice` 최근 252거래일 일간 수익률
2. `FactMacroIndicator` 같은 기간 VIX/us_10y_yield/dollar_index 일간 변화율
3. `scipy.stats.linregress(macro_changes, stock_returns)` → beta 계수 3개
4. `calculate_sector_momentum()` → 섹터 모멘텀 순위
5. `detect_regime()` → 현재 레짐 + 해당 레짐 구간 중 종목 평균 수익률
6. 그레이드: beta_vix < -0.5 & regime bull → Favorable, beta_vix > 0.5 & regime bear → Headwind

**입력 DB**: `FactDailyPrice`, `FactMacroIndicator`
**외부 함수**: `detect_regime()` (src/ai/regime.py), `calculate_sector_momentum()` (src/analysis/external.py)
**의존성**: `scipy.stats.linregress` (이미 requirements에 포함)

**테스트**:
- `test_layer6_beta_regression` — VIX 베타 회귀 계수
- `test_layer6_regime` — 레짐 감지 + 레짐별 수익률
- `test_layer6_insufficient_data` — 매크로 데이터 부족 → None

**DoD**: 3개 테스트 통과, scipy 오류 시 None 반환

---

### Task 4: compute_all_layers() + DeepDiveResult 확장 (1h)

**파일 수정**: `src/deepdive/layers.py`, `src/deepdive/schemas.py`

**layers.py 변경**:
```python
def compute_all_layers(
    session: Session, stock_id: int, date_id: int,
    sector_id: int | None = None,
    ticker: str | None = None,
    reference_date: date | None = None,
) -> dict:
    """6개 레이어 통합 계산."""
    return {
        "layer1": compute_layer1_fundamental(session, stock_id),
        "layer2": compute_layer2_valuation(session, stock_id, sector_id),
        "layer3": compute_layer3_technical(session, stock_id, date_id),
        "layer4": compute_layer4_flow(session, stock_id),
        "layer5": compute_layer5_narrative(session, stock_id, ticker or "", reference_date or date.today()),
        "layer6": compute_layer6_macro(session, stock_id, sector_id, date_id),
    }
```

**schemas.py DeepDiveResult 확장**:
```python
class DeepDiveResult(BaseModel):
    ticker: str
    stock_id: int
    current_price: float
    daily_change_pct: float
    layer1: FundamentalHealth | None = None
    layer2: ValuationContext | None = None      # Phase 2 추가
    layer3: TechnicalProfile | None = None
    layer4: FlowProfile | None = None
    layer5: NarrativeProfile | None = None      # Phase 2 추가
    layer6: MacroSensitivity | None = None      # Phase 2 추가
    ai_result: AIResult | None = None
    debate_result: CLIDebateResult | None = None # Phase 2 추가
    scenarios: dict | None = None                # Phase 2 추가
```

**DoD**: 기존 Phase 1 테스트 30개 여전히 통과, 새 레이어 None 허용

---

### Task 5: build_stock_context() XML 블록 확장 (1.5h)

**파일 수정**: `src/deepdive/ai_prompts.py`

**추가 XML 블록 3개**:

```xml
<layer2_valuation>
밸류에이션 등급: {valuation_grade}
PER 5년 백분위: {per_5y_percentile}%
PBR 5년 백분위: {pbr_5y_percentile}%
섹터 PER 프리미엄: {sector_per_premium}%
DCF 내재 성장률: {dcf_implied_growth}%
PEG: {peg_ratio} | FCF Yield: {fcf_yield}%
</layer2_valuation>

<layer5_narrative>
내러티브 등급: {narrative_grade}
감성 추이: 30일={sentiment_30d} | 60일={sentiment_60d} | 90일={sentiment_90d}
감성 방향: {sentiment_trend}
임박 촉매: {catalysts_joined}
리스크 이벤트: {risks_joined}
</layer5_narrative>

<layer6_macro>
거시 민감도 등급: {macro_grade}
VIX 베타: {beta_vix} | 10Y 베타: {beta_10y} | Dollar 베타: {beta_dollar}
섹터 모멘텀 순위: {rank}/{total}
현재 레짐: {regime} | 레짐 평균 수익률: {regime_avg_return}%
</layer6_macro>
```

**Phase 1 의존**: 기존 `build_stock_context()` 함수에 `elif` 블록 3개 추가

**테스트**: `test_build_context_with_6_layers` — 6개 XML 블록 모두 포함

**DoD**: 기존 context 테스트 통과 + 신규 1개

---

### Task 6: Bull/Bear/Synth 프롬프트 상수 정의 (1h)

**파일 수정**: `src/deepdive/ai_prompts.py` (+상수 3개)

**BULL_SYSTEM_PROMPT**:
```
너는 30년 경력 성장주 롱온리 포트폴리오 매니저다.
이 종목을 보유하거나 추가 매수할 이유를 찾아라.
단계적으로 깊이 사고한 뒤 결론을 내려라.

분석 지침:
- 6개 레이어 데이터를 모두 활용, 매수 관점에 유리한 근거 집중
- 밸류에이션이 비싸도 성장 스토리로 정당화 가능한지 평가
- 기술적 약세 = "중장기 매수 기회"로 해석 가능한지
- 내부자 매도는 세금/다각화 목적 가능성 고려
- 보유 종목은 평단가 대비 수익률, 보유기간 맥락 반영

반드시 아래 JSON 형식만 출력. 다른 텍스트 없이 JSON만:
{"action":"ADD"|"HOLD", "conviction":1-10,
 "bull_case":["근거1","근거2","근거3"],
 "scenarios":{"1M":{"base":{"prob":0.5,"low":가격,"high":가격},...},"3M":{...},"6M":{...}},
 "catalysts":["촉매1"], "key_risks_acknowledged":["인정 리스크1"]}
```

**BEAR_SYSTEM_PROMPT**:
```
너는 30년 경력 숏셀러 겸 리스크 매니저다.
이 종목의 하방 리스크와 매도/축소 이유를 찾아라.
단계적으로 깊이 사고한 뒤 결론을 내려라.

분석 지침:
- 리스크/약점 집중: 성장 둔화, 마진 압박, 경쟁 심화
- 밸류에이션 과열은 절대적+상대적 수치 모두 제시
- 기술적 약세 → 하방 시나리오 구체화
- 매크로 역풍 정량화
- 보유 종목: 큰 수익 = 이익실현 적기, 손실 = 추가 하락 리스크

반드시 아래 JSON 형식만 출력. 다른 텍스트 없이 JSON만:
{"action":"TRIM"|"EXIT"|"HOLD", "conviction":1-10,
 "bear_case":["리스크1","리스크2","리스크3"],
 "scenarios":{"1M":{...},"3M":{...},"6M":{...}},
 "stop_loss_level":가격, "key_strengths_acknowledged":["인정 강점1"]}
```

**SYNTH_SYSTEM_PROMPT**:
```
너는 30년 경력 수석 CIO다. Bull/Bear 양측 토론을 종합하여 최종 판단을 내려라.
단계적으로 깊이 사고한 뒤 결론을 내려라.

판단 기준:
1. 논거 구체성 + 데이터 근거
2. 논리적 일관성
3. 현재 시장 환경(레짐) 정합성
4. 리스크/보상 비대칭성

보유자 관점 (보유 종목만):
- HOLD = 현 포지션 유지  - ADD = 추가 매수 (확신 높을 때)
- TRIM = 일부 매도       - EXIT = 전량 매도 (확신 높을 때)
- +30% 이상 수익 → 이익실현 검토  - -15% 이상 손실 → 손절 검토

반드시 아래 JSON 형식만 출력. 다른 텍스트 없이 JSON만:
{"action_grade":"HOLD"|"ADD"|"TRIM"|"EXIT",
 "conviction":1-10, "uncertainty":"low"|"medium"|"high",
 "reasoning":"200자 이내 종합 판단",
 "scenarios":{"1M":{"base":{"prob":0.5,"low":가격,"high":가격},...},"3M":{...},"6M":{...}},
 "consensus_strength":"high"|"medium"|"low",
 "what_missing":"반대 의견 강조",
 "key_levels":{"support":가격,"resistance":가격,"stop_loss":가격},
 "next_review_trigger":"재검토 트리거 조건"}
```

**DoD**: 3개 프롬프트 상수 정의, 각 800자 이내

---

### Task 7: ai_debate_cli.py — 토론 오케스트레이터 (3h)

**파일 생성**: `src/deepdive/ai_debate_cli.py` (~250줄)

**핵심 데이터 모델 (schemas.py에 추가)**:
```python
@dataclass(frozen=True)
class DebateRound:
    round_num: int
    role: str           # "bull" | "bear" | "synthesizer"
    raw_text: str
    parsed: dict | None

@dataclass(frozen=True)
class CLIDebateResult:
    rounds: tuple[DebateRound, ...]
    final_result: AIResult | None
    scenarios: dict | None
    consensus_strength: str          # high/medium/low
    bull_summary: str | None         # R2 bull text
    bear_summary: str | None         # R2 bear text
```

**핵심 함수**:
```python
def run_deepdive_debate(
    entry: WatchlistEntry,
    layers: dict,
    current_price: float,
    daily_change: float,
    timeout: int = 600,
    model: str = "opus",
) -> CLIDebateResult | None:
    """3라운드 CLI 토론 실행. 5회 순차 호출."""
```

**호출 흐름** (5회 순차):
```
[1] run_deepdive_cli(r1_prompt, BULL_SYSTEM_PROMPT, timeout, model) → bull_r1
[2] run_deepdive_cli(r1_prompt, BEAR_SYSTEM_PROMPT, timeout, model) → bear_r1
[3] run_deepdive_cli(r2_bull_prompt, BULL_SYSTEM_PROMPT, timeout, model) → bull_r2
[4] run_deepdive_cli(r2_bear_prompt, BEAR_SYSTEM_PROMPT, timeout, model) → bear_r2
[5] run_deepdive_cli(r3_synth_prompt, SYNTH_SYSTEM_PROMPT, timeout, model) → synth
```

**프롬프트 빌더 함수**:
```python
def _build_r1_user_prompt(stock_context: str) -> str
def _build_r2_bull_prompt(stock_context: str, bear_r1_text: str) -> str
def _build_r2_bear_prompt(stock_context: str, bull_r1_text: str) -> str
def _build_r3_synth_prompt(stock_context: str, bull_r2_text: str, bear_r2_text: str) -> str
```

**폴백 전략**:
- R1 Bull 실패 → R2 Bull 스킵, Bear R1을 R3에 직접 전달
- R1 Bear 실패 → R2 Bear 스킵, Bull R1을 R3에 직접 전달
- R2 양쪽 실패 → R1 결과로 R3 진행
- R3 실패 → `run_deepdive_simple()` 폴백 (Phase 1 단일 호출)
- 전체 실패 → None 반환

**Phase 1 의존**: `run_deepdive_cli()`, `_parse_ai_response()`, `build_stock_context()` 재사용

**테스트**:
- `test_debate_5_calls_sequential` — mock CLI call_count == 5
- `test_debate_r1_fallback` — R1 Bull 실패 → R2 스킵
- `test_debate_full_failure_fallback` — 전체 실패 → simple 폴백

**DoD**: 5회 순차 호출, 각 실패 격리, None 안전 반환

---

### Task 8: 시나리오 예측 파싱 + 스키마 (2h)

**파일 수정**: `src/deepdive/schemas.py` (+15줄)
**파일 생성**: `src/deepdive/scenarios.py` (~80줄)

**스키마**:
```python
class ScenarioForecast(BaseModel, frozen=True):
    horizon: str          # "1M" | "3M" | "6M"
    scenario: str         # "BASE" | "BULL" | "BEAR"
    probability: float    # 0.0-1.0
    price_low: float
    price_high: float
    trigger_condition: str | None
```

**파싱 함수**:
```python
def parse_scenarios(synth_parsed: dict, current_price: float) -> list[ScenarioForecast]:
    """Synthesizer JSON의 scenarios 필드 → ScenarioForecast 리스트.
    검증: probability 합계 ~1.0, price_low < price_high, 현재가 +-80% 범위.
    반환: 최대 9개 (3 horizon x 3 scenario), 검증 실패 시 빈 리스트."""
```

**Synthesizer 출력 JSON shape**:
```json
{
  "scenarios": {
    "1M": {
      "base": {"prob": 0.50, "low": 175.0, "high": 185.0, "trigger": "실적 후 가이던스 유지"},
      "bull": {"prob": 0.25, "low": 185.0, "high": 200.0, "trigger": "AI 매출 서프라이즈"},
      "bear": {"prob": 0.25, "low": 160.0, "high": 175.0, "trigger": "중국 규제 강화"}
    },
    "3M": { ... }, "6M": { ... }
  }
}
```

**→ DB 매핑** (fact_deepdive_forecasts):

| JSON | DB 컬럼 |
|------|---------|
| horizon key ("1M") | horizon |
| scenario key ("base") | scenario (대문자 변환) |
| prob | probability |
| low | price_low |
| high | price_high |
| trigger | trigger_condition |

**테스트**:
- `test_parse_scenarios_valid` — 9개 파싱
- `test_parse_scenarios_missing` — scenarios 필드 없을 때 빈 리스트
- `test_parse_scenarios_sanity_check` — 가격 범위 이상 시 필터링

**DoD**: 9개 파싱, 검증 실패 시 빈 리스트 반환

---

### Task 9: DeepDiveRepository 확장 (1h)

**파일 수정**: `src/db/repository.py` (DeepDiveRepository에 +3개 메서드)

```python
@staticmethod
def insert_forecast(session: Session, **kwargs) -> FactDeepDiveForecast:
    """시나리오 예측 INSERT."""

@staticmethod
def insert_forecasts_batch(
    session: Session, report_id: int, date_id: int,
    stock_id: int, ticker: str,
    forecasts: list,  # list[ScenarioForecast]
) -> int:
    """9개 시나리오 예측 일괄 INSERT. 반환: INSERT 건수."""

@staticmethod
def get_forecasts_by_report(
    session: Session, report_id: int,
) -> list[FactDeepDiveForecast]:
    """보고서별 시나리오 예측 조회."""
```

**테스트**: `test_insert_forecast`, `test_get_forecasts_by_report`

**DoD**: 2개 테스트 통과, FK cascade 검증

---

### Task 10: 파이프라인 step3/step5/step7 확장 (2.5h)

**파일 수정**: `src/deepdive_pipeline.py`

**step3 변경**: `compute_all_layers()` 호출 시 `sector_id`, `ticker`, `reference_date` 추가

**step5 변경**: `run_deepdive_simple()` → `run_deepdive_debate()` 교체
- `self._debate_results: dict[str, CLIDebateResult] = {}` 인스턴스 변수 추가
- debate 결과에서 `final_result`를 `_ai_results`에 저장

**step7 변경**:
- `insert_report()` 호출 시 추가: `layer2_summary`, `layer5_summary`, `layer6_summary`, `ai_bull_text`, `ai_bear_text`, `consensus_strength`
- forecast INSERT: `DeepDiveRepository.insert_forecasts_batch()` 호출
- `_layer_summary()` 확장: `valuation_grade`, `narrative_grade`, `macro_grade` 지원

**Phase 1 의존**: 기존 step 구조, `_get_current_price()`, `_log_step()`, force 모드 삭제 로직

**테스트**: `test_debate_mode_pipeline`, `test_forecast_persist`

**DoD**: 기존 pipeline 테스트 통과, debate mock 전체 흐름 검증

---

### Task 11: /personal/{ticker} 라우트 (2h)

**파일 수정**: `src/web/routes/personal.py` (+~90줄)
**파일 수정**: `src/web/templates/personal.html` (카드에 링크 추가)

```python
@router.get("/personal/{ticker}")
def personal_detail(ticker: str, request: Request, db: Session = Depends(get_db)):
    """종목 상세 분석 페이지 — 6레이어 + 토론 + 시나리오."""
```

**쿼리 흐름**:
1. DimStock by ticker
2. 최신 FactDeepDiveReport (get_latest_report)
3. report_json 파싱 → 6개 레이어 데이터
4. FactDeepDiveForecast by report_id → 시나리오 데이터
5. 보유 정보 (WatchlistRepository.get_holding)
6. 현재 가격 (FactDailyPrice 최신)
7. 시나리오 차트 데이터 변환 (`_build_scenario_chart_data`)
8. 레이더 차트 데이터 변환 (`_build_radar_data`)

**등급 → 레이더 숫자 변환**:

| 레이어 | 등급 | 점수 |
|--------|------|------|
| L1 | A:9, B:7, C:5, D:3, F:1 |
| L2 | Cheap:9, Fair:6, Rich:3, Extreme:1 |
| L3 | Bullish:9, Neutral:5, Bearish:2 |
| L4 | Accumulation:9, Neutral:5, Distribution:2 |
| L5 | Positive:9, Neutral:5, Negative:2 |
| L6 | Favorable:9, Neutral:5, Headwind:2 |

**personal.html 수정**: 카드 전체를 `<a href="/personal/{{ card.ticker }}">` 링크로 래핑

**테스트**: `test_personal_detail_200`, `test_personal_detail_not_found`

**DoD**: 2개 테스트, 보고서 없는 종목도 에러 없이 렌더링

---

### Task 12: personal_detail.html 템플릿 (3h)

**파일 생성**: `src/web/templates/personal_detail.html` (~380줄)

**4탭 구조** (순수 JS 토글, Alpine.js 미사용):

| 탭 | 내용 |
|----|------|
| 요약 | AI 종합 판단, 액션 배지, conviction 바, key_levels, what_missing, 보유정보 |
| 6레이어 | layerRadarChart + 6개 아코디언 패널 (등급 배지 + 핵심 수치) |
| 시나리오 | scenarioRangeChart + 시나리오 테이블 (horizon, scenario, prob, price range) |
| 토론 | Bull 논거(녹 카드) + Bear 논거(적 카드) + consensus 배지 + 최종 판정 |

**레이아웃**:
- 브레드크럼: 개인 분석 > {TICKER}
- 헤더: 종목명 + 현재가 + 일간변화 + 액션 배지
- 탭 바: Tailwind border-b + 활성탭 인디고 하이라이트
- 면책: "투자 참고용이며 투자 권유가 아닙니다"
- 반응형: 모바일 1열, 데스크톱 2열 일부
- 다크모드: dark: prefix

**DoD**: 4탭 렌더링, 데이터 없으면 "분석 대기중", 모바일 반응형

---

### Task 13: charts.js — scenarioRangeChart + layerRadarChart (2h)

**파일 수정**: `src/web/static/charts.js` (+~100줄)

**scenarioRangeChart(domId, data)**:
```javascript
// data: { currentPrice: 180, horizons: [
//   { label: '1M', base: {low,high}, bull: {low,high}, bear: {low,high} }, ...
// ]}
// ECharts 수평 바: 각 horizon 행에 3개 범위 바 + 현재가 markLine
```

**layerRadarChart(domId, scores)**:
```javascript
// scores: { fundamental: 7, valuation: 4, technical: 8, flow: 6, narrative: 5, macro: 7 }
// 6축 레이더 (한국어 라벨: 펀더멘털/밸류에이션/기술적/수급/내러티브/매크로)
```

**기존 컨벤션 준수**: `initChart()`, `colorWithAlpha()`, `fmtNum`, 다크모드 `reinitAllCharts` 호환

**DoD**: 2개 차트 함수, 다크모드 동기화, 빈 데이터 처리

---

### Task 14: 테스트 확장 (~18개 추가, 3h)

**파일 수정/생성**:
- `tests/test_deepdive_layers.py` (+9개: Layer 2/5/6 각 3개)
- `tests/test_deepdive_ai.py` (신규, +6개: debate + scenario)
- `tests/test_deepdive_pipeline.py` (+2개: debate 모드 + forecast)
- `tests/test_deepdive_web.py` (+2개: detail 페이지)

**총 48개 = 기존 30 + 신규 18**

**DoD**: `pytest tests/test_deepdive_*.py -v` 48개 전체 통과

---

## 6. AI Debate 프롬프트 설계 초안

### R1 User Prompt (Bull/Bear 공통)

```
아래 종목 데이터를 분석하라. 6개 레이어 데이터를 모두 활용하여 최대한 강력한 논거를 제시하라.

{stock_context — 6개 레이어 XML 전체}

투자 참고용이며 투자 권유가 아닙니다.
```

### R2 Bull User Prompt (Bear R1 반박)

```
아래는 리스크 분석가(Bear)의 R1 분석이다.

<opponent_r1>
{bear_r1_text}
</opponent_r1>

위 리스크 분석을 읽고:
1. 반박할 수 있는 논거에 구체적 데이터로 반박하라
2. 인정할 논거는 인정하되, 매수 관점이 더 강한 이유를 설명하라
3. 새로운 매수 근거가 있으면 추가하라
4. 최종 JSON을 갱신하라

{stock_context}
```

### R2 Bear User Prompt (Bull R1 반박)

```
아래는 성장 투자 전문가(Bull)의 R1 분석이다.

<opponent_r1>
{bull_r1_text}
</opponent_r1>

위 매수 분석을 읽고:
1. 반박할 수 있는 논거에 구체적 데이터로 반박하라
2. 인정할 논거는 인정하되, 리스크가 더 큰 이유를 설명하라
3. 새로운 리스크 요인이 있으면 추가하라
4. 최종 JSON을 갱신하라

{stock_context}
```

### R3 Synthesizer User Prompt

```
Bull Agent(매수 전문가)와 Bear Agent(리스크 전문가)의 교차 검증 결과이다.

<bull_r2>
{bull_r2_text}
</bull_r2>

<bear_r2>
{bear_r2_text}
</bear_r2>

양측 논거를 평가하여 최종 판정을 JSON으로 출력하라.
- 논거의 구체성, 데이터 근거, 논리 일관성을 기준으로 판단
- 팽팽한 경우 보수적으로 판정 (HOLD 선호)
- 시나리오별 가격 범위와 확률을 구체적으로 제시하라

{stock_context}

투자 참고용이며 투자 권유가 아닙니다.
```

---

## 7. Claude CLI Opus Debate 호출 검증

### Phase 1에서 확인된 사항

- `run_deepdive_cli()` 함수가 정상 동작 (subprocess, --model opus, --system-prompt)
- CLI에 별도 `--thinking` 플래그 없음 → 프롬프트에 "단계적으로 깊이 사고" 문구로 유도
- `--output-format json` 사용 가능하나 Phase 1에서는 미사용 (텍스트 파싱이 더 유연)
- `--max-budget-usd` 사용 가능 → Phase 2에서 종목당 비용 캡 가능

### Phase 2 Debate 호출 방식 (종목당)

```
[1] claude -p --model opus --system-prompt "{BULL_PROMPT}" < r1_user_prompt
[2] claude -p --model opus --system-prompt "{BEAR_PROMPT}" < r1_user_prompt
[3] claude -p --model opus --system-prompt "{BULL_PROMPT}" < r2_bull_prompt
[4] claude -p --model opus --system-prompt "{BEAR_PROMPT}" < r2_bear_prompt
[5] claude -p --model opus --system-prompt "{SYNTH_PROMPT}" < r3_synth_prompt
```

### 기존 SDK debate (src/ai/debate.py)와 차이점

| 항목 | SDK debate | CLI debate (Phase 2) |
|------|------------|----------------------|
| 호출 방식 | Anthropic SDK `call_agent()` | `subprocess.run(["claude", "-p", ...])` |
| 병렬 실행 | R1 Bull/Bear 병렬 (ThreadPool) | 순차 (CLI 동시 실행 불가) |
| Tool Use | R3에서 `submit_stock_analysis` 강제 | 없음. JSON 텍스트 응답만 |
| 모델 | sonnet (일일 파이프라인) | opus (deep dive) |
| 파싱 | Tool Use input 직접 추출 | `_parse_ai_response()` regex fallback |

### 비용/시간 추정

| 항목 | 값 |
|------|------|
| 호출 수/종목 | 5회 |
| 12종목 전체 | 60회/일 |
| Opus 응답 시간 | 30-120초/회 |
| 전체 소요 시간 | 30-120분 |
| 예상 비용 | $15-45/일 |

---

## 8. 시나리오 예측 데이터 구조

### Synthesizer 출력 JSON

```json
{
  "action_grade": "HOLD",
  "conviction": 7,
  "uncertainty": "medium",
  "reasoning": "성장 모멘텀 유지, 밸류에이션 부담",
  "scenarios": {
    "1M": {
      "base": {"prob": 0.50, "low": 175.0, "high": 185.0, "trigger": "실적 후 가이던스 유지"},
      "bull": {"prob": 0.25, "low": 185.0, "high": 200.0, "trigger": "AI 매출 서프라이즈"},
      "bear": {"prob": 0.25, "low": 160.0, "high": 175.0, "trigger": "중국 규제 강화"}
    },
    "3M": { "base": {...}, "bull": {...}, "bear": {...} },
    "6M": { "base": {...}, "bull": {...}, "bear": {...} }
  },
  "consensus_strength": "medium",
  "what_missing": "옵션 시장 시그널 부재",
  "key_levels": {"support": 170.0, "resistance": 195.0, "stop_loss": 155.0},
  "next_review_trigger": "실적 발표 후 가이던스 변경 시"
}
```

### DB 매핑 (fact_deepdive_forecasts, 종목당 9 rows)

| forecast_id | report_id | ticker | horizon | scenario | probability | price_low | price_high | trigger_condition |
|-------------|-----------|--------|---------|----------|-------------|-----------|------------|-------------------|
| auto | 42 | NVDA | 1M | BASE | 0.50 | 175.0 | 185.0 | 실적 후 가이던스 유지 |
| auto | 42 | NVDA | 1M | BULL | 0.25 | 185.0 | 200.0 | AI 매출 서프라이즈 |
| auto | 42 | NVDA | 1M | BEAR | 0.25 | 160.0 | 175.0 | 중국 규제 강화 |
| ... | ... | ... | 3M/6M | ... | ... | ... | ... | ... |

`actual_price`, `actual_date`, `hit_range`는 Phase 3 백테스트에서 UPDATE.

---

## 9. 상세 페이지 구성

### 라우트 구조

```
GET /personal              → 카드 그리드 (Phase 1, 카드에 링크 추가)
GET /personal/{ticker}     → 종목 상세 (Phase 2 신규)
```

### 페이지 레이아웃

```
┌─────────────────────────────────────────────────────┐
│ ← 개인 분석 > NVDA                                  │
│ NVIDIA Corporation                                   │
│ $180.00 (+2.5%)  |  ADD [8/10] conviction           │
├─────────────────────────────────────────────────────┤
│ [요약]  [6레이어]  [시나리오]  [토론]                   │
├─────────────────────────────────────────────────────┤
│ (선택된 탭 콘텐츠)                                     │
└─────────────────────────────────────────────────────┘
```

### 탭 1: 요약
- AI 종합 판단 (report.ai_synthesis) — 인디고 그래디언트 카드
- 액션 등급 배지 + conviction 바 + uncertainty
- key_levels (지지/저항/손절) — 3열 KPI 카드
- what_missing — 황색 주의 카드
- 보유 정보 (있으면): 수량, 평단, P&L
- next_review_trigger

### 탭 2: 6레이어
- 상단: layerRadarChart (6축)
- 하단: 6개 아코디언 (등급 배지 + 핵심 수치 + 상세 metrics)

### 탭 3: 시나리오
- scenarioRangeChart (현재가 markLine + 3 horizon 바)
- 시나리오 테이블 (horizon, scenario, probability, price range, trigger)

### 탭 4: 토론
- Bull 논거 (ai_bull_text) — 녹색 테두리 카드
- Bear 논거 (ai_bear_text) — 적색 테두리 카드
- consensus_strength 배지 (high=녹, medium=황, low=적)
- 최종 판정 (ai_synthesis) — 인디고 카드

### 차트 함수 호출

```html
<script>
document.addEventListener('DOMContentLoaded', function() {
    var radarData = {{ radar_data | safe }};
    if (radarData) layerRadarChart('radarChart', radarData);

    var scenarioData = {{ scenario_chart | safe }};
    if (scenarioData) scenarioRangeChart('scenarioChart', scenarioData);
});
</script>
```

---

## 10. 통합 테스트 시나리오

### A: 전체 파이프라인 E2E (mock)
1. 워치리스트 2종목 (AAPL, NVDA), NVDA 보유정보 있음
2. `DeepDivePipeline.run()` (CLI 5회 mock)
3. 검증: step3에서 6레이어 계산, step5에서 debate 5회, step7에서 reports 2건 + forecasts 18건

### B: debate 부분 실패
1. AAPL R1 Bull 실패 (CLI 타임아웃)
2. R2 Bull 스킵 → Bear R1 결과로 R3 진행
3. final_result 정상 반환

### C: 상세 페이지 렌더링
1. NVDA 보고서 + 9개 forecast 존재
2. GET /personal/NVDA → 200, 4개 탭 + 차트 데이터 JSON 포함

### D: 보고서 없는 종목
1. MSFT 워치리스트에 있으나 보고서 미생성
2. GET /personal/MSFT → 200, "분석 대기중" 메시지

---

## 11. Phase 2 완료 정의

| # | 기준 | 검증 방법 |
|---|------|----------|
| 1 | 6개 레이어 모두 계산 | `compute_all_layers()` → dict에 layer1-6 key |
| 2 | 3라운드 토론 5회 호출 | mock `run_deepdive_cli` call_count == 5 |
| 3 | 시나리오 9개 INSERT | fact_deepdive_forecasts 종목당 9건 |
| 4 | 상세 페이지 4탭 렌더 | GET /personal/{ticker} 200 + 탭 HTML |
| 5 | scenarioRangeChart 동작 | JS 함수 정의 + 페이지 호출 |
| 6 | layerRadarChart 동작 | 6축 레이더 렌더링 |
| 7 | 48개 테스트 통과 | `pytest tests/test_deepdive_*.py` 전체 |
| 8 | 파일 < 400줄 | 신규 파일 전부 400줄 미만 |
| 9 | 한국어 UI | 모든 웹 텍스트 한국어 |
| 10 | DB 마이그레이션 불필요 | Phase 1 컬럼 재사용 |

---

## 12. Phase 3로 넘길 것

| # | 항목 | 이유 |
|---|------|------|
| 1 | 페어 분석 (dim_watchlist_pairs) | 코사인 유사도 + 시총 필터 별도 로직 |
| 2 | Diff 감지 (fact_deepdive_changes) | 전일 대비 비교 + UI 변경 배지 |
| 3 | /personal/{ticker}/history | 타임라인 + conviction 추이 |
| 4 | /personal/forecasts | 정확도 리더보드 + hit_range 계산 |
| 5 | actionTimelineChart | 액션 등급 변경 시각화 |
| 6 | cron 스크립트 | crontab 래퍼 + 로그 로테이션 |
| 7 | 예측 actual_price 백테스트 | hit_range 자동 UPDATE |
| 8 | 페어 비교 탭 | 상세 페이지 내 페어 데이터 |
| 9 | 변경 배지 (카드 그리드) | diff 감지 연동 |

---

## 13. 위험 요소

| # | 위험 | 심각도 | 경감 방안 |
|---|------|--------|----------|
| 1 | **AI 비용 폭증**: 60회/일 Opus → $15-45/일 | 높음 | `--max-budget-usd 5` 종목당 캡, 주말 스킵 |
| 2 | **실행 시간**: 60회 x 30-120초 = 30-120분 | 중 | spec에 "속도 중요하지 않음" 명시 |
| 3 | **JSON 파싱 실패**: scenarios 구조 불완전 | 중 | parse_scenarios() 검증 + 빈 리스트 폴백, regex fallback |
| 4 | **5년 밸류에이션 히스토리 부족**: 비SP500 종목 | 중 | 가용 기간 계산, 1년 미만 "데이터 부족" |
| 5 | **Layer 6 scipy 의존**: linregress 실패 | 낮음 | try/except + None, scipy 이미 설치됨 |
| 6 | **layers.py 400줄 초과** | 높음 | T0에서 레이어별 파일 분할 |
| 7 | **CLI 순차 호출 병목**: 5회 순차 = 종목당 최대 50분 | 중 | Phase 2.5에서 R1 병렬화 검토 |
| 8 | **뉴스 데이터 빈약**: 비SP500 종목 FactNews 적음 | 중 | Layer 5 뉴스 0건 → neutral, "데이터 부족" 표시 |

### master_plan과 어긋나는 부분

| master_plan | Phase 2 조정 | 이유 |
|-------------|-------------|------|
| Layer 6에 "페어 상대 성과" 포함 | Phase 3으로 이관 | 페어 데이터(dim_watchlist_pairs)가 Phase 3에서 생성 |
| Layer 4에 "옵션 PCR/IV" Phase 2 검토 | 계속 제외 | yfinance options chain 비용/속도 미검증 |
| debate R1 Bull/Bear 병렬 | 순차 실행 | CLI subprocess 동시 실행 불안정 |
