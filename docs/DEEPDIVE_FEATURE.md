# Deep Dive 개인 분석 — 기능 명세서

> Phase 1 ~ Phase 10 전체 구현 완료 · 172개 Deep Dive 테스트 통과
> 최종 업데이트: 2026-04-11

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

### 기타 테이블 (변경 없음)

- `FactDeepDiveForecast`: 9개 시나리오 예측, 만기 시 actual_price/hit_range 업데이트
- `FactDeepDiveAction`: 액션 히스토리 (prev_action_grade, prev_conviction 포함)
- `FactDeepDiveChange`: 일일 변경 감지 결과

---

## 9. 웹 대시보드

### 라우트

| Path | 설명 |
|------|------|
| `/personal` | 워치리스트 카드 그리드 (EV·R/R·entry 거리·NOW/AVOID 배지) |
| `/personal/{ticker}` | 종목 상세 — Hero + **실행 가이드 카드** + 6탭 |
| `/personal/{ticker}/history` | 과거 분석 타임라인 (conviction/action 변화) |
| `/personal/forecasts` | 예측 정확도 리더보드 (종목별 hit_rate / direction_accuracy) |

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

---

## 13. 주요 파일 맵

### 소스 코드
```
src/deepdive/
├── schemas.py                — 모든 DTO (AIResult, ExecutionGuide 등)
├── layers.py                 — 6레이어 오케스트레이션 + _safe_call
├── layers_fundamental.py     — Layer 1
├── layers_valuation.py       — Layer 2
├── layers_technical.py       — Layer 3 (+ atr_14 추가)
├── layers_flow.py            — Layer 4
├── layers_narrative.py       — Layer 5
├── layers_macro.py           — Layer 6
├── ai_prompts.py             — build_stock_context + run_deepdive_cli
├── ai_debate_cli.py          — 3라운드 토론 + 프롬프트
├── execution_guide.py        — 실행 가이드 계산 [Phase 5]
├── alert_engine.py           — 알림 트리거 [Phase 8]
├── forecast_evaluator.py     — 정확도 평가 + hit_rate discount [Phase 9]
├── diff_detector.py          — 변경 감지
├── pair_analysis.py          — 페어 자동 선정
├── scenarios.py              — 시나리오 파싱
└── watchlist_manager.py      — 워치리스트 I/O

src/deepdive_pipeline.py      — 9단계 오케스트레이터
src/web/routes/personal.py    — 웹 라우트
src/web/templates/personal.html          — 카드 그리드
src/web/templates/personal_detail.html   — 상세 페이지
src/web/templates/personal_history.html  — 히스토리
src/web/templates/personal_forecasts.html — 정확도 리더보드
```

### 테스트 (172개)
```
tests/test_deepdive_watchlist.py           — 워치리스트 로드
tests/test_deepdive_layers.py              — 6레이어 계산
tests/test_deepdive_ai.py                  — 3라운드 토론
tests/test_deepdive_ai_extended.py         — [Phase 4] 신규 필드 + 재시도
tests/test_deepdive_execution_guide.py     — [Phase 5] 35 케이스
tests/test_deepdive_feedback_loop.py       — [Phase 9] hit_rate discount
tests/test_deepdive_alerts.py              — [Phase 8] 트리거 16 케이스
tests/test_deepdive_pipeline.py            — 파이프라인 통합
tests/test_deepdive_web.py                 — 웹 라우트
tests/test_deepdive_diff.py                — 변경 감지
tests/test_deepdive_forecast.py            — 시나리오 평가
tests/test_deepdive_pairs.py               — 페어 선정
```

---

## 14. 운영 가이드

### 첫 실행
```bash
# 워치리스트 등록
investmate watchlist add AAPL
investmate watchlist add MSFT
# 보유 포지션이 있으면
investmate watchlist hold AAPL 15 165.20

# 첫 분석 (강제 실행)
investmate deepdive run --force

# 결과 확인
investmate deepdive latest
investmate web  # → http://localhost:8000/personal
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

**현 한계**
- CLI subprocess 5회 순차 호출 — 워치리스트 12종목 × 5콜 = 60콜, 수십 분 소요. 병렬화 옵션은 향후 과제
- `invalidation_conditions`는 UI에 표시만 되고 **자동 모니터링 없음** — 알림 엔진은 가격 기반 트리거만 평가
- SDK Tool Use 전환 — 현재는 Claude CLI subprocess. SDK structured output으로 전환하면 신뢰성·속도 개선 여지
- 촉매 캘린더(earnings date, ex-div) 알림 — Phase 5의 내러티브 레이어에 데이터는 있으나 alert_engine 트리거 미구현
- 옵션 체인 기반 implied move — 데이터 소스 부재로 out of scope

**확장 여지**
- `invalidation_hit` 자동 감지: `next_review_trigger` 문자열을 rule 기반으로 파싱 (예: "RSI 40 하회" → 현재 RSI 추적)
- 리밸런싱 제안 자동화: 포트폴리오 전체의 Sharpe/R/R 최적화 관점에서 제안 비중 조정
- AI 오버라이드 반사실 분석: DailyPipeline의 `counterfactual.py` 패턴을 deepdive에도 적용
- 주간 요약 리포트: 7일치 변경·알림·정확도를 PDF로 이메일 발송

---

## 16. 면책

본 시스템은 **투자 참고용**이며 투자 권유가 아니다. 모든 분석·추천·가이드는 과거 데이터와 AI 모델의 예측에 기반하며 실제 투자 결과를 보장하지 않는다. 최종 투자 결정과 그에 따른 책임은 전적으로 사용자에게 있다.
