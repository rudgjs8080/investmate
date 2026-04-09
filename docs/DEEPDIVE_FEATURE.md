# Deep Dive 개인화 분석 — 기능 명세서

> Phase 1 + Phase 2 + Phase 3 전체 구현 완료 (89개 테스트 통과)

---

## 1. 기능 개요

사용자가 직접 관리하는 워치리스트(~12종목)를 매일 자동으로 deep dive 분석하고,
웹 대시보드에서 확인하는 시스템. 기존 daily pipeline(S&P 500 전체 스캔)과
**완전히 독립된 별도 프로세스**로 동작한다.

### 핵심 가치

| 가치 | 설명 |
|------|------|
| 개인화 분석 | 워치리스트 종목만 6레이어 심층 분석 |
| AI 토론 | Bull/Bear/Synthesizer 3라운드 멀티 에이전트 토론 |
| 시나리오 예측 | 3개 호라이즌(1M/3M/6M) x 3개 시나리오(BASE/BULL/BEAR) = 9개 예측 |
| 일일 변경 감지 | 전일 대비 액션/확신도/확률/리스크/트리거 변경 자동 추적 |
| 페어 비교 | 동종 섹터+시총+코사인 유사도 기반 top 5 페어 자동 선정 |
| 예측 정확도 | 만기 도래 예측의 적중률+방향 정확도 자동 측정 |
| 과거 누적 | 모든 분석 결과를 날짜별로 DB에 영구 보존 |

---

## 2. 8단계 파이프라인

```
Step 1: dd_s1_load     — 워치리스트 로드 + 자동 등록
Step 2: dd_s2_collect   — 비S&P500 또는 오늘 데이터 없는 종목 수집
Step 3: dd_s3_compute   — 종목별 6개 레이어 계산
Step 4: dd_s4_pairs     — 페어 자동 선정 (7일 staleness 체크)       [Phase 3]
Step 5: dd_s5_ai        — 종목별 3라운드 CLI 토론
Step 6: dd_s6_diff      — 전일 대비 변경점 추출                      [Phase 3]
Step 7: dd_s7_persist   — 보고서+액션+변경사항+만기예측 DB 저장       [Phase 3 확장]
Step 8: dd_s8_notify    — 텔레그램 알림 (변경 감지 포함)              [Phase 3 확장]
```

### 파이프라인 특성

- **Resilient**: 개별 종목 실패 격리 (try/except per ticker)
- **Step Checkpointing**: `--force`로 재실행, 이미 완료된 스텝 스킵
- **Graceful Shutdown**: SIGTERM/SIGINT 핸들링
- **cron 자동 실행**: `scripts/run_deepdive.sh` (평일 07:00)

---

## 3. 6개 분석 레이어 (Phase 1)

| Layer | 이름 | 주요 지표 | 등급 |
|-------|------|-----------|------|
| L1 | 펀더멘털 헬스체크 | F-Score, Z-Score, 마진 추세, ROE, 부채비율 | A/B/C/D/F |
| L2 | 밸류에이션 컨텍스트 | PER/PBR 5년 백분위, 섹터 프리미엄, DCF, PEG, FCF Yield | Cheap/Fair/Rich/Extreme |
| L3 | 멀티타임프레임 기술적 | 추세 정렬, 52주 위치, RSI, MACD, S/R, ATR | Bullish/Neutral/Bearish |
| L4 | 수급/포지셔닝 | 내부자 거래, 공매도, 애널리스트, 기관 동향 | Accumulation/Neutral/Distribution |
| L5 | 내러티브 + 촉매 | 감성 30/60/90일, 촉매, 리스크 이벤트, 경영진 변경 | Positive/Neutral/Negative |
| L6 | 거시 민감도 | VIX/금리/달러 베타, 섹터 모멘텀 순위, 레짐 | Favorable/Neutral/Headwind |

---

## 4. AI 토론 시스템 (Phase 2)

### 3라운드 토론 흐름

```
R1: Bull 독립분석 + Bear 독립분석 (병렬 2회 호출)
R2: Bull(Bear R1 반박) + Bear(Bull R1 반박) (병렬 2회 호출)
R3: Synthesizer(Bull R2 + Bear R2 종합) — Tool Use 판정 (1회 호출)
```

### AI 판정 결과

| 필드 | 설명 |
|------|------|
| `action_grade` | HOLD / ADD / TRIM / EXIT |
| `conviction` | 1-10 확신도 |
| `uncertainty` | low / medium / high |
| `reasoning` | 200자 이내 종합 판단 |
| `what_missing` | 반대 의견 강조 |

### 시나리오 예측 (Phase 2)

| 호라이즌 | 시나리오 | 내용 |
|----------|---------|------|
| 1M / 3M / 6M | BASE | 가장 가능한 시나리오 (확률 ~50%) |
| 1M / 3M / 6M | BULL | 상방 시나리오 (확률 ~25%) |
| 1M / 3M / 6M | BEAR | 하방 시나리오 (확률 ~25%) |

각 시나리오: `price_low`, `price_high`, `probability`, `trigger_condition`

---

## 5. 페어 자동 선정 (Phase 3)

### 3단계 필터

```
단계 1: GICS 섹터 매칭 → ~20-80 종목
단계 2: 시총 근접도 (0.3x~3x) → ~10-30 종목
단계 3: 60일 수익률 코사인 유사도 → top 5 선정
```

- **갱신 주기**: 7 캘린더일 staleness (step4에서 체크)
- **코사인 유사도**: numpy 기반, 최소 20 거래일 데이터 필요
- **결과**: `PeerComparison` DTO (유사도, 시총비, 60일 수익률, PER 비교)
- **AI 주입**: `<pair_comparison>` XML 블록으로 토론 프롬프트에 포함

---

## 6. 일일 변경 감지 (Phase 3)

### 5가지 변경 유형

| 변경 유형 | 감지 조건 | 심각도 |
|-----------|----------|--------|
| `action_changed` | 액션 등급 변경 (예: HOLD -> ADD) | **critical** |
| `conviction_shift` | 확신도 \|변화\| >= 2 | **warning** |
| `probability_shift` | 동일 시나리오 확률 변화 >= 10%p | **info** |
| `new_risk` | 이전에 없던 리스크 이벤트 | **warning** |
| `trigger_hit` | 이전 리뷰 트리거 조건 도달 | **critical** |

### 변경 정보 활용

- 카드 그리드: 빨간 배지로 변경 건수 표시
- 상세 페이지: "오늘의 변경사항" 섹션
- 히스토리 페이지: 변경 이력 테이블
- 알림: 액션 변경/신규 리스크 메시지에 포함

---

## 7. 예측 정확도 측정 (Phase 3)

### 만기 판정

```
forecast_date + HORIZON_DAYS[horizon] <= as_of_date → 만기 도래
HORIZON_DAYS = {"1M": 30, "3M": 90, "6M": 180}
```

### 적중 판정

```
hit_range = (price_low <= actual_price <= price_high)
```

### 정확도 점수

```
hit_rate = 적중 수 / 평가 수
direction_accuracy = 방향 정확 수 / 평가 수
overall_score = hit_rate * 0.6 + direction_accuracy * 0.4
```

- **방향 정확도**: BASE midpoint 기준으로 BULL(actual > mid), BEAR(actual < mid) 판정
- **그룹별 집계**: by_horizon (1M/3M/6M), by_scenario (BASE/BULL/BEAR)

---

## 8. 웹 대시보드

### 라우트 구성 (4개)

| 경로 | 설명 |
|------|------|
| `/personal` | 워치리스트 카드 그리드 (변경 배지, P&L, 확신도 바) |
| `/personal/forecasts` | 예측 정확도 리더보드 + 바 차트 + 시나리오별 히트율 |
| `/personal/{ticker}` | 종목 상세 (요약/6레이어/시나리오/토론 4탭 + 변경사항) |
| `/personal/{ticker}/history` | 과거 분석 타임라인 + 변경 이력 + 시나리오 정확도 |

### 차트 (charts.js)

| 함수 | 용도 | Phase |
|------|------|-------|
| `layerRadarChart` | 6레이어 레이더 차트 | Phase 2 |
| `scenarioRangeChart` | 시나리오 가격 범위 차트 | Phase 2 |
| `actionTimelineChart` | 확신도 추이 + 등급 변경 마커 | Phase 3 |
| `accuracyBarChart` | 종목별 적중률/방향/종합 수평 바 | Phase 3 |

---

## 9. DB 테이블 (7개)

| 테이블 | 유형 | 용도 |
|--------|------|------|
| `dim_watchlist` | Dimension | 워치리스트 종목 (active/inactive) |
| `dim_watchlist_holdings` | Dimension | 보유 정보 (수량, 평단가) |
| `dim_watchlist_pairs` | Dimension | 페어 종목 (ticker, peer_ticker, similarity) |
| `fact_deepdive_reports` | Fact | 일별 분석 리포트 (6레이어 요약 + AI 결과 JSON) |
| `fact_deepdive_actions` | Fact | 액션 등급 히스토리 (prev_grade 포함) |
| `fact_deepdive_forecasts` | Fact | 시나리오 예측 (actual_price/hit_range 백필) |
| `fact_deepdive_changes` | Fact | 일일 변경 감지 결과 |

---

## 10. 신규 파일 목록 및 역할

### 핵심 모듈 (`src/deepdive/`)

| 파일 | 줄수 | Phase | 역할 |
|------|------|-------|------|
| `__init__.py` | 1 | P1 | 패키지 초기화 |
| `schemas.py` | 192 | P1+P3 | 불변 DTO: 6개 레이어 결과, AIResult, ScenarioForecast, DebateRound, CLIDebateResult, PeerComparison, ChangeRecord, ForecastAccuracy |
| `layers.py` | 61 | P1 | 6개 레이어 통합 오케스트레이터 (`compute_all_layers`) |
| `layers_fundamental.py` | 148 | P1 | L1: F-Score, Z-Score, 마진 추세, ROE 계산 |
| `layers_valuation.py` | 242 | P1 | L2: PER/PBR 백분위, DCF, PEG, FCF Yield 계산 |
| `layers_technical.py` | 154 | P1 | L3: 추세 정렬, RSI, MACD, S/R, ATR 계산 |
| `layers_flow.py` | 145 | P1 | L4: 내부자, 공매도, 애널리스트, 기관 동향 분석 |
| `layers_narrative.py` | 178 | P1 | L5: 뉴스 감성, 촉매, 리스크 이벤트 추출 |
| `layers_macro.py` | 212 | P1 | L6: VIX/금리/달러 베타, 레짐, 섹터 모멘텀 계산 |
| `layers_utils.py` | 28 | P1 | 공용 유틸: safe_float, 비율 계산, 반올림 |
| `watchlist_manager.py` | 141 | P1 | 워치리스트 로드, 비S&P500 자동 등록, WatchlistEntry DTO |
| `ai_prompts.py` | 312 | P1+P3 | `<stock_context>` XML 빌더 (6레이어+보유정보+페어비교), CLI 호출, JSON 파싱 |
| `ai_debate_cli.py` | 256 | P2+P3 | 3라운드 CLI 토론 오케스트레이터 (Bull/Bear/Synthesizer), pair_results 전달 |
| `scenarios.py` | 74 | P2 | Synthesizer JSON → ScenarioForecast 파싱 |
| `pair_analysis.py` | 259 | P3 | 섹터+시총+코사인 유사도 기반 페어 자동 선정, 7일 staleness |
| `diff_detector.py` | 203 | P3 | 5가지 변경 유형 감지 (action/conviction/probability/risk/trigger) |
| `forecast_evaluator.py` | 179 | P3 | 만기 도래 예측 actual_price 백필 + 정확도 점수 계산 |

### 파이프라인

| 파일 | 줄수 | Phase | 역할 |
|------|------|-------|------|
| `src/deepdive_pipeline.py` | 525 | P1+P2+P3 | 8단계 파이프라인: signal handling, checkpointing, resilient, 요약 JSON 저장 |

### DB 확장 (`src/db/`)

| 파일 | 변경 | Phase | 역할 |
|------|------|-------|------|
| `models.py` | 수정 | P1 | 7개 Deep Dive 테이블 ORM 모델 (DimWatchlist ~ FactDeepDiveChange) |
| `repository.py` | 수정 | P1+P3 | WatchlistRepository (9개 메서드), DeepDiveRepository (18개 메서드) |

### 웹 라우트 + 템플릿

| 파일 | 줄수 | Phase | 역할 |
|------|------|-------|------|
| `src/web/routes/personal.py` | 327 | P2+P3 | 4개 라우트: 카드그리드, 예측정확도, 종목상세, 히스토리 |
| `src/web/templates/personal.html` | 110 | P2+P3 | 카드 그리드 (변경 배지, P&L, 확신도 바) |
| `src/web/templates/personal_detail.html` | 229 | P2+P3 | 4탭 상세 (요약+변경사항, 6레이어, 시나리오, 토론) |
| `src/web/templates/personal_history.html` | 163 | P3 | 타임라인 차트 + 분석/변경/예측 이력 |
| `src/web/templates/personal_forecasts.html` | 136 | P3 | 리더보드 + 바 차트 + 시나리오별 히트율 |

### 차트 + 알림

| 파일 | 변경 | Phase | 역할 |
|------|------|-------|------|
| `src/web/static/charts.js` | 수정 | P2+P3 | +4개 함수: layerRadar, scenarioRange, actionTimeline, accuracyBar |
| `src/alerts/notifier.py` | 수정 | P3 | `send_deepdive_summary()` 강화: action_changes, new_risks 파라미터 |

### 스크립트

| 파일 | 줄수 | Phase | 역할 |
|------|------|-------|------|
| `scripts/seed_watchlist.py` | ~50 | P1 | 초기 12종목 워치리스트 시드 |
| `scripts/run_deepdive.sh` | 22 | P3 | cron 실행 래퍼 (venv 활성화, 로그) |

### 테스트 (8개 파일, 89개 테스트)

| 파일 | 줄수 | 테스트 수 | Phase | 대상 |
|------|------|----------|-------|------|
| `tests/test_deepdive_watchlist.py` | 102 | 10 | P1 | 워치리스트 CRUD + Manager |
| `tests/test_deepdive_layers.py` | 228 | 12 | P1 | 6개 분석 레이어 |
| `tests/test_deepdive_ai.py` | 169 | 8 | P2 | AI debate + scenario 파싱 |
| `tests/test_deepdive_pipeline.py` | 203 | 14 | P1+P3 | 파이프라인 통합 + Phase 3 steps |
| `tests/test_deepdive_web.py` | 149 | 13 | P2+P3 | 4개 라우트 200 응답 + 데이터 |
| `tests/test_deepdive_pairs.py` | 141 | 8 | P3 | 코사인 유사도 + 페어 선정 + staleness |
| `tests/test_deepdive_diff.py` | 136 | 12 | P3 | 5가지 변경 유형 감지 |
| `tests/test_deepdive_forecast.py` | 154 | 12 | P3 | 만기 계산 + 적중 + 정확도 점수 |

---

## 11. Phase별 구현 요약

### Phase 1: 기반 구축

- DB 스키마 7개 테이블
- 워치리스트 관리 (CRUD + 비S&P500 자동 등록)
- 6개 분석 레이어 (layers_fundamental ~ layers_macro)
- 단일 AI 호출 (ai_prompts.py)
- 파이프라인 기본 골격 (6개 step)
- `/personal` 카드 그리드 + `/personal/{ticker}` 기본 상세
- 30개 테스트

### Phase 2: AI 토론 + 시나리오

- 3라운드 멀티 에이전트 토론 (ai_debate_cli.py)
- 시나리오 예측 파싱 (scenarios.py)
- 레이어 레이더 차트 + 시나리오 범위 차트
- `/personal/{ticker}` 4탭 구조 (요약/6레이어/시나리오/토론)
- 45개 테스트 (기존 30 + 신규 15)

### Phase 3: 페어 + 변경감지 + 예측정확도

- 페어 자동 선정 (pair_analysis.py)
- 일일 변경 감지 (diff_detector.py)
- 예측 만기 평가 + 정확도 (forecast_evaluator.py)
- 파이프라인 8단계 확장 (step4_pairs, step6_diff)
- AI 프롬프트에 `<pair_comparison>` 블록
- `/personal/forecasts` 리더보드 + `/personal/{ticker}/history` 타임라인
- 카드 변경 배지 + 상세 변경사항 섹션
- 알림 강화 (액션 변경/신규 리스크)
- cron 스크립트
- 89개 테스트 (기존 45 + 신규 44)

---

## 12. 통계 요약

| 항목 | 수치 |
|------|------|
| 프로덕션 파일 | 36개 |
| 프로덕션 코드 | ~5,400줄 |
| 테스트 파일 | 8개 |
| 테스트 코드 | ~1,280줄 |
| 테스트 수 | 89개 (전부 통과) |
| DB 테이블 | 7개 |
| 웹 라우트 | 4개 |
| 차트 함수 | 4개 |
| 파이프라인 단계 | 8개 |
| 분석 레이어 | 6개 |
| 변경 감지 유형 | 5가지 |
| 시나리오 예측 | 9개/종목 (3H x 3S) |
