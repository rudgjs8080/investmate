# Investmate 초고도화 — 영역 분류

## 전체 현황
- 110개 소스 파일 (~21,800 LOC), 97개 테스트 (1,120개), 16개 웹 라우트
- Star Schema DB (6 Dim + 17 Fact + 1 Bridge)
- 10-step 일일 파이프라인 + 주간 파이프라인 + 웹 대시보드

---

## 영역 목록

### 1. 데이터 수집 (Data Ingestion)
- **파일**: `src/data/` (7파일, ~1,445 LOC)
- **핵심**: yahoo_client, enhanced_collector, macro_collector, news_scraper, schemas
- **현재 수준**: CircuitBreaker + Pydantic 기본 검증
- **고도화 방향**: 데이터 검증 레이어, 스테이징 존, 소스 추상화, 리니지 추적
- **담당 직무**: 데이터 엔지니어

### 2. 데이터베이스 (Database)
- **파일**: `src/db/` (6파일, ~2,027 LOC)
- **핵심**: engine, models (24 테이블), repository, migrate, helpers
- **현재 수준**: SQLite WAL + 커스텀 migrate (ALTER TABLE만)
- **고도화 방향**: PostgreSQL 지원, Alembic, 커넥션 풀링, Repository Protocol
- **담당 직무**: DB 엔지니어 / 백엔드 엔지니어

### 3. 분석 & 스크리닝 (Analysis & Screening)
- **파일**: `src/analysis/` (14파일, ~4,835 LOC)
- **핵심**: screener (1,178 LOC), factors, fundamental, technical, signals, regime, performance
- **현재 수준**: 5차원 적응형 스코어링, 4모드 팩터, 규칙 기반 레짐
- **고도화 방향**: screener 분해, 플러그인 스코어링, HMM 레짐, Barra 팩터, Brinson 귀인
- **담당 직무**: 퀀트 개발자 / 퀀트 애널리스트

### 4. AI 시스템 (AI System)
- **파일**: `src/ai/` (17파일, ~3,931 LOC)
- **핵심**: claude_analyzer, debate, agents, feedback, calibrator, validator, counterfactual, lesson_store
- **현재 수준**: 4단계 fallback, 3라운드 토론, 멀티 호라이즌 피드백, 자기학습
- **고도화 방향**: subprocess 제거, 프롬프트 버전 관리, 비용 추적, 평가 프레임워크, async 전환
- **담당 직무**: AI/ML 엔지니어 (LLM 전문)

### 5. ML 모델 (Machine Learning)
- **파일**: `src/ml/` (6파일, ~643 LOC)
- **핵심**: trainer (LightGBM), features (28개), scorer, drift_detector, registry
- **현재 수준**: 단일 모델, 고정 하이퍼파라미터, pickle 직렬화
- **고도화 방향**: 모델 레지스트리, 피처 스토어, Optuna, 앙상블, 피처 레벨 드리프트
- **담당 직무**: ML 엔지니어 (퀀트)

### 6. 포트폴리오 & 리스크 (Portfolio & Risk)
- **파일**: `src/portfolio/` (9파일, ~1,754 LOC)
- **핵심**: position_sizer, risk_constraints, efficient_frontier, drawdown_manager, execution_cost, turnover
- **현재 수준**: ERC/VolTarget/HalfKelly, 시그모이드 틸트, 7+제약 조건
- **고도화 방향**: 리스크 모델 고도화, 실시간 리스크 모니터링, 트랜잭션 코스트 모델 정교화
- **담당 직무**: 퀀트 개발자 / 리스크 엔지니어

### 7. 리포트 (Reports)
- **파일**: `src/reports/` (14파일, ~6,012 LOC)
- **핵심**: weekly_assembler (1,283 LOC), prompt_builder (783 LOC), daily_report, weekly_*, explainer
- **현재 수준**: MD/JSON/PDF 출력, 문자열 연결 기반
- **고도화 방향**: Jinja2 템플릿, 인터랙티브 HTML 리포트, 대형 파일 분해
- **담당 직무**: 풀스택 개발자 (Python + Frontend)

### 8. 웹 대시보드 (Web Dashboard)
- **파일**: `src/web/` (16 라우트 + 14 템플릿, ~2,643 LOC)
- **핵심**: FastAPI + Jinja2 + Tailwind + ECharts, 16개 페이지
- **현재 수준**: 인증 없음, CORS 없음, N+1 쿼리
- **고도화 방향**: JWT 인증, 레이트 리밋, 쿼리 최적화, WebSocket, PWA
- **담당 직무**: 풀스택 개발자 (보안 중심)

### 9. 파이프라인 오케스트레이션 (Pipeline & CLI)
- **파일**: `src/pipeline.py` (1,587 LOC), `src/main.py` (1,114 LOC), `src/backtest/`
- **핵심**: 10-step DailyPipeline, Click CLI (30+ 커맨드), step checkpointing
- **현재 수준**: 단일 클래스 순차 실행, 스텝 레벨 체크포인트
- **고도화 방향**: DAG 기반 병렬 실행, PipelineStep Protocol, CLI 분리, dry run
- **담당 직무**: 시니어 백엔드 엔지니어 / 시스템 아키텍트

### 10. DevOps & 인프라 (DevOps & Infrastructure)
- **파일**: `Makefile`, `pyproject.toml`, `scripts/`, 배포 문서
- **핵심**: 수동 SSH 배포, 파일 로깅만, cron 스케줄링
- **현재 수준**: CI/CD 없음, Docker 없음, 관측성 없음
- **고도화 방향**: Docker, GitHub Actions, OpenTelemetry, Prometheus, structlog
- **담당 직무**: DevOps / 플랫폼 엔지니어

### 11. 테스팅 & 품질 (Testing & Quality)
- **파일**: `tests/` (97파일, ~16,300 LOC), `pyproject.toml`
- **핵심**: pytest 1,120개, 커버리지 65%, 인메모리 SQLite
- **현재 수준**: 단위/통합 테스트만, E2E/부하/프로퍼티 테스트 없음, mypy 없음
- **고도화 방향**: 커버리지 85%+, E2E (VCR), 부하 테스트, mypy strict, 뮤테이션 테스트
- **담당 직무**: QA 엔지니어 / 테스트 아키텍트

---

## 의존성 관계

```
[독립] 1.데이터수집  2.DB  10.DevOps  11.테스팅
         │           │
         ▼           ▼
[의존] 3.분석    4.AI   5.ML   6.포트폴리오
         │        │
         ▼        ▼
[후순위] 7.리포트  8.웹  9.파이프라인
```

- 1, 2, 10, 11은 독립적으로 즉시 착수 가능
- 3~6은 1, 2가 어느 정도 진행된 후 착수
- 7~9는 3~6이 안정화된 후 착수

---

## 팀 편성 옵션

### 10인 (이상적)
| 역할 | 영역 |
|------|------|
| 데이터 엔지니어 | 1 |
| DB 엔지니어 | 2 |
| 퀀트 개발자 | 3 |
| AI/ML 엔지니어 (LLM) | 4 |
| ML 엔지니어 (퀀트) | 5 |
| 리스크 엔지니어 | 6 |
| 풀스택 개발자 (리포트) | 7 |
| 풀스택 개발자 (보안) | 8 |
| 시니어 백엔드 엔지니어 | 9 |
| QA 엔지니어 | 10+11 |

### 5인 (현실적)
| 담당 | 영역 | 프로필 |
|------|------|--------|
| A | 3 + 5 + 6 | 퀀트/ML — 도메인 전문가 |
| B | 2 + 9 | 백엔드/DB — 시스템 아키텍트 |
| C | 4 + 10 | AI + DevOps — AI 인프라 |
| D | 7 + 8 + 11 | 풀스택/QA — 인터페이스 + 품질 |
| E | 1 | 데이터 엔지니어 — 파이프라인 전담 |

### 3인 (최소)
| 담당 | 영역 | 프로필 |
|------|------|--------|
| 리드 | 2 + 3 + 5 + 6 + 9 | 퀀트 + 아키텍트 (도메인 + 시스템) |
| AI/인프라 | 1 + 4 + 10 | AI + 데이터 + DevOps |
| 프론트/QA | 7 + 8 + 11 | UI + 보안 + 테스트 |
