# Changelog

## [Unreleased]

### AI 예측 정교화 2시간 루프 (2026-03-21)
- `src/db/models.py`: `FactAIFeedback` 테이블 추가 (예측 vs 실제 추적)
- `src/ai/feedback.py` 생성: AI 피드백 수집 + 성과 분석 (승률/방향정확도/섹터별/신뢰도별)
- `src/ai/calibrator.py` 생성: 목표가/손절가 캘리브레이션 (과대추정 자동 보정)
- `src/data/event_collector.py` 생성: FOMC 일정 + 실적 캘린더 수집
- `src/ai/data_enricher.py` 확장: 목표가 범위, 컨센서스 평균, 공매도 모멘텀
- `src/reports/prompt_builder.py` 대폭 강화:
  - AI 피드백 섹션 (과거 성과 + 적응형 지시)
  - FOMC 일정 + 실적 발표일 반영
  - 보강 데이터 (52주 고저, Beta, 선행PER, PEG, FCF, 목표가 범위)
  - 시나리오 분석 종목별 + 상관관계 경고
  - 딥다이브 프롬프트 강화 (3단계 매수, 시나리오, 포트폴리오 배분)
  - 적응형 지시 (약점/강점 섹터, 목표가 편향 교정)
- `src/pipeline.py`: 멀티 라운드 AI (Round 1 스크리닝 → Round 2 딥다이브), 캘리브레이션 통합, 피드백 자동 수집
- `src/main.py`: `investmate ai performance` CLI 명령어 추가
- 테스트 16개 추가: 피드백 4, 캘리브레이터 5+3, 이벤트 4
- **최종: 401 tests**

### Cycle O-R: AI 캐시 + 프롬프트 시나리오 + 최종 (2026-03-20)
- `src/ai/cache.py` 생성: 프롬프트 해시 기반 AI 응답 캐시 (동일 분석 재실행 방지)
- `src/pipeline.py`: AI 캐시 통합 (캐시 히트 시 CLI 호출 스킵)
- `src/reports/prompt_builder.py`: 시나리오 분석 요청 (Best/Base/Worst case), 과거 성과 피드백 구조
- `src/reports/daily_report.py`: 핵심 요약에 AI 고신뢰 추천 하이라이트
- `tests/test_ai_cache.py` 생성: 캐시 키/저장/로드 테스트 6개
- `tests/test_response_schema.py` 생성: 응답 스키마 frozen dataclass 테스트 5개
- **최종: 385 tests, 71% coverage**

### Cycle G-N: AI 분석 심화 고도화 (2026-03-20)
- `src/reports/terminal.py`: AI 분석 결과 패널 추가 (추천/제외/미실행 + 매매전략)
- `src/reports/daily_report.py`: AI 포트폴리오 요약 섹션, 핵심 요약 AI 통계, 요약 테이블 AI 컬럼
- `src/reports/prompt_builder.py`: 역할 강화 (CFA 포트폴리오 매니저), 컴팩트 데이터 카드, 스타일별 지시(공격/균형/보수), 딥다이브 프롬프트 함수
- `src/main.py`: `ai rerun` 명령어 강화 (파싱+DB 업데이트+검증+미언급 기본 승인)
- `src/reports/explainer.py`: AI 리스크 평가 반영 (제외 경고, HIGH 리스크 경고, 낮은 신뢰도)
- `tests/test_claude_analyzer.py`: 확장 스키마 3개 + 신뢰도 클램핑 테스트
- `tests/test_explainer.py`: AI 리스크 설명 테스트 2개
- **최종: 374 tests, 70% coverage**

### Cycle A-F: STEP 4.5 AI 분석 초고도화 (2026-03-20)
- `src/db/models.py`: `ai_approved` nullable 변경 + `ai_confidence`, `ai_risk_level`, `ai_entry_strategy`, `ai_exit_strategy` 컬럼 추가
- `src/pipeline.py`: STEP 4.5 필수화, CLI 미설치 시 프롬프트 저장 + None 유지, ensure_all_tickers, AI 검증기 통합
- `src/reports/daily_report.py`: AI 3-상태 표시 (미실행/추천/제외), 신뢰도/리스크/매매전략 표시
- `src/reports/report_models.py`: AI 확장 필드 4개 추가
- `src/reports/assembler.py`: 새 AI 필드 DB→리포트 모델 전달
- `src/ai/response_schema.py` 생성: AI 응답 구조화 스키마 (AIStockAnalysis, AIPortfolioSummary)
- `src/ai/validator.py` 생성: 목표가/손절가 일관성 자동 보정, 신뢰도-승인 일관성 검증
- `src/ai/claude_analyzer.py`: JSON 파싱에 confidence/risk_level/entry/exit 추출 추가
- `src/reports/prompt_builder.py`: 시장 체제 분류, 신뢰도/리스크/매매전략 요청, 포트폴리오 분석 포함
- `src/config.py`: `ai_enabled`, `ai_timeout`, `ai_style` 설정 추가
- `tests/test_ai_validator.py` 생성: 검증기 테스트 9개
- **최종: 369 tests**

### Cycle 23: 최종 폴리시 (2026-03-20)
- `pyproject.toml`: fail_under 60 → 65 상향
- `METRICS.md`: 최종 수치 업데이트 (360 tests, 72.3%, fail_under=65)
- **2차 5루프 최종: 360 tests, 72.3% coverage**

### Cycle 22: 가중치 검증 + 엣지 케이스 테스트 (2026-03-20)
- `tests/test_screener_scoring.py`: 스크리너/펀더멘털 가중치 합계 1.0 검증 테스트
- `tests/test_external.py`: 섹터 모멘텀 동일 수익률, 플랫 마켓 감쇄 테스트

### Cycle 21: 프롬프트 품질 + helpers 검증 + CLI 테스트 (2026-03-20)
- `src/reports/prompt_builder.py`: AI 프롬프트에 섹터 분포 테이블 추가
- `src/db/helpers.py`: id_to_date에 month/day 범위 검증 추가
- `tests/test_cli.py`: CLI 엣지 케이스 테스트 4개 추가 (invalid date, backtest help 등)

### Cycle 20: FactValuation 배치 로드 (2026-03-20)
- `src/db/repository.py`: `ValuationRepository.get_latest_all()` 배치 메서드 추가
- `src/analysis/screener.py`: _passes_fundamental_filter에 val_map 캐시 전달
- 효과: 스크리너 FactValuation 쿼리 500회 → 1회

### Cycle 19: 테스트 갭 해소 + 터미널 배당수익률 (2026-03-20)
- `tests/test_external.py`: 달러 인덱스 3개 + 매크로 불충분 1개 + 단어경계 1개 테스트 추가
- `tests/test_fundamental.py`: _score_dividend_yield 범위 테스트 추가
- `src/reports/terminal.py`: 기본적 분석 테이블에 배당수익률 표시

### Cycle 18: 리포트 데이터 완성도 + 설정 확장 (2026-03-20)
- `src/reports/daily_report.py`: 배당수익률 + 기관 보유 상위 3개 표시 추가
- `src/reports/exporter.py` 삭제: 미사용 코드 (import 대상 모듈 미존재)
- `src/config.py`: screener_min_data_days, screener_min_volume 설정 추가
- `src/analysis/screener.py`: 필터 임계값 config에서 로드

### Cycle 17: pipeline date_map 캐시 전면 적용 (2026-03-20)
- `src/pipeline.py`: step2, step3, step4(수익률) 모두 load_date_map 캐시 적용
- 효과: 파이프라인 전체 ~1500회 개별 날짜 쿼리 → 3회 배치 쿼리

### Cycle 16: 최종 폴리시 (2026-03-20)
- `tests/test_format_utils.py` 생성: fmt_large_number 테스트 9개 (커버리지 0→100%)
- `pyproject.toml`: fail_under 50 → 60 상향
- 최종: **347 tests, 70.9% coverage**

### Cycle 15: date_map 캐시 테스트 (2026-03-20)
- `tests/test_date_map.py` 생성: load_date_map + prices_to_dataframe 캐시 테스트 6개

### Cycle 14: 추천 이유 + 빈 추천 처리 (2026-03-20)
- `src/analysis/screener.py`: `_generate_reason()`에 스토캐스틱 매수전환/과매도 추가
- `src/pipeline.py`: 빈 추천 결과 early return 처리

### Cycle 13: 스크리너 성능 + MacroRepository 확장 (2026-03-20)
- `src/analysis/screener.py`: `load_date_map()` 1회 캐시 → 500회 개별 쿼리 제거
- `src/db/repository.py`: `MacroRepository.get_previous()` 추가 (매크로 추세 분석용)

### Cycle 12: 스토캐스틱 시그널 완전 통합 (2026-03-20)
- `src/db/seed.py`: stoch_bullish/stoch_bearish 시그널 타입 시딩 추가
- `src/analysis/signals.py`: 스토캐스틱 시그널 가중치 추가
- `src/reports/explainer.py`: 스토캐스틱 시그널 한국어 번역 + 초보자 설명 추가

### Cycle 11: 어셈블러/백테스트 정확성 (2026-03-20)
- `src/reports/assembler.py`: 시그널 중복 제거 시 강도 최대값 유지 (기존: 첫 번째 유지)
- `src/backtest/engine.py`: 샤프 비율 연환산 적용 (√(252/20), 기존: raw 값)
- `src/data/yahoo_client.py`: volume=0 데이터 skip (데이터 불완전 방지)

### Cycle 10: 달러 인덱스 + 배당수익률 + 스토캐스틱 시그널 (2026-03-20)
- `src/analysis/external.py`: 달러 인덱스 스코어링 추가 (>105: -1.0, <95: +1.0)
- `src/analysis/fundamental.py`: 배당수익률 스코어링 추가 (`_score_dividend_yield`, 가중치 10%)
- `src/analysis/signals.py`: 스토캐스틱 K-D 교차 시그널 추가 (stoch_bullish/stoch_bearish)

### Cycle 9: STEP 5+6 리포트/알림 완성도 (2026-03-20)
- `src/ai/claude_analyzer.py`: 티커 파싱 최소 2자 + 비티커 블랙리스트 (TOP, BUY, RSI 등)
- `src/reports/format_utils.py` 생성: 숫자 포맷 통합 (`fmt_large_number()`)
- `src/alerts/notifier.py`: 알림에 시그널 건수, VIX 추가

### Cycle 8: STEP 2 기술적 분석 성능/정확성 (2026-03-20)
- `src/analysis/technical.py`: `load_date_map()` 추가 — 500회 개별 쿼리 → 1회 배치 캐시
- `src/analysis/technical.py`: 미등록 지표 코드 warning 로깅 (기존: silent skip)
- `src/analysis/signals.py`: RSI 임계값 상수화 (`RSI_OVERSOLD=30`, `RSI_OVERBOUGHT=70`)
- `src/analysis/screener.py`: 이유 텍스트 RSI 임계값 35→30 일관성 수정

### Cycle 7: STEP 3 외부 분석 정확도 (2026-03-20)
- `src/analysis/external.py`: 뉴스 감성 키워드 단어 경계 매칭 (`\b` regex, 기존: substring)
- `src/analysis/external.py`: 매크로 완전성 검증 (유효 지표 <3 → 중립 5 반환)
- `src/analysis/external.py`: 플랫 마켓 섹터 모멘텀 감쇄 (spread<1% → 5.0 방향 압축)

### Cycle 6: STEP 1 데이터 수집 신뢰성 (2026-03-20)
- `src/data/yahoo_client.py`: 실패 티커 추적 — 반환 타입 `tuple[dict, list[str]]`
- `src/data/news_scraper.py`: datetime 파싱 실패 기사 skip (기존: now() fallback)
- `src/data/macro_collector.py`: 5회 순차 호출 → 1회 배치 호출, 수집 완전성 검증
- `src/pipeline.py`: 적시성 검증 5개 샘플 종목으로 개선 (기존: stocks[0] 단일)

### Cycle 5: STEP 4 스코어링 정밀화 (2026-03-20)
- `src/analysis/screener.py`: Smart Money 내부자 기여분 상한 +3.0 (CEO 포함, 기존 최대 +5.0)
- `src/analysis/screener.py`: 모멘텀 점수 선형 보간 (`ret_20d / 5.0`, 기존 step function)
- `src/analysis/screener.py`: 기관 보유 비중 → Smart Money 점수 반영 (>30%: +1.0, >15%: +0.5)
- `src/analysis/screener.py`: 랭킹 정밀도 `round(total, 2)` → `round(total, 4)`
- 테스트 4개 추가 (Smart Money 상한 2, 모멘텀 선형 2)

### Cycle 4: 통합 테스트 + 커버리지 (2026-03-20)
- `tests/test_scoring_integration.py` 생성: 전체 screen_and_rank 파이프라인 통합 테스트 3개
- `pyproject.toml`: `fail_under` 45 → 50 상향
- 최종: 328 tests, 70.55% coverage

### Cycle 3: 백테스트 엔진 (2026-03-20)
- `src/backtest/engine.py` 생성: BacktestEngine (승률, 평균수익률, 샤프비율, 최대낙폭)
- `src/backtest/comparator.py` 생성: 가중치 비교 (기본/기술중심/펀더멘털중심/모멘텀중심)
- `src/main.py`: `investmate backtest run/compare-weights` CLI 명령어 추가
- `tests/test_backtest.py` 생성: 백테스트 엔진 + 가중치 비교 테스트 17개

### Cycle 2: 임계값/편향 수정 — 스코어링 개선 2/2 (2026-03-20)
- `src/analysis/screener.py`: 내부자 거래 시간 감쇠 적용 (`exp(-age/30)`, 반감기 ~21일)
- `src/analysis/fundamental.py`: 데이터 누락 시 5.0→3.5 감점 (PER, PBR, ROE, Debt 전부)
- `src/analysis/screener.py`: SMA120 필터 완화 (-5% 이내 + RSI<40 → 통과)
- `src/analysis/screener.py`: 애널리스트 목표가 비대칭 보정 (+1.0/-2.0 → +1.5/-1.5)
- 테스트 5개 추가 (SMA120 완화 4, None 감점 일관성 1)

### Cycle 1: 미사용 데이터 활용 — 스코어링 개선 1/2 (2026-03-20)
- `src/analysis/screener.py`: 공매도 비율(`short_pct_of_float`) → Smart Money 점수 반영
- `src/analysis/screener.py`: 실적 서프라이즈(`EarningsSurpriseRepository`) → Fundamental 점수 보정
- `src/analysis/screener.py`: 스토캐스틱 K/D → Technical 점수 반영 (과매도/과매수/매수전환)
- `src/analysis/signals.py`: RSI 과매도 strength 공식 수정 (`10-rsi/3` → `(30-rsi)/3+5`)
- 테스트 11개 추가 (스토캐스틱 3, 공매도 2, 실적 서프라이즈 2, RSI strength 4)

### Cycle 0: 인프라 안정화 (2026-03-20)
- `src/db/migrate.py` 생성: ORM 모델과 실제 DB 스키마 비교 후 누락 컬럼 자동 추가
- `src/main.py` 수정: `run` 명령 시작 시 `ensure_schema()` 호출하여 DB 자동 업그레이드
- `tests/test_migrate.py` 생성: 마이그레이션 유틸리티 테스트 7개
- `CHANGELOG.md`, `TODO.md`, `METRICS.md` 생성
- **수정된 이슈**: `return_10d` 컬럼 누락으로 STEP 5 크래시 → 자동 마이그레이션으로 해결
