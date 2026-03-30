# CLAUDE.md — investmate: AI 주식 투자 가이드 프로젝트

> 이 문서는 Claude Code의 plan 모드에서 프로젝트 전체를 이해하고
> 스스로 구현 계획을 세울 수 있도록 작성된 프로젝트 명세서입니다.

---

## 프로젝트 개요

S&P 500 전 종목의 데이터를 매일 자동으로 수집하고, 기술적/기본적/외부 요인/품질 분석을 종합하여
매수 적합 종목을 자동으로 선별하고 데일리 리포트를 생성하는 **퀀트 파이프라인**.

**핵심 원칙:**

- S&P 500 전 종목(약 500개)을 대상으로 매일 전수 스캔
- 수집 → 분석 → 스크리닝 → AI 분석 → 리포트를 하나의 파이프라인으로 한번에 실행
- cron으로 매일 장 마감 후 자동 실행되는 배치 구조 (AWS EC2 운영 중)
- 데이터를 매일 DB에 축적하여 장기적으로 추적/관리
- AI 분석은 Claude Code CLI (`claude -p`) + Anthropic SDK (Tool Use) 4단계 fallback
- **시장 체제 감지** (Bull/Bear/Range/Crisis)에 따라 스코어링 가중치 자동 조절
- **ML 모델** (LightGBM)이 60일 데이터 축적 후 자동 활성화되어 규칙 기반과 블렌딩
- T+1 실행가 기반 성과 추적, 거래비용 차감

---

## 기술 스택

| 항목          | 선택                            | 비고                                     |
| ------------- | ------------------------------- | ---------------------------------------- |
| 언어          | Python 3.11+                    |                                          |
| 패키지 매니저 | uv 또는 poetry                  |                                          |
| DB            | SQLite (기본)                   | 단일 파일, WAL 모드. SQLAlchemy ORM 사용 |
| ORM           | SQLAlchemy 2.0+                 | 동기 세션, batch_write_mode 최적화       |
| 마이그레이션  | 경량 자동 (migrate.py)          | ALTER TABLE + 인덱스 자동 생성           |
| CLI           | Click                           |                                          |
| 터미널 UI     | Rich                            | 테이블, 차트, 패널, 스피너               |
| 웹 대시보드   | FastAPI + Jinja2 + Tailwind     | ECharts 5.x, 13개 라우트, 11개 템플릿    |
| 데이터 처리   | pandas, numpy                   |                                          |
| 기술적 분석   | ta (Technical Analysis)         |                                          |
| 스크래핑      | BeautifulSoup4, requests        |                                          |
| 주식 데이터   | yfinance                        | 배치 다운로드 지원                       |
| AI 분석       | Anthropic SDK + Claude CLI      | Tool Use 구조화 출력, 4단계 fallback     |
| ML            | LightGBM                        | 28피처, 70% 규칙 + 30% ML 블렌딩         |
| 데이터 검증   | Pydantic v2 + pydantic-settings |                                          |
| 환경변수      | python-dotenv                   |                                          |
| 테스트        | pytest, pytest-cov              | 778개 테스트, 64개 테스트 파일           |
| 로깅          | Python logging (JSON 구조화)    | 배치 실행 로그 파일 기록                 |
| 배포          | AWS EC2 (t2.micro Free Tier)    | Nginx, systemd, cron, S3 백업            |
| 모니터링      | Telegram Bot                    | 배치 결과 상세 알림, 헬스체크 자동 복구  |
| 리트라이      | Tenacity                        | 지수 백오프 + SimpleCircuitBreaker       |
| 설정          | pydantic-settings               | 환경변수 > .env > config.json > 기본값   |

---

## 프로젝트 구조

```
investmate/
├── CLAUDE.md                    # 이 파일 (프로젝트 명세)
├── AWS_DEPLOYMENT.md            # AWS Free Tier 배포 가이드 (14단계)
├── pyproject.toml
├── CHANGELOG.md                 # 변경 이력
├── TODO.md                      # 과제 목록
├── METRICS.md                   # 테스트/성과 지표
├── Makefile                     # 개발 명령어
├── .env                         # 환경변수 (gitignore)
├── src/
│   ├── __init__.py
│   ├── main.py                  # CLI 진입점 (Click) + Windows UTF-8 인코딩 설정
│   ├── config.py                # 설정 관리 (Environment enum: DEV/TEST/PROD)
│   ├── pipeline.py              # 데일리 파이프라인 오케스트레이터 (STEP 0~6 + 4.5)
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── claude_analyzer.py   # AI 분석 (Tool Use + 4단계 fallback + 스트리밍)
│   │   ├── response_schema.py   # AI 응답 구조화 스키마 (frozen dataclasses)
│   │   ├── validator.py         # AI 결과 검증기 + 신뢰도 캘리브레이션
│   │   ├── feedback.py          # AI 예측 피드백 시스템 (ECE + 교정 곡선)
│   │   ├── calibrator.py        # 목표가/손절가 캘리브레이션 (look-ahead 보호)
│   │   ├── cache.py             # AI 응답 캐시 (SHA256 해시 기반)
│   │   ├── data_enricher.py     # yfinance 보강 데이터 (52주, Beta, PEG, FCF)
│   │   ├── prompt_registry.py   # 프롬프트 버전 관리 (v1_base/v2_cot/v3_debate)
│   │   └── evaluator.py         # AI 성과 평가 (방향 정확도, ECE)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py            # SQLAlchemy 엔진 + batch_write_mode 컨텍스트 매니저
│   │   ├── helpers.py           # date_to_id / id_to_date 유틸리티 (배치 캐시)
│   │   ├── migrate.py           # 경량 스키마 마이그레이션 + 인덱스 자동 생성
│   │   ├── models.py            # ORM 모델 (6 Dimension + 10 Fact + 1 Bridge + 10 Index)
│   │   ├── repository.py        # 데이터 접근 레이어 (범위 조회 + 배치 로드)
│   │   └── seed.py              # 디멘션 초기 데이터 시딩 (S&P 500 종목 포함)
│   ├── data/
│   │   ├── __init__.py
│   │   ├── yahoo_client.py      # yfinance 래퍼 (배치 다운로드, 실패 티커 추적)
│   │   ├── enhanced_collector.py # 강화 데이터 수집 (내부자/기관/애널리스트/실적/공매도)
│   │   ├── news_scraper.py      # 뉴스 스크래핑 (yfinance v2 API 대응)
│   │   ├── macro_collector.py   # 매크로 지표 수집 (VIX, 금리, 환율, 금, 유가)
│   │   ├── backfill_macro.py    # 매크로 히스토리 백필 (3년치)
│   │   ├── event_collector.py   # 이벤트 캘린더 (FOMC 일정, 실적 발표일)
│   │   ├── kr_names.py          # S&P 500 한글 종목명 매핑 (170+)
│   │   ├── sp500.py             # S&P 500 종목 목록 관리
│   │   └── schemas.py           # Pydantic 스키마
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── technical.py         # 기술적 분석 (17+ 지표)
│   │   ├── fundamental.py       # 기본적 분석 (9개 팩터, 섹터 상대 밸류에이션)
│   │   ├── external.py          # 외부 요인 분석 (매크로, 뉴스 감성, 섹터 모멘텀, 금/유가)
│   │   ├── signals.py           # 매수/매도 시그널 (10종, 가중 강도)
│   │   ├── screener.py          # 종목 스크리닝 + 랭킹 엔진 (체제 적응형 가중치)
│   │   ├── performance.py       # 추천 성과 추적 (T+1 실행가, 거래비용 차감)
│   │   ├── quality.py           # 재무 품질 (Piotroski F-Score, Altman Z-Score, 발생주의)
│   │   ├── regime.py            # 시장 체제 감지 (Bull/Bear/Range/Crisis + 적응형 가중치)
│   │   ├── sentiment.py         # LLM 기반 뉴스 감성 분석 (키워드 fallback)
│   │   ├── relative_strength.py # RS 백분위 (개별 종목 vs S&P 500 3개월 수익률)
│   │   └── support_resistance.py # 지지/저항 수준 자동 감지 (피벗 클러스터링)
│   ├── reports/
│   │   ├── __init__.py
│   │   ├── report_models.py     # 리포트 데이터 모델 (frozen dataclasses)
│   │   ├── assembler.py         # DB → EnrichedDailyReport 조립
│   │   ├── daily_report.py      # 두괄식 Markdown/JSON 리포트 생성기
│   │   ├── explainer.py         # 초보자 친화 한국어 설명 생성기
│   │   ├── prompt_builder.py    # AI 프롬프트 생성기 (CoT + Bull/Bear 토론, ThreadPoolExecutor)
│   │   ├── comparator.py        # 추천 종목 일간 비교 (신규/이탈/순위변동)
│   │   ├── terminal.py          # Rich 터미널 출력 (두괄식 핵심 요약)
│   │   └── format_utils.py      # 숫자 포맷 유틸리티 (T/B/M/K 변환)
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── engine.py            # 백테스트 엔진 (Sortino/Calmar/Omega, 유동성 기반 거래비용)
│   │   ├── comparator.py        # 가중치 비교 (기본/기술중심/펀더멘털중심/모멘텀중심)
│   │   └── walk_forward.py      # 워크포워드 백테스트 (IS/OOS 분할, 과적합 감지)
│   ├── ml/
│   │   ├── __init__.py
│   │   ├── features.py          # 피처 엔지니어링 (28개: 기술10+기본8+수급5+외부5)
│   │   ├── trainer.py           # LightGBM 학습 (AUC 최적화)
│   │   ├── scorer.py            # ML 스코어러 (60일 데이터 후 활성화, 70/30 블렌딩)
│   │   ├── evaluator.py         # 모델 평가 (정확도, Precision@10, 방향 정확도)
│   │   └── registry.py          # 모델 레지스트리 (pkl 직렬화)
│   ├── portfolio/
│   │   ├── __init__.py
│   │   ├── optimizer.py         # 포트폴리오 최적화 (Max Sharpe/Min Var/Risk Parity/Equal)
│   │   └── efficient_frontier.py # 효율적 프론티어 (Ledoit-Wolf 공분산, 30포인트)
│   ├── alerts/
│   │   ├── __init__.py
│   │   └── notifier.py          # 이메일/텔레그램/슬랙 알림
│   └── web/                     # FastAPI 웹 대시보드
│       ├── app.py               # FastAPI 앱 팩토리 (글로벌 예외 핸들러)
│       ├── deps.py              # DB 의존성 주입 (세션, 마이그레이션)
│       ├── routes/              # 라우트 (13개 모듈)
│       │   ├── dashboard.py     # 메인 대시보드 (/)
│       │   ├── recommendations.py # 추천 상세 (/recommendations/{date})
│       │   ├── performance.py   # P&L 추적 (/performance)
│       │   ├── market.py        # 시장 환경 (/market)
│       │   ├── stock.py         # 종목 상세 (/stock/{ticker}) + S/R 감지
│       │   ├── ai_accuracy.py   # AI 정확도 + 캘리브레이션 곡선 (/ai-accuracy)
│       │   ├── heatmap.py       # S&P 500 히트맵 + 섹터 필터 (/heatmap)
│       │   ├── screener.py      # 인터랙티브 스크리너 (/screener) — 15개 필터
│       │   ├── portfolio.py     # 포트폴리오 최적화 (/portfolio) — 4전략
│       │   ├── chat.py          # Claude AI 채팅 (멀티턴, 캐시, Haiku 라우팅)
│       │   ├── api.py           # JSON API (헬스체크, 스파크라인, 파라미터 검증)
│       │   └── api_export.py    # CSV 내보내기 API
│       ├── templates/           # Jinja2 + Tailwind CSS 템플릿 (11개)
│       │   ├── base.html        # 레이아웃 (네비 활성, 스킵 내비, 모바일 메뉴)
│       │   ├── dashboard.html
│       │   ├── recommendations.html
│       │   ├── performance.html # 모바일 카드뷰, 수익률 방향 화살표
│       │   ├── market.html
│       │   ├── stock.html
│       │   ├── ai_accuracy.html
│       │   ├── heatmap.html     # 색상 범례, 섹터 필터, 추천 하이라이트
│       │   ├── screener.html    # 모바일 필터 드로어, 프리셋 (가치/성장/배당/과매도)
│       │   ├── portfolio.html   # 효율적 프론티어 시각화
│       │   └── chat.html
│       └── static/
│           ├── charts.js        # ECharts 헬퍼 (스켈레톤 로딩, 토스트, 디바운스)
│           └── style.css        # WCAG AA 접근성, 다크모드 대비
├── tests/                       # 64 테스트 파일, 778개 테스트
│   ├── conftest.py
│   ├── test_db.py               # DB 레이어 + 범위 조회 fallback 테스트
│   ├── test_pipeline.py         # 파이프라인 기본 테스트
│   ├── test_pipeline_steps.py   # 파이프라인 단계별 테스트
│   ├── test_analysis.py
│   ├── test_screener.py         # 필터, 모멘텀, 외부 점수, 추천 근거 테스트
│   ├── test_screener_scoring.py # 스코어링 함수 단위 테스트 (가중치 검증)
│   ├── test_screener_db.py      # 스크리너 DB 통합 테스트
│   ├── test_scoring_integration.py # 스코어링 통합 테스트
│   ├── test_sector_valuation.py # 섹터 상대 밸류에이션 테스트
│   ├── test_signals.py          # 10종 시그널 + RSI strength 테스트
│   ├── test_quality.py          # Piotroski/Altman Z/발생주의 테스트
│   ├── test_regime.py           # 시장 체제 감지 테스트
│   ├── test_sentiment.py        # LLM 감성 분석 테스트
│   ├── test_relative_strength.py # RS 백분위 테스트
│   ├── test_explainer.py        # 초보자 설명 + 투자 의견 테스트
│   ├── test_daily_report.py     # 두괄식 리포트 생성 테스트
│   ├── test_prompt_builder.py   # AI 프롬프트 생성 테스트
│   ├── test_prompt_registry.py  # 프롬프트 버전 관리 테스트
│   ├── test_assembler.py        # 리포트 조립기 순수 함수 테스트
│   ├── test_assembler_integration.py # 리포트 조립 통합 테스트
│   ├── test_comparator.py       # 추천 비교 테스트
│   ├── test_notifier.py         # 알림 모듈 테스트
│   ├── test_news_scraper.py     # 뉴스 스크래퍼 mock 테스트
│   ├── test_macro_collector.py  # 매크로 수집 mock 테스트
│   ├── test_enhanced_collector.py # 강화 데이터 수집 mock 테스트
│   ├── test_yahoo_client.py     # yfinance 래퍼 테스트 (실패 추적)
│   ├── test_claude_analyzer.py  # AI 분석기 테스트
│   ├── test_external.py         # 달러 인덱스, 매크로 완전성, 단어경계 테스트
│   ├── test_fundamental.py      # 배당수익률, FCF, PEG, EV/EBITDA 테스트
│   ├── test_backtest.py         # 백테스트 엔진 + 가중치 비교 테스트
│   ├── test_walk_forward.py     # 워크포워드 백테스트 테스트
│   ├── test_risk_metrics.py     # Sortino/Calmar/Omega 비율 테스트
│   ├── test_migrate.py          # DB 스키마 마이그레이션 테스트
│   ├── test_date_map.py         # 날짜 매핑 캐시 테스트
│   ├── test_format_utils.py     # 숫자 포맷 유틸리티 테스트
│   ├── test_cli.py              # CLI 명령어 + 엣지 케이스 테스트
│   ├── test_config.py           # 설정 기본 테스트
│   ├── test_config_extended.py  # 환경/검증 테스트
│   ├── test_sp500.py            # S&P 500 목록 관리 테스트
│   ├── test_ai_validator.py     # AI 검증기 테스트
│   ├── test_ai_feedback.py      # AI 피드백 수집/분석 테스트
│   ├── test_ai_cache.py         # AI 캐시 테스트
│   ├── test_calibrator.py       # AI 캘리브레이션 테스트
│   ├── test_calibrator_integration.py # 캘리브레이션 통합 테스트
│   ├── test_event_collector.py  # FOMC/실적 캘린더 테스트
│   ├── test_data_enricher.py    # 보강 데이터 테스트
│   ├── test_response_schema.py  # AI 응답 스키마 테스트
│   ├── test_evaluator.py        # AI 평가 테스트
│   ├── test_ml_features.py      # ML 피처 추출 테스트
│   ├── test_ml_trainer.py       # ML 학습 테스트
│   ├── test_ml_scorer.py        # ML 스코어링 테스트
│   ├── test_ml_evaluator.py     # ML 모델 평가 테스트
│   ├── test_performance.py      # 성과 추적 테스트
│   ├── test_performance_tracking.py # T+1 실행가 + 거래비용 테스트
│   ├── test_portfolio_optimizer.py # 포트폴리오 최적화 테스트
│   ├── test_engine.py           # DB 엔진 테스트
│   ├── test_terminal.py         # 터미널 출력 테스트
│   ├── test_chat.py             # 채팅 API 테스트
│   ├── test_technical_store.py  # 지표 저장 테스트
│   ├── test_web_api.py          # 웹 API 테스트
│   ├── test_web_heatmap.py      # 히트맵 라우트 테스트
│   └── test_web_screener.py     # 스크리너 라우트 테스트
├── scripts/                     # 자동화 스크립트
│   ├── auto_improve.sh          # Claude Code 자율 개선 루프
│   ├── run_pipeline.sh          # 파이프라인 실행 래퍼
│   └── improve_prompts/         # 개선 프롬프트 템플릿
├── logs/                        # 배치 실행 로그 (gitignore)
├── reports/                     # 생성된 리포트 (구조화)
│   ├── daily/                   # 일별 리포트 (MD + JSON)
│   ├── prompts/                 # AI 분석용 프롬프트
│   └── ai_analysis/             # AI 분석 원문 + 딥다이브
├── results/                     # 백테스트 결과 (gitignore)
└── data/                        # 런타임 데이터 (gitignore)
    ├── investmate.db            # SQLite DB 파일
    ├── ai_cache/                # AI 응답 캐시
    └── models/                  # ML 학습 모델 (pkl)
```

---

## 핵심 아키텍처: 데일리 배치 파이프라인

### 실행 방식

하나의 명령으로 전체 파이프라인이 순차 실행:

```bash
investmate run
```

이 명령 하나가 아래 7단계를 순서대로 실행한다.

### pipeline.py -- 파이프라인 오케스트레이터

```python
class DailyPipeline:
    def run(self):
        self.step0_performance()  # 과거 추천 성과 업데이트 (T+1 실행가 + 수익률)
        self.step1_collect()      # S&P 500 전 종목 데이터 수집 + 강화 데이터 + 뉴스
        self.step2_analyze()      # 기술적 지표 계산 + 시그널 판단 (중복 방지)
        self.step3_external()     # 외부 요인 분석 + 섹터 모멘텀 계산
        self.step4_screen()       # 스크리닝 + 랭킹 + 추천 종목 뉴스 수집
        self.step4_5_ai_analysis()# Claude AI 분석 (Tool Use + 4단계 fallback)
        self.step5_report()       # 두괄식 데일리 리포트 생성
        self.step6_notify()       # 알림 발송 (Telegram/Slack/Email)
```

**파이프라인 특성:**

- **Resilient**: 단계 실패 시 로그 남기고 다음 단계 계속 진행
- **Graceful Shutdown**: SIGTERM/SIGINT 시그널 핸들러, `_interrupted` 플래그로 안전 종료
- **Step Checkpointing**: `_is_step_done()`으로 완료된 단계 스킵 (재실행 시 이어서 진행)
- **Force Mode**: `--force` 플래그로 체크포인트 무시하고 전체 재실행
- **Per-Stock Error Isolation**: 개별 종목 실패가 전체 파이프라인에 영향 주지 않음
- **JSON Summary**: `logs/{date}_summary.json`에 단계별 결과 요약 저장 (텔레그램 알림용)
- 재실행 시 기존 추천/시그널을 삭제 후 재생성한다 (중복 방지)

---

### STEP 0 — 성과 업데이트 (자동)

**performance.py:**

- `fill_execution_prices()`: 과거 추천의 T+1 시가를 실행가로 기록
- `update_recommendation_returns()`: 1d/5d/20d 사후 수익률 계산 (거래비용 차감)
- 배치 종목 로딩으로 N+1 쿼리 방지

### STEP 1 — 데이터 수집

**S&P 500 종목 목록 관리 (sp500.py):**

- Wikipedia 또는 yfinance에서 현재 S&P 500 구성 종목 목록을 가져옴
- dim_stocks에 없는 신규 종목은 자동 추가, 제외된 종목은 is_sp500=False 처리

**배치 수집 (yahoo_client.py):**

- yfinance.download()의 멀티 티커 기능 활용: 500개 종목을 한번에 요청 (**실패 티커 추적**)
- 마지막 수집일 이후 ~ 오늘까지의 일봉 데이터 증분 수집 (**volume=0 데이터 skip**)
- **적시성 검증**: 5개 샘플 종목의 최소 날짜로 판단
- 최초 실행 시 전 종목 최근 2년치 히스토리를 한번에 가져옴
- fact_daily_prices에 UPSERT, flush() 2500건 단위 메모리 관리

**재무 데이터:**

- 분기 실적 시즌에 맞춰 수집 (ThreadPoolExecutor max_workers=4 병렬)
- fact_financials에 원본 저장, fact_valuations에 파생 지표 계산 후 저장

**매크로 지표 수집 (macro_collector.py):**

- yfinance **배치 1회 호출**로 매크로 지표 동시 수집:
  - ^VIX (공포지수), ^TNX (10년 금리), ^IRX (13주 금리)
  - DX-Y.NYB (달러 인덱스), ^GSPC (S&P 500), GC=F (금), CL=F (유가)
- **수집 완전성 로깅**: 유효 지표 수 < 3이면 경고
- fact_macro_indicators 테이블에 저장 (gold_price, oil_price, yield_spread 포함)

**강화 데이터 수집 (enhanced_collector.py):**

- 내부자 거래, 기관 보유 현황, 애널리스트 컨센서스, 실적 서프라이즈, 공매도
- 503개 종목 순차 수집, 배치 50개 단위 딜레이

**뉴스 수집 (news_scraper.py):**

- yfinance v2 API 구조 대응
- **날짜 파싱 실패 기사는 skip** (감성 편향 방지)
- STEP 1에서 시장 전체 뉴스 수집, STEP 4에서 추천 후보 종목별 추가 수집

### STEP 2 — 기술적 분석

**전 종목 지표 계산 (technical.py):**

- ta 라이브러리로 17+ 지표 일괄 계산:
  - SMA(5, 20, 60, 120), EMA(12, 26)
  - RSI(14), MACD(12, 26, 9), 볼린저밴드(20, 2σ)
  - 스토캐스틱(14, 3, 3), 거래량 이동평균(20)
- 결과를 fact_indicator_values에 저장 (EAV 패턴)

**시그널 판단 (signals.py):**

- 10종 시그널 감지: golden_cross, death_cross, rsi_oversold, rsi_overbought,
  macd_bullish, macd_bearish, bb_lower_break, bb_upper_break, stoch_bullish, stoch_bearish
- **가중 강도 스코어링**: 시그널별 default_weight 반영
- fact_signals에 기록 (재실행 시 DELETE 후 재생성)

### STEP 3 — 외부 요인 분석

**매크로 환경 점수 (external.py):**

- VIX 수준: <15 강세(+2), <20 안정(+1), >25 주의(-1), >30 위험(-2)
- 금리 추이: >5% 고금리(-1), <3% 저금리(+1)
- 달러 인덱스: >105 강달러(-1), <95 약달러(+1)
- S&P 500 추세: 20일선 위(+1), 아래(-1)
- **금/유가/금리 스프레드**: 추가 매크로 팩터
- **완전성 검증**: 유효 지표 5/8개 미만 → 중립(5) 반환
- → 시장 환경 종합 점수 산출 (1-10)

**뉴스 감성 분석:**

- **LLM 기반** (sentiment.py): Claude AI 감성 분석 (Haiku 모델)
- **키워드 fallback**: 단어 경계 `\b` 매칭, 27+ 추가 키워드
- **시간 감쇠**: 오래된 뉴스일수록 가중치 감소

**섹터 모멘텀 분석:**

- 섹터별 최근 20일 평균 수익률 → 1-10 정규화 점수
- **플랫 마켓 감쇄**: spread<1% → 5.0 방향 압축
- `_sector_momentum` 인스턴스 변수로 STEP 4에 전달

### STEP 4 — 스크리닝 + 랭킹

**screener.py — 핵심 엔진:**

1단계 — 필터링 (500개 → 약 30-50개):

- 기본 필터: 최소 거래량, 데이터 충분 여부, **달러 거래량 필터**
- 기술적 필터: RSI < 70, 가격이 SMA120 위 (**-5% 완화: RSI<40이면 통과**)
- 기본적 필터: PER > 0, 부채비율 적정
- **실적 캘린더 프리필터**: 실적 발표 직전 종목 주의 플래그

2단계 -- 5차원 스코어링 (**시장 체제 적응형 가중치**):

| 항목               | 기본 비중 | 점수 기준                                                                 |
| ------------------ | --------- | ------------------------------------------------------------------------- |
| 기술적 시그널 점수 | 25%       | 매수 시그널 수/강도, RSI 위치, MACD 상태, 스토캐스틱 K/D                  |
| 기본적 분석 점수   | 25%       | PER, PEG, PBR, ROE, 부채비율, 배당수익률, 성장률, FCF, EV/EBITDA (9팩터)  |
| 수급/스마트머니    | 15%       | 내부자 매수(시간감쇠), 애널리스트(균형보정), 공매도, 기관 보유            |
| 외부 요인 점수     | 15%       | VIX, S&P 500, 금리, 달러, 섹터 모멘텀, 매크로 완전성                      |
| 가격 모멘텀 점수   | 20%       | 선형 보간 수익률, 이동평균 배열, 거래량 추세, **적응형 모멘텀(VIX 연동)** |

**시장 체제별 가중치 조절 (regime.py):**

| 체제   | 기술적 | 기본적 | 수급 | 외부 | 모멘텀 | 트리거                 |
| ------ | ------ | ------ | ---- | ---- | ------ | ---------------------- |
| Bull   | 20%    | 20%    | 15%  | 15%  | 30%    | S&P > SMA200, VIX < 20 |
| Bear   | 15%    | 35%    | 20%  | 20%  | 10%    | S&P < SMA200, VIX > 25 |
| Range  | 30%    | 25%    | 15%  | 15%  | 15%    | 횡보 시장 (기본)       |
| Crisis | 10%    | 30%    | 25%  | 25%  | 10%    | VIX > 30, S&P < SMA50  |

**품질 필터 (quality.py):**

- **Piotroski F-Score** (0-9점): 수익성, 레버리지, 운영효율 종합
- **Altman Z-Score**: 부도 위험 분류 (safe >3.0, gray 1.8-3.0, distress <1.8)
- **발생주의 품질**: (순이익-OCF)/총자산 → high/medium/low

**추가 필터:**

- **섹터 상한** (40%): 단일 섹터 과집중 방지
- **상관관계 필터**: 60일 기준 0.75 이상 상관 종목 제거
- **회사명 기반 중복 제거**: GOOGL/GOOG 같은 dual-class 상위 1개만 선정
- 애널리스트 목표가 현재가 대비 -15% 이하 → 스마트머니 -1.5 감점
- 데이터 누락 시 중립(5.0) 대신 약한 감점(3.5) 적용

3단계 -- 랭킹:

- 기본값: 상위 10개 종목
- 추천 근거 자동 생성 (구체적 숫자 포함)
- **팩터 귀인 분석**: 각 팩터별 기여도 산출
- 결과를 fact_daily_recommendations에 저장

### STEP 4.5 -- AI 분석

**claude_analyzer.py — 4단계 Fallback:**

1. **Tool Use** (Anthropic SDK): 구조화 JSON 스키마로 강제 출력
2. **Streaming** (Anthropic SDK): 스트리밍 응답으로 타임아웃 방지
3. **SDK 일반**: 표준 API 호출
4. **CLI** (`claude -p`): Claude Code CLI 비대화형 모드

**프롬프트 시스템 (prompt_builder.py + prompt_registry.py):**

- **3개 버전**: v1_base (CFA 분석), v2_cot (Chain-of-Thought 6단계), v3_debate (Bull/Bear 토론, 기본)
- **통합 프롬프트**: Round 1(스크리닝) + Round 2(딥다이브)를 하나로 병합
- **ThreadPoolExecutor**: 프롬프트 데이터 병렬 조립
- **적응형 지시**: 과거 승률/섹터 성과 기반 자동 조정

**AI 검증 및 피드백:**

- **validator.py**: 목표가/손절가 일관성 보정, 신뢰도-승인 일관성 경고
- **calibrator.py**: 과대/과소추정 편향 자동 보정 (look-ahead 35일 보호)
- **feedback.py**: ECE(Expected Calibration Error), 교정 곡선, 섹터별 승률
- **evaluator.py**: 방향 정확도, 목표가 오차, 승률 20d

**모델 라우팅:**

- 분석: `claude-sonnet-4-20250514` (분석 품질 최적)
- 채팅: `claude-haiku-4-5-20251001` (빠른 응답)
- 감성: `claude-haiku-4-5-20251001` (비용 최적)

**AI 스타일:** aggressive / balanced / conservative 프롬프트 분기

**설정:** `INVESTMATE_AI_ENABLED`, `INVESTMATE_AI_TIMEOUT`, `INVESTMATE_AI_STYLE`, `INVESTMATE_AI_BACKEND`

### STEP 5 -- 데일리 리포트 생성

**daily_report.py + explainer.py + assembler.py:**

리포트 형식: **두괄식** (핵심 먼저, 상세는 나중에)

1. **핵심 요약 (30초 브리핑)**:
   - 시장 분위기 한 줄 요약
   - 오늘의 추천 종목 (종목별 한줄 이유)
   - 투자 의견 (시장 점수 기반 구체적 행동 제안)
   - 섹터 분포

2. **추천 종목 카드** (각 종목):
   - 한줄 요약 헤드라인
   - "왜 추천하나요?" (초보자 한국어 설명)
   - "숫자로 보면" (핵심 지표 한 줄)
   - "주의할 점" (리스크 쉬운 설명)
   - `<details>` 접이식 상세

3. **시장 환경 상세**: VIX, S&P 500, 금리, 달러 테이블
4. **전체 시그널 목록**: 매수/매도 시그널 (한국어 번역)
5. **면책 조항**

**추천 비교 (comparator.py):**

- 오늘 vs 어제 추천 비교: 신규/이탈/순위변동 추적
- 시장 점수 변화 추이

**출력 형식:**

- 터미널: Rich 패널 (핵심 요약 → 시장 환경 → TOP N 테이블 → 종목 카드)
- Markdown: reports/daily/YYYY-MM-DD.md
- JSON: reports/daily/YYYY-MM-DD.json
- 프롬프트: reports/prompts/YYYY-MM-DD_prompt.txt
- AI 분석: reports/ai_analysis/YYYY-MM-DD_ai_analysis.md + deep_dive.md

### STEP 6 -- 알림 발송

**notifier.py:**

- 이메일: smtplib (Gmail SMTP)
- 텔레그램: Bot API — 상세 배치 결과 알림 (format_summary.py 포맷)
- 슬랙: Webhook

---

## CLI 명령어

```
investmate
├── run                          # 데일리 파이프라인 전체 실행 (핵심 명령)
│   ├── --date YYYY-MM-DD        # 특정 날짜 기준으로 실행 (기본: 오늘)
│   ├── --top N                  # 추천 종목 수 (기본: 10)
│   ├── --skip-notify            # 알림 발송 스킵
│   ├── --step 1-6               # 특정 단계만 실행 (디버깅용)
│   └── --force                  # 체크포인트 무시, 전체 재실행
│
├── report                       # 리포트 조회
│   ├── latest                   # 가장 최근 리포트 출력
│   ├── show YYYY-MM-DD          # 특정 날짜 리포트 조회
│   └── list                     # 저장된 리포트 목록
│
├── stock <ticker>               # 개별 종목 상세 조회
│   ├── --period 6mo             # 분석 기간
│   └── --export json|csv|md     # 내보내기
│
├── history                      # 히스토리 조회
│   ├── signals <ticker>         # 과거 시그널 이력
│   ├── recommendations          # 과거 추천 이력 + 사후 수익률
│   └── pipeline                 # 파이프라인 실행 이력
│
├── db                           # DB 관리
│   ├── init                     # DB 초기화 + S&P 500 시딩
│   ├── status                   # DB 상태
│   ├── backup                   # DB 백업
│   └── update-sp500             # S&P 500 구성 종목 업데이트
│
├── backtest                     # 백테스트
│   ├── run --start --end        # 과거 추천 데이터로 백테스트 실행
│   ├── compare-weights          # 가중치 비교 (기본/기술/펀더멘털/모멘텀)
│   └── walk-forward             # 워크포워드 검증
│
├── ai                           # AI 분석 관리
│   ├── latest                   # 가장 최근 AI 분석 결과
│   ├── show YYYY-MM-DD          # 특정 날짜 AI 분석 조회
│   ├── rerun                    # AI 분석만 재실행
│   └── performance              # AI 예측 성과 대시보드
│
├── prompt                       # AI 프롬프트 조회
│   ├── latest                   # 가장 최근 프롬프트
│   └── show YYYY-MM-DD          # 특정 날짜 프롬프트
│
└── config                       # 설정 관리
    ├── show                     # 현재 설정 확인
    └── set <key> <value>        # 설정 변경
```

---

## 배치 스케줄링 (AWS EC2)

```bash
# 화~토 08:00 KST (미국 장 마감 후) — 데일리 파이프라인
0 8 * * 2-6 /home/ec2-user/scripts/daily_run.sh >> /home/ec2-user/investmate/logs/cron.log 2>&1

# 매일 09:00 KST — S3 백업
0 9 * * * /home/ec2-user/scripts/backup_s3.sh >> /home/ec2-user/investmate/logs/backup.log 2>&1

# 5분마다 — 헬스체크 (자동 복구 + 텔레그램 알림)
*/5 * * * * /home/ec2-user/scripts/healthcheck.sh >> /home/ec2-user/investmate/logs/health.log 2>&1

# 매시간 — 시스템 리소스 모니터링
0 * * * * /home/ec2-user/scripts/system_check.sh >> /home/ec2-user/investmate/logs/system.log 2>&1
```

---

## 웹 대시보드 (FastAPI)

**URL:** `http://<EC2_IP>/` (Nginx 리버스 프록시, basic auth)

| 경로                      | 설명                                        |
| ------------------------- | ------------------------------------------- |
| `/`                       | 메인 대시보드 (시장 요약 + 오늘의 추천)     |
| `/recommendations/{date}` | 추천 상세 (5차원 점수, AI 분석 결과)        |
| `/performance`            | P&L 추적 (1d/5d/20d 수익률, 모바일 카드뷰)  |
| `/market`                 | 시장 환경 (VIX, 금리, S&P 500 추세)         |
| `/stock/{ticker}`         | 종목 상세 (차트, S/R 감지, 기술적/기본적)   |
| `/ai-accuracy`            | AI 정확도 (캘리브레이션 곡선, 월간 추이)    |
| `/heatmap`                | S&P 500 히트맵 (섹터 필터, 추천 하이라이트) |
| `/screener`               | 인터랙티브 스크리너 (15개 필터, 4개 프리셋) |
| `/portfolio`              | 포트폴리오 최적화 (효율적 프론티어 시각화)  |
| `/chat`                   | Claude AI 채팅 (멀티턴, 1시간 캐시)         |
| `/api/health`             | 헬스체크 엔드포인트                         |
| `/api/*`                  | JSON API (차트 데이터, 스파크라인 등)       |
| `/api/export/*`           | CSV 내보내기                                |

**프론트엔드 기술:** Tailwind CSS + ECharts 5.x + Jinja2, WCAG AA 접근성, 반응형 모바일

---

## DB 스키마 설계

Star Schema 기반 설계. SQLAlchemy ORM 모델로 구현. 모든 테이블에 created_at, updated_at 자동 관리.
**10개 인덱스**가 8개 테이블에 적용되어 조회 성능 최적화.

### 설계 원칙

- **Fact 테이블**: 측정 가능한 이벤트/수치 데이터
- **Dimension 테이블**: 분석 축이 되는 속성 데이터
- **Bridge 테이블**: 다대다 관계 해소 (뉴스 ↔ 종목)
- **EAV 패턴**: 기술적 지표를 행으로 저장 → 새 지표 추가 시 스키마 변경 불필요
- **원본/파생 분리**: 원본 재무데이터(fact_financials)와 계산된 밸류에이션(fact_valuations) 분리

---

### Dimension 테이블 (6개)

#### dim_stocks (종목 마스터)

| 컬럼      | 타입                                | 설명                   |
| --------- | ----------------------------------- | ---------------------- |
| stock_id  | Integer, PK                         | 서로게이트 키          |
| ticker    | String, unique                      | 종목 코드 (AAPL, MSFT) |
| name      | String                              | 종목명                 |
| market_id | Integer, FK → dim_markets           |                        |
| sector_id | Integer, FK → dim_sectors, nullable |                        |
| is_active | Boolean, default=True               | 추적 활성화            |
| is_sp500  | Boolean, default=False              | 현재 S&P 500 구성 여부 |
| ipo_date  | Date, nullable                      | 상장일                 |
| added_at  | DateTime                            | 등록일                 |

**인덱스:** `idx_stocks_sp500` (is_sp500, is_active)

#### dim_markets / dim_sectors / dim_date / dim_indicator_types / dim_signal_types

(구조 동일 — 시장 구분, GICS 섹터, 날짜 디멘션, 지표 정의, 시그널 정의)

---

### Fact 테이블 (10개)

#### fact_daily_prices (일봉 데이터)

- stock_id, date_id, OHLCV + adj_close
- **인덱스:** `idx_prices_stock_date`, `idx_prices_date`, UNIQUE(stock_id, date_id)

#### fact_indicator_values (기술적 지표 — EAV)

- stock_id, date_id, indicator_type_id, value
- **인덱스:** `idx_indicators_stock_date`, UNIQUE(stock_id, date_id, indicator_type_id)

#### fact_financials (원본 재무제표)

- stock_id, period, revenue, operating_income, net_income, total_assets/liabilities/equity, operating_cashflow
- **인덱스:** `idx_financials_stock`, UNIQUE(stock_id, period)

#### fact_valuations (파생 밸류에이션)

- stock_id, date_id, market_cap, PER, PBR, ROE, debt_ratio, dividend_yield, EV/EBITDA, **short_ratio**
- **인덱스:** `idx_valuations_stock_date`, UNIQUE(stock_id, date_id)

#### fact_signals (시그널 이력)

- stock_id, date_id, signal_type_id, strength(1-10), description
- **인덱스:** `idx_signals_stock_date`

#### fact_macro_indicators (매크로 지표)

- date_id, vix, us_10y_yield, us_13w_yield, dollar_index, sp500_close, sp500_sma20, market_score
- **추가 컬럼:** gold_price, oil_price, yield_spread
- **인덱스:** `idx_macro_date`, UNIQUE(date_id)

#### fact_daily_recommendations (데일리 추천 결과)

- run_date_id, stock_id, rank, total_score
- 5차원 점수: technical_score, fundamental_score, smart_money_score, external_score, momentum_score
- recommendation_reason, price_at_recommendation, **execution_price** (T+1 시가)
- 사후 수익률: return_1d, return_5d, return_20d
- AI 결과: ai_approved, ai_reason, ai_target_price, ai_stop_loss, ai_confidence, ai_risk_level, ai_entry_strategy, ai_exit_strategy
- **인덱스:** `idx_recs_rundate`, `idx_recs_stock`

#### fact_ai_feedback (AI 예측 피드백)

- recommendation_id, ticker, sector, ai_approved, ai_confidence
- ai_target_price, price_at_rec, actual_price_20d, return_20d
- direction_correct, target_hit, target_error_pct

#### 강화 데이터 Fact 테이블

- **fact_insider_trades**: 내부자 거래 (이름, 직함, 유형, 주식수, 금액)
- **fact_institutional_holdings**: 기관 보유 (기관명, 주식수, 금액, 비율)
- **fact_analyst_consensus**: 애널리스트 컨센서스 (strong_buy~strong_sell, 목표가)
- **fact_earnings_surprise**: 실적 서프라이즈 (EPS 예상/실제, 서프라이즈%)

#### fact_news / fact_collection_logs

- 뉴스: title, url(unique), source, published_at, sentiment_score
- 파이프라인 로그: step, status(success/failed/interrupted), 시작/종료 시각, records_count

### Bridge 테이블 (1개)

- **bridge_news_stock**: news_id + stock_id (다대다), relevance(0~1)

---

### 초기 디멘션 데이터 (seed.py)

DB 초기화(db init) 시 자동 시딩:

- **dim_markets:** US 시장
- **dim_stocks + dim_sectors:** Wikipedia/yfinance S&P 500 전 종목 + GICS 섹터
- **dim_indicator_types:** 16종 (SMA_5/20/60/120, EMA_12/26, RSI_14, MACD/SIGNAL/HIST, BB_UPPER/MIDDLE/LOWER, STOCH_K/D, VOLUME_SMA_20)
- **dim_signal_types:** 10종 (golden/death cross, RSI over/under, MACD bull/bear, BB break, stoch bull/bear)
- **dim_date:** 2015-01-01 ~ 2030-12-31

---

## ML 파이프라인

### 피처 엔지니어링 (28개)

| 카테고리 | 피처 수 | 주요 피처                                                  |
| -------- | ------- | ---------------------------------------------------------- |
| 기술적   | 10      | RSI, MACD hist, BB 위치, SMA 거리, 볼륨비, 스토캐스틱      |
| 기본적   | 8       | PER, PBR, ROE, 부채비율, FCF, 배당, F-Score, Z-Score       |
| 수급     | 5       | 애널리스트 업사이드, 공매도, 기관, 내부자, 실적 서프라이즈 |
| 외부     | 5       | VIX, S&P/SMA20, 섹터 모멘텀, 금리 스프레드, 시장 점수      |

### LightGBM 학습

- 타겟: `return_20d > 0` (이진 분류)
- 최적화: AUC, 31 leaves, 0.05 LR, 100 rounds
- **자동 활성화**: 60 거래일 데이터 축적 후
- **블렌딩**: 70% 규칙 기반 + 30% ML 확률

---

## 포트폴리오 최적화

**4가지 전략:**

| 전략         | 설명                             |
| ------------ | -------------------------------- |
| Max Sharpe   | 위험 대비 수익률 최대화          |
| Min Variance | 포트폴리오 변동성 최소화         |
| Risk Parity  | 역변동성 가중 (균등 리스크 기여) |
| Equal Weight | 단순 균등 배분                   |

- **공분산 추정**: Ledoit-Wolf 수축 (이상치에 강건)
- **시장 충격 추정**: 0.025% × sqrt(포지션/일거래량)
- **효율적 프론티어**: 30포인트 시각화

---

## 백테스트 시스템

### 기본 백테스트 (engine.py)

- Sortino, Calmar, Omega 비율
- IS/OOS 분할 검증
- **유동성 기반 거래비용**: 거래량에 따른 차등 적용
- 무위험 수익률: 4% (설정 가능)

### 워크포워드 백테스트 (walk_forward.py)

- 롤링 윈도우 (train_months + test_months)
- **과적합 감지**: OOS Sharpe / IS Sharpe > 0.7이면 안전
- 윈도우별 IS/OOS 성과, 평균 수익률, 승률 산출

---

## 설정 시스템

**우선순위:** 환경변수 > `.env` 파일 > `~/.investmate/config.json` > 기본값

| 환경변수                     | 기본값                    | 설명                      |
| ---------------------------- | ------------------------- | ------------------------- |
| INVESTMATE_ENV               | dev                       | 실행 환경 (dev/test/prod) |
| INVESTMATE_DB_PATH           | data/investmate.db        | DB 파일 경로              |
| INVESTMATE_TOP_N             | 10                        | 추천 종목 수              |
| INVESTMATE_AI_ENABLED        | true                      | AI 분석 활성화            |
| INVESTMATE_AI_TIMEOUT        | 300                       | AI 호출 타임아웃 (초)     |
| INVESTMATE_AI_STYLE          | balanced                  | AI 스타일                 |
| INVESTMATE_AI_BACKEND        | auto                      | AI 백엔드 (auto/sdk/cli)  |
| INVESTMATE_AI_MODEL_ANALYSIS | claude-sonnet-4-20250514  | 분석 모델                 |
| INVESTMATE_AI_MODEL_CHAT     | claude-haiku-4-5-20251001 | 채팅 모델                 |
| INVESTMATE_MAX_SECTOR_PCT    | 0.4                       | 섹터 상한 비율            |
| INVESTMATE_TX_COST_BPS       | 20                        | 거래비용 (왕복 bps)       |
| INVESTMATE_RISK_FREE_RATE    | 4.0                       | 무위험 수익률 (연간 %)    |
| INVESTMATE_MIN_DATA_DAYS     | 60                        | 최소 데이터 일수          |
| INVESTMATE_MIN_VOLUME        | 100000                    | 최소 거래량               |

---

## API 키 정책

| 서비스        | 필요 여부    | 비고                                |
| ------------- | ------------ | ----------------------------------- |
| Anthropic API | ❌ 불필요    | Claude Code CLI 인증 사용 (Pro Max) |
| yfinance      | ❌ 키 불필요 | 무료 오픈소스                       |
| 이메일 (SMTP) | 선택         | Gmail 앱 비밀번호                   |
| 텔레그램 Bot  | 선택         | Bot Token + Chat ID                 |
| 슬랙 Webhook  | 선택         | Webhook URL                         |

→ **기본 구성에서는 API 키 없이 모든 핵심 기능이 동작.**
→ AI 분석은 Claude Code CLI 인증 (`~/.claude/.credentials.json`)을 공유.

---

## AWS EC2 배포

**상세 가이드:** [AWS_DEPLOYMENT.md](AWS_DEPLOYMENT.md) (14단계 초보자용)

| 항목       | 구성                               |
| ---------- | ---------------------------------- |
| 인스턴스   | t2.micro (Free Tier)               |
| OS         | Amazon Linux 2023                  |
| 웹서버     | Nginx (리버스 프록시 + basic auth) |
| 앱서버     | FastAPI (systemd: investmate-web)  |
| 배치       | cron (화~토 08:00 KST)             |
| 백업       | S3 (매일 09:00 KST)                |
| 모니터링   | 헬스체크 5분 주기 + 텔레그램 알림  |
| AI         | Claude CLI (인증 정보 복사)        |
| Elastic IP | 고정 IP 할당                       |

---

## 사용 시나리오

### 최초 설정 (1회)

```bash
pip install -e .
investmate db init              # DB 초기화 + S&P 500 시딩
investmate run                  # 최초 수집 + 분석 (2년치, 약 15-30분)
```

### 일상 사용

```bash
investmate run                  # 매일 1회 실행 (또는 cron 자동화)
investmate report latest        # 오늘의 추천 리포트 확인
investmate stock AAPL           # 특정 종목 상세 조회
```

### 웹 대시보드

```bash
investmate web                  # http://localhost:8000 에서 대시보드 접속
```

### 과거 추천 성과 확인

```bash
investmate history recommendations    # 과거 추천의 사후 수익률 확인
investmate ai performance             # AI 예측 정확도 대시보드
investmate backtest run --start 2025-01-01 --end 2025-03-01
```

---

## 비기능 요구사항

- **배치 안정성:** 특정 종목/단계 실패 시 로그 남기고 계속 진행 (resilient pipeline)
- **Graceful Shutdown:** SIGTERM/SIGINT 시그널 핸들러, 안전한 종료
- **Step Checkpointing:** 완료된 단계 스킵, `--force`로 전체 재실행
- **증분 수집:** 마지막 수집일 이후 데이터만 수집 (최초는 2년치)
- **중복 방지:** UPSERT 패턴, Fact 테이블의 UNIQUE 제약. 재실행 시 DELETE 후 재생성
- **리트라이:** Tenacity 지수 백오프 + SimpleCircuitBreaker
- **배치 로깅:** JSON 구조화 로그, 단계별 시작/종료 시간, 처리 레코드 수
- **파일 로깅:** logs/YYYY-MM-DD.log + logs/{date}\_summary.json
- **레이트 리밋:** yfinance 50개 단위, 재무 max_workers=4, 강화 50개 딜레이
- **DB 안전성:** SQLite WAL 모드, batch_write_mode (synchronous OFF + WAL checkpoint)
- **DB 인덱스:** 10개 인덱스 on 8개 테이블 (자동 생성)
- **DB 조회:** `date_id <= X` 범위 조회 (파이프라인 실행일과 거래일 불일치 대응)
- **성능 최적화:** date_map 배치 캐시, batch stock loading (N+1 방지), 매크로 배치
- **T+1 실행가:** 추천 다음 거래일 시가 기준 성과 추적
- **거래비용 차감:** 왕복 20bps (설정 가능)
- **한국어 UI:** 모든 CLI 출력, 도움말, 에러 메시지는 한국어
- **초보자 친화:** "왜 추천하나요?", "숫자로 보면", "주의할 점" 섹션
- **면책 조항:** "투자 참고용이며 투자 권유가 아닙니다"
- **Windows 호환:** UTF-8 인코딩, ASCII 안전 문자
- **환경 분리:** DEV/TEST/PROD Environment enum, `validate_settings()` 필수 설정 검증
- **글로벌 예외 핸들러:** FastAPI 500 에러 안전 처리
- **파라미터 검증:** API 파라미터 ge/le 제약
- **테스트:** in-memory SQLite, Mock 외부 API, 778개 테스트 (64개 파일)
