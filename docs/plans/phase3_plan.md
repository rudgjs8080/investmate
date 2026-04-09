# Deep Dive Phase 3 — 상세 구현 계획서

> Phase 1 완료 (30개 테스트), Phase 2 완료 (45개 테스트) 기반.
> 페어 자동 선정 + 일일 변경 감지 + 과거 회고 + 예측 정확도 + 알림 강화.

---

## 1. Phase 3 범위 재확인

### Phase 1+2 결과 반영 사항

| 확인 항목 | 결과 | Phase 3 영향 |
|-----------|------|-------------|
| DB 7개 테이블 | 모두 생성 완료 (Phase 1) | 마이그레이션 불필요 |
| `dim_watchlist_pairs` | **비어 있음** — 스키마만 존재 | Phase 3에서 채움 |
| `fact_deepdive_changes` | **비어 있음** — 스키마만 존재 | Phase 3에서 채움 |
| `fact_deepdive_forecasts.actual_price/actual_date/hit_range` | **모두 NULL** | Phase 3에서 만기 도래 시 채움 |
| 파이프라인 steps | step1/2/3/5/7/8 (6개) — step4, step6 없음 | Phase 3에서 step4_pairs, step6_diff 추가 |
| 6개 레이어 | 전체 구현 완료 (layers_fundamental/valuation/technical/flow/narrative/macro) | 변경 없음 |
| 3라운드 토론 | `ai_debate_cli.py` (252줄) 완전 동작 | 변경 없음, pair 컨텍스트만 주입 |
| 시나리오 예측 | `scenarios.py` (74줄), 9개/종목 (3H×3S) INSERT 정상 | 정확도 측정 대상 |
| 웹 라우트 | `/personal` (카드), `/personal/{ticker}` (상세 4탭) | +2개 라우트 추가 |
| charts.js | `layerRadarChart`, `scenarioRangeChart` 존재 | +`actionTimelineChart`, `accuracyBarChart` 추가 |
| Phase 1+2 미완료/연기 task | **없음** — 모든 Phase 2 task 완료 | 흡수할 것 없음 |
| 기술부채 | 파이프라인 파일이 440줄 (400줄 권장 초과) | Phase 3에서 헬퍼 함수 분리 고려 |

### 포함

| # | 항목 | 설명 |
|---|------|------|
| 1 | 페어 자동 선정 | GICS 섹터+시총 0.3x~3x+코사인 유사도 top 5, `dim_watchlist_pairs` 채움 |
| 2 | 일일 변경 감지 | step6: 액션/확신/확률/리스크/트리거 변경 감지, `fact_deepdive_changes` 채움 |
| 3 | 과거 분석 회고 | `/personal/{ticker}/history` + `personal_history.html` + `actionTimelineChart` |
| 4 | 예측 정확도 리더보드 | `/personal/forecasts` + `personal_forecasts.html` + 만기 매칭 + 정확도 점수 |
| 5 | 알림 강화 | step8에 변경 감지 결과 포함 (액션 변경 건수, 신규 리스크, 만기 도래) |
| 6 | 예측 만기 업데이트 | step7에서 만기 도래 예측의 `actual_price`/`hit_range` 백필 |
| 7 | 파이프라인 step4/step6 배선 | `dd_s4_pairs`, `dd_s6_diff` 를 steps 리스트에 삽입 |
| 8 | cron 스크립트 | `scripts/run_deepdive.sh` — 일일 자동 실행 래퍼 |
| 9 | 테스트 | ~24개 신규 (총 69개) |

### 제외

| 항목 | 사유 |
|------|------|
| 옵션 PCR/IV 데이터 | 데이터 수집 미구현 (Phase 1 결정 유지) |
| weekly_pipeline 에 페어 step 추가 | 독립성 유지 위해 deepdive_pipeline 내부에서 7일 staleness 체크로 처리 |
| 모바일 반응형 전면 개편 | 기존 Tailwind 반응형으로 충분 |

### master_plan.md 대비 변경 사항

| master_plan 기술 | 변경 | 사유 |
|------------------|------|------|
| "주 1회 자동 갱신 (weekly_pipeline에 step 추가)" | **deepdive_pipeline step4에서 7일 staleness 체크** | Phase 1+2에서 deepdive_pipeline이 daily pipeline과 완전 독립으로 구현됨. weekly_pipeline은 주간 리포트 전용이며 deepdive와 무관. 독립성 유지가 더 적합 |
| step6_diff 후 "웹 카드에 변경 배지" | **카드 + 상세 페이지 모두에 변경 정보 표시** | Phase 2에서 상세 페이지가 4탭 구조로 완성되어 변경 섹션 추가가 자연스러움 |
| 예측 정확도에 "시나리오별 적중률" | **시나리오별 + 호라이즌별 + 종합 점수** | 실제 `fact_deepdive_forecasts` 구조가 horizon/scenario 양축이므로 양방향 분석 가능 |

---

## 2. Phase 1+2 → Phase 3 인터페이스 매핑

### 2.1 함수/클래스 확장 지점

| Phase 2 산출물 | Phase 3 확장 | 파일 |
|---------|-------------|------|
| `DeepDivePipeline.run()` steps 6개 | +step4_pairs, +step6_diff → **총 8개** | `src/deepdive_pipeline.py` |
| `build_stock_context()` XML (6레이어) | +`<pair_comparison>` 블록 | `src/deepdive/ai_prompts.py` |
| `DeepDiveRepository` (8개 메서드) | +10개 메서드 → **총 18개** | `src/db/repository.py` |
| `WatchlistRepository` (6개 메서드) | +3개 (pairs CRUD) → **총 9개** | `src/db/repository.py` |
| `send_deepdive_summary()` (1줄 요약) | 확장: 액션변경/리스크/만기 포함 | `src/alerts/notifier.py` |
| `/personal` 카드 (변경 배지 없음) | +변경 건수 배지 | `src/web/templates/personal.html` |
| `/personal/{ticker}` (4탭) | +변경사항 섹션 (요약 탭 하단), +히스토리 링크 | `src/web/templates/personal_detail.html` |
| `charts.js` (2개 deep dive 함수) | +`actionTimelineChart`, +`accuracyBarChart` | `src/web/static/charts.js` |
| personal.py 라우트 (2개) | +2개 (history, forecasts) → **총 4개** | `src/web/routes/personal.py` |

### 2.2 DB 테이블 매핑

| 테이블 | Phase 2 상태 | Phase 3 사용 |
|--------|-------------|-------------|
| `dim_watchlist_pairs` | 비어 있음 (스키마만) | **step4_pairs가 채움**: upsert top 5 peers/종목 |
| `fact_deepdive_changes` | 비어 있음 (스키마만) | **step6_diff가 채움**: 변경 감지 결과 INSERT |
| `fact_deepdive_forecasts` | 9행/종목 (actual_price=NULL) | **step7에서 만기 건 actual_price/hit_range 업데이트** |
| `fact_deepdive_reports` | 완전 사용 중 | 변경 없음 (diff 감지 시 이전 report 조회만) |
| `fact_deepdive_actions` | 완전 사용 중 | 변경 없음 (히스토리 페이지 조회만) |
| `dim_watchlist` | 완전 사용 중 | 변경 없음 |
| `dim_watchlist_holdings` | 완전 사용 중 | 변경 없음 |

> DB 마이그레이션 불필요 — Phase 1에서 모든 컬럼 이미 정의됨.

### 2.3 기존 분석 함수 재사용 (Phase 3 신규)

| 함수 | 위치 | Phase 3 사용 |
|------|------|-------------|
| `StockRepository.get_by_ticker()` | `src/db/repository.py:56` | 페어 후보 조회 |
| `DimStock.is_sp500`, `.sector_id` | `src/db/models.py:135-139` | 페어 후보 필터링 (S&P 500 유니버스) |
| `FactValuation.market_cap` | `src/db/models.py:249` | 시총 필터 (0.3x~3x) |
| `FactDailyPrice.close` | `src/db/models.py:154` | 60일 수익률 코사인 유사도 |
| `date_to_id()`, `id_to_date()` | `src/db/helpers.py` | 만기 날짜 계산 |
| `NarrativeProfile.risk_events` | `src/deepdive/schemas.py:79` | diff 감지: 신규 리스크 비교 |
| `DeepDiveRepository.get_latest_report()` | `src/db/repository.py:939` | diff 감지: 이전 리포트 로드 |
| `DeepDiveRepository.get_forecasts_by_report()` | `src/db/repository.py:1008` | diff 감지: 이전 예측 비교 |

---

## 3. Task 단위 분할 (16개, 각 1-3시간)

| Task | 이름 | 예상 시간 | 의존 |
|------|------|----------|------|
| T1 | 스키마: PeerComparison + ChangeRecord + ForecastAccuracy | 1h | — |
| T2 | 리포지토리: pairs 메서드 3개 | 1h | — |
| T3 | 리포지토리: diff/changes 메서드 4개 | 1h | — |
| T4 | 리포지토리: forecast 메서드 4개 | 1.5h | — |
| T5 | 리포지토리: actions 조회 메서드 1개 | 0.5h | — |
| T6 | pair_analysis.py — 페어 자동 선정 알고리즘 | 3h | T1, T2 |
| T7 | diff_detector.py — 변경 감지 로직 | 2.5h | T1, T3 |
| T8 | forecast_evaluator.py — 만기 감지 + 정확도 점수 | 2.5h | T1, T4 |
| T9 | 파이프라인 step4/step6/step7-만기/step8-강화 배선 | 3h | T6, T7, T8 |
| T10 | ai_prompts.py: `<pair_comparison>` 블록 추가 | 1h | T6 |
| T11 | charts.js: actionTimelineChart + accuracyBarChart | 1.5h | — |
| T12 | personal.py: history + forecasts 라우트 | 2h | T4, T5 |
| T13 | personal_history.html 템플릿 | 2.5h | T11, T12 |
| T14 | personal_forecasts.html 템플릿 | 2h | T8, T11, T12 |
| T15 | personal.html + personal_detail.html 업데이트 (변경 배지/섹션) | 1.5h | T7, T12 |
| T16 | 테스트 (~24개 신규) + cron 스크립트 | 3h | T6-T15 |

**총 예상**: ~28.5시간 (16개 task, 20개 미만 → 3A/3B 분할 불필요)

---

## 4. Task 의존성 그래프

```
T1 (스키마) ──────────────┐
                          │
T2 (repo: pairs) ─────────┼── T6 (pair_analysis.py) ──┐
                          │                            ├── T10 (AI pair 블록)
T3 (repo: diff/changes) ──┼── T7 (diff_detector.py) ──┤
                          │                            ├── T9 (파이프라인 배선)
T4 (repo: forecast) ──────┼── T8 (forecast_eval.py) ──┘        │
                          │                                     │
T5 (repo: actions) ───────────── T12 (라우트) ── T13 (history 템플릿)
                                      │          T14 (forecasts 템플릿)
T11 (charts.js) ──────────────────────┤
                                      └── T15 (카드/상세 업데이트)

T16 (테스트 + cron) ← 모든 Task 완료 후
```

**권장 실행 순서**:

| 실행 단계 | Tasks | 병렬 여부 | 소요 |
|-----------|-------|----------|------|
| 3.0 기반 | T1, T2, T3, T4, T5, T11 | **6개 모두 병렬** | ~1.5h |
| 3.1 핵심 로직 | T6, T7, T8 | **3개 병렬** | ~3h |
| 3.2 통합 | T9, T10 | T9 먼저 → T10 | ~4h |
| 3.3 웹 | T12 → T13+T14+T15 병렬 | 1 → 3 병렬 | ~4.5h |
| 3.4 테스트 | T16 | 단일 | ~3h |

**크리티컬 패스**: T1 → T6 → T9 → T12 → T13 → T16 (13.5h)

---

## 5. 각 Task별 상세

### Task 1: 스키마 — PeerComparison + ChangeRecord + ForecastAccuracy (1h)

**건드릴 파일**: `src/deepdive/schemas.py` (수정, +35줄)

**핵심 함수 시그니처**:

```python
@dataclass(frozen=True)
class PeerComparison:
    """단일 페어 비교 결과."""
    peer_ticker: str
    peer_name: str
    similarity_score: float       # 코사인 유사도 0.0-1.0
    market_cap_ratio: float       # peer/target 시총 비율
    return_60d_peer: float        # 페어 60일 수익률 %
    return_60d_target: float      # 대상 60일 수익률 %
    per_peer: float | None        # 페어 PER
    per_target: float | None      # 대상 PER

@dataclass(frozen=True)
class ChangeRecord:
    """단일 변경 감지 결과."""
    change_type: str              # action_changed/conviction_shift/probability_shift/new_risk/trigger_hit
    description: str              # 사람이 읽을 수 있는 설명
    severity: str                 # critical/warning/info

@dataclass(frozen=True)
class ForecastAccuracy:
    """종목별 예측 정확도 요약."""
    ticker: str
    total_evaluated: int          # 평가 완료된 예측 수
    hit_count: int                # 적중 수 (actual이 범위 내)
    hit_rate: float               # hit_count / total_evaluated
    direction_correct: int        # 방향 정확 수
    direction_accuracy: float     # direction_correct / total_evaluated
    overall_score: float          # hit_rate * 0.6 + direction_accuracy * 0.4
    by_horizon: dict              # {"1M": {"hit_rate": 0.6, "count": 5}, ...}
    by_scenario: dict             # {"BASE": {"hit_rate": 0.7, "count": 5}, ...}
```

**입출력**: 모듈 간 DTO로만 사용. DB 직접 접근 없음.

**기존 코드 의존**: 기존 `schemas.py`의 `@dataclass(frozen=True)` 패턴 (DebateRound, CLIDebateResult) 준수.

**테스트**: T16에서 간접 검증 (인스턴스 생성 + 필드 접근).

**완료 정의**: 3개 dataclass 정의, frozen=True, 기존 45개 테스트 통과.

---

### Task 2: 리포지토리 — pairs 메서드 (1h)

**건드릴 파일**: `src/db/repository.py` (수정, +40줄 in WatchlistRepository)

**핵심 함수 시그니처**:

```python
class WatchlistRepository:
    # ... 기존 6개 메서드 유지 ...

    @staticmethod
    def upsert_pairs(
        session: Session, ticker: str,
        pairs: list[dict],  # [{"peer_ticker": str, "similarity_score": float}, ...]
    ) -> int:
        """페어 종목 UPSERT. 기존 pairs 삭제 후 재삽입. 반환: INSERT 건수."""

    @staticmethod
    def get_pairs(session: Session, ticker: str) -> list[DimWatchlistPair]:
        """종목의 페어 목록. ORDER BY similarity_score DESC."""

    @staticmethod
    def get_pairs_updated_at(session: Session, ticker: str) -> datetime | None:
        """종목 페어의 최신 updated_at. 없으면 None."""
```

**입출력**:
- `upsert_pairs`: DELETE WHERE ticker + bulk INSERT → 반환 건수
- `get_pairs`: SELECT WHERE ticker ORDER BY score DESC → list
- `get_pairs_updated_at`: SELECT MAX(updated_at) WHERE ticker → datetime | None

**기존 코드 의존**: `DimWatchlistPair` ORM 모델 (`src/db/models.py:829`). import 추가 필요.

**테스트**: T16에서 `test_upsert_pairs`, `test_get_pairs_ordered`.

**완료 정의**: 3개 메서드, staleness 체크용 updated_at 반환 정상.

---

### Task 3: 리포지토리 — diff/changes 메서드 (1h)

**건드릴 파일**: `src/db/repository.py` (수정, +50줄 in DeepDiveRepository)

**핵심 함수 시그니처**:

```python
class DeepDiveRepository:
    # ... 기존 8개 메서드 유지 ...

    @staticmethod
    def get_previous_report(
        session: Session, stock_id: int, before_date_id: int,
    ) -> FactDeepDiveReport | None:
        """지정 date_id 이전의 최신 리포트. diff 감지용."""
        # WHERE stock_id = :stock_id AND date_id < :before_date_id
        # ORDER BY date_id DESC LIMIT 1

    @staticmethod
    def insert_changes_batch(
        session: Session, date_id: int, stock_id: int, ticker: str,
        changes: list,  # list[ChangeRecord]
    ) -> int:
        """변경 감지 결과 일괄 INSERT. 반환: INSERT 건수."""

    @staticmethod
    def get_changes_by_date(
        session: Session, date_id: int,
    ) -> list[FactDeepDiveChange]:
        """날짜별 변경 목록 (알림용). ORDER BY severity DESC."""

    @staticmethod
    def get_changes_by_ticker(
        session: Session, ticker: str, limit: int = 60,
    ) -> list[FactDeepDiveChange]:
        """종목별 변경 이력 (히스토리 페이지용). ORDER BY date_id DESC."""
```

**기존 코드 의존**: `FactDeepDiveChange` ORM 모델 (`src/db/models.py:947`). import 추가 필요.

**테스트**: T16에서 `test_get_previous_report`, `test_insert_changes_batch`.

**완료 정의**: 4개 메서드, 이전 리포트 없을 때 None 반환.

---

### Task 4: 리포지토리 — forecast 메서드 (1.5h)

**건드릴 파일**: `src/db/repository.py` (수정, +60줄 in DeepDiveRepository)

**핵심 함수 시그니처**:

```python
class DeepDiveRepository:

    @staticmethod
    def get_matured_forecasts(
        session: Session, as_of_date: date,
    ) -> list[FactDeepDiveForecast]:
        """만기 도래한 미평가 예측 조회.
        actual_price IS NULL인 전체 예측을 로드 후
        Python에서 date_id → date 변환 + horizon별 만기일 계산하여 필터.
        (SQLite date 연산 제약으로 Python 필터링)"""

    @staticmethod
    def update_forecast_actual(
        session: Session, forecast_id: int,
        actual_price: float, actual_date: date, hit_range: bool,
    ) -> None:
        """만기 도래 예측의 실제 가격/적중 여부 업데이트."""
        # UPDATE SET actual_price, actual_date, hit_range, updated_at

    @staticmethod
    def get_all_evaluated_forecasts(
        session: Session,
    ) -> list[FactDeepDiveForecast]:
        """평가 완료된 전체 예측 (리더보드용).
        WHERE hit_range IS NOT NULL ORDER BY ticker, horizon."""

    @staticmethod
    def get_evaluated_forecasts_by_ticker(
        session: Session, ticker: str,
    ) -> list[FactDeepDiveForecast]:
        """종목별 평가 완료 예측. WHERE hit_range IS NOT NULL AND ticker = :ticker."""
```

**입출력**:
- `get_matured_forecasts`: 전체 미평가 예측 로드 → Python 만기 필터 → list
- `update_forecast_actual`: forecast_id로 단건 UPDATE
- `get_all_evaluated_forecasts`: hit_range NOT NULL 조건 → list
- `get_evaluated_forecasts_by_ticker`: ticker + hit_range 조건 → list

**기존 코드 의존**: `id_to_date()` (`src/db/helpers.py`), `FactDeepDiveForecast` (`src/db/models.py:884`).

**구현 핵심 — `get_matured_forecasts` 내부 필터**:

```python
HORIZON_DAYS = {"1M": 30, "3M": 90, "6M": 180}

# 모든 미평가 예측 로드
all_pending = session.execute(
    select(FactDeepDiveForecast)
    .where(FactDeepDiveForecast.actual_price.is_(None))
).scalars().all()

# Python에서 만기 필터
matured = []
for f in all_pending:
    forecast_date = id_to_date(f.date_id)
    maturity_date = forecast_date + timedelta(days=HORIZON_DAYS.get(f.horizon, 30))
    if maturity_date <= as_of_date:
        matured.append(f)
return matured
```

**테스트**: T16에서 `test_get_matured_forecasts`, `test_update_forecast_actual`.

**완료 정의**: 4개 메서드, SQLite 날짜 제약 우회, None 안전.

---

### Task 5: 리포지토리 — actions 조회 (0.5h)

**건드릴 파일**: `src/db/repository.py` (수정, +15줄 in DeepDiveRepository)

**핵심 함수 시그니처**:

```python
@staticmethod
def get_actions_by_ticker(
    session: Session, ticker: str, limit: int = 60,
) -> list[FactDeepDiveAction]:
    """종목별 액션 이력 (히스토리 페이지용). ORDER BY date_id DESC LIMIT :limit."""
```

**테스트**: T16에서 간접 검증.

**완료 정의**: 1개 메서드, limit 동작 확인.

---

### Task 6: pair_analysis.py — 페어 자동 선정 알고리즘 (3h)

**건드릴 파일**: `src/deepdive/pair_analysis.py` (신규, ~180줄)

**핵심 함수 시그니처**:

```python
def refresh_peers_if_stale(
    session: Session,
    stock_id: int,
    ticker: str,
    sector_id: int | None,
    staleness_days: int = 7,
) -> list[PeerComparison]:
    """staleness 체크 후 필요 시 갱신. 반환: 현재 페어 목록."""

def select_peers(
    session: Session,
    stock_id: int,
    ticker: str,
    sector_id: int | None,
    top_n: int = 5,
) -> list[PeerComparison]:
    """GICS 섹터 + 시총 + 코사인 유사도 top N 페어 선정."""

def _get_sector_candidates(
    session: Session,
    sector_id: int,
    exclude_stock_id: int,
) -> list[DimStock]:
    """동일 섹터 S&P 500 종목 조회."""

def _filter_by_market_cap(
    session: Session,
    candidates: list[DimStock],
    target_market_cap: float,
    low_ratio: float = 0.3,
    high_ratio: float = 3.0,
) -> list[DimStock]:
    """시총 0.3x~3x 필터."""

def _compute_cosine_similarities(
    session: Session,
    target_stock_id: int,
    candidate_ids: list[int],
    lookback_days: int = 60,
) -> dict[int, float]:
    """60일 수익률 코사인 유사도 계산. 반환: {stock_id: similarity}."""

def _cosine_sim(a: list[float], b: list[float]) -> float:
    """numpy 코사인 유사도."""
```

**입력**:
- `DimStock` (sector_id, is_sp500, stock_id) → 페어 후보 유니버스
- `FactValuation` (market_cap) → 최신 시총
- `FactDailyPrice` (close) → 최근 60거래일 종가

**출력**: `list[PeerComparison]` (최대 5개)

**기존 코드 의존**:
- `WatchlistRepository.get_pairs_updated_at()` → staleness 체크
- `WatchlistRepository.upsert_pairs()` → DB 저장
- `WatchlistRepository.get_pairs()` → 기존 페어 로드
- `numpy` (이미 requirements에 포함)

**에지 케이스**:
- 섹터 후보 5개 미만 → 가용 전체 반환
- 시총 데이터 없음 → 시총 필터 스킵
- 가격 데이터 20일 미만 → 빈 리스트 반환
- 비S&P500 종목 → S&P500 내에서 동일 섹터 검색

**테스트**:
- `test_cosine_similarity_identical` — 동일 수익률 → 1.0
- `test_cosine_similarity_opposite` — 반대 수익률 → -1.0
- `test_select_peers_sector_filter` — 동일 섹터만 반환
- `test_select_peers_market_cap_filter` — 0.3x~3x 범위
- `test_refresh_peers_staleness` — 7일 미만 → 재사용

**완료 정의**: top 5 페어 선정, `dim_watchlist_pairs`에 저장, staleness 체크 동작.

---

### Task 7: diff_detector.py — 변경 감지 로직 (2.5h)

**건드릴 파일**: `src/deepdive/diff_detector.py` (신규, ~160줄)

**핵심 함수 시그니처**:

```python
def detect_changes(
    current_ai_result: AIResult,
    current_layers: dict,
    current_forecasts: list | None,
    previous_report: FactDeepDiveReport | None,
    previous_forecasts: list[FactDeepDiveForecast] | None,
) -> list[ChangeRecord]:
    """현재 분석 결과와 이전 리포트 비교. 반환: 변경 목록."""

def _detect_action_change(
    current_grade: str,
    prev_grade: str | None,
    current_conviction: int,
    prev_conviction: int | None,
) -> list[ChangeRecord]:
    """액션 등급 변경 + 확신도 변화 감지."""

def _detect_probability_shifts(
    current_forecasts: list,
    previous_forecasts: list,
    threshold_pp: float = 10.0,
) -> list[ChangeRecord]:
    """시나리오 확률 10%p 이상 변화 감지."""

def _detect_new_risks(
    current_risk_events: list[str],
    previous_risk_events: list[str],
) -> list[ChangeRecord]:
    """신규 리스크 이벤트 감지."""

def _detect_trigger_hits(
    previous_trigger: str | None,
    current_layers: dict,
    current_ai_result: AIResult,
) -> list[ChangeRecord]:
    """이전 next_review_trigger 조건 도달 감지."""

def _extract_previous_data(
    report: FactDeepDiveReport,
) -> dict:
    """이전 리포트의 report_json에서 비교에 필요한 데이터 추출."""
```

**입력**:
- `current_ai_result`: 오늘 AI 분석 결과 (AIResult)
- `current_layers`: 오늘 레이어 결과 dict (layer5의 risk_events 사용)
- `current_forecasts`: 오늘 시나리오 예측 (있으면)
- `previous_report`: 전일 `FactDeepDiveReport` (report_json 파싱)
- `previous_forecasts`: 전일 `FactDeepDiveForecast` 리스트

**출력**: `list[ChangeRecord]`

**변경 감지 기준 상세**:

| 변경 유형 | 감지 조건 | severity |
|-----------|----------|----------|
| `action_changed` | `current.action_grade != prev.action_grade` | **critical** |
| `conviction_shift` | `abs(current.conviction - prev.conviction) >= 2` | **warning** |
| `probability_shift` | 동일 horizon+scenario의 `abs(curr.prob - prev.prob) >= 0.10` | **info** |
| `new_risk` | `current layer5.risk_events`에 있지만 `prev`에 없는 항목 | **warning** |
| `trigger_hit` | 이전 리포트 `next_review_trigger` 텍스트의 핵심 키워드가 현재 데이터에 매칭 | **critical** |

**이전 데이터 추출 로직**: `report.report_json`은 JSON 문자열. `json.loads()` 후:
- `data["ai_result"]["action_grade"]`, `data["ai_result"]["conviction"]`
- `data["layers"]["layer5"]["risk_events"]` (있으면)
- next_review_trigger는 report_json에 포함되지 않음 → `report.ai_synthesis` 텍스트에서 추출 시도 또는 스킵

**테스트**:
- `test_detect_action_changed` — HOLD→ADD → critical
- `test_detect_conviction_shift` — 7→4 (|3|≥2) → warning
- `test_detect_probability_shift` — 50%→35% (15pp≥10) → info
- `test_detect_new_risk` — 신규 리스크 → warning
- `test_detect_no_previous` — 이전 리포트 없음 → 빈 리스트
- `test_detect_no_changes` — 동일 결과 → 빈 리스트

**완료 정의**: 5가지 변경 유형 감지, severity 정확, 이전 리포트 없을 때 빈 리스트.

---

### Task 8: forecast_evaluator.py — 만기 감지 + 정확도 점수 (2.5h)

**건드릴 파일**: `src/deepdive/forecast_evaluator.py` (신규, ~140줄)

**핵심 함수 시그니처**:

```python
HORIZON_DAYS: dict[str, int] = {"1M": 30, "3M": 90, "6M": 180}

def evaluate_matured_forecasts(
    session: Session,
    as_of_date: date,
) -> int:
    """만기 도래 예측 찾아서 actual_price/hit_range 업데이트. 반환: 업데이트 건수."""

def _get_actual_price_at_date(
    session: Session,
    stock_id: int,
    target_date: date,
    max_lookback_days: int = 5,
) -> tuple[float, date] | None:
    """만기일 종가 조회. 비거래일이면 5일 이내 직전 거래일."""

def compute_accuracy_scores(
    forecasts: list[FactDeepDiveForecast],
) -> list[ForecastAccuracy]:
    """평가 완료된 예측 → 종목별 정확도 점수. 반환: 종목별 ForecastAccuracy."""

def _score_single_ticker(
    ticker: str,
    ticker_forecasts: list[FactDeepDiveForecast],
) -> ForecastAccuracy:
    """단일 종목 정확도 계산."""
```

**만기 판정 로직**:

```
forecast_date = id_to_date(forecast.date_id)
maturity_date = forecast_date + timedelta(days=HORIZON_DAYS[forecast.horizon])
is_matured = maturity_date <= as_of_date
```

**실제 가격 매칭 로직**:

```
target_date_id = date_to_id(maturity_date)
# FactDailyPrice WHERE stock_id AND date_id <= target_date_id
#   AND date_id >= date_to_id(maturity_date - timedelta(days=5))
# ORDER BY date_id DESC LIMIT 1
```

**적중 판정**:

```
hit_range = (forecast.price_low <= actual_price <= forecast.price_high)
```

**정확도 점수 공식 상세**:

```python
# 1. hit_rate = 적중 수 / 평가 수
hit_rate = hit_count / total_evaluated

# 2. 방향 정확도
#    - BULL 시나리오: actual > base_midpoint → correct
#    - BEAR 시나리오: actual < base_midpoint → correct
#    - BASE 시나리오: hit_range == True → correct
#    base_midpoint = 동일 horizon의 BASE 시나리오 (price_low + price_high) / 2
direction_accuracy = direction_correct / total_evaluated

# 3. 종합 점수
overall_score = hit_rate * 0.6 + direction_accuracy * 0.4
```

**방향 정확도에서 base_midpoint 매칭**: 동일 `report_id` + `horizon`에서 `scenario='BASE'`인 예측의 `(price_low + price_high) / 2`.

**에지 케이스**:
- 만기일에 가격 데이터 없음 (상장폐지 등) → 스킵 (actual_price NULL 유지)
- 평가 수 0 → overall_score = 0.0
- BASE 시나리오가 없는 경우 → 방향 정확도 스킵, hit_rate만 사용

**테스트**:
- `test_maturity_date_calculation` — 1M=30d, 3M=90d, 6M=180d
- `test_hit_range_in_range` — 범위 내 → True
- `test_hit_range_outside` — 범위 외 → False
- `test_accuracy_score_calculation` — 가중 공식 검증
- `test_accuracy_empty_forecasts` — 평가 0건 → score 0

**완료 정의**: 만기 예측 actual_price 업데이트, 정확도 점수 정확, 에지 케이스 처리.

---

### Task 9: 파이프라인 step4/step6/step7-만기/step8-강화 배선 (3h)

**건드릴 파일**: `src/deepdive_pipeline.py` (수정, +~120줄)

**변경 1 — import 추가**:

```python
from src.deepdive.schemas import PeerComparison, ChangeRecord
```

**변경 2 — 인스턴스 변수 추가** (`__init__`):

```python
self._pair_results: dict[str, list] = {}       # ticker → list[PeerComparison]
self._change_results: dict[str, list] = {}     # ticker → list[ChangeRecord]
```

**변경 3 — steps 리스트 업데이트** (`run()` 내부):

```python
steps = [
    ("dd_s1_load", self.step1_load_watchlist),
    ("dd_s2_collect", self.step2_collect_extras),
    ("dd_s3_compute", self.step3_compute_layers),
    ("dd_s4_pairs", self.step4_pairs),              # 신규
    ("dd_s5_ai", self.step5_ai_analysis),
    ("dd_s6_diff", self.step6_diff_detection),       # 신규
    ("dd_s7_persist", self.step7_persist),            # 수정
    ("dd_s8_notify", self.step8_notify),              # 수정
]
```

**변경 4 — step4_pairs()** 신규 메서드:

```python
def step4_pairs(self) -> int:
    """dd_s4_pairs: 페어 자동 선정 (7일 staleness 체크)."""
    from src.deepdive.pair_analysis import refresh_peers_if_stale

    count = 0
    for entry in self._watchlist_entries:
        try:
            with get_session(self.engine) as session:
                stock = StockRepository.get_by_ticker(session, entry.ticker)
                sector_id = stock.sector_id if stock else None
                peers = refresh_peers_if_stale(
                    session, entry.stock_id, entry.ticker, sector_id,
                )
                self._pair_results[entry.ticker] = peers
                count += len(peers)
        except Exception as e:
            logger.warning("페어 선정 실패 (%s): %s", entry.ticker, e)
    return count
```

**변경 5 — step5_ai_analysis() 수정**: `run_deepdive_debate()` 호출 시 pair_results 전달 (T10에서 ai_prompts.py 수정 후 연동).

**변경 6 — step6_diff_detection()** 신규 메서드:

```python
def step6_diff_detection(self) -> int:
    """dd_s6_diff: 전일 대비 변경점 추출."""
    from src.deepdive.diff_detector import detect_changes

    count = 0
    for entry in self._watchlist_entries:
        ai_result = self._ai_results.get(entry.ticker)
        if ai_result is None:
            continue
        try:
            with get_session(self.engine) as session:
                prev_report = DeepDiveRepository.get_previous_report(
                    session, entry.stock_id, self.run_date_id,
                )
                prev_forecasts = (
                    DeepDiveRepository.get_forecasts_by_report(
                        session, prev_report.report_id,
                    ) if prev_report else None
                )
                debate = self._debate_results.get(entry.ticker)
                changes = detect_changes(
                    current_ai_result=ai_result,
                    current_layers=self._layer_results.get(entry.ticker, {}),
                    current_forecasts=debate.scenarios if debate else None,
                    previous_report=prev_report,
                    previous_forecasts=prev_forecasts,
                )
                self._change_results[entry.ticker] = changes
                count += len(changes)
        except Exception as e:
            logger.warning("변경감지 실패 (%s): %s", entry.ticker, e)
    return count
```

**변경 7 — step7_persist() 수정**:
- 메서드 시작 부분에 만기 예측 업데이트 추가:
  ```python
  from src.deepdive.forecast_evaluator import evaluate_matured_forecasts
  with get_session(self.engine) as session:
      matured_count = evaluate_matured_forecasts(session, self.target_date)
      if matured_count:
          logger.info("예측 만기 업데이트: %d건", matured_count)
  ```
- 기존 루프 내 `insert_action()` 이후에 changes INSERT 추가:
  ```python
  changes = self._change_results.get(entry.ticker, [])
  if changes:
      DeepDiveRepository.insert_changes_batch(
          session, self.run_date_id, entry.stock_id, entry.ticker, changes,
      )
  ```

**변경 8 — step8_notify() 수정**:
- 액션 변경 목록 구성 (critical severity인 change에서 추출)
- 신규 리스크 목록 구성 (new_risk type인 change에서 추출)
- `send_deepdive_summary()` 호출 시 새 파라미터 전달

**기존 코드 의존**: 모든 기존 step 패턴 (try/except 격리, _log_step, 체크포인팅).

**테스트**: T16에서 `test_step4_pairs_called`, `test_step6_diff_called`, `test_step7_maturity_update`, `test_step8_enhanced_notify`.

**완료 정의**: 8개 step 전부 실행, FactCollectionLog 8개 success, 기존 테스트 통과.

---

### Task 10: ai_prompts.py — `<pair_comparison>` 블록 (1h)

**건드릴 파일**: `src/deepdive/ai_prompts.py` (수정, +25줄)

**변경 위치**: `build_stock_context()` 함수, layer6 블록 이후

**추가 코드**:

```python
def build_stock_context(
    entry: WatchlistEntry, layers: dict,
    current_price: float, daily_change: float,
    pair_results: list | None = None,  # 파라미터 추가
) -> str:
    # ... 기존 코드 ...

    # Layer 6 블록 이후에 추가:
    if pair_results:
        parts.append("")
        parts.append("<pair_comparison>")
        parts.append(f"Top {len(pair_results)} 동종 페어:")
        for p in pair_results:
            parts.append(
                f"  {p.peer_ticker} ({p.peer_name}): "
                f"시총비={p.market_cap_ratio:.1f}x, "
                f"60일수익률={p.return_60d_peer:+.1f}% vs 대상 {p.return_60d_target:+.1f}%, "
                f"PER={p.per_peer or 'N/A'} vs {p.per_target or 'N/A'}, "
                f"유사도={p.similarity_score:.2f}"
            )
        parts.append("</pair_comparison>")
```

**파이프라인 연동**: `step5_ai_analysis()`에서 `build_stock_context()` 호출 시 `pair_results=self._pair_results.get(entry.ticker, [])` 전달. 이를 위해 `run_deepdive_debate()` 시그니처에도 `pair_results` 파라미터 추가 필요:

```python
def run_deepdive_debate(
    entry, layers, current_price, daily_change,
    timeout=600, model="opus",
    pair_results=None,  # 추가
) -> CLIDebateResult | None:
```

**테스트**: T16에서 `test_build_context_with_pairs`.

**완료 정의**: 페어 있을 때 `<pair_comparison>` 포함, 없을 때 생략.

---

### Task 11: charts.js — actionTimelineChart + accuracyBarChart (1.5h)

**건드릴 파일**: `src/web/static/charts.js` (수정, +80줄)

**함수 1 — actionTimelineChart**:

```javascript
function actionTimelineChart(domId, data) {
    // data: {
    //   dates: ["2025-01-15", ...],
    //   convictions: [7, 8, ...],
    //   grades: ["HOLD", "ADD", ...],
    // }
    // 렌더링:
    //   - X축: dates
    //   - 좌측 Y축: conviction (1-10, 라인)
    //   - 마커: grade 변경 시점에 색상 마커
    //     ADD=green, HOLD=gray, TRIM=orange, EXIT=red
    //   - tooltip: 날짜 + 등급 + conviction
}
```

**함수 2 — accuracyBarChart**:

```javascript
function accuracyBarChart(domId, data) {
    // data: [
    //   {ticker: "AAPL", hit_rate: 65, direction: 70, overall: 67},
    //   ...
    // ]
    // 렌더링:
    //   - Y축: tickers (수평 바)
    //   - X축: 0-100%
    //   - 3개 그룹 바: hit_rate(파랑), direction(초록), overall(보라)
    //   - tooltip: 종목 + 각 수치
}
```

**컨벤션 준수**:
- `initChart()` + ECharts 옵션 패턴
- `colorWithAlpha()`, `fmtNum`, `fmtPercent` 재사용
- 다크모드 동기화 (`reinitAllCharts` 호환)
- 반응형 높이 CSS 클래스

**테스트**: 프론트엔드 테스트 없음 (기존 패턴 동일). 수동 검증.

**완료 정의**: 두 함수 렌더링 정상, 다크모드 호환, 빈 데이터 시 에러 없음.

---

### Task 12: personal.py — history + forecasts 라우트 (2h)

**건드릴 파일**: `src/web/routes/personal.py` (수정, +100줄)

**중요 — 라우트 순서**:

```python
@router.get("/personal")                    # 기존
@router.get("/personal/forecasts")          # 신규 — {ticker} 보다 먼저!
@router.get("/personal/{ticker}")           # 기존
@router.get("/personal/{ticker}/history")   # 신규
```

> FastAPI는 경로 매칭을 선언 순서대로 하므로 `/personal/forecasts`를 `/personal/{ticker}` 앞에 선언해야 "forecasts"가 ticker로 잡히지 않음.

**라우트 1 — /personal/{ticker}/history**:

```python
@router.get("/personal/{ticker}/history")
def personal_history(
    ticker: str, request: Request, db: Session = Depends(get_db),
):
    """과거 분석 회고 페이지."""
    # 1. DimStock by ticker
    # 2. get_reports_by_ticker(session, ticker, limit=60)
    # 3. get_actions_by_ticker(session, ticker, limit=60)
    # 4. get_changes_by_ticker(session, ticker, limit=60)
    # 5. get_evaluated_forecasts_by_ticker(session, ticker)
    # 6. 타임라인 차트 데이터 구성:
    #    timeline_data = {
    #      "dates": [id_to_date(a.date_id).isoformat() for a in actions],
    #      "convictions": [a.conviction for a in actions],
    #      "grades": [a.action_grade for a in actions],
    #    }
    # 7. render personal_history.html
```

**라우트 2 — /personal/forecasts**:

```python
@router.get("/personal/forecasts")
def personal_forecasts(
    request: Request, db: Session = Depends(get_db),
):
    """예측 정확도 리더보드."""
    # 1. get_all_evaluated_forecasts(session)
    # 2. compute_accuracy_scores(forecasts) → list[ForecastAccuracy]
    # 3. 종합 점수 내림차순 정렬
    # 4. 차트 데이터:
    #    chart_data = [
    #      {"ticker": a.ticker, "hit_rate": a.hit_rate*100,
    #       "direction": a.direction_accuracy*100, "overall": a.overall_score*100}
    #      for a in accuracy_scores
    #    ]
    # 5. 시나리오별 요약:
    #    scenario_summary = {"BASE": {}, "BULL": {}, "BEAR": {}}
    # 6. render personal_forecasts.html
```

**기존 라우트 수정**:
- `personal_dashboard()`: changes 건수 조회 추가 (카드 배지용)
- `personal_detail()`: changes 목록 조회 추가 (변경사항 섹션용)

**테스트**: T16에서 `test_history_route_200`, `test_forecasts_route_200`.

**완료 정의**: 4개 라우트 모두 200, 데이터 정확, 라우트 순서 정상.

---

### Task 13: personal_history.html 템플릿 (2.5h)

**건드릴 파일**: `src/web/templates/personal_history.html` (신규, ~180줄)

**레이아웃 구성**:

```
{% extends "base.html" %}

Breadcrumb: 개인 분석 > {ticker} ({name}) > 히스토리

[액션 타임라인 차트]  ← actionTimelineChart
  conviction 추이 라인 + 등급 변경 마커

[분석 이력]  ← 날짜순 카드 또는 테이블
  각 행: 날짜 | action_grade 배지 | conviction 바 | AI synthesis 발췌

[변경 이력]  ← 테이블
  각 행: 날짜 | change_type | description | severity 배지
  severity 색상: critical=red, warning=orange, info=blue

[시나리오 정확도]  ← 테이블 (평가 완료된 것만)
  각 행: 날짜 | horizon | scenario | 예측 범위 | 실제 가격 | 적중 여부 (✓/✗)
  적중=green, 미적중=red

빈 상태: "아직 분석 이력이 없습니다. 파이프라인을 먼저 실행하세요."
```

**템플릿 데이터** (라우트에서 전달):
- `ticker`, `stock`, `reports`, `actions`, `changes`, `evaluated_forecasts`
- `timeline_data` (JSON, actionTimelineChart용)
- `current_path`

**차트 초기화**:

```javascript
{% if timeline_data %}
<script>
  actionTimelineChart('timelineChart', {{ timeline_data | tojson }});
</script>
{% endif %}
```

**extends**: `base.html` (기존 Tailwind + 다크모드 인프라)

**테스트**: T16에서 `test_history_route_200` (렌더링 에러 없음).

**완료 정의**: 페이지 렌더링, 차트 초기화, 빈 상태 처리, 다크모드 호환.

---

### Task 14: personal_forecasts.html 템플릿 (2h)

**건드릴 파일**: `src/web/templates/personal_forecasts.html` (신규, ~160줄)

**레이아웃 구성**:

```
{% extends "base.html" %}

Breadcrumb: 개인 분석 > 예측 정확도

[정확도 바 차트]  ← accuracyBarChart
  종목별 hit_rate / direction / overall 비교

[리더보드 테이블]  ← overall_score 내림차순
  # | Ticker | Name | 평가 수 | 적중률 | 방향 정확도 | 종합 점수
  종합 점수 셀: 색상 (>=70% green, >=50% yellow, <50% red)

[기간별 필터]  ← 클라이언트 사이드 JS 토글
  버튼: 전체 | 1M | 3M | 6M
  data-horizon 속성으로 필터링

[시나리오별 히트율]  ← 요약 테이블
  Scenario | 평가 수 | 적중률 | 평균 확률
  BASE     | 15     | 67%   | 0.50
  BULL     | 15     | 40%   | 0.28
  BEAR     | 15     | 45%   | 0.22

빈 상태: "아직 만기 도래한 예측이 없습니다. 분석 시작 후 1개월 뒤부터 데이터가 축적됩니다."

면책 문구: "투자 참고용이며 투자 권유가 아닙니다."
```

**기간별 필터 구현** (순수 JS, Alpine.js 미사용):

```javascript
document.querySelectorAll('[data-filter]').forEach(btn => {
    btn.addEventListener('click', () => {
        const horizon = btn.dataset.filter;
        document.querySelectorAll('[data-horizon]').forEach(row => {
            row.style.display = (horizon === 'all' || row.dataset.horizon === horizon) ? '' : 'none';
        });
    });
});
```

**테스트**: T16에서 `test_forecasts_route_200`.

**완료 정의**: 리더보드 정렬, 필터 동작, 빈 상태 처리.

---

### Task 15: personal.html + personal_detail.html 업데이트 (1.5h)

**건드릴 파일**:
- `src/web/templates/personal.html` (수정, +15줄)
- `src/web/templates/personal_detail.html` (수정, +30줄)

**personal.html 변경 — 변경 배지**:

카드 `<a>` 태그에 `class="relative"` 추가. 액션 배지 옆에:

```html
{% if card.change_count and card.change_count > 0 %}
<span class="absolute top-2 right-2 w-5 h-5 bg-red-500 text-white text-xs
             rounded-full flex items-center justify-center font-bold">
    {{ card.change_count }}
</span>
{% endif %}
```

**personal_detail.html 변경**:

1. Breadcrumb 영역에 히스토리 링크 추가:
```html
<a href="/personal/{{ ticker }}/history"
   class="text-sm text-indigo-500 hover:underline">히스토리 보기 &rarr;</a>
```

2. 요약 탭 하단에 변경사항 섹션 추가:
```html
{% if changes %}
<div class="mt-6 border-t pt-4">
    <h4 class="text-lg font-semibold mb-3">오늘의 변경사항</h4>
    {% for c in changes %}
    <div class="flex items-center gap-2 py-1">
        <span class="px-2 py-0.5 rounded text-xs font-medium
            {% if c.severity == 'critical' %}bg-red-100 text-red-800
            {% elif c.severity == 'warning' %}bg-yellow-100 text-yellow-800
            {% else %}bg-blue-100 text-blue-800{% endif %}">
            {{ c.severity }}
        </span>
        <span class="text-sm">{{ c.description }}</span>
    </div>
    {% endfor %}
</div>
{% endif %}
```

**라우트 수정** (`personal.py`):
- `personal_dashboard()`: 오늘 date_id 기준 `get_changes_by_date()` → 종목별 change_count 집계
- `personal_detail()`: `get_changes_by_ticker()` (limit=10, 오늘 날짜 필터) → template에 전달

**테스트**: T16에서 `test_card_change_badge`, `test_detail_changes_section`.

**완료 정의**: 카드 배지 표시, 상세 변경 섹션 표시, 히스토리 링크 동작.

---

### Task 16: 테스트 + cron 스크립트 (3h)

**건드릴 파일**:
- `tests/test_deepdive_pairs.py` (신규, ~120줄)
- `tests/test_deepdive_diff.py` (신규, ~130줄)
- `tests/test_deepdive_forecast.py` (신규, ~120줄)
- `tests/test_deepdive_pipeline.py` (수정, +4개 테스트)
- `tests/test_deepdive_web.py` (수정, +4개 테스트)
- `scripts/run_deepdive.sh` (신규, ~25줄)

**test_deepdive_pairs.py** (5개):

```python
def test_cosine_similarity_identical():
    """동일 수익률 벡터 → 유사도 1.0."""

def test_cosine_similarity_opposite():
    """반대 수익률 벡터 → 유사도 -1.0."""

def test_select_peers_sector_filter():
    """동일 섹터 종목만 반환."""

def test_select_peers_market_cap_filter():
    """0.3x~3x 범위 내 종목만 반환."""

def test_refresh_peers_staleness():
    """7일 미만 → 기존 페어 재사용, 7일 이상 → 갱신."""
```

**test_deepdive_diff.py** (6개):

```python
def test_detect_action_changed():
    """HOLD→ADD 감지 → severity=critical."""

def test_detect_conviction_shift():
    """|7-4|=3 ≥ 2 → severity=warning."""

def test_detect_probability_shift():
    """50%→35% = 15pp ≥ 10 → severity=info."""

def test_detect_new_risk():
    """신규 리스크 이벤트 → severity=warning."""

def test_detect_no_previous():
    """이전 리포트 없음 → 빈 리스트."""

def test_detect_no_changes():
    """동일 결과 → 빈 리스트."""
```

**test_deepdive_forecast.py** (5개):

```python
def test_maturity_date_calculation():
    """1M=30d, 3M=90d, 6M=180d 확인."""

def test_hit_range_in_range():
    """actual_price가 [low, high] 범위 내 → True."""

def test_hit_range_outside():
    """actual_price가 범위 외 → False."""

def test_accuracy_score_calculation():
    """hit_rate*0.6 + direction*0.4 가중 공식 검증."""

def test_accuracy_empty_forecasts():
    """평가 0건 → overall_score = 0."""
```

**test_deepdive_pipeline.py 추가** (4개):

```python
def test_step4_pairs_called():
    """step4가 steps 리스트에 포함, 호출됨."""

def test_step6_diff_called():
    """step6가 steps 리스트에 포함, 호출됨."""

def test_step7_maturity_update():
    """만기 도래 예측 actual_price 업데이트."""

def test_step8_enhanced_notify():
    """강화된 알림 메시지 포맷."""
```

**test_deepdive_web.py 추가** (4개):

```python
def test_history_route_200():
    """/personal/AAPL/history → 200."""

def test_forecasts_route_200():
    """/personal/forecasts → 200."""

def test_card_change_badge():
    """카드에 change_count 전달."""

def test_detail_changes_section():
    """상세 페이지에 changes 전달."""
```

**cron 스크립트** — `scripts/run_deepdive.sh`:

```bash
#!/usr/bin/env bash
# Deep Dive daily pipeline cron wrapper
# Crontab 예시:
#   0 7 * * 1-5 /path/to/investmate/scripts/run_deepdive.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# 가상환경 활성화
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
    source .venv/Scripts/activate
fi

# 로그 디렉토리
mkdir -p logs

# 실행
python -m src.main deepdive run --date "$(date +%Y-%m-%d)" \
    2>&1 | tee -a "logs/deepdive_$(date +%Y%m%d).log"
```

**총 신규 테스트**: 24개 (기존 45 + 신규 24 = **69개**)

**완료 정의**: 69개 테스트 전부 통과, 기존 45개 회귀 없음.

---

## 6. 페어 자동 선정 알고리즘 상세

### 유니버스

`DimStock` WHERE `is_sp500=True` AND `is_active=True` (~500종목). DB에 이미 존재하므로 외부 API 호출 불필요.

### 3단계 필터

```
단계 1: GICS 섹터 매칭
  DimStock.sector_id == target.sector_id
  → ~20-80 종목 (섹터 규모 의존)
  [폴백] 후보 < 10 → 산업 무시, 섹터만 매칭

단계 2: 시총 근접도
  FactValuation 최신 market_cap 조회
  0.3 <= peer_cap / target_cap <= 3.0
  → ~10-30 종목
  [폴백] 후보 < 5 → 0.1x-10x 완화

단계 3: 60일 수익률 코사인 유사도
  FactDailyPrice 최근 60거래일 종가 → 일간 수익률
  numpy 코사인: dot(a,b) / (norm(a) * norm(b))
  상위 5개 선택
```

### 코사인 유사도 계산

```python
import numpy as np

def _cosine_sim(a: list[float], b: list[float]) -> float:
    """두 수익률 벡터의 코사인 유사도."""
    a_arr = np.array(a)
    b_arr = np.array(b)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))
```

### 갱신 주기 / 트리거

- **주기**: 7 캘린더일 staleness
- **트리거**: `deepdive_pipeline.step4_pairs()` 실행 시 `WatchlistRepository.get_pairs_updated_at()`으로 체크
- **갱신 흐름**: staleness 초과 시 `select_peers()` 실행 → `upsert_pairs()` 저장
- weekly_pipeline에는 추가하지 않음 (독립성 유지)

### 데이터 소스

| 데이터 | 소스 | 비고 |
|--------|------|------|
| 섹터/산업 | `DimStock.sector_id` + `DimSector` | 이미 DB에 존재 |
| 시총 | `FactValuation.market_cap` 최신 | 일일 파이프라인이 수집 |
| 60일 종가 | `FactDailyPrice.close` | 일일 파이프라인이 수집 |
| PER | `FactValuation.per` 최신 | 비교 표시용 |

---

## 7. Diff Detection 로직 상세

### 전일 리포트 로드 방법

```python
prev_report = DeepDiveRepository.get_previous_report(session, stock_id, current_date_id)
# → WHERE stock_id = :sid AND date_id < :current_date_id
#   ORDER BY date_id DESC LIMIT 1
```

`report_json` 파싱:

```python
prev_data = json.loads(prev_report.report_json)
prev_grade = prev_data["ai_result"]["action_grade"]
prev_conviction = prev_data["ai_result"]["conviction"]
prev_risks = prev_data.get("layers", {}).get("layer5", {}).get("risk_events", [])
```

### 변경 감지 기준 (수치 임계값 포함)

| 변경 유형 | 조건 | severity | 예시 description |
|-----------|------|----------|-----------------|
| `action_changed` | `curr_grade != prev_grade` | critical | "HOLD → ADD (conviction 7→8)" |
| `conviction_shift` | `abs(curr_conv - prev_conv) >= 2` (등급 동일 시) | warning | "확신도 7 → 4 (3단계 하락)" |
| `probability_shift` | `abs(curr_prob - prev_prob) >= 0.10` (동일 horizon+scenario) | info | "1M BASE 확률 50% → 35% (-15pp)" |
| `new_risk` | `curr_risks - prev_risks != {}` (set 차집합) | warning | "신규 리스크: 규제 리스크 부각" |
| `trigger_hit` | prev trigger 키워드가 현재 데이터에 매칭 | critical | "트리거 도달: 실적 발표 후 가이던스 하향" |

### 변경 카테고리 분류

```
critical → 즉시 주의 필요 (액션 변경, 트리거 도달)
warning  → 모니터링 필요 (확신도 변화, 신규 리스크)
info     → 참고 사항 (확률 변화)
```

### fact_deepdive_changes 스키마 (이미 존재)

```sql
fact_deepdive_changes (
    change_id   INTEGER PRIMARY KEY,
    date_id     INTEGER NOT NULL,
    stock_id    INTEGER NOT NULL,
    ticker      VARCHAR(20) NOT NULL,
    change_type VARCHAR(30) NOT NULL,    -- action_changed/conviction_shift/probability_shift/new_risk/trigger_hit
    description TEXT NOT NULL,            -- 사람이 읽을 수 있는 설명
    severity    VARCHAR(10) DEFAULT 'info', -- critical/warning/info
    created_at  DATETIME,
    updated_at  DATETIME
)
INDEX: idx_dd_changes_date(date_id), idx_dd_changes_ticker(ticker, date_id)
```

---

## 8. 과거 분석 회고 페이지 구성

### 라우트

```
GET /personal/{ticker}/history
```

### 쿼리

```python
reports = DeepDiveRepository.get_reports_by_ticker(session, ticker, limit=60)
actions = DeepDiveRepository.get_actions_by_ticker(session, ticker, limit=60)
changes = DeepDiveRepository.get_changes_by_ticker(session, ticker, limit=60)
forecasts = DeepDiveRepository.get_evaluated_forecasts_by_ticker(session, ticker)
```

### 템플릿 구조

```
personal_history.html (extends base.html)
├── Breadcrumb: 개인 분석 > AAPL (Apple Inc.) > 히스토리
├── 액션 타임라인 차트 (chart-lg)
│   └── actionTimelineChart: conviction 라인 + grade 마커
├── 분석 이력 섹션
│   └── 날짜별 카드: date | grade 배지 | conviction 바 | synthesis 발췌
├── 변경 이력 테이블
│   └── date | type | description | severity 배지
└── 시나리오 정확도 테이블
    └── date | horizon | scenario | 예측범위 | 실제가 | 적중여부
```

### 차트 (charts.js 컨벤션 준수)

`actionTimelineChart`:
- `initChart(domId)` 호출 → ECharts 인스턴스
- X축: dates (category)
- Y축: conviction 1-10 (min/max 고정)
- 시리즈 1: conviction 라인 (primary color)
- 시리즈 2: grade 마커 (scatter, 색상별)
- tooltip: `enhancedTooltipFormatter` 패턴 적용
- 반응형: `chart-lg` CSS 클래스

### 데이터 쿼리

기존 `DeepDiveRepository.get_reports_by_ticker()` (Phase 1에서 구현, `src/db/repository.py:952`) 재사용. limit=60 = 약 3개월치.

---

## 9. 예측 정확도 측정 로직

### 1M/3M/6M 시점 도래 판단

```python
HORIZON_DAYS = {"1M": 30, "3M": 90, "6M": 180}

forecast_date = id_to_date(forecast.date_id)           # 예측 생성일
maturity_date = forecast_date + timedelta(days=HORIZON_DAYS[forecast.horizon])
is_matured = maturity_date <= target_date               # target_date = 파이프라인 실행일
```

### 실제 가격 매칭

```python
def _get_actual_price_at_date(session, stock_id, target_date, max_lookback=5):
    """target_date 또는 직전 5거래일 이내 종가."""
    target_date_id = date_to_id(target_date)
    min_date_id = date_to_id(target_date - timedelta(days=max_lookback))

    price = session.execute(
        select(FactDailyPrice)
        .where(
            FactDailyPrice.stock_id == stock_id,
            FactDailyPrice.date_id <= target_date_id,
            FactDailyPrice.date_id >= min_date_id,
        )
        .order_by(FactDailyPrice.date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if price:
        return float(price.close), id_to_date(price.date_id)
    return None
```

### Base/Bull/Bear 시나리오별 적중 기준

```
hit_range = (forecast.price_low <= actual_price <= forecast.price_high)
```

모든 시나리오에 동일한 적중 기준 적용 (범위 내 여부).

### 방향 정확도

동일 `report_id` + `horizon`에서 `scenario='BASE'` 찾기:

```python
base_midpoint = (base_forecast.price_low + base_forecast.price_high) / 2

if forecast.scenario == "BULL":
    direction_correct = (actual_price > base_midpoint)
elif forecast.scenario == "BEAR":
    direction_correct = (actual_price < base_midpoint)
else:  # BASE
    direction_correct = forecast.hit_range
```

### 정확도 점수 공식

```python
# 종목별 계산
hit_rate = hit_count / total_evaluated                              # 0.0-1.0
direction_accuracy = direction_correct_count / total_evaluated       # 0.0-1.0
overall_score = hit_rate * 0.6 + direction_accuracy * 0.4           # 0.0-1.0

# 호라이즌별 분리
by_horizon = {
    "1M": {"hit_rate": 0.65, "count": 10, "direction": 0.70},
    "3M": {"hit_rate": 0.60, "count": 8, "direction": 0.63},
    "6M": {"hit_rate": 0.55, "count": 4, "direction": 0.50},
}

# 시나리오별 분리
by_scenario = {
    "BASE": {"hit_rate": 0.67, "count": 15, "avg_prob": 0.50},
    "BULL": {"hit_rate": 0.40, "count": 15, "avg_prob": 0.28},
    "BEAR": {"hit_rate": 0.45, "count": 15, "avg_prob": 0.22},
}
```

---

## 10. 리더보드 페이지 구성

### 라우트

```
GET /personal/forecasts
```

### 템플릿 구조

```
personal_forecasts.html (extends base.html)
├── Breadcrumb: 개인 분석 > 예측 정확도
├── 정확도 바 차트 (chart-lg)
│   └── accuracyBarChart: 종목별 hit/direction/overall 수평 바
├── 기간별 필터 버튼 (전체/1M/3M/6M)
├── 리더보드 테이블
│   └── # | Ticker | Name | 평가수 | 적중률 | 방향정확도 | 종합점수 | 등급배지
├── 시나리오별 히트율 테이블
│   └── Scenario | Count | Hit Rate | Avg Probability
└── 면책 문구
```

### 정렬/필터

- **기본 정렬**: overall_score DESC
- **기간별 필터**: 클라이언트 사이드 JS
  - 테이블 row에 `data-horizon="1M"`, `data-horizon="3M"`, `data-horizon="6M"` 속성
  - 버튼 클릭 시 해당 horizon만 표시
- **등급 배지**: ≥70% green("우수"), ≥50% yellow("보통"), <50% red("개선 필요")

### 차트

`accuracyBarChart`:
- 수평 그룹 바 (ECharts bar, horizontal)
- Y축: 종목 (ticker)
- X축: 0-100%
- 3개 시리즈: hit_rate(#6366f1), direction(#22c55e), overall(#a855f7)
- responsive, dark mode

---

## 11. weekly_pipeline에 페어 갱신 step 추가 방법

> **결정: Phase 3에서는 weekly_pipeline에 step을 추가하지 않음.**

### 사유

1. Phase 1+2에서 deepdive_pipeline은 daily pipeline과 **완전 독립**으로 설계됨
2. weekly_pipeline은 주간 리포트 생성 전용 (5-step: report → AI → PDF → email → notify)
3. 페어 갱신은 deepdive_pipeline의 step4_pairs에서 7일 staleness 체크로 처리
4. 이 방식이 deepdive의 독립성을 유지하면서 주 1회 이상 갱신 효과를 달성

### 대안 (향후 고려)

만약 deepdive_pipeline이 매일 실행되지 않는 상황이 발생하면, weekly_pipeline에 `dd_weekly_pairs` step을 추가하여 주 1회 강제 갱신할 수 있음. 현재는 불필요.

---

## 12. 알림 메시지 포맷 변경

### 현재 (Phase 2)

```
[Investmate] Deep dive 완료: 12종목 분석
액션: HOLD 8건, ADD 3건, TRIM 1건
```

### Phase 3 확장

```
[Investmate] Deep dive 완료: 12종목
액션 변경 2건: NVDA HOLD→TRIM, AAPL ADD→HOLD
신규 리스크 1건: TSLA 규제 이슈
예측 만기 3건: 적중 2/3
HOLD 7건, ADD 2건, TRIM 2건, EXIT 1건
```

### `send_deepdive_summary()` 시그니처 확장

```python
def send_deepdive_summary(
    run_date: date,
    stock_count: int,
    action_summary: dict[str, int],
    failed_count: int = 0,
    channel: str | None = None,
    # Phase 3 신규 파라미터:
    action_changes: list[tuple[str, str, str]] | None = None,
    # [(ticker, old_grade, new_grade), ...]
    new_risks: list[tuple[str, str]] | None = None,
    # [(ticker, risk_description), ...]
    matured_summary: str | None = None,
    # "3건 (적중 2/3)"
) -> bool:
```

### step8_notify 변경

```python
# 액션 변경 추출
action_changes = []
for ticker, changes in self._change_results.items():
    for c in changes:
        if c.change_type == "action_changed":
            # description에서 old→new 파싱 또는 직접 구성
            action_changes.append((ticker, prev_grade, curr_grade))

# 신규 리스크 추출
new_risks = []
for ticker, changes in self._change_results.items():
    for c in changes:
        if c.change_type == "new_risk":
            new_risks.append((ticker, c.description))
```

---

## 13. Phase 3 통합 테스트 시나리오

### End-to-End 검증 순서

```
1. DB 초기화
   investmate db init

2. 워치리스트 시드
   python scripts/seed_watchlist.py   # 12종목

3. Deep Dive 1회차 실행
   investmate deepdive run --date 2026-04-01 --force
   검증:
   - FactCollectionLog: 8개 step 모두 success
   - fact_deepdive_reports: 12건 INSERT
   - fact_deepdive_actions: 12건 (prev_action_grade=NULL — 첫 실행)
   - fact_deepdive_forecasts: ~108건 (12종목 × 9 시나리오)
   - dim_watchlist_pairs: 12종목 × 5페어 = ~60건
   - fact_deepdive_changes: 0건 (첫 실행, 이전 리포트 없음)

4. Deep Dive 2회차 실행 (다음 날)
   investmate deepdive run --date 2026-04-02 --force
   검증:
   - fact_deepdive_changes: N건 (변경 감지됨)
   - fact_deepdive_actions: prev_action_grade 채워짐
   - dim_watchlist_pairs: updated_at 변경 없음 (7일 미만)
   - step8 알림 메시지에 변경 내용 포함

5. 만기 도래 시뮬레이션 (31일 후)
   investmate deepdive run --date 2026-05-02
   검증:
   - fact_deepdive_forecasts: 1M 만기 건의 actual_price/hit_range 채워짐
   - step7 로그에 "예측 만기 업데이트: N건"

6. 웹 검증
   investmate web
   - /personal → 카드에 변경 배지 표시
   - /personal/AAPL → 변경사항 섹션, 히스토리 링크
   - /personal/AAPL/history → 타임라인 차트, 변경/분석 이력
   - /personal/forecasts → 리더보드 (만기 도래 건 존재 시)

7. 단위 테스트
   pytest tests/test_deepdive_*.py -v --tb=short
   → 69개 전부 통과
```

### 자동화 테스트로 검증하는 항목

| 검증 | 테스트 |
|------|--------|
| 코사인 유사도 계산 정확성 | test_cosine_similarity_* |
| 변경 감지 5가지 유형 | test_detect_* |
| 만기 날짜 계산 | test_maturity_date_calculation |
| 적중 범위 판정 | test_hit_range_* |
| 정확도 점수 공식 | test_accuracy_score_calculation |
| 파이프라인 8-step 실행 | test_step4/6 |
| 웹 라우트 200 | test_history/forecasts_route_200 |

---

## 14. Phase 3 완료 정의

다음이 **모두** 충족되면 "Deep Dive 기능 전체 완성":

| # | 기준 | 검증 방법 |
|---|------|----------|
| 1 | 파이프라인 8개 step 전부 정상 실행 | `investmate deepdive run` → FactCollectionLog 8개 success |
| 2 | 페어 자동 선정 | `dim_watchlist_pairs` 행 존재, 7일 staleness 재사용 |
| 3 | 변경 감지 | `fact_deepdive_changes` 행 존재 (2회 이상 실행 후) |
| 4 | 예측 만기 추적 | `fact_deepdive_forecasts.actual_price` NOT NULL 건 존재 (30일+ 후) |
| 5 | `/personal/{ticker}/history` 정상 렌더링 | 브라우저 접속 200, 차트 표시 |
| 6 | `/personal/forecasts` 정상 렌더링 | 브라우저 접속 200, 리더보드 표시 |
| 7 | 카드 변경 배지 | `/personal` 카드에 빨간 숫자 배지 |
| 8 | 강화 알림 | Telegram 메시지에 액션 변경/리스크 포함 |
| 9 | 69개 테스트 전부 통과 | `pytest tests/test_deepdive_*.py -v` |
| 10 | cron 스크립트 | `scripts/run_deepdive.sh` 실행 가능 |
| 11 | 기존 기능 회귀 없음 | `pytest tests/ -v` 전체 통과 |

---

## 15. Deep Dive 기능 전체 회고 항목

Phase 3 완료 후 `docs/plans/final_retrospective.md`에 작성할 항목:

### 구조

```markdown
# Deep Dive 기능 전체 회고 (Final Retrospective)

## 1. 프로젝트 요약
- 총 기간 (Phase 1 시작일 ~ Phase 3 완료일)
- 총 커밋 수, 총 코드 줄 수 (src/deepdive/ + 관련 수정)
- 총 테스트 수 (최종)

## 2. Phase별 회고
### Phase 1 (MVP)
- 목표 vs 실제 (기간, task 수)
- 잘한 것 / 개선점

### Phase 2 (토론 + 상세)
- 목표 vs 실제
- AI CLI 호출 안정성 학습
- 잘한 것 / 개선점

### Phase 3 (완성)
- 목표 vs 실제
- 잘한 것 / 개선점

## 3. 아키텍처 결정 평가
- 독립 파이프라인 (daily vs deepdive) → 적절했는가?
- CLI 기반 AI 호출 → SDK 대비 장단점
- Star Schema 테이블 설계 → 조회 성능
- 7일 staleness 기반 페어 갱신 → 적절한 빈도였는가?

## 4. AI 관련 학습
- Opus 모델 분석 품질 (기대 vs 실제)
- 토론 방식의 효과 (단일 호출 대비 개선 정도)
- JSON 파싱 안정성 (실패율, fallback 빈도)
- 비용 추적 (일일/월간 추정)

## 5. 예측 정확도 초기 결과
- 1M/3M/6M 적중률 (첫 한 달)
- Base/Bull/Bear 시나리오별 편향
- 정확도 개선을 위한 향후 방향

## 6. 기술부채 목록
- 파일 크기 초과 (deepdive_pipeline.py)
- 미구현 기능 (옵션 PCR/IV)
- 성능 최적화 필요 지점

## 7. 향후 로드맵 제안
- Phase 4 후보 기능들
- 데이터 소스 확장
- 포트폴리오 수준 deepdive 통합
```

---

## 16. 위험 요소와 미해결 질문

### 위험 요소

| # | 리스크 | 심각도 | 경감 방안 |
|---|--------|--------|----------|
| 1 | **코사인 유사도 데이터 부족**: 신규 등록 종목의 가격 데이터 60일 미만 | 중 | 20일 미만이면 페어 선정 스킵, 가용 기간으로 계산 |
| 2 | **SQLite 날짜 연산 제약**: 만기 도래 계산을 SQL로 할 수 없음 | 저 | Python 필터링으로 우회 (성능 영향 미미 — 예측 수 수백 건 수준) |
| 3 | **파이프라인 실행 시간 증가**: step4(페어) + step6(diff) 추가 | 저 | 페어 7일 캐시로 대부분 스킵. diff는 이전 리포트 1건 조회만 |
| 4 | **라우트 순서 충돌**: `/personal/forecasts`가 `{ticker}`로 매칭될 위험 | 중 | **반드시** `/personal/forecasts`를 `{ticker}` 앞에 선언 |
| 5 | **deepdive_pipeline.py 줄 수 초과**: 현재 440줄 + ~120줄 = ~560줄 | 중 | step4/step6를 별도 메서드 파일로 분리 가능 (필요 시) |
| 6 | **만기 도래 비거래일**: 주말/공휴일에 가격 없음 | 저 | 5거래일 lookback으로 직전 종가 매칭 |
| 7 | **trigger_hit 감지 정확도**: 자연어 트리거 조건 매칭이 부정확할 수 있음 | 저 | 키워드 기반 간이 매칭. 오탐이 발생하면 Phase 4에서 AI 기반으로 전환 |

### 미해결 질문

| # | 질문 | 대응 |
|---|------|------|
| 1 | **방향 정확도에서 BASE midpoint가 없는 경우**: report_id 내에 BASE 시나리오가 파싱 실패로 누락될 수 있음 | BASE 없으면 방향 정확도 스킵, hit_rate만으로 overall_score 계산 |
| 2 | **페어 비교 탭**: 상세 페이지에 "페어 비교" 탭을 추가할지 별도 섹션으로 할지 | Phase 3에서는 AI 프롬프트 주입만. 웹 UI 탭은 Phase 4 후보 |
| 3 | **정확도 리더보드 최소 데이터**: 평가 건수 1-2건일 때 점수가 의미 있는가 | 최소 5건 이상일 때만 등급 배지 표시, 미만이면 "데이터 부족" |
| 4 | **deepdive_pipeline.py 파일 분할**: 560줄 예상 — 400줄 초과 | 구현 시 step4/step6 로직이 길면 별도 모듈로 추출하여 파이프라인은 호출만 담당하도록 리팩터 |
