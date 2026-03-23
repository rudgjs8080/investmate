# CLAUDE.md — investmate: AI 주식 투자 가이드 프로젝트

> 이 문서는 Claude Code의 plan 모드에서 프로젝트 전체를 이해하고
> 스스로 구현 계획을 세울 수 있도록 작성된 프로젝트 명세서입니다.

---

## 프로젝트 개요

S&P 500 전 종목의 데이터를 매일 자동으로 수집하고, 기술적/기본적/외부 요인 분석을 종합하여
매수 적합 종목을 자동으로 선별하고 데일리 리포트를 생성하는 배치 파이프라인.

**핵심 원칙:**

- S&P 500 전 종목(약 500개)을 대상으로 매일 전수 스캔
- 수집 → 분석 → 스크리닝 → 리포트를 하나의 파이프라인으로 한번에 실행
- cron/launchd로 매일 장 마감 후 자동 실행되는 배치 구조
- 데이터를 매일 DB에 축적하여 장기적으로 추적/관리
- AI 분석은 Claude Code CLI (`claude -p`)를 통해 자동 호출 (필수 단계)
- CLI 미설치 시 프롬프트만 저장하고 Claude.ai에 수동 질의 가능

---

## 기술 스택

| 항목          | 선택                     | 비고                                        |
| ------------- | ------------------------ | ------------------------------------------- |
| 언어          | Python 3.11+             |                                             |
| 패키지 매니저 | uv 또는 poetry           |                                             |
| DB            | SQLite (기본)            | 단일 파일, 서버 불필요. SQLAlchemy ORM 사용 |
| ORM           | SQLAlchemy 2.0+          | 동기 세션 사용                              |
| 마이그레이션  | Alembic                  | 스키마 변경 관리                            |
| CLI           | Click                    |                                             |
| 터미널 UI     | Rich                     | 테이블, 차트, 패널, 스피너                  |
| 데이터 처리   | pandas, numpy            |                                             |
| 기술적 분석   | ta (Technical Analysis)  |                                             |
| 스크래핑      | BeautifulSoup4, requests |                                             |
| 주식 데이터   | yfinance                 | 배치 다운로드 지원                          |
| 데이터 검증   | Pydantic v2              |                                             |
| 환경변수      | python-dotenv            |                                             |
| 테스트        | pytest, pytest-cov       |                                             |
| 로깅          | Python logging           | 배치 실행 로그 파일 기록                    |

---

## 프로젝트 구조

```
investmate/
├── CLAUDE.md                    # 이 파일 (프로젝트 명세)
├── pyproject.toml
├── CHANGELOG.md                 # 변경 이력
├── TODO.md                      # 과제 목록
├── METRICS.md                   # 테스트/성과 지표
├── Makefile                     # 개발 명령어
├── src/
│   ├── __init__.py
│   ├── main.py                  # CLI 진입점 (Click) + Windows UTF-8 인코딩 설정
│   ├── config.py                # 설정 관리
│   ├── pipeline.py              # 데일리 파이프라인 오케스트레이터 (STEP 1~6 + 4.5)
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── claude_analyzer.py   # Claude Code CLI 통한 AI 분석 (필수 단계)
│   │   ├── response_schema.py   # AI 응답 구조화 스키마 (frozen dataclasses)
│   │   ├── validator.py         # AI 결과 검증기 (목표가/손절가 일관성)
│   │   ├── feedback.py          # AI 예측 피드백 시스템 (성과 추적 + 자기 교정)
│   │   ├── calibrator.py        # 목표가/손절가 캘리브레이션 (편향 보정)
│   │   ├── cache.py             # AI 응답 캐시 (프롬프트 해시 기반)
│   │   └── data_enricher.py     # yfinance 보강 데이터 (52주, Beta, PEG, FCF)
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py            # SQLAlchemy 엔진, 세션 팩토리
│   │   ├── helpers.py           # date_to_id / id_to_date 유틸리티 (검증 포함)
│   │   ├── migrate.py           # 경량 스키마 마이그레이션 (ALTER TABLE ADD COLUMN)
│   │   ├── models.py            # ORM 모델 (Dimension/Fact/Bridge + 강화 데이터 테이블)
│   │   ├── repository.py        # 데이터 접근 레이어 (범위 조회 + 배치 로드 지원)
│   │   └── seed.py              # 디멘션 초기 데이터 시딩 (S&P 500 종목 포함)
│   ├── data/
│   │   ├── __init__.py
│   │   ├── yahoo_client.py      # yfinance 래퍼 (배치 다운로드)
│   │   ├── enhanced_collector.py # 강화 데이터 수집 (내부자/기관/애널리스트/실적/공매도)
│   │   ├── news_scraper.py      # 뉴스 스크래핑 (yfinance v2 API 대응)
│   │   ├── macro_collector.py   # 매크로 지표 수집 (VIX, 금리, 환율)
│   │   ├── backfill_macro.py    # 매크로 히스토리 백필 (3년치)
│   │   ├── event_collector.py   # 이벤트 캘린더 (FOMC 일정, 실적 발표일)
│   │   ├── kr_names.py          # S&P 500 한글 종목명 매핑 (170+)
│   │   ├── sp500.py             # S&P 500 종목 목록 관리
│   │   └── schemas.py           # Pydantic 스키마
│   ├── analysis/
│   │   ├── __init__.py
│   │   ├── technical.py         # 기술적 분석
│   │   ├── fundamental.py       # 기본적 분석
│   │   ├── external.py          # 외부 요인 분석 (매크로, 뉴스 감성, 섹터 모멘텀)
│   │   ├── signals.py           # 매수/매도 시그널
│   │   ├── screener.py          # 종목 스크리닝 + 랭킹 엔진 (회사 중복 제거 포함)
│   │   ├── performance.py       # 추천 성과 추적 (승률, 평균수익률, 섹터별)
│   │   └── support_resistance.py # 지지/저항 수준 자동 감지
│   ├── reports/
│   │   ├── __init__.py
│   │   ├── report_models.py     # 리포트 데이터 모델 (frozen dataclasses)
│   │   ├── assembler.py         # DB → EnrichedDailyReport 조립 (범위 조회, 시그널 강도우선 중복 제거)
│   │   ├── daily_report.py      # 두괄식 Markdown/JSON 리포트 생성기
│   │   ├── explainer.py         # 초보자 친화 한국어 설명 생성기
│   │   ├── prompt_builder.py    # Claude AI 분석용 프롬프트 생성기 (섹터 분포 포함)
│   │   ├── terminal.py          # Rich 터미널 출력 (두괄식 핵심 요약)
│   │   └── format_utils.py      # 숫자 포맷 유틸리티 (T/B/M/K 변환)
│   ├── backtest/
│   │   ├── __init__.py
│   │   ├── engine.py            # 백테스트 엔진 (승률, 샤프비율, 최대낙폭)
│   │   └── comparator.py        # 가중치 비교 (기본/기술중심/펀더멘털중심/모멘텀중심)
│   ├── ml/
│   │   ├── __init__.py
│   │   ├── scorer.py            # ML 스코어러 (60일 데이터 축적 후 활성화 예정)
│   │   ├── features.py          # 피처 엔지니어링 (TODO)
│   │   ├── trainer.py           # 모델 학습 (TODO)
│   │   ├── evaluator.py         # 모델 평가 (TODO)
│   │   └── registry.py          # 모델 레지스트리 (TODO)
│   ├── alerts/
│   │   ├── __init__.py
│   │   └── notifier.py          # 이메일/텔레그램/슬랙 알림 (구현 완료)
│   └── web/                     # FastAPI 웹 대시보드
│       ├── app.py               # FastAPI 앱 팩토리
│       ├── deps.py              # DB 의존성 주입 (세션, 마이그레이션)
│       ├── routes/              # 라우트 (10개 모듈)
│       │   ├── dashboard.py     # 메인 대시보드 (/)
│       │   ├── recommendations.py # 추천 상세 (/recommendations/{date})
│       │   ├── performance.py   # P&L 추적 (/performance)
│       │   ├── market.py        # 시장 환경 (/market)
│       │   ├── stock.py         # 종목 상세 (/stock/{ticker}) + S/R 감지
│       │   ├── ai_accuracy.py   # AI 정확도 (/ai-accuracy)
│       │   ├── heatmap.py       # S&P 500 히트맵 (/heatmap)
│       │   ├── chat.py          # Claude AI 채팅 API
│       │   ├── api.py           # JSON API (차트 데이터, 8개 엔드포인트)
│       │   └── api_export.py    # CSV 내보내기 API
│       ├── templates/           # Jinja2 + Tailwind CSS 템플릿 (9개)
│       └── static/              # ECharts 헬퍼 (charts.js) + CSS
├── tests/                       # 42 테스트 파일, 408개 테스트
│   ├── conftest.py
│   ├── test_db.py               # DB 레이어 + 범위 조회 fallback 테스트
│   ├── test_pipeline.py
│   ├── test_analysis.py
│   ├── test_screener.py         # 필터, 모멘텀, 외부 점수, 추천 근거 테스트
│   ├── test_screener_scoring.py # 스코어링 함수 단위 테스트 (가중치 검증 포함)
│   ├── test_scoring_integration.py # 스코어링 통합 테스트
│   ├── test_signals.py          # 10종 시그널 + RSI strength 테스트
│   ├── test_explainer.py        # 초보자 설명 + 투자 의견 테스트
│   ├── test_daily_report.py     # 두괄식 리포트 생성 테스트
│   ├── test_prompt_builder.py   # AI 프롬프트 생성 테스트
│   ├── test_assembler.py        # 리포트 조립기 순수 함수 테스트
│   ├── test_notifier.py         # 알림 모듈 테스트
│   ├── test_news_scraper.py     # 뉴스 스크래퍼 mock 테스트
│   ├── test_macro_collector.py  # 매크로 수집 mock 테스트 (배치 1회)
│   ├── test_enhanced_collector.py # 강화 데이터 수집 mock 테스트
│   ├── test_yahoo_client.py     # yfinance 래퍼 테스트 (실패 추적)
│   ├── test_claude_analyzer.py  # AI 분석기 테스트
│   ├── test_external.py         # 달러 인덱스, 매크로 완전성, 단어경계 테스트
│   ├── test_fundamental.py      # 배당수익률, None→3.5 테스트
│   ├── test_backtest.py         # 백테스트 엔진 + 가중치 비교 테스트
│   ├── test_migrate.py          # DB 스키마 마이그레이션 테스트
│   ├── test_date_map.py         # 날짜 매핑 캐시 테스트
│   ├── test_format_utils.py     # 숫자 포맷 유틸리티 테스트
│   ├── test_cli.py              # CLI 명령어 + 엣지 케이스 테스트
│   ├── test_config.py
│   ├── test_sp500.py            # S&P 500 목록 관리 테스트
│   ├── test_ai_validator.py     # AI 검증기 테스트
│   ├── test_ai_feedback.py      # AI 피드백 수집/분석 테스트
│   ├── test_ai_cache.py         # AI 캐시 테스트
│   ├── test_calibrator.py       # AI 캘리브레이션 테스트
│   ├── test_calibrator_integration.py # 캘리브레이션 통합 테스트
│   ├── test_event_collector.py  # FOMC/실적 캘린더 테스트
│   ├── test_data_enricher.py    # 보강 데이터 테스트
│   └── test_response_schema.py  # AI 응답 스키마 테스트
├── scripts/                     # 자동화 스크립트
│   ├── auto_improve.sh          # Claude Code 자율 개선 루프
│   └── run_pipeline.sh          # 파이프라인 실행 래퍼
├── logs/                        # 배치 실행 로그 (gitignore)
├── reports/                     # 생성된 리포트 (구조화)
│   ├── daily/                   # 일별 리포트 (MD + JSON)
│   ├── prompts/                 # AI 분석용 프롬프트
│   └── ai_analysis/             # AI 분석 원문 + 딥다이브
└── data/                        # 런타임 데이터 (gitignore)
    ├── investmate.db            # SQLite DB 파일
    └── ai_cache/                # AI 응답 캐시
```

---

## 핵심 아키텍처: 데일리 배치 파이프라인

### 실행 방식

하나의 명령으로 전체 파이프라인이 순차 실행:

```bash
investmate run
```

이 명령 하나가 아래 6단계를 순서대로 실행한다.

### pipeline.py -- 파이프라인 오케스트레이터

```python
class DailyPipeline:
    def run(self):
        self.step1_collect()      # S&P 500 전 종목 데이터 수집 + 강화 데이터 + 뉴스
        self.step2_analyze()      # 기술적 지표 계산 + 시그널 판단 (중복 방지)
        self.step3_external()     # 외부 요인 분석 + 섹터 모멘텀 계산
        self.step4_screen()       # 스크리닝 + 랭킹 + 추천 종목 뉴스 수집
        self.step4_5_ai_analysis()# Claude Code CLI AI 분석 (선택)
        self.step5_report()       # 두괄식 데일리 리포트 생성
        self.step6_notify()       # 알림 발송 (Telegram/Slack/Email)
```

각 단계는 이전 단계의 DB 데이터를 참조하며, 어떤 단계에서 실패하더라도 로그를 남기고 다음 단계를 계속 진행한다 (resilient pipeline). 재실행 시 기존 추천/시그널을 삭제 후 재생성한다 (중복 방지).

---

### STEP 1 — 데이터 수집

**S&P 500 종목 목록 관리 (sp500.py):**

- Wikipedia 또는 yfinance에서 현재 S&P 500 구성 종목 목록을 가져옴
- dim_stocks에 없는 신규 종목은 자동 추가, 제외된 종목은 is_sp500=False 처리
- 종목 변경 이력도 기록

**배치 수집 (yahoo_client.py):**

- yfinance.download()의 멀티 티커 기능 활용: 500개 종목을 한번에 요청 (**실패 티커 추적**)
- 마지막 수집일 이후 ~ 오늘까지의 일봉 데이터 증분 수집 (**volume=0 데이터 skip**)
- **적시성 검증**: 5개 샘플 종목의 최소 날짜로 판단 (기존: 단일 종목)
- 최초 실행 시 전 종목 최근 2년치 히스토리를 한번에 가져옴
- fact_daily_prices에 UPSERT

**재무 데이터:**

- 분기 실적 시즌에 맞춰 수집 (매 실행 시 확인, 새 분기 데이터 있으면 저장)
- fact_financials에 원본 저장, fact_valuations에 파생 지표 계산 후 저장

**매크로 지표 수집 (macro_collector.py):**

- yfinance **배치 1회 호출**로 5개 매크로 지표 동시 수집:
  - ^VIX: 공포지수
  - ^TNX: 미국 10년 국채 금리
  - ^IRX: 미국 13주 국채 금리
  - DX-Y.NYB: 달러 인덱스
  - ^GSPC: S&P 500 지수
- **수집 완전성 로깅**: 유효 지표 수 < 3이면 경고
- fact_macro_indicators 테이블에 저장

**강화 데이터 수집 (enhanced_collector.py):**

- 내부자 거래 (insider_transactions)
- 기관 보유 현황 (institutional_holders)
- 애널리스트 컨센서스 (recommendations + analyst_price_targets)
- 실적 서프라이즈 (earnings_history)
- 공매도 데이터 (shortRatio, shortPercentOfFloat)
- 503개 종목 순차 수집, 배치 50개 단위 딜레이

**뉴스 수집 (news_scraper.py):**

- yfinance v2 API 구조 대응 (content.canonicalUrl.url, content.provider.displayName)
- **날짜 파싱 실패 기사는 skip** (기존: datetime.now() fallback → 감성 편향 방지)
- STEP 1에서 시장 전체 뉴스 수집 (^GSPC 또는 SPY fallback)
- STEP 4에서 매수 후보로 선정된 종목에 대해서만 추가 뉴스 수집 + 감성 분석

### STEP 2 — 기술적 분석

**전 종목 지표 계산 (technical.py):**

- fact_daily_prices에서 전 종목 데이터를 DataFrame으로 로드
- ta 라이브러리로 일괄 계산:
  - SMA(5, 20, 60, 120), EMA(12, 26)
  - RSI(14)
  - MACD(12, 26, 9)
  - 볼린저밴드(20, 2σ)
  - 스토캐스틱(14, 3, 3)
  - 거래량 이동평균(20)
- 결과를 fact_indicator_values에 저장 (EAV 패턴)

**시그널 판단 (signals.py):**

- 전 종목에 대해 시그널 일괄 판단
- dim_signal_types에 정의된 10종 시그널 감지 (8종 기본 + stoch_bullish/stoch_bearish)
- RSI 임계값 상수화 (RSI_OVERSOLD=30, RSI_OVERBOUGHT=70)
- fact_signals에 기록 (재실행 시 같은 종목/날짜의 기존 시그널 삭제 후 재생성)

### STEP 3 — 외부 요인 분석

**매크로 환경 점수 (external.py):**

- VIX 수준: <15 강세(+2), <20 안정(+1), >25 주의(-1), >30 위험(-2)
- 금리 추이: >5% 고금리(-1), <3% 저금리(+1)
- 달러 인덱스: >105 강달러(-1), <95 약달러(+1)
- S&P 500 추세: 20일선 위(+1), 아래(-1)
- **완전성 검증**: 유효 지표 3개 미만 → 중립(5) 반환
- → 시장 환경 종합 점수 산출 (1-10), fact_macro_indicators.market_score에 저장

**뉴스 감성 분석:**

- 키워드 기반 감성 분석 (**단어 경계 `\b` 매칭**, false positive 방지)
- 시장 전체 뉴스 감성 점수 산출

**섹터 모멘텀 분석:**

- dim_sectors 기준으로 섹터별 최근 20일 평균 수익률 계산
- `calculate_sector_momentum()`으로 1-10 정규화 점수 산출 (**플랫 마켓 감쇄**: spread<1% → 5.0 방향 압축)
- `_sector_momentum` 인스턴스 변수로 STEP 4에 전달
- 실제 반영 예: Energy 10.0, Utilities 6.4, IT 5.5, Consumer Staples 1.0

### STEP 4 — 스크리닝 + 랭킹

**screener.py — 핵심 엔진:**

1단계 — 필터링 (500개 → 약 30-50개):

- 기본 필터: 최소 거래량 충족, 데이터 충분 여부
- 기술적 필터: RSI < 70 (과매수 제외), 가격이 SMA120 위 (장기 상승 추세)
- 기본적 필터: PER > 0 (적자 기업 제외), 부채비율 적정

2단계 -- 스코어링 (필터 통과 종목에 점수 부여):

| 항목               | 비중 | 점수 기준                                                                                          |
| ------------------ | ---- | -------------------------------------------------------------------------------------------------- |
| 기술적 시그널 점수 | 25%  | 매수 시그널 수/강도, RSI 위치, MACD 상태, **스토캐스틱 K/D**                                       |
| 기본적 분석 점수   | 25%  | PER, PBR, ROE, 부채비율, 성장률, **배당수익률(10%)**, **실적 서프라이즈(±1.0)**                    |
| 수급/스마트머니    | 15%  | 내부자 매수(**시간감쇠**, **상한 +3.0**), 애널리스트(**균형보정 ±1.5**), **공매도**, **기관 보유** |
| 외부 요인 점수     | 15%  | VIX, S&P 500, 금리, **달러 인덱스**, 섹터 모멘텀(**플랫 감쇄**), **매크로 완전성 검증**            |
| 가격 모멘텀 점수   | 20%  | **선형 보간 수익률** (5%당 1.0), 이동평균 배열, 거래량 추세                                        |

추가 필터:

- 애널리스트 목표가가 현재가 대비 -15% 이하인 종목은 스마트머니 점수 -1.5 감점 (균형 보정)
- SMA120 필터 완화: -5% 이내 + RSI<40이면 통과 (회복 초기 종목 포용)
- 데이터 누락 시 중립(5.0) 대신 약한 감점(3.5) 적용

3단계 -- 랭킹 (종합 점수 기준 상위 N개 선정):

- 기본값: 상위 10개 종목
- 설정에서 N 변경 가능
- **회사명 기반 중복 제거**: GOOGL/GOOG, BRK-A/BRK-B 같은 dual-class 주식은 상위 1개만 선정
- 각 종목에 대한 추천 근거(왜 이 종목인지) 자동 생성 (구체적 숫자 포함)
- 선정된 종목에 대해 추가 뉴스 수집 + 감성 분석
- 재실행 시 같은 날짜의 기존 추천을 삭제 후 재생성 (중복 방지)
- 결과를 fact_daily_recommendations에 저장

### STEP 4.5 -- AI 분석 (필수)

**claude_analyzer.py + validator.py + response_schema.py:**

- Claude Code CLI (`claude -p`) 비대화형 모드로 자동 호출 (필수 단계)
- CLI 미설치 시: 프롬프트만 저장하고 `ai_approved=None` (미실행) 표시
- **프롬프트 고도화**: 시장 체제 분류, 신뢰도/리스크 레벨, 매매 전략 요청
- **구조화 JSON 스키마**: confidence(1-10), risk_level(LOW/MEDIUM/HIGH), entry/exit_strategy
- **ensure_all_tickers_reviewed**: AI가 언급하지 않은 종목도 기본 승인 (benefit of the doubt)
- **AI 검증기**: 목표가/손절가 일관성 자동 보정, 신뢰도-승인 일관성 경고
- 결과를 fact_daily_recommendations에 업데이트:
  - `ai_approved` (True/False/None=미실행)
  - `ai_reason`, `ai_target_price`, `ai_stop_loss`
  - `ai_confidence`(1-10), `ai_risk_level`, `ai_entry_strategy`, `ai_exit_strategy`
- **AI 피드백 루프**: `FactAIFeedback` 테이블에 예측 vs 실제 결과 추적
  - 방향 정확도, 목표가 오차, 섹터별 승률, 신뢰도별 교정 곡선
  - 프롬프트에 과거 성과 자동 주입 (자기 교정)
- **목표가 캘리브레이션**: `calibrator.py` — 과대/과소추정 편향 자동 보정
- **멀티 라운드 분석**: Round 1(스크리닝) → Round 2(딥다이브, 추천 3종목+)
  - 딥다이브: 3단계 분할 매수, 시나리오 분석, 포트폴리오 배분
- **외부 이벤트**: FOMC 일정 + 실적 발표일 캘린더 프롬프트 반영
- **보강 데이터**: 52주 고저, Beta, 선행 PER, PEG, FCF, 목표가 범위
- **AI 캐시**: 동일 프롬프트 해시 → 재분석 스킵 (data/ai_cache/)
- **AI 스타일**: aggressive/balanced/conservative 프롬프트 분기
- **적응형 프롬프트**: 과거 승률/섹터 성과 기반 자동 지시 조정
- **시나리오 분석**: Best/Base/Worst case + 상관관계 경고
- 설정: `INVESTMATE_AI_ENABLED`, `INVESTMATE_AI_TIMEOUT`, `INVESTMATE_AI_STYLE`
- CLI: `investmate ai performance` — AI 성과 대시보드

### STEP 5 -- 데일리 리포트 생성

**daily_report.py + explainer.py + assembler.py:**

리포트 형식: **두괄식** (핵심 먼저, 상세는 나중에)

1. **핵심 요약 (30초 브리핑)**:
   - 시장 분위기 한 줄 요약
   - 오늘의 추천 종목 (종목별 한줄 이유)
   - 투자 의견 (시장 점수 기반 구체적 행동 제안)
   - 섹터 분포 (Energy 3개, IT 3개 등)
2. **추천 종목 카드** (각 종목):
   - 한줄 요약 헤드라인
   - "왜 추천하나요?" (초보자도 이해 가능한 쉬운 한국어 설명)
   - "숫자로 보면" (핵심 지표 한 줄)
   - "주의할 점" (리스크 쉬운 설명)
   - `<details>` 접이식 상세 (기술적/기본적(**배당수익률 포함**)/수급(**기관보유 상위3**)/실적/뉴스)
3. **시장 환경 상세**: VIX, S&P 500, 금리, 달러 테이블
4. **전체 시그널 목록**: 매수/매도 시그널 (한국어 번역)
5. **면책 조항**

**초보자 친화 설명 (explainer.py):**

- `explain_stock()`: 기술적/기본적 지표를 쉬운 한국어로 변환
  - RSI 31 -> "최근 많이 하락해서 반등 가능성이 높아요"
  - PER 14 -> "기업 이익 대비 주가가 저렴한 편이에요"
  - 골든크로스 -> "단기선이 장기선을 돌파, 상승 전환 신호"
- `market_investment_opinion()`: 시장 점수 기반 행동 제안
  - 강세: "3-5개 분할 매수" / 약세: "현금 비중 높이고 과매도 반등 노려라"
- `summarize_market()`: 시장 분위기 한 줄 요약

**시그널 한국어 번역:**

- rsi_overbought -> "RSI 과매수"
- macd_bullish -> "MACD 매수 전환"
- bb_lower_break -> "볼린저 하단 돌파"
- golden_cross -> "골든크로스"
- stoch_bullish -> "스토캐스틱 매수 전환"
- stoch_bearish -> "스토캐스틱 매도 전환"

**출력 형식:**

- 터미널: Rich 패널 (핵심 요약 Panel -> 시장 환경 -> TOP N 테이블 -> 종목 카드)
- Markdown: reports/YYYY-MM-DD.md 자동 저장 (두괄식)
- JSON: reports/YYYY-MM-DD.json 자동 저장 (Claude.ai 질의용)
- 프롬프트: reports/YYYY-MM-DD_prompt.txt (Claude AI 분석용)
- AI 분석: reports/YYYY-MM-DD_ai_analysis.md (AI 원문 응답)
- AI 딥다이브: reports/YYYY-MM-DD_ai_deep_dive.md (Round 2 심층 분석)

### STEP 6 -- 알림 발송 (선택)

**notifier.py:**

- 설정 파일에 알림 채널이 설정된 경우에만 실행
- `send_daily_summary(run_date, market_mood, top_tickers, market_score, channel, buy_signal_count, sell_signal_count, vix)`
- 지원 채널:
  - 이메일: smtplib (Gmail SMTP) - `INVESTMATE_SMTP_USER`, `INVESTMATE_SMTP_PASS`, `INVESTMATE_EMAIL_TO`
  - 텔레그램: Bot API - `INVESTMATE_TELEGRAM_TOKEN`, `INVESTMATE_TELEGRAM_CHAT_ID`
  - 슬랙: Webhook - `INVESTMATE_SLACK_WEBHOOK`
- 데일리 리포트 요약 (시장 분위기 + 추천 TOP 5 티커)을 알림으로 발송

---

## CLI 명령어

```
investmate
├── run                          # 데일리 파이프라인 전체 실행 (핵심 명령)
│   ├── --date YYYY-MM-DD        # 특정 날짜 기준으로 실행 (기본: 오늘)
│   ├── --top N                  # 추천 종목 수 (기본: 10)
│   ├── --skip-notify            # 알림 발송 스킵
│   └── --step 1-6               # 특정 단계만 실행 (디버깅용)
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
│   └── compare-weights          # 가중치 비교 (기본/기술/펀더멘털/모멘텀)
│
├── ai                           # AI 분석 관리
│   ├── latest                   # 가장 최근 AI 분석 결과
│   ├── show YYYY-MM-DD          # 특정 날짜 AI 분석 조회
│   ├── rerun                    # AI 분석만 재실행 (프롬프트 재사용 + DB 반영)
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

**가장 중요한 명령: `investmate run`**

- 이것 하나만 매일 실행하면 수집 → 분석 → 스크리닝 → 리포트가 전부 완료됨
- cron에 등록하면 완전 자동화

---

## 배치 스케줄링

cron (Linux/Mac) 등록 예시:

```bash
# 매일 미국 장 마감 후 (EST 16:30 = KST 06:30) 실행
30 6 * * 1-5 cd /path/to/investmate && investmate run >> logs/cron.log 2>&1
```

**배치 실행 시 고려사항:**

- 전 종목 수집 + 분석에 예상 소요 시간: 약 5-15분
- yfinance 배치 다운로드 시 500개 종목을 50개씩 나눠서 요청 (레이트 리밋 방지)
- 각 단계별 소요 시간을 fact_collection_logs에 기록
- 실패 시 로그에 에러 기록 후 다음 단계 계속 진행
- 로그 파일: logs/YYYY-MM-DD.log

---

## DB 스키마 설계

Star Schema 기반 설계. Fact/Dimension 테이블을 명확히 분리하여 확장성 확보.
SQLAlchemy ORM 모델로 구현. 모든 테이블에 created_at, updated_at 자동 관리.

### 설계 원칙

- **Fact 테이블**: 측정 가능한 이벤트/수치 데이터 (가격, 지표, 시그널, 뉴스, 매크로, 추천, 수집로그)
- **Dimension 테이블**: 분석 축이 되는 속성 데이터 (종목, 시장, 섹터, 날짜, 지표유형, 시그널유형)
- **Bridge 테이블**: 다대다 관계 해소 (뉴스 ↔ 종목)
- **EAV 패턴**: 기술적 지표를 컬럼이 아닌 행으로 저장 → 새 지표 추가 시 스키마 변경 불필요
- **원본/파생 분리**: 원본 재무데이터(fact_financials)와 계산된 밸류에이션(fact_valuations)을 분리

---

### Dimension 테이블

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

#### dim_markets (시장 구분)

| 컬럼          | 타입             | 설명             |
| ------------- | ---------------- | ---------------- |
| market_id     | Integer, PK      |                  |
| code          | String, unique   | US               |
| name          | String           | 미국             |
| currency      | String           | USD              |
| timezone      | String           | America/New_York |
| trading_hours | String, nullable | 09:30-16:00      |

#### dim_sectors (섹터/산업 분류 — GICS 기반)

| 컬럼           | 타입             | 설명                 |
| -------------- | ---------------- | -------------------- |
| sector_id      | Integer, PK      |                      |
| sector_name    | String           | Technology           |
| industry_group | String, nullable | Software & Services  |
| industry       | String, nullable | Application Software |

#### dim_date (날짜 디멘션)

| 컬럼           | 타입             | 설명             |
| -------------- | ---------------- | ---------------- |
| date_id        | Integer, PK      | YYYYMMDD 형식    |
| date           | Date, unique     |                  |
| year           | Integer          |                  |
| quarter        | Integer          | 1~4              |
| month          | Integer          | 1~12             |
| week_of_year   | Integer          |                  |
| day_of_week    | Integer          | 0=월 ~ 6=일      |
| is_trading_day | Boolean          | NYSE 거래일 여부 |
| fiscal_quarter | String, nullable | 2025Q1           |

#### dim_indicator_types (기술적 지표 정의)

| 컬럼              | 타입           | 설명                          |
| ----------------- | -------------- | ----------------------------- |
| indicator_type_id | Integer, PK    |                               |
| code              | String, unique | SMA_20, RSI_14, MACD          |
| name              | String         | 지표 한글명                   |
| category          | String         | trend / momentum / volatility |
| params            | JSON, nullable | {"period": 20}                |
| description       | Text, nullable |                               |

#### dim_signal_types (시그널 종류 정의)

| 컬럼           | 타입           | 설명                       |
| -------------- | -------------- | -------------------------- |
| signal_type_id | Integer, PK    |                            |
| code           | String, unique | golden_cross, rsi_oversold |
| name           | String         | 골든크로스                 |
| direction      | String         | BUY / SELL / HOLD          |
| default_weight | Float          | 복합 스코어 가중치         |
| description    | Text, nullable |                            |

---

### Fact 테이블

#### fact_daily_prices (일봉 데이터)

| 컬럼      | 타입                     | 설명      |
| --------- | ------------------------ | --------- |
| price_id  | Integer, PK              |           |
| stock_id  | Integer, FK → dim_stocks |           |
| date_id   | Integer, FK → dim_date   |           |
| open      | Decimal                  | 시가      |
| high      | Decimal                  | 고가      |
| low       | Decimal                  | 저가      |
| close     | Decimal                  | 종가      |
| adj_close | Decimal                  | 수정 종가 |
| volume    | BigInteger               | 거래량    |

**UNIQUE(stock_id, date_id)**

#### fact_indicator_values (기술적 지표 — EAV)

| 컬럼               | 타입                              | 설명    |
| ------------------ | --------------------------------- | ------- |
| indicator_value_id | Integer, PK                       |         |
| stock_id           | Integer, FK → dim_stocks          |         |
| date_id            | Integer, FK → dim_date            |         |
| indicator_type_id  | Integer, FK → dim_indicator_types |         |
| value              | Decimal                           | 지표 값 |

**UNIQUE(stock_id, date_id, indicator_type_id)**

#### fact_financials (원본 재무제표)

| 컬럼               | 타입                     | 설명         |
| ------------------ | ------------------------ | ------------ |
| financial_id       | Integer, PK              |              |
| stock_id           | Integer, FK → dim_stocks |              |
| period             | String                   | 2025Q1       |
| revenue            | Decimal, nullable        | 매출         |
| operating_income   | Decimal, nullable        | 영업이익     |
| net_income         | Decimal, nullable        | 순이익       |
| total_assets       | Decimal, nullable        | 총자산       |
| total_liabilities  | Decimal, nullable        | 총부채       |
| total_equity       | Decimal, nullable        | 자기자본     |
| operating_cashflow | Decimal, nullable        | 영업현금흐름 |

**UNIQUE(stock_id, period)**

#### fact_valuations (파생 밸류에이션)

| 컬럼           | 타입                     | 설명       |
| -------------- | ------------------------ | ---------- |
| valuation_id   | Integer, PK              |            |
| stock_id       | Integer, FK → dim_stocks |            |
| date_id        | Integer, FK → dim_date   |            |
| market_cap     | Decimal, nullable        | 시가총액   |
| per            | Decimal, nullable        |            |
| pbr            | Decimal, nullable        |            |
| roe            | Decimal, nullable        |            |
| debt_ratio     | Decimal, nullable        | 부채비율   |
| dividend_yield | Decimal, nullable        | 배당수익률 |
| ev_ebitda      | Decimal, nullable        |            |

**UNIQUE(stock_id, date_id)**

#### fact_signals (시그널 이력)

| 컬럼           | 타입                           | 설명      |
| -------------- | ------------------------------ | --------- |
| signal_id      | Integer, PK                    |           |
| stock_id       | Integer, FK → dim_stocks       |           |
| date_id        | Integer, FK → dim_date         |           |
| signal_type_id | Integer, FK → dim_signal_types |           |
| strength       | Integer                        | 1-10 강도 |
| description    | Text, nullable                 | 상세 설명 |

#### fact_macro_indicators (매크로 지표)

| 컬럼         | 타입                   | 설명                       |
| ------------ | ---------------------- | -------------------------- |
| macro_id     | Integer, PK            |                            |
| date_id      | Integer, FK → dim_date |                            |
| vix          | Decimal, nullable      | 공포지수                   |
| us_10y_yield | Decimal, nullable      | 미국 10년 국채 금리        |
| us_13w_yield | Decimal, nullable      | 미국 13주 국채 금리        |
| dollar_index | Decimal, nullable      | 달러 인덱스                |
| sp500_close  | Decimal, nullable      | S&P 500 종가               |
| sp500_sma20  | Decimal, nullable      | S&P 500 20일 이동평균      |
| market_score | Integer, nullable      | 시장 환경 종합 점수 (1-10) |

**UNIQUE(date_id)**

#### fact_daily_recommendations (데일리 추천 결과)

파이프라인 실행 결과. 사후 수익률 추적에 활용.
| 컬럼 | 타입 | 설명 |
|------|------|------|
| recommendation_id | Integer, PK | |
| run_date_id | Integer, FK -> dim_date | 파이프라인 실행일 |
| stock_id | Integer, FK -> dim_stocks | |
| rank | Integer | 추천 순위 (1=최상위) |
| total_score | Decimal | 종합 점수 |
| technical_score | Decimal | 기술적 분석 점수 |
| fundamental_score | Decimal | 기본적 분석 점수 |
| smart_money_score | Decimal | 수급/스마트머니 점수 |
| external_score | Decimal | 외부 요인 점수 |
| momentum_score | Decimal | 가격 모멘텀 점수 |
| recommendation_reason | Text | 추천 근거 요약 |
| price_at_recommendation | Decimal | 추천 시점 종가 |
| return_1d | Decimal, nullable | 1일 후 수익률 (사후 업데이트) |
| return_5d | Decimal, nullable | 5일 후 수익률 (사후 업데이트) |
| return_20d | Decimal, nullable | 20일 후 수익률 (사후 업데이트) |
| ai_approved | Boolean, nullable | AI 분석 승인 여부 |
| ai_reason | Text, nullable | AI 분석 근거 |
| ai_target_price | Decimal, nullable | AI 제시 목표가 |
| ai_stop_loss | Decimal, nullable | AI 제시 손절가 |
| ai_confidence | Integer, nullable | AI 신뢰도 (1-10) |
| ai_risk_level | String, nullable | AI 리스크 (LOW/MEDIUM/HIGH) |
| ai_entry_strategy | Text, nullable | AI 매수 전략 |
| ai_exit_strategy | Text, nullable | AI 익절/손절 전략 |

#### fact_ai_feedback (AI 예측 피드백)

| 컬럼              | 타입              | 설명                |
| ----------------- | ----------------- | ------------------- |
| feedback_id       | Integer, PK       |                     |
| recommendation_id | Integer, FK       | 원본 추천 참조      |
| ticker            | String            |                     |
| sector            | String, nullable  |                     |
| ai_approved       | Boolean, nullable | AI 판단             |
| ai_confidence     | Integer, nullable | AI 신뢰도           |
| ai_target_price   | Decimal, nullable | AI 목표가           |
| price_at_rec      | Decimal, nullable | 추천 시점 가격      |
| actual_price_20d  | Decimal, nullable | 20일 후 실제 가격   |
| return_20d        | Decimal, nullable | 20일 실제 수익률    |
| direction_correct | Boolean, nullable | 방향 예측 정확 여부 |
| target_hit        | Boolean, nullable | 목표가 도달 여부    |
| target_error_pct  | Decimal, nullable | 목표가 오차 %       |

#### 강화 데이터 Fact 테이블

**fact_insider_trades** -- 내부자 거래

- stock_id, date_id, insider_name, insider_title, transaction_type, shares, value

**fact_institutional_holdings** -- 기관 보유

- stock_id, date_id, institution_name, shares, value, pct_of_shares

**fact_analyst_consensus** -- 애널리스트 컨센서스

- stock_id, date_id, strong_buy, buy, hold, sell, strong_sell, target_mean/high/low/median

**fact_earnings_surprise** -- 실적 서프라이즈

- stock_id, date_id, period, eps_estimate, eps_actual, surprise_pct

#### fact_news (뉴스)

| 컬럼            | 타입              | 설명       |
| --------------- | ----------------- | ---------- |
| news_id         | Integer, PK       |            |
| title           | String            |            |
| summary         | Text, nullable    |            |
| url             | String, unique    |            |
| source          | String            |            |
| published_at    | DateTime          |            |
| sentiment_score | Decimal, nullable | -1.0 ~ 1.0 |

#### fact_collection_logs (파이프라인 실행 이력)

| 컬럼          | 타입                   | 설명                            |
| ------------- | ---------------------- | ------------------------------- |
| log_id        | Integer, PK            |                                 |
| run_date_id   | Integer, FK → dim_date |                                 |
| step          | String                 | step1_collect, step2_analyze 등 |
| status        | String                 | success, failed, skipped        |
| started_at    | DateTime               | 시작 시각                       |
| finished_at   | DateTime, nullable     | 완료 시각                       |
| records_count | Integer, default=0     | 처리된 레코드 수                |
| message       | Text, nullable         | 에러 메시지 등                  |

---

### Bridge 테이블

#### bridge_news_stock (뉴스 ↔ 종목 다대다)

| 컬럼      | 타입                         | 설명       |
| --------- | ---------------------------- | ---------- |
| news_id   | Integer, FK → fact_news, PK  |            |
| stock_id  | Integer, FK → dim_stocks, PK |            |
| relevance | Decimal, nullable            | 관련도 0~1 |

---

### 초기 디멘션 데이터 (seed.py)

DB 초기화(db init) 시 자동 시딩:

**dim_markets:** {code: "US", name: "미국", currency: "USD", timezone: "America/New_York"}

**dim_stocks + dim_sectors:** Wikipedia/yfinance에서 S&P 500 전 종목 + GICS 섹터 정보를 가져와 시딩. is_sp500=True로 마킹.

**dim_indicator_types:** SMA_5, SMA_20, SMA_60, SMA_120, EMA_12, EMA_26, RSI_14, MACD, MACD_SIGNAL, MACD_HIST, BB_UPPER, BB_MIDDLE, BB_LOWER, STOCH_K, STOCH_D, VOLUME_SMA_20

**dim_signal_types:** golden_cross(BUY, 0.8), death_cross(SELL, 0.8), rsi_oversold(BUY, 0.6), rsi_overbought(SELL, 0.6), macd_bullish(BUY, 0.7), macd_bearish(SELL, 0.7), bb_lower_break(BUY, 0.5), bb_upper_break(SELL, 0.5), stoch_bullish(BUY, 0.5), stoch_bearish(SELL, 0.5)

**dim_date:** 2015-01-01 ~ 2030-12-31 날짜 행 사전 생성 (NYSE 캘린더 반영)

---

## API 키 정책

| 서비스        | 필요 여부    | 비고                                               |
| ------------- | ------------ | -------------------------------------------------- |
| Anthropic API | ❌ 불필요    | Claude.ai Pro Max 사용. JSON → Claude.ai 수동 질의 |
| yfinance      | ❌ 키 불필요 | 무료 오픈소스                                      |
| 이메일 (SMTP) | 선택         | Gmail 앱 비밀번호                                  |
| 텔레그램 Bot  | 선택         | Bot Token + Chat ID                                |
| 슬랙 Webhook  | 선택         | Webhook URL                                        |

→ **기본 구성에서는 API 키 없이 모든 핵심 기능이 동작.**

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
investmate run                  # 매일 1회 실행
investmate report latest        # 오늘의 추천 리포트 확인
investmate stock AAPL           # 특정 종목 상세 조회
```

### 자동화 (cron 등록)

```bash
crontab -e
30 6 * * 1-5 cd /path/to/investmate && investmate run >> logs/cron.log 2>&1
```

### Claude.ai AI 분석

```bash
cat reports/2025-03-16.json | pbcopy   # macOS 클립보드 복사
# → Claude.ai에 붙여넣고 "매수 추천 종목의 투자 분석을 해줘" 요청
```

### 과거 추천 성과 확인

```bash
investmate history recommendations    # 과거 추천의 사후 수익률 확인
```

---

## 비기능 요구사항

- **배치 안정성:** 특정 종목/단계 실패 시 로그 남기고 계속 진행 (resilient pipeline)
- **증분 수집:** 마지막 수집일 이후 데이터만 수집 (최초는 2년치)
- **중복 방지:** UPSERT 패턴, Fact 테이블의 UNIQUE 제약. 재실행 시 시그널/추천 DELETE 후 재생성
- **배치 로깅:** 단계별 시작/종료 시간, 처리 레코드 수를 fact_collection_logs에 기록
- **파일 로깅:** logs/YYYY-MM-DD.log에 상세 실행 로그
- **레이트 리밋:** yfinance 배치 요청 시 50개 단위 분할, 재무 수집 max_workers=4, 강화 수집 배치 50개 딜레이
- **DB 안전성:** SQLite WAL 모드, 트랜잭션 단위 커밋
- **DB 조회:** 지표/시그널 조회 시 `date_id <= X` 범위 조회 (파이프라인 실행일과 거래일 불일치 대응)
- **디멘션 시딩:** DB 초기화 시 S&P 500 종목 + 지표/시그널 정의 + 날짜 자동 시딩
- **추천 추적:** fact_daily_recommendations에 사후 수익률(1d/5d/10d/20d) 업데이트
- **한국어 UI:** 모든 CLI 출력, 도움말, 에러 메시지는 한국어. 시그널 코드도 한국어 번역
- **초보자 친화:** 리포트에 "왜 추천하나요?", "숫자로 보면", "주의할 점" 섹션 포함
- **면책 조항:** 리포트에 항상 "투자 참고용이며 투자 권유가 아닙니다" 포함
- **설정 파일:** ~/.investmate/config.json (추천 종목 수, 알림 설정, DB 경로 등)
- **Windows 호환:** main.py에서 `sys.stdout.reconfigure(encoding='utf-8')`, 터미널에 ASCII 안전 문자 사용
- **리포트 품질 검증:** `_log_report_quality()` 함수로 RSI/MACD/PER 채움률, 시그널/뉴스 건수 로깅
- **자동 스키마 마이그레이션:** `ensure_schema(engine)`으로 ORM 모델과 DB 차이 자동 보정
- **성능 최적화:** date_map 배치 캐시 (500→1 쿼리), ValuationRepository.get_latest_all (500→1), 매크로 배치 (5→1 API)
- **설정 외부화:** `screener_min_data_days`, `screener_min_volume` 환경변수로 조정 가능
- **테스트:** in-memory SQLite, Mock 외부 API, 커버리지 65%+ (현재 408개 테스트, 69%)
