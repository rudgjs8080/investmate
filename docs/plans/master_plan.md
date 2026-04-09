# Deep Dive 개인화 분석 기능 — 구현 계획서

## Context

사용자가 직접 관리하는 워치리스트(~12종목)를 매일 자동으로 deep dive 분석하고 웹에서 확인하는 기능을 추가한다. 기존 daily pipeline(S&P 500 전체 스캔)과 **완전히 독립된 별도 프로세스**로 동작하며, **일일 변경 감지**와 **과거 분석 누적 보관**이 핵심 가치다.

---

## 1. 신규 파일 목록

| # | 경로 | 설명 |
|---|------|------|
| 1 | `src/deepdive_pipeline.py` | Deep dive 전용 8단계 파이프라인 (DailyPipeline 패턴 복제) |
| 2 | `src/deepdive/__init__.py` | Deep dive 패키지 초기화 |
| 3 | `src/deepdive/layers.py` | 6개 분석 레이어 계산 로직 (Layer 1~6) |
| 4 | `src/deepdive/pair_analysis.py` | 페어 자동 선정 + 상대 비교 (코사인 유사도) |
| 5 | `src/deepdive/diff_detector.py` | 전일 리포트 대비 변경점 추출 |
| 6 | `src/deepdive/ai_prompts.py` | Bull/Bear/Synthesizer 프롬프트 빌더 + 보유정보 주입 |
| 7 | `src/deepdive/ai_debate_cli.py` | Claude CLI 기반 3라운드 토론 오케스트레이터 |
| 8 | `src/deepdive/schemas.py` | Pydantic 스키마 (DeepDiveReport, LayerResult 등) |
| 9 | `src/deepdive/watchlist_manager.py` | 워치리스트 CRUD + 비S&P500 종목 dim_stocks 자동 등록 |
| 10 | `src/web/routes/personal.py` | `/personal` 라우트 4개 (카드, 상세, 히스토리, 예측) |
| 11 | `src/web/templates/personal.html` | 워치리스트 카드 그리드 템플릿 |
| 12 | `src/web/templates/personal_detail.html` | 종목 상세 (6레이어 + AI + 시나리오) |
| 13 | `src/web/templates/personal_history.html` | 과거 분석 타임라인 + 정확도 |
| 14 | `src/web/templates/personal_forecasts.html` | 예측 정확도 리더보드 |
| 15 | `scripts/seed_watchlist.py` | 초기 12종목 시드 (INSERT OR IGNORE) |
| 16 | `scripts/run_deepdive.sh` | Deep dive cron 실행 래퍼 |
| 17 | `tests/test_deepdive_pipeline.py` | 파이프라인 통합 테스트 |
| 18 | `tests/test_deepdive_layers.py` | 6개 레이어 단위 테스트 |
| 19 | `tests/test_deepdive_ai.py` | CLI 토론 mock 테스트 |
| 20 | `tests/test_deepdive_watchlist.py` | 워치리스트 CRUD + 시드 테스트 |
| 21 | `tests/test_deepdive_diff.py` | Diff 감지 테스트 |
| 22 | `tests/test_deepdive_web.py` | `/personal` 라우트 테스트 |

---

## 2. 수정 파일 목록

| 파일 | 변경 요지 |
|------|----------|
| `src/db/models.py` | 7개 신규 테이블 ORM 모델 추가 (dim_watchlist, dim_watchlist_holdings, dim_watchlist_pairs, fact_deepdive_reports, fact_deepdive_forecasts, fact_deepdive_actions, fact_deepdive_changes) |
| `src/db/repository.py` | `WatchlistRepository`, `DeepDiveRepository` 클래스 추가 |
| `src/main.py` | Click 그룹 `watchlist` (add/remove/list/set-holding)와 `deepdive` (run/show/history) 명령 추가 |
| `src/config.py` | `ai_model_deepdive` (기본값: `opus`), `deepdive_timeout` (기본값: 600) 설정 추가 |
| `src/web/app.py` | `personal_router` import 및 `include_router()` 등록 |
| `src/web/static/charts.js` | 시나리오 가격 범위 차트(`scenarioRangeChart`), 레이더 차트(`layerRadarChart`), 액션 타임라인(`actionTimelineChart`) 함수 추가 |
| `src/web/templates/base.html` | 네비게이션에 "개인 분석" (`/personal`) 메뉴 항목 추가 |
| `src/alerts/notifier.py` | `send_deepdive_summary()` 함수 추가 |
| `src/ai/constants.py` | `NON_TICKERS`에 "ADD", "TRIM", "EXIT" 추가 (파싱 충돌 방지) |

---

## 3. DB 스키마 변경

### 3.1 신규 테이블 ORM 정의

마이그레이션은 `src/db/migrate.py`의 `ensure_schema()`가 ORM metadata 기반으로 자동 처리 — 별도 마이그레이션 SQL 불필요.

```sql
-- dim_watchlist: 워치리스트 종목 관리
CREATE TABLE dim_watchlist (
    watchlist_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          VARCHAR(20) NOT NULL UNIQUE,
    added_at        DATETIME NOT NULL DEFAULT (datetime('now')),
    active          BOOLEAN NOT NULL DEFAULT 1,
    note            TEXT,
    created_at      DATETIME DEFAULT (datetime('now')),
    updated_at      DATETIME DEFAULT (datetime('now'))
);
CREATE INDEX idx_watchlist_active ON dim_watchlist(active);

-- dim_watchlist_holdings: 보유 정보 (선택적)
CREATE TABLE dim_watchlist_holdings (
    holding_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          VARCHAR(20) NOT NULL UNIQUE,
    avg_cost        NUMERIC NOT NULL,
    shares          INTEGER NOT NULL,
    opened_at       DATE,
    created_at      DATETIME DEFAULT (datetime('now')),
    updated_at      DATETIME DEFAULT (datetime('now'))
);

-- dim_watchlist_pairs: 자동 선정된 페어 종목
CREATE TABLE dim_watchlist_pairs (
    pair_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          VARCHAR(20) NOT NULL,
    peer_ticker     VARCHAR(20) NOT NULL,
    similarity_score NUMERIC,
    updated_at      DATETIME DEFAULT (datetime('now')),
    UNIQUE(ticker, peer_ticker)
);
CREATE INDEX idx_pairs_ticker ON dim_watchlist_pairs(ticker);

-- fact_deepdive_reports: 매일 누적 풀 리포트 (절대 덮어쓰지 않음)
CREATE TABLE fact_deepdive_reports (
    report_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date_id         INTEGER NOT NULL REFERENCES dim_date(date_id),
    stock_id        INTEGER NOT NULL REFERENCES dim_stocks(stock_id) ON DELETE CASCADE,
    ticker          VARCHAR(20) NOT NULL,
    action_grade    VARCHAR(4) NOT NULL,        -- HOLD/ADD/TRIM/EXIT
    conviction      INTEGER NOT NULL,           -- 1-10
    uncertainty     VARCHAR(6) NOT NULL,        -- low/medium/high
    report_json     TEXT NOT NULL,              -- 전체 분석 JSON (6레이어+AI+시나리오)
    layer1_summary  TEXT,
    layer2_summary  TEXT,
    layer3_summary  TEXT,
    layer4_summary  TEXT,
    layer5_summary  TEXT,
    layer6_summary  TEXT,
    ai_bull_text    TEXT,
    ai_bear_text    TEXT,
    ai_synthesis    TEXT,
    consensus_strength VARCHAR(10),
    what_missing    TEXT,
    created_at      DATETIME DEFAULT (datetime('now')),
    updated_at      DATETIME DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX uq_dd_reports_stock_date ON fact_deepdive_reports(stock_id, date_id);
CREATE INDEX idx_dd_reports_ticker_date ON fact_deepdive_reports(ticker, date_id);

-- fact_deepdive_forecasts: 시나리오 예측 (정확도 측정용)
CREATE TABLE fact_deepdive_forecasts (
    forecast_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id       INTEGER NOT NULL REFERENCES fact_deepdive_reports(report_id) ON DELETE CASCADE,
    date_id         INTEGER NOT NULL,
    stock_id        INTEGER NOT NULL,
    ticker          VARCHAR(20) NOT NULL,
    horizon         VARCHAR(2) NOT NULL,        -- 1M/3M/6M
    scenario        VARCHAR(4) NOT NULL,        -- BASE/BULL/BEAR
    probability     NUMERIC,                    -- 0.0-1.0
    price_low       NUMERIC,
    price_high      NUMERIC,
    trigger_condition TEXT,
    actual_price    NUMERIC,                    -- 후일 업데이트
    actual_date     DATE,
    hit_range       BOOLEAN,                    -- 실제가 범위 내 여부
    created_at      DATETIME DEFAULT (datetime('now')),
    updated_at      DATETIME DEFAULT (datetime('now'))
);
CREATE INDEX idx_dd_forecasts_report ON fact_deepdive_forecasts(report_id);
CREATE INDEX idx_dd_forecasts_ticker ON fact_deepdive_forecasts(ticker, horizon);

-- fact_deepdive_actions: 액션 등급 히스토리
CREATE TABLE fact_deepdive_actions (
    action_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date_id         INTEGER NOT NULL,
    stock_id        INTEGER NOT NULL,
    ticker          VARCHAR(20) NOT NULL,
    action_grade    VARCHAR(4) NOT NULL,
    conviction      INTEGER NOT NULL,
    prev_action_grade VARCHAR(4),
    prev_conviction INTEGER,
    created_at      DATETIME DEFAULT (datetime('now')),
    updated_at      DATETIME DEFAULT (datetime('now'))
);
CREATE INDEX idx_dd_actions_ticker ON fact_deepdive_actions(ticker, date_id);

-- fact_deepdive_changes: 일일 변경 감지 결과
CREATE TABLE fact_deepdive_changes (
    change_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    date_id         INTEGER NOT NULL,
    stock_id        INTEGER NOT NULL,
    ticker          VARCHAR(20) NOT NULL,
    change_type     VARCHAR(30) NOT NULL,       -- action_changed/new_risk/probability_shift/trigger_hit
    description     TEXT NOT NULL,
    severity        VARCHAR(10) NOT NULL DEFAULT 'info',  -- info/warning/critical
    created_at      DATETIME DEFAULT (datetime('now')),
    updated_at      DATETIME DEFAULT (datetime('now'))
);
CREATE INDEX idx_dd_changes_date ON fact_deepdive_changes(date_id);
CREATE INDEX idx_dd_changes_ticker ON fact_deepdive_changes(ticker, date_id);
```

### 3.2 Step 이름 컨벤션

`FactCollectionLog.step`이 `String(30)`이므로 deep dive step 이름을 25자 이내로 축약:
`dd_s1_load`, `dd_s2_collect`, `dd_s3_compute`, `dd_s4_pairs`, `dd_s5_ai`, `dd_s6_diff`, `dd_s7_persist`, `dd_s8_notify`

---

## 4. 파이프라인 단계별 흐름

### DeepDivePipeline 클래스

```
Class: DeepDivePipeline(engine, target_date, ticker=None, force=False, skip_notify=False)
  - DailyPipeline과 동일한 signal handling / checkpointing / resilient 패턴
  - ticker=None이면 전체 active 워치리스트, 지정 시 해당 종목만
```

### Step 1: `dd_s1_load` — 워치리스트 로드

| | 내용 |
|---|------|
| **입력** | DB: dim_watchlist(active=1), dim_watchlist_holdings, dim_stocks |
| **처리** | 1) active 워치리스트 조회 → 2) dim_stocks에 없는 ticker는 yfinance `.info`로 자동 등록 (name, sector, market, is_sp500=False) → 3) holdings JOIN으로 보유정보 매핑 → 4) 특정 ticker 지정 시 필터 |
| **출력** | `dict[str, WatchlistEntry]` — ticker → (stock_id, name, sector, holding_info) |

### Step 2: `dd_s2_collect` — 데이터 보강

| | 내용 |
|---|------|
| **입력** | Step 1의 ticker 리스트, 기존 DB |
| **처리** | 각 ticker에 대해 오늘자 데이터 존재 여부 확인. **이미 daily pipeline이 수집한 S&P500 종목은 대부분 스킵**. 비S&P500(SMR 등)만 풀 수집: `batch_download_prices()`, `fetch_financial_data()`, `collect_all_enhanced()` 재사용 |
| **출력** | 수집 레코드 수 (DB 반영) |

### Step 3: `dd_s3_compute` — 6개 레이어 계산

| | 내용 |
|---|------|
| **입력** | DB의 가격/재무/밸류에이션/지표/매크로/뉴스 |
| **처리** | 종목별 Layer 1~6 계산 (상세 → 섹션 6) |
| **출력** | `dict[str, LayerResults]` — ticker → 6개 레이어 결과 |

### Step 4: `dd_s4_pairs` — 페어 분석

| | 내용 |
|---|------|
| **입력** | dim_watchlist_pairs, 가격 데이터 |
| **처리** | 1) 기존 페어 로드 (7일 이내면 재사용) → 2) 없거나 오래되면 자동 갱신: 동일 GICS 섹터+산업, 시총 0.3x~3x, 60일 수익률 코사인 유사도 top 5 → 3) 페어 대비 성과·밸류에이션 비교 |
| **출력** | `dict[str, list[PairComparison]]` |

### Step 5: `dd_s5_ai` — AI Deep Analysis

| | 내용 |
|---|------|
| **입력** | Step 3 레이어 + Step 4 페어 + 보유정보 |
| **처리** | 종목별 CLI 호출. Phase 1: 단일 호출. Phase 2: 3라운드 debate (R1 Bull+Bear 순차 → R2 교차반박 → R3 Synthesizer). `--model opus --system-prompt` 플래그 사용 |
| **출력** | `dict[str, AIDeepDiveResult]` — action_grade, conviction, uncertainty, scenarios, what_missing |

### Step 6: `dd_s6_diff` — 변경 감지

| | 내용 |
|---|------|
| **입력** | Step 5 AI 결과, DB의 전일 fact_deepdive_reports |
| **처리** | 전일 리포트와 비교: action_grade 변경, conviction 2+ 변화, 시나리오 확률 10%p+ 변화, 신규 리스크 등장, 트리거 도달. severity 분류 (critical/warning/info) |
| **출력** | `dict[str, list[ChangeRecord]]` |

### Step 7: `dd_s7_persist` — DB 저장

| | 내용 |
|---|------|
| **입력** | Steps 3~6 전체 결과 |
| **처리** | 1) fact_deepdive_reports INSERT (새 row) → 2) fact_deepdive_forecasts INSERT (9개 시나리오/종목) → 3) fact_deepdive_actions INSERT → 4) fact_deepdive_changes INSERT → 5) 과거 예측 actual_price 업데이트 (1M/3M/6M 경과 건) |
| **출력** | 총 INSERT 수 |

### Step 8: `dd_s8_notify` — 알림

| | 내용 |
|---|------|
| **입력** | Step 5/6 요약 |
| **처리** | 1줄 요약 구성 → `_send_telegram()` 호출 |
| **출력** | `Deep dive 완료: 12종목 분석, 액션 변경 2건, 신규 리스크 1건` |

---

## 5. 기존 모듈 재사용 매핑

| Deep Dive 기능 | 재사용 모듈/함수 | 위치 |
|---|---|---|
| 가격 수집 | `batch_download_prices()` | `src/data/providers/yfinance_provider.py` |
| 재무 수집 | `fetch_financial_data()` | `src/data/providers/yfinance_provider.py` |
| 내부자/기관/애널리스트/실적/공매도 | `collect_all_enhanced()` | `src/data/enhanced_collector.py` |
| 매크로 | `collect_macro()` | `src/data/macro_collector.py` |
| 뉴스 | news_scraper 모듈 | `src/data/` |
| 실적 캘린더 | `collect_earnings_calendar()` | `src/data/event_collector.py` |
| FOMC 일정 | `get_next_fomc_date()` | `src/data/event_collector.py` |
| F-Score | `calculate_piotroski()` | `src/analysis/quality.py` |
| Z-Score | `calculate_altman_z()` | `src/analysis/quality.py` |
| 기술 지표 | `calculate_indicators()` | `src/analysis/technical.py` |
| 시그널 | `detect_signals()` | `src/analysis/signals.py` |
| S/R 레벨 | `find_support_resistance()` | `src/analysis/support_resistance.py` |
| 상대 강도 | `calculate_rs_ranks()` | `src/analysis/relative_strength.py` |
| 펀더멘털 분석 | `analyze_fundamentals()` | `src/analysis/fundamental.py` |
| 매크로 점수 | `analyze_macro()` | `src/analysis/external.py` |
| 뉴스 감성 | `analyze_news_sentiment()` | `src/analysis/external.py` |
| 섹터 모멘텀 | `calculate_sector_momentum()` | `src/analysis/external.py` |
| 시장 레짐 | `detect_regime()` | `src/ai/regime.py` |
| CLI 호출 패턴 | `run_claude_analysis()` 구조 참고 | `src/ai/claude_analyzer.py:248-299` |
| JSON 파싱 | `_try_parse_json()`, `_extract_json_robust()` | `src/ai/claude_analyzer.py` |
| 토론 구조 패턴 | debate 3라운드 흐름 참고 | `src/ai/debate.py` |
| 텔레그램 알림 | `_send_telegram()` | `src/alerts/notifier.py` |
| 체크포인팅 | `_is_step_done()`, `_log_step()` 패턴 | `src/pipeline.py` |
| DB 세션 | `get_session()` | `src/db/engine.py` |
| 날짜 유틸 | `date_to_id()`, `ensure_date_ids()` | `src/db/helpers.py` |
| 웹 의존성 | `get_db()` | `src/web/deps.py` |
| 차트 | `initChart()`, `lineChartOption()`, `gaugeChartOption()` | `src/web/static/charts.js` |

---

## 6. 신규 분석 로직 — 6 레이어 상세

### Layer 1: 펀더멘털 헬스체크

1. **마진 트렌드**: `FactFinancial` 최근 8분기 → gross_margin(`operating_income/revenue`), net_margin(`net_income/revenue`). QoQ/YoY 변화율. 4분기 연속 개선/악화 플래그
2. **FCF**: `operating_cashflow`를 FCF 프록시. 4분기 트렌드, revenue 대비 FCF 비율
3. **부채/이자보상**: `total_liabilities/total_assets` (debt ratio), `operating_income/(total_liabilities*0.05)` (이자보상 프록시)
4. **ROE/ROIC**: `net_income/total_equity` (ROE), `operating_income/(total_assets - total_liabilities*0.5)` (ROIC 프록시). 4분기 트렌드
5. **F-Score**: `calculate_piotroski()` 직접 호출. 7+ = 건전, 3- = 위험
6. **Z-Score**: `calculate_altman_z()` 직접 호출. 3.0+ = 안전, 1.8- = 위험
7. **가이던스 vs 실적**: `FactEarningsSurprise` 최근 4분기 surprise_pct 평균, beat/miss 연속 패턴
8. **출력**: health_grade (A/B/C/D/F) + 상세 메트릭

### Layer 2: 밸류에이션 컨텍스트

1. **5년 % rank**: `FactValuation` 최근 ~1260거래일 PER/PBR/EV_EBITDA 로드. 현재값 백분위 = `(현재 이하 관측수) / 전체 * 100`
2. **섹터 대비**: 동일 sector_id 전 종목 PER/PBR 중앙값 대비 premium/discount %
3. **DCF implied growth**: `current_price / FCF_per_share` 기반 역산. 할인율 10% 가정, 현 주가 정당화에 필요한 연간 FCF 성장률
4. **PEG**: `PER / EPS_growth_rate`. EPS 성장률 = 최근 TTM vs 전년 TTM
5. **FCF yield**: `FCF_TTM / market_cap * 100%`
6. **출력**: valuation_grade (Cheap/Fair/Rich/Extreme) + 상세

### Layer 3: 멀티 타임프레임 기술적

1. **추세 정렬**: 일봉(`calculate_indicators()` 재사용) + 주봉(5일 resample) + 월봉(21일 resample). close vs SMA20 vs SMA50 정배열/역배열/혼합
2. **52주 위치**: `(close - 52w_low) / (52w_high - 52w_low) * 100%`
3. **MA crossover 임박**: `abs(SMA50 - SMA200) / SMA200 * 100`. 2% 이내 = 임박
4. **RSI/MACD divergence**: 최근 20일 윈도우에서 가격 신고가 vs RSI/MACD 신고가 불일치 감지
5. **상대강도**: `calculate_rs_ranks()` 재사용. S&P 500 대비 RS percentile
6. **S/R 레벨**: `find_support_resistance()` 직접 호출. 가장 가까운 S/R과의 거리%
7. **ATR regime**: 14일 ATR / close * 100 = 변동성%. 20일 SMA 대비 High/Normal/Low 분류
8. **출력**: technical_grade (Bullish/Neutral/Bearish) + 상세

### Layer 4: 수급/포지셔닝

1. **내부자 90일**: DB에서 최근 90일 내부자거래. 순매수/매도 금액·건수. C-suite 가중치 2x
2. **13F 기관**: 최신 기관 보유 변화. 순증/순감 합계
3. **공매도**: `short_ratio`, `short_pct_of_float` 추이. days_to_cover 평가
4. **애널리스트 컨센서스**: 매수비율 = `(strong_buy + buy) / total`. 목표가 upside/downside
5. **옵션 PCR/IV**: Phase 1에서는 "데이터 미수집" 표시. Phase 2 이후 yfinance options chain 검토
6. **출력**: flow_grade (Accumulation/Neutral/Distribution) + 상세

### Layer 5: 내러티브 + 촉매

1. **뉴스 감성 추이**: `FactNews` + `BridgeNewsStock` 최근 90일. 30d/60d/90d 윈도우별 sentiment_score 평균. 추이 방향 (개선/악화/안정)
2. **임박 촉매**: `collect_earnings_calendar()`, `get_next_fomc_date()` 재사용. yfinance calendar로 추가 이벤트
3. **리스크 이벤트**: 최근 7일 부정 뉴스 급증 감지 (부정 키워드 빈도)
4. **경영진 변화**: 뉴스 제목에서 "CEO/CFO/resign/appoint" 키워드 감지 (간이)
5. **출력**: narrative_grade (Positive/Neutral/Negative) + 상세

### Layer 6: 거시 민감도

1. **베타 회귀**: 최근 252거래일 일간 수익률. 종목 vs VIX/10Y/Dollar/Oil 변화율. `scipy.stats.linregress` 또는 `numpy.polyfit`
2. **섹터 모멘텀 위치**: `calculate_sector_momentum()` 재사용. 해당 섹터의 전체 순위
3. **페어 상대 성과**: Step 4 결과에서 종목 vs 페어 평균 성과 비교
4. **레짐별 과거 행동**: `detect_regime()` 현재 레짐 판단. 과거 각 레짐 기간 중 종목 평균 수익률/변동성 역산
5. **출력**: macro_grade (Favorable/Neutral/Headwind) + 상세

---

## 7. AI 프롬프트 설계 초안

### 7.1 공통 데이터 블록 (모든 에이전트에 주입)

```xml
<stock_context>
종목: {ticker} ({name})
섹터: {sector} | 시가총액: {market_cap_formatted}
현재가: ${current_price} | 일간: {daily_change}%

<!-- 보유 종목만 -->
<holding_context>
보유 수량: {shares}주 | 평단가: ${avg_cost}
보유 수익률: {pnl_pct}% (${pnl_amount})
보유 기간: {holding_days}일
포지션 가치: ${position_value}
</holding_context>

<layer1_fundamental> {Layer 1 구조화 텍스트} </layer1_fundamental>
<layer2_valuation>   {Layer 2 구조화 텍스트} </layer2_valuation>
<layer3_technical>   {Layer 3 구조화 텍스트} </layer3_technical>
<layer4_flow>        {Layer 4 구조화 텍스트} </layer4_flow>
<layer5_narrative>   {Layer 5 구조화 텍스트} </layer5_narrative>
<layer6_macro>       {Layer 6 구조화 텍스트} </layer6_macro>

<pair_comparison> {페어 비교 요약} </pair_comparison>
</stock_context>
```

### 7.2 Bull Agent — system prompt

```
너는 30년 경력 성장주 롱온리 포트폴리오 매니저다.
이 종목을 보유하거나 추가 매수할 이유를 찾아라.

분석 지침:
- 6개 레이어 데이터를 모두 활용, 매수 관점에 유리한 근거 집중
- 밸류에이션이 비싸도 성장 스토리로 정당화 가능한지 평가
- 기술적 약세 = "중장기 매수 기회"로 해석 가능한지
- 내부자 매도는 세금/다각화 목적 가능성 고려
- 보유 종목은 평단가 대비 수익률, 보유기간 맥락 반영

출력 (JSON):
{"action":"ADD"|"HOLD", "conviction":1-10,
 "bull_case":["근거1","근거2","근거3"],
 "scenarios":{"1M":{"base":{...},"bull":{...},"bear":{...}}, "3M":{...}, "6M":{...}},
 "catalysts":["촉매1"], "key_risks_acknowledged":["인정 리스크1"]}
```

### 7.3 Bear Agent — system prompt

```
너는 30년 경력 숏셀러 겸 리스크 매니저다.
이 종목의 하방 리스크와 매도/축소 이유를 찾아라.

분석 지침:
- 리스크/약점 집중: 성장 둔화, 마진 압박, 경쟁 심화
- 밸류에이션 과열은 절대적+상대적 수치 모두 제시
- 기술적 약세 → 하방 시나리오 구체화
- 매크로 역풍 정량화
- 보유 종목: 큰 수익 = 이익실현 적기, 손실 = 추가 하락 리스크

출력 (JSON):
{"action":"TRIM"|"EXIT"|"HOLD", "conviction":1-10,
 "bear_case":["리스크1","리스크2","리스크3"],
 "scenarios":{...}, "stop_loss_level":price,
 "key_strengths_acknowledged":["인정 강점1"]}
```

### 7.4 Synthesizer Agent — system prompt

```
너는 30년 경력 수석 CIO다. Bull/Bear 양측 토론을 종합하여 최종 판단을 내려라.

판단 기준:
1. 논거 구체성 + 데이터 근거
2. 논리적 일관성
3. 현재 시장 환경(레짐) 정합성
4. 리스크/보상 비대칭성

보유자 관점 (보유 종목만):
- HOLD = 현 포지션 유지 + 모니터링
- ADD = 추가 매수 (확신 높을 때만)
- TRIM = 일부 매도 비중 축소
- EXIT = 전량 매도 (확신 높을 때만)
- +30% 이상 수익 → 일부 이익실현 검토
- -15% 이상 손실 → 손절 검토

출력 (JSON):
{"action_grade":"HOLD"|"ADD"|"TRIM"|"EXIT",
 "conviction":1-10, "uncertainty":"low"|"medium"|"high",
 "reasoning":"200자 이내 종합 판단",
 "scenarios":{"1M":{...},"3M":{...},"6M":{...}},
 "what_missing":"반대 의견 강조",
 "key_levels":{"support":price,"resistance":price,"stop_loss":price},
 "next_review_trigger":"재검토 트리거 조건"}
```

### 7.5 보유정보 주입 방식

`dim_watchlist_holdings`에 데이터가 있으면:
1. `pnl_pct = (current_price - avg_cost) / avg_cost * 100`
2. `pnl_amount = (current_price - avg_cost) * shares`
3. `holding_days = (target_date - opened_at).days`
4. `position_value = current_price * shares`
5. → `<holding_context>` 블록 삽입 + Synthesizer 보유자 관점 활성화

보유정보 없으면 `<holding_context>` 및 보유자 관점 섹션 모두 생략.

---

## 8. Claude CLI Opus + Thinking 호출 방법

### 확인 완료 사항 (claude --help 검증)

| 항목 | CLI 플래그 | 상태 |
|------|-----------|------|
| **모델 지정** | `--model opus` 또는 `--model claude-opus-4-6` | **확인됨** |
| **시스템 프롬프트** | `--system-prompt <prompt>` | **확인됨** |
| **비대화형 출력** | `-p` / `--print` | 기존 사용 중 |
| **JSON 출력** | `--output-format json` | 가용 (파싱 안정성 향상) |
| **예산 제한** | `--max-budget-usd <amount>` | 종목당 비용 제어 가능 |

### Extended Thinking

CLI에 별도 `--thinking` 플래그는 없음. Opus 모델은 복잡한 분석 시 자연스럽게 extended thinking을 사용. 프롬프트에 "단계적으로 깊이 사고한 뒤 결론을 내려라"를 추가하여 유도.

### 구현 함수

```python
def run_deepdive_cli(
    prompt: str,
    system_prompt: str | None = None,
    timeout: int = 600,
    model: str = "opus",
) -> str | None:
    """Deep dive 전용 Claude CLI 호출. Opus + thinking."""
    claude_path = shutil.which("claude")
    if not claude_path:
        return None

    cmd = [claude_path, "-p", "--model", model]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]

    env = os.environ.copy()
    node_path = shutil.which("node")
    if node_path:
        env["PATH"] = str(Path(node_path).parent) + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        cmd, input=prompt,
        capture_output=True, text=True, timeout=timeout,
        env=env, encoding="utf-8", errors="replace",
    )
    return result.stdout.strip() if result.returncode == 0 else None
```

### CLI 기반 토론 흐름

```python
def run_deepdive_debate(stock_data, holding_context, timeout=600):
    # R1: Bull + Bear 독립 (순차 — CLI 병렬 불가)
    bull_r1 = run_deepdive_cli(stock_data, system_prompt=BULL_PERSONA, timeout=timeout)
    bear_r1 = run_deepdive_cli(stock_data, system_prompt=BEAR_PERSONA, timeout=timeout)

    # R2: 교차반박
    bull_r2 = run_deepdive_cli(
        f"Bear의 분석:\n{bear_r1}\n\n위 분석에 반박하라:\n{stock_data}",
        system_prompt=BULL_PERSONA, timeout=timeout)
    bear_r2 = run_deepdive_cli(
        f"Bull의 분석:\n{bull_r1}\n\n위 분석에 반박하라:\n{stock_data}",
        system_prompt=BEAR_PERSONA, timeout=timeout)

    # R3: Synthesizer 종합 판정
    synth = run_deepdive_cli(
        f"Bull:\n{bull_r2}\n\nBear:\n{bear_r2}\n\n종합 판정:\n{stock_data}",
        system_prompt=SYNTH_PERSONA, timeout=timeout)

    return parse_debate_result(bull_r1, bear_r1, bull_r2, bear_r2, synth)
```

**핵심**: `--system-prompt`으로 persona 분리 → SDK의 system message와 동일 효과. 기존 debate.py와 달리 Tool Use 없이 텍스트 응답 파싱.

---

## 9. 웹 라우트 + 템플릿 + 차트 구성

### 9.1 라우트 등록 (`src/web/routes/personal.py`)

```python
router = APIRouter(tags=["personal"])

@router.get("/personal")              # 카드 그리드
@router.get("/personal/{ticker}")      # 종목 상세
@router.get("/personal/{ticker}/history")  # 히스토리
@router.get("/personal/forecasts")     # 예측 정확도
```

`src/web/app.py`에 `app.include_router(personal_router)` 추가.

### 9.2 `/personal` — 카드 그리드

**쿼리**: dim_watchlist(active) JOIN dim_watchlist_holdings JOIN fact_deepdive_reports(최신) JOIN fact_daily_prices(최신) JOIN fact_deepdive_changes(최신)

**카드 구성**:
- 티커 + 종목명
- 현재가 + 일간 수익률 (녹/적 색상)
- 액션 배지: HOLD(회색), ADD(녹), TRIM(주황), EXIT(적)
- Conviction 바 (10점 만점)
- 변경 배지 (changes 존재 시 빨간 뱃지)
- 보유 종목: 평단가 대비 손익 (% + 금액)
- 면책 문구: "투자 참고용이며 투자 권유가 아닙니다"

### 9.3 `/personal/{ticker}` — 상세 페이지

**탭 구조**:
1. **요약** — action, conviction, uncertainty, AI 종합, what_missing
2. **Layer 1~6** — 아코디언 형태 각 레이어 상세
3. **시나리오** — 1M/3M/6M 가격 범위 차트
4. **페어 비교** — 성과·밸류에이션 비교 테이블
5. **촉매 일정** — 이벤트 타임라인
6. **토론 기록** — Bull/Bear 논거 (Phase 2)

**차트**:
- 가격 + SMA50/200 + S/R 레벨 (기존 `lineChartOption` 활용)
- 시나리오 가격 범위 (신규 `scenarioRangeChart`)
- 6축 레이더 (신규 `layerRadarChart`)
- 밸류에이션 5년 히스토그램
- 뉴스 감성 추이 (기존 line chart)

### 9.4 `/personal/{ticker}/history`

- 액션 등급 변경 타임라인 (신규 `actionTimelineChart`)
- Conviction 추이 라인 차트
- 시나리오 예측 vs 실제 가격 비교
- 일별 변경사항 로그

### 9.5 `/personal/forecasts`

- 종목별 정확도 테이블: hit_range 비율, 방향 정확도
- 기간별 필터 (1M/3M/6M)
- 시나리오별 hit rate (Base/Bull/Bear)

### 9.6 `charts.js` 추가 함수

| 함수 | 용도 |
|------|------|
| `scenarioRangeChart(id, data)` | 1M/3M/6M 가격 범위 수평 바 차트 |
| `layerRadarChart(id, scores)` | 6축 레이더 (Fund/Val/Tech/Flow/Narr/Macro) |
| `actionTimelineChart(id, history)` | 날짜별 HOLD/ADD/TRIM/EXIT 타임라인 |

모두 기존 `initChart()` + ECharts 옵션 패턴 준수. `colorWithAlpha()`, `fmtNum` 등 기존 유틸 재사용. 다크모드 동기화 자동 (`reinitAllCharts` 호환).

---

## 10. 단계별 구현 순서

### Phase 1 (MVP) — 예상 2~3주

**목표**: 워치리스트 CRUD + 파이프라인 뼈대 + 3개 레이어 + 단순 AI + 카드 페이지

| 순서 | 작업 | 파일 |
|------|------|------|
| 1 | DB 7개 테이블 ORM 정의 | `src/db/models.py` |
| 2 | WatchlistRepository + DeepDiveRepository | `src/db/repository.py` |
| 3 | 워치리스트 매니저 (CRUD + 비S&P500 자동등록) | `src/deepdive/watchlist_manager.py` |
| 4 | CLI `watchlist` 그룹 (add/remove/list/set-holding) | `src/main.py` |
| 5 | 시드 스크립트 (12종목) | `scripts/seed_watchlist.py` |
| 6 | 파이프라인 뼈대 (Step 1/2/7/8) | `src/deepdive_pipeline.py` |
| 7 | Layer 1 (Fundamental) | `src/deepdive/layers.py` |
| 8 | Layer 3 (Technical) | `src/deepdive/layers.py` |
| 9 | Layer 4 (Flow) | `src/deepdive/layers.py` |
| 10 | Step 3 연결 (3개 레이어) | `src/deepdive_pipeline.py` |
| 11 | 단순 AI (debate 없이 단일 CLI 호출) | `src/deepdive/ai_prompts.py`, `ai_debate_cli.py` |
| 12 | Step 5 (단순 모드) | `src/deepdive_pipeline.py` |
| 13 | `/personal` 카드 페이지 | `src/web/routes/personal.py`, `personal.html` |
| 14 | CLI `deepdive run` 명령 | `src/main.py` |
| 15 | 테스트 (워치리스트, 레이어, 파이프라인) | `tests/test_deepdive_*.py` |
| 16 | `send_deepdive_summary()` 알림 | `src/alerts/notifier.py` |

### Phase 2 — 예상 2주

**목표**: 나머지 3 레이어 + 토론 + 시나리오 + 상세 페이지

| 순서 | 작업 |
|------|------|
| 1 | Layer 2 (Valuation) — 5년 히스토리, DCF, PEG |
| 2 | Layer 5 (Narrative) — 뉴스 감성 추이, 촉매 |
| 3 | Layer 6 (Macro) — 베타 회귀, 레짐별 행동 |
| 4 | Step 3 확장 → 6개 레이어 전체 |
| 5 | CLI 3라운드 토론 구현 |
| 6 | 시나리오 예측 파싱 (1M/3M/6M × Base/Bull/Bear) |
| 7 | fact_deepdive_forecasts 저장 |
| 8 | `/personal/{ticker}` 상세 페이지 (6탭) |
| 9 | charts.js: `scenarioRangeChart`, `layerRadarChart` |
| 10 | 테스트 확장 |

### Phase 3 — 예상 1.5주

**목표**: 페어 분석 + diff 감지 + 히스토리 + 정확도

| 순서 | 작업 |
|------|------|
| 1 | 페어 자동 선정 (코사인 유사도) |
| 2 | Step 4 (pair_analysis) 구현 |
| 3 | Diff 감지 로직 |
| 4 | Step 6 (diff_detection) 구현 |
| 5 | 변경 배지 → 카드 그리드 표시 |
| 6 | `/personal/{ticker}/history` 히스토리 페이지 |
| 7 | `/personal/forecasts` 정확도 리더보드 |
| 8 | 예측 actual_price 업데이트 로직 |
| 9 | cron 스크립트 (`scripts/run_deepdive.sh`) |
| 10 | `actionTimelineChart` 추가 |
| 11 | 테스트 완성 |

---

## 11. 테스트 전략과 주요 테스트 케이스

### 테스트 패턴

- **엔진**: in-memory SQLite (기존 `conftest.py` fixture 재사용)
- **외부 API**: mock (yfinance, Claude CLI subprocess)
- **CLI**: `subprocess.run` mock으로 Claude 응답 시뮬레이션
- **웹**: FastAPI TestClient

### 주요 테스트 케이스 (~36개)

#### 워치리스트 (`test_deepdive_watchlist.py`)
1. `test_add_watchlist_item` — ticker 추가, 중복 방지 (UNIQUE)
2. `test_remove_watchlist_item` — active=False soft delete
3. `test_set_holding` — 보유정보 UPSERT
4. `test_seed_watchlist` — 12종목 시드, 재실행 안전
5. `test_auto_register_non_sp500` — SMR 등 dim_stocks 자동 등록
6. `test_list_active_only` — active=True만 필터

#### 레이어 (`test_deepdive_layers.py`)
7. `test_layer1_f_score` — F-Score 계산 정확성
8. `test_layer1_margin_trend` — 4분기 마진 트렌드
9. `test_layer2_percentile` — 5년 PER 백분위
10. `test_layer2_dcf_implied_growth` — DCF 역산 성장률
11. `test_layer3_trend_alignment` — 일/주/월 추세 정렬
12. `test_layer3_divergence` — RSI 다이버전스 감지
13. `test_layer4_insider_signal` — 내부자 순매수/매도
14. `test_layer5_sentiment_trend` — 30/60/90일 감성 추이
15. `test_layer6_beta_regression` — VIX 베타 회귀 계수

#### AI (`test_deepdive_ai.py`)
16. `test_cli_call_with_model_flag` — `--model opus` 포함 확인
17. `test_cli_system_prompt` — `--system-prompt` 포함 확인
18. `test_parse_action_grade` — JSON action_grade 파싱
19. `test_holding_context_injection` — 보유 종목 holding_context 포함
20. `test_no_holding_context` — 비보유 종목 holding_context 미포함
21. `test_debate_3_rounds` — R1→R2→R3 순차 mock
22. `test_cli_timeout` — 타임아웃 → None 반환
23. `test_malformed_json_fallback` — 비정형 응답 시 regex fallback

#### 파이프라인 (`test_deepdive_pipeline.py`)
24. `test_checkpointing` — 완료 step 스킵
25. `test_force_mode` — force=True 재실행
26. `test_single_ticker` — 특정 종목만 실행
27. `test_graceful_shutdown` — SIGINT 중단
28. `test_resilient_per_ticker` — 개별 실패 격리
29. `test_never_overwrite_reports` — 매일 새 row INSERT

#### Diff (`test_deepdive_diff.py`)
30. `test_first_run_no_previous` — 첫 실행 시 "신규 분석"
31. `test_action_change_detected` — HOLD→ADD 감지
32. `test_conviction_change` — 2+ 변화 감지
33. `test_probability_shift` — 10%p+ 시나리오 확률 변화

#### 웹 (`test_deepdive_web.py`)
34. `test_personal_page_200` — `/personal` 정상 렌더링
35. `test_personal_detail_200` — `/personal/AAPL` 정상
36. `test_empty_watchlist` — 빈 워치리스트 안내 메시지

---

## 12. 미해결 질문과 리스크

### 미해결 질문

| # | 질문 | 영향 | 대응 |
|---|------|------|------|
| 1 | **옵션 데이터 (PCR/IV)**: yfinance options chain 비용과 속도 | Layer 4 완성도 | Phase 1에서 제외, "데이터 미수집" 표시. Phase 2 이후 검토 |
| 2 | **비S&P500 데이터 완전성**: SMR 등의 enhanced data(기관/애널리스트)가 부실할 수 있음 | Layer 4 정확도 | 데이터 부재 시 해당 항목 "N/A" 표시, 가용 데이터만으로 분석 |
| 3 | **페어 갱신 위치**: weekly_pipeline에 step 추가 vs deepdive_pipeline 내 7일 체크 | 아키텍처 | **제안**: deepdive_pipeline Step 4에서 7일 경과 체크 방식 (독립성 유지) |
| 4 | **5년 밸류에이션 히스토리**: 신규 등록 종목은 DB에 히스토리 부족 | Layer 2 정확도 | 가용 기간으로 계산, 1년 미만이면 "데이터 부족" 표시 |

### 리스크

| # | 리스크 | 심각도 | 경감 방안 |
|---|--------|--------|----------|
| 1 | **AI 비용**: Opus 12종목 × 3라운드 = 36회/일 → $10-30/일 | 중 | Phase 1: debate 없이 12회로 절반. `--max-budget-usd` 활용 |
| 2 | **실행 시간**: Opus 응답 30-120초 × 36 = 최대 60분+ | 저 | spec에 "속도 중요하지 않음" 명시. cron 07:00에 충분한 여유 |
| 3 | **CLI 안정성**: 네트워크 불안정 시 CLI 실패 | 중 | 종목별 try/except 격리 (기존 resilient 패턴). 실패 종목 로깅 후 스킵 |
| 4 | **JSON 파싱 실패**: AI가 유효하지 않은 JSON 반환 | 중 | `_try_parse_json()` + regex fallback + `--output-format json` 활용 |
| 5 | **SQLite 동시성**: daily(06:30) + deepdive(07:00) DB 동시 접근 | 저 | WAL 모드 + busy_timeout=5000 이미 설정. 30분 간격으로 대부분 비겹침 |
| 6 | **dim_stocks 자동등록**: yfinance `.info` 구조 종목마다 다름 | 중 | 방어 코드: `.get()` + 기본값 fallback. 필수 필드(ticker, name)만 요구 |

---

## Verification Plan

1. **DB**: `investmate db status`로 7개 신규 테이블 생성 확인
2. **CLI**: `investmate watchlist add NVDA` → `investmate watchlist list`로 CRUD 검증
3. **시드**: `python scripts/seed_watchlist.py` → 12종목 등록 확인
4. **파이프라인**: `investmate deepdive run --ticker AAPL --force`로 단일 종목 실행
5. **웹**: `investmate web` → `http://localhost:8000/personal` 접속하여 카드 렌더링 확인
6. **테스트**: `pytest tests/test_deepdive_*.py -v --cov=src/deepdive`로 커버리지 80%+ 확인
