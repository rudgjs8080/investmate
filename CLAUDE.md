# CLAUDE.md — investmate

> 상세 명세(파일 트리, DB 컬럼 상세, 테스트 목록 등)는 [CLAUDE_FULL.md](CLAUDE_FULL.md) 참조.

---

## 프로젝트 개요

S&P 500 전 종목(~500개)을 매일 자동 스캔하여 매수 적합 종목을 선별하는 **퀀트 파이프라인**.
`investmate run` 한 명령으로 수집→분석→스크리닝→AI 분석→리포트→알림까지 순차 실행된다.

**핵심 원칙:**

- cron 배치 — 일일(화~토 06:30 KST) + 주간(일 09:00 KST), AWS EC2 t2.micro
- 데이터를 매일 SQLite에 축적, 장기 추적
- AI: Anthropic SDK Tool Use + 4단계 fallback, **멀티 에이전트 토론** (Bull/Bear/Synthesizer)
- **멀티 호라이즌 피드백** (5d/10d/20d/60d) + 시간 감쇠 → 점진적 패널티
- **적응형 스코어링**: 피드백 상관관계 기반 가중치 자동 최적화
- **포트폴리오 레벨 AI**: 섹터/상관/집중 리스크를 AI 프롬프트에 주입
- **반사실 분석**: AI 오버라이드의 대안 시뮬레이션 → 자기학습
- 시장 체제 감지 (Bull/Bear/Range/Crisis) + 레짐 전환 감지
- ML (LightGBM 28피처, 네이티브 .txt 포맷 + AUC 품질 게이트) + 드리프트 감지 + 자동 재학습
- 포지션 사이징 (ERC/Vol Target/Half-Kelly) + 비선형 시그모이드 틸트
- 팩터 투자 (Value/Momentum/Quality/LowVol/Size) — legacy/factor/blend/adaptive 4모드
- T+1 실행가 성과 추적, 거래비용 차감 (왕복 20bps)

---

## 기술 스택

| 항목 | 선택 | 비고 |
|------|------|------|
| 언어 | Python 3.11+ | |
| DB | SQLite (WAL) | SQLAlchemy 2.0+ ORM, 경량 migrate.py |
| CLI | Click + Rich | 한국어 UI |
| 웹 | FastAPI + Jinja2 + Tailwind | ECharts 5.x, 16개 라우트, 차트 auto-scale/crosshair/다크모드 동기화 |
| 데이터 | yfinance, pandas, ta | 배치 다운로드, 프로바이더 추상화, 서킷브레이커, 데이터 검증, CNN F&G |
| AI | Anthropic SDK + Claude CLI | Tool Use, 멀티 에이전트 토론, 4단계 fallback |
| ML | LightGBM | 28피처, 네이티브 .txt 포맷, AUC 임계값(0.55), 70/30 블렌딩, 드리프트 감지 |
| 설정 | pydantic-settings | 환경변수 > .env > config.json > 기본값 |
| 테스트 | pytest, pytest-cov | 1169개 테스트, 100개 파일 |
| 배포 | AWS EC2 t2.micro | Nginx, systemd, cron, S3 백업, Telegram 모니터링 |

---

## 파이프라인 상세

```python
class DailyPipeline:
    def run(self):
        self.step0_performance()        # 과거 추천 성과 업데이트 (T+1 실행가 + 수익률)
        self.step1_collect()            # S&P 500 전 종목 데이터 수집 + 강화 + 뉴스
        self.step2_analyze()            # 기술적 지표 + 시그널 판단
        self.step3_external()           # 외부 요인 + 섹터 모멘텀
        self.step4_screen()             # 스크리닝 + 랭킹 (적응형 가중치)
        self.step4_5_ai_analysis()      # 멀티 에이전트 토론 → 캘리브레이션 → 검증
        self.step4_6_position_sizing()  # 포지션 사이징 + 리스크 제약 + 턴오버
        self.step4_7_factor_returns()   # 팩터 롱숏 수익률 계산
        self.step5_report()             # 멀티 호라이즌 피드백 + 두괄식 리포트
        self.step6_notify()             # Telegram / Slack / Email
```

- **STEP 0**: `fill_execution_prices()` T+1 시가 기록, `update_recommendation_returns()` 1d/5d/10d/20d/60d 수익률
- **STEP 0.5**: AI 회고 — 20일 전 추천 복기 → 교훈 추출 → `fact_ai_lessons`
- **STEP 1**: yfinance 배치 수집 (증분, 실패 티커 추적, 서킷브레이커 보호), 재무(ThreadPool max_workers=4), 매크로(VIX/금리/달러/금/유가 배치 1회), **Fear & Greed Index** (CNN API), 강화(내부자/기관/애널리스트/실적/공매도), 뉴스(v2 API)
- **STEP 2**: ta 라이브러리 17+ 지표 (SMA/EMA/RSI/MACD/BB/Stoch), 10종 시그널 (golden/death cross, RSI, MACD, BB, Stoch)
- **STEP 3**: 매크로 점수(1-10, F&G Extreme Fear/Greed 보정 포함), LLM 뉴스 감성(Haiku + 키워드 fallback), 섹터 모멘텀(20일)
- **STEP 4**: 필터링(500→30~50) → 5차원 스코어링(체제 적응형) → TOP N 랭킹. 품질필터(F-Score, Z-Score), 섹터상한(40%), 상관필터(0.75)
- **STEP 4.5**: 토론 R1(Bull/Bear 독립분석 병렬) → R2(교차반박 병렬) → R3(Synthesizer Tool Use 판정) → 캘리브레이션 → 검증 → 제약 적용
- **STEP 4.6**: Vol Target/ERC/Half-Kelly + 비선형 시그모이드 틸트 + 리스크 제약 + 드로다운 관리 + 실행비용
- **STEP 5**: 멀티 호라이즌 피드백 수집 + 교훈 만료/효과성 갱신 + 캘리브레이션 셀 갱신 + 두괄식 리포트 생성

**파이프라인 특성:** Resilient (개별 종목 실패 격리), Step Checkpointing (`--force`로 재실행), Graceful Shutdown (SIGTERM/SIGINT)

### 주간 파이프라인 (`src/weekly_pipeline.py`)

매주 일요일 09:00 KST cron 자동 실행 (`scripts/run_weekly.sh`).

```
STEP 1: 주간 리포트 생성 (데이터 어셈블)
STEP 2: AI 주간 코멘터리
STEP 3: PDF 생성
STEP 4: 이메일 발송 (PDF 첨부)
STEP 5: 알림 발송 (Telegram/Slack)
```

- CLI: `investmate report weekly [--year] [--week] [--skip-notify] [--skip-email] [--force]`
- 출력: `reports/weekly/{year}-W{week}.{json,md,pdf}`

---

## AI 시스템 아키텍처

### 분석 파이프라인 (`src/ai/`)

| 모듈 | 역할 |
|------|------|
| `constants.py` | AI 상수 중앙화: 모델명(`get_analysis_model()`/`get_chat_model()`), VIX 임계값, 레짐별 추천수 상한, NON_TICKERS 필터 |
| `regime.py` | 시장 체제 분류 통합 모듈: VIX+S&P 기반 crisis/bear/bull/range 판정 (DB/매크로/레코드 3가지 인터페이스) |
| `cost_tracker.py` | AI API 비용 추적: 모델별 단가, 일일 예산($5), 용도별 breakdown, 일간 요약 |
| `claude_analyzer.py` | Tool Use + 4단계 fallback (Tool Use→Streaming→SDK→CLI) |
| `debate.py` + `agents.py` | 3라운드 멀티 에이전트 토론 (R1 독립→R2 교차반박→R3 합성), 컨센서스 측정(high≥70%/medium≥40%/low<40%) + 패널티 |
| `feedback.py` | 멀티 호라이즌(5d/10d/20d/60d) 피드백, 시간 감쇠(반감기 30일), 점진적 패널티(0-4점), ConstraintRules 자동 생성 |
| `calibrator.py` | 목표가/손절가 캘리브레이션 (regime×sector×confidence×horizon), look-ahead 35일 보호, MIN_CELL_SAMPLES=10 |
| `validator.py` | AI 결과 검증 + enforce_constraints (신뢰도 상한/섹터 차단/추천 수 제한) |
| `scoring_advisor.py` | 피드백 상관관계 기반 적응형 스코어링 가중치 (min 30 samples) |
| `rebalance_trigger.py` | 손절/레짐 변경/상관 드리프트 기반 리밸런싱 트리거 |
| `counterfactual.py` | AI 오버라이드 반사실 시뮬레이션 (고득점 거부/저득점 승인 사후 검증) |
| `lesson_store.py` | 자기학습 교훈 축적 (90일 만료, 효과성 추적, 카테고리: sector/regime/timing/valuation/general) |
| `retrospective.py` | 20일 후 추천 복기 (가격 경로, max gain/loss) |

### 프롬프트 시스템 (`src/reports/prompt_builder.py`)

- **페르소나**: 리스크 매니저 겸 퀀트 애널리스트 (손실 회피 우선)
- **`<hard_rules>` 6개**: 신뢰도 상한(VIX), 추천 수 제한, 섹터 차단, 캘리브레이션, 목표가 보수적, 피드백 강제
- **`<reasoning_process>` 9단계**: 체제 판단→스타일→차단 확인→분석→신뢰도→캘리브레이션→상한→추천수→목표가
- **`<portfolio_context>`**: 섹터 분포, 상관계수, 집중 리스크, 전일 오버랩
- **`<counterfactual_lessons>`**: 반사실 분석 톱 3 인사이트
- **자동 스타일**: VIX/승률 기반 defensive/balanced/aggressive 전환

### 모델 라우팅

| 용도 | 모델 |
|------|------|
| 분석/코멘터리 | `claude-sonnet-4-20250514` |
| 채팅/감성 | `claude-haiku-4-5-20251001` |

---

## 데이터 수집 아키텍처 (`src/data/`)

### 프로바이더 추상화 (`src/data/providers/`)

Protocol 기반 데이터 소스 추상화 — yfinance 외 다른 소스(Polygon, EODHD) 교체 가능.

| 프로바이더 | 역할 |
|-----------|------|
| `base.py` | Protocol 정의: `PriceProvider`, `FinancialProvider`, `MacroProvider` |
| `yfinance_provider.py` | yfinance 구현: 배치 다운로드 + 지수 백오프 재시도(3회) + 60초 타임아웃 + 서킷브레이커 |

### Fear & Greed Index (`src/data/fear_greed.py`)

CNN Fear & Greed API (`production.dataviz.cnn.io`) 기반 투자자 심리 지표 수집.

| 함수 | 역할 |
|------|------|
| `fetch_fear_greed()` | 최신 F&G 값 조회 (0-100, 등급 분류) — 일일 파이프라인용 |
| `fetch_fear_greed_history()` | ~1년치 히스토리 조회 — 초기 백필용 |
| `backfill_fear_greed_to_db()` | DB에 히스토리 일괄 UPSERT (`investmate db backfill-fg`) |

- 등급: Extreme Fear(0-25) / Fear(25-45) / Neutral(45-55) / Greed(55-75) / Extreme Greed(75-100)
- `Referer` 헤더 필수 (CNN 봇 차단 우회)

### 데이터 품질 (`src/data/`)

| 모듈 | 역할 |
|------|------|
| `circuit_breaker.py` | 서킷브레이커 패턴: 연속 N회 실패 시 차단 (fail_threshold=5, reset=60초) |
| `validation.py` | 가격/매크로 데이터 검증: 범위 체크, 50% 급변 감지, 100x 거래량 스파이크, VIX 0-100 |
| `utils.py` | 공용 유틸: `safe_float()`, `flatten_multiindex()`, `extract_ticker_data()` |

### ML 시스템 (`src/ml/`)

| 모듈 | 역할 |
|------|------|
| `trainer.py` | LightGBM 네이티브 `.txt` 포맷 + JSON 메타데이터, AUC 임계값(0.55) 품질 게이트, 시간순 train/test 분할, early stopping(20) |
| `scorer.py` | `.txt` 우선 로드 + `.pkl` 폴백, 모델 캐싱, rule 70% / ML 30% 블렌딩, 60일+ 데이터 준비도 체크 |
| `registry.py` | 모델 관리: 네이티브 `.txt` 우선, JSON 사이드카 메타데이터 |

---

## 스코어링 + 리스크 관리

### 5차원 스코어링 (시장 체제 적응형)

| 차원 | Range(기본) | Bull | Bear | Crisis |
|------|-------------|------|------|--------|
| 기술적 | 30% | 20% | 15% | 10% |
| 기본적 | 25% | 20% | 35% | 30% |
| 수급 | 15% | 15% | 20% | 25% |
| 외부 | 15% | 15% | 20% | 25% |
| 모멘텀 | 15% | 30% | 10% | 10% |

**모드:** `legacy` / `factor` (5개 학술 팩터) / `blend` (ratio 혼합) / `adaptive` (AI 피드백 기반 가중치)

### 리스크 제약

| 제약 | 종류 | 기본값 |
|------|------|--------|
| 단일 종목 상한 | 하드 | 10% |
| 단일 섹터 상한 | 하드 | 30% |
| 일일 VaR 한도 | 하드 | 2% (95% CI) |
| 레버리지 | 하드 | 1.0x |
| 포트폴리오 트레일링 스톱 | 하드 | 고점 대비 -10% → 50% 축소 |
| 개별 종목 손절 | AI > ATR×2 | AI stop_loss 우선 |
| 연환산 턴오버 | 소프트 | 1200% 초과 시 경고 |

---

## DB 설계

Star Schema (SQLAlchemy ORM). `src/db/models.py` 참조. 마이그레이션: `src/db/migrate.py` (ALTER TABLE ADD COLUMN 자동).

- **Dimension (6):** dim_stocks, dim_markets, dim_sectors, dim_date, dim_indicator_types, dim_signal_types
- **Fact (17):** daily_prices, indicator_values, financials, valuations, signals, macro_indicators, daily_recommendations, ai_feedback, ai_lessons, ai_debate, calibration_cells, factor_returns, agent_accuracy, counterfactuals, ml_model_log, ml_drift_check, news
- **Bridge (1):** bridge_news_stock
- **핵심 테이블:** `fact_daily_recommendations` (5차원 점수 + AI 결과 + 포지션 사이징 + 사후 수익률), `fact_ai_feedback` (멀티 호라이즌 5d/10d/20d/60d + 방향 정확도 + 시간 감쇠 가중치), `fact_macro_indicators` (VIX/금리/달러/금/유가/yield_spread + **fear_greed_index/rating**)

---

## 웹 대시보드

| 경로 | 설명 |
|------|------|
| `/` | 메인 대시보드 (시장 요약 + 오늘의 추천) |
| `/recommendations/{date}` | 추천 상세 (5차원 점수, AI 분석) |
| `/performance` | P&L 추적 (1d/5d/20d 수익률) |
| `/market` | 시장 환경 (F&G 게이지 + 시장점수 히어로, F&G/VIX/S&P 듀얼Y축 차트, 매크로 KPI) |
| `/stock/{ticker}` | 종목 상세 (차트, S/R, 기술/기본) |
| `/ai-accuracy` | AI 정확도 (캘리브레이션 곡선) |
| `/heatmap` | S&P 500 히트맵 (섹터 필터) |
| `/screener` | 인터랙티브 스크리너 (15개 필터) |
| `/portfolio` | 포트폴리오 최적화 (효율적 프론티어) |
| `/factors` | 팩터 대시보드 (누적수익률/IC) |
| `/weekly-reports` | 주간 리포트 목록 + 상세 |
| `/chat` | Claude AI 채팅 (멀티턴, 1시간 캐시) |

### 차트 시스템 (`src/web/static/charts.js`)

| 기능 | 설명 |
|------|------|
| Y축 auto-scale | `min: 'dataMin', max: 'dataMax'` — 데이터 범위에 맞게 자동 조정 |
| `colorWithAlpha()` | hex/rgb/rgba 어떤 포맷이든 안전한 알파값 적용 |
| 숫자 포맷 | `fmtNum`, `fmtPercent`, `fmtPrice`, `fmtCompact`, `fmtDate` 한국어 포맷 |
| 향상된 tooltip | 날짜 포맷 + 색상 dot + 시리즈명 + 포맷값 + 단위(`opts.unit`/`opts.decimals`) |
| crosshair | 라인 차트 기본 활성화, 바 차트 shadow 모드 |
| `buildMarkLine()` | VIX 20/25/30 등 수평 참조선 헬퍼 (`opts.markLines`) |
| multi-series | `[{name, data, color, lineType}]` 배열 시 다중 시리즈 자동 감지 (후방 호환) |
| smart dataZoom | 120포인트 이하면 slider 숨김, inside zoom만 활성화 |
| 자동 스켈레톤 | `initChart()` 시 shimmer 로딩, `setOption()` 시 자동 해제 |
| 다크모드 동기화 | `reinitAllCharts()` — 테마 토글 시 전체 차트 재생성 |
| 차트 내보내기 | `opts.toolbox = true`로 ECharts 내장 PNG 저장 |
| 배치 스파크라인 | `/api/sparklines?tickers=...` — 10+ 개별 요청을 1회 배치로 통합 |
| 반응형 높이 | CSS 클래스 `chart-sm/md/lg/xl` (모바일 180px ~ 데스크탑 520px) |
| 게이지 차트 | `gaugeChartOption()` — 반원형 게이지 (F&G 등), 5구간 색상, 바늘+값+등급 표시 |
| 시리즈 토글 | F&G/VIX/S&P 500 개별 on/off 버튼, `legendToggleSelect` API 연동 |

---

## 설정 전체

**우선순위:** 환경변수 > `.env` > `~/.investmate/config.json` > 기본값

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `INVESTMATE_ENV` | dev | 실행 환경 (dev/test/prod) |
| `INVESTMATE_DB_PATH` | data/investmate.db | DB 파일 경로 |
| `INVESTMATE_TOP_N` | 10 | 추천 종목 수 |
| `INVESTMATE_AI_ENABLED` | true | AI 분석 활성화 |
| `INVESTMATE_AI_TIMEOUT` | 300 | AI 호출 타임아웃 (초) |
| `INVESTMATE_AI_STYLE` | balanced | AI 스타일 |
| `INVESTMATE_AI_BACKEND` | auto | AI 백엔드 (auto/sdk/cli) |
| `INVESTMATE_AI_MODE` | debate | AI 모드 (debate/legacy) |
| `INVESTMATE_AI_MODEL_ANALYSIS` | claude-sonnet-4-20250514 | 분석 모델 |
| `INVESTMATE_AI_MODEL_CHAT` | claude-haiku-4-5-20251001 | 채팅 모델 |
| `INVESTMATE_MAX_SECTOR_PCT` | 0.4 | 섹터 상한 비율 |
| `INVESTMATE_TX_COST_BPS` | 20 | 거래비용 (왕복 bps) |
| `INVESTMATE_RISK_FREE_RATE` | 4.0 | 무위험 수익률 (%) |
| `INVESTMATE_MIN_DATA_DAYS` | 60 | 최소 데이터 일수 |
| `INVESTMATE_MIN_VOLUME` | 100000 | 최소 거래량 |
| `INVESTMATE_SIZING_ENABLED` | true | 포지션 사이징 활성화 |
| `INVESTMATE_SIZING_STRATEGY` | vol_target | 사이징 (erc/vol_target/half_kelly) |
| `INVESTMATE_SIZING_TILT_MODE` | sigmoid | 틸트 (linear/sigmoid/calibrated) |
| `INVESTMATE_TARGET_VOL` | 15.0 | 목표 변동성 (%) |
| `INVESTMATE_MAX_STOCK_PCT` | 0.10 | 종목 최대 비중 |
| `INVESTMATE_MAX_SECTOR_WEIGHT` | 0.30 | 섹터 최대 비중 |
| `INVESTMATE_DAILY_VAR_LIMIT` | 2.0 | 일일 VaR (%) |
| `INVESTMATE_TRAILING_STOP` | 10.0 | 트레일링 스톱 (%) |
| `INVESTMATE_ATR_MULTIPLIER` | 2.0 | ATR 손절 배수 |
| `INVESTMATE_FACTOR_MODE` | legacy | 팩터 모드 (legacy/factor/blend/adaptive) |
| `INVESTMATE_FACTOR_BLEND_RATIO` | 0.5 | 팩터 블렌딩 비율 |
| `INVESTMATE_FEEDBACK_HORIZONS` | 5,10,20,60 | 피드백 호라이즌 (일) |
| `INVESTMATE_FEEDBACK_DECAY_HALFLIFE` | 30 | 시간 감쇠 반감기 (일) |
| `INVESTMATE_DEBATE_CONSENSUS_PENALTY` | 1 | 낮은 합의 시 신뢰도 차감 |
| `INVESTMATE_PORTFOLIO_CONTEXT_ENABLED` | true | 포트폴리오 컨텍스트 AI 주입 |
| `INVESTMATE_REBALANCE_ENABLED` | true | 리밸런싱 트리거 활성화 |
| `INVESTMATE_COUNTERFACTUAL_ENABLED` | true | 반사실 분석 활성화 |
| `INVESTMATE_ML_DRIFT_THRESHOLD` | 0.10 | ML 드리프트 임계값 |
| `INVESTMATE_ML_AUTO_RETRAIN` | true | 드리프트 시 자동 재학습 |
| `INVESTMATE_TELEGRAM_TOKEN` | - | Telegram 봇 토큰 |
| `INVESTMATE_TELEGRAM_CHAT_ID` | - | Telegram 채팅 ID |
| `INVESTMATE_SLACK_WEBHOOK` | - | Slack 웹훅 URL |
| `INVESTMATE_SMTP_USER` | - | 이메일 발신 계정 |
| `INVESTMATE_SMTP_PASS` | - | 이메일 앱 비밀번호 |
| `INVESTMATE_EMAIL_TO` | - | 이메일 수신 주소 |

---

## CLI 명령어

```
investmate run [--date] [--top N] [--force] [--step N] [--skip-notify]
investmate report latest | show YYYY-MM-DD | list | weekly | weekly-latest
investmate stock <TICKER> [--period 6mo] [--export json|csv|md]
investmate history recommendations | signals <TICKER> | pipeline
investmate backtest run --start --end | compare-weights | walk-forward
investmate ai latest | show YYYY-MM-DD | rerun | performance
investmate prompt latest | show YYYY-MM-DD
investmate db init | status | backup | update-sp500 | backfill-fg
investmate config show | set <key> <value>
investmate web                              # http://localhost:8000
```

---

## 비기능 요구사항

- **안정성:** Resilient pipeline, Graceful Shutdown (SIGTERM/SIGINT), Step Checkpointing
- **데이터:** 증분 수집, UPSERT 중복 방지, SQLite WAL, batch_write_mode
- **성능:** date_map 배치 캐시, batch stock loading (N+1 방지), 매크로 배치 1회
- **인덱스:** 10+ 인덱스 on 8개 테이블 (자동 생성)
- **레이트 리밋:** yfinance 50개 단위, 재무 max_workers=4, 강화 50개 딜레이
- **국제화:** 한국어 UI, Windows UTF-8 호환, ASCII 안전 문자
- **알림:** Telegram/Slack/Email 3채널 지원, pydantic-settings 통해 `.env` 로딩
- **보안:** API 키 불필요 (Claude Code CLI 인증 공유), 환경변수 기반 시크릿
- **테스트:** in-memory SQLite, Mock 외부 API, 1169개 테스트 (100개 파일)
- **면책:** "투자 참고용이며 투자 권유가 아닙니다"

---

## API 키 정책

기본 구성에서 **API 키 없이 모든 핵심 기능 동작**.
AI 분석은 Claude Code CLI 인증 (`~/.claude/.credentials.json`) 공유.
이메일/텔레그램/슬랙은 선택 설정.
