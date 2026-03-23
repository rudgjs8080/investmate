# REQUIREMENTS.md — 추가 요구사항

> CLAUDE.md의 기본 명세를 보완하는 추가/변경 사항입니다.
> CLAUDE.md와 함께 읽어주세요.

---

## 1. AI 분석 자동화 (Claude Code CLI)

### 개요

STEP 4(스크리닝) 완료 후, Claude Code의 비대화형 모드(`claude -p`)를 활용하여
Claude AI에게 자동으로 분석을 요청하고 결과를 리포트에 포함한다.
API 키 없이 Claude.ai Pro Max 구독 + Claude Code로 완전 자동화.

### 작동 원리

Claude Code는 `claude -p` 플래그로 비대화형 실행이 가능하다.
stdin으로 프롬프트를 넘기면 stdout으로 AI 응답을 받을 수 있다.

```bash
# 기본 사용법
cat reports/2025-03-16_prompt.txt | claude -p > reports/2025-03-16_ai_analysis.md
```

### 파이프라인 통합 (STEP 4.5 신규)

기존 STEP 4와 STEP 5 사이에 AI 분석 단계를 삽입:

```
STEP 4 — 스크리닝 (규칙 기반)
  500개 → 조건 필터링 → 스코어링 → 상위 10개 선정

STEP 4.5 — Claude AI 자동 분석 (신규)
  10개 → Claude Code CLI로 맥락/리스크 분석 → 최종 3-5개 추천 (ai_approved=True)
```

```python
import subprocess

class DailyPipeline:
    def run(self):
        self.step1_collect()
        self.step2_analyze()
        self.step3_external()
        self.step4_screen()
        self.step4_5_ai_analysis()   # 신규: Claude AI 자동 분석
        self.step5_report()           # AI 분석 결과가 리포트에 포함됨
        self.step6_notify()

    def step4_5_ai_analysis(self):
        """Claude Code CLI를 통한 AI 분석 자동 호출"""
        prompt_path = self.prompt_builder.build(self.top_n_stocks)

        result = subprocess.run(
            ["claude", "-p"],
            input=open(prompt_path).read(),
            capture_output=True,
            text=True,
            timeout=300  # 5분 타임아웃
        )

        if result.returncode == 0:
            self.save_ai_analysis(result.stdout)
        else:
            # Claude Code 실패 시 AI 분석 없이 계속 진행
            logger.warning(f"AI 분석 실패: {result.stderr}")
```

### 출력 파일

- `reports/YYYY-MM-DD_prompt.txt` — Claude에게 보낸 프롬프트 (기록용)
- `reports/YYYY-MM-DD_ai_analysis.md` — Claude AI 분석 결과
- `reports/YYYY-MM-DD.md` — 최종 리포트 (AI 분석 결과 포함)
- `reports/YYYY-MM-DD.json` — JSON 내보내기 (AI 분석 결과 포함)

### 에러 처리

- Claude Code가 설치되지 않았거나 로그인되지 않은 경우 → AI 분석 스킵, 로그 기록
- 타임아웃 (5분 초과) → AI 분석 스킵, 로그 기록
- 파이프라인 전체가 실패하지 않도록 try/except로 감싸기
- AI 분석 없이도 규칙 기반 + ML 스코어링 리포트는 정상 생성

### 프롬프트 구조

```
당신은 20년 경력의 월가 시니어 투자 애널리스트입니다.
아래는 {날짜} 기준으로 S&P 500 전 종목(약 500개)을 자동 분석한 결과입니다.
데이터는 모두 실제 시장 데이터이며, 기술적/기본적/외부 요인 분석을 거쳐 스크리닝된 결과입니다.

■ 시장 환경 요약
- VIX: {값} ({안정/주의/위험})
- S&P 500: {종가} (20일선 {위/아래}, 추세: {상승/하락/횡보})
- 10년 국채 금리: {값}% (추세: {상승/하락/안정})
- 달러 인덱스: {값} (추세: {강세/약세/안정})
- 시장 환경 종합 점수: {점수}/10

■ 스크리닝 결과 TOP {N} (500개 중 선별)

{각 종목별 아래 데이터 포함}
{순위}. {티커} — {종목명} (종합 {점수}/10)
   현재가: ${가격} | 전일 대비: {등락률}%
   [기술적 분석]
   - RSI(14): {값} | MACD: {상태} | 볼린저: {위치}
   - 이동평균 배열: {정배열/역배열/혼조}
   - 매수 시그널: {발생한 시그널 목록}
   [기본적 분석]
   - PER: {값} (업종 평균: {값}) | PBR: {값} | ROE: {값}%
   - 매출 성장률(QoQ): {값}% | 부채비율: {값}%
   - 최근 실적 서프라이즈: {상회/하회/부합} ({차이}%)
   [수급/내부자 동향]
   - 내부자 최근 3개월 거래: {순매수/순매도} ${금액}
   - 기관 보유 비중 변화: {증가/감소/유지}
   - 애널리스트 컨센서스: Buy {수} / Hold {수} / Sell {수}, 목표가 ${가격}
   [외부 요인]
   - 섹터({섹터명}) 최근 1개월 수익률: {값}%
   - 관련 뉴스 감성: {긍정/부정/중립} (점수: {값})
   - 주요 뉴스: "{뉴스 제목 1}", "{뉴스 제목 2}"
   [리스크]
   - 실적 발표 예정일: {날짜 또는 "없음"}
   - 공매도 비율: {값}%

■ 분석 요청
위 데이터를 바탕으로 다음을 분석해주세요:

1. 최종 매수 추천: TOP {N} 중 실제 매수를 추천하는 3-5개 종목을 선정하고,
   각 종목의 추천 근거를 데이터 기반으로 상세히 설명해주세요.

2. 제외 종목: TOP {N} 중 매수를 추천하지 않는 종목이 있다면,
   그 이유를 구체적으로 설명해주세요 (수치에 안 잡히는 리스크 포함).

3. 포트폴리오 분산: 추천 종목이 특정 섹터에 쏠려있다면
   분산을 위한 대안 종목을 TOP {N} 내에서 제시해주세요.

4. 매매 전략: 각 추천 종목에 대해
   - 적정 매수 가격대 (현재가 기준 ±N%)
   - 손절 기준 (어느 가격에서 손절할지)
   - 목표 수익률 (1개월, 3개월 기준)
   을 제시해주세요.

5. 시장 리스크: 현재 매크로 환경에서 주의해야 할 점이 있다면 경고해주세요.

※ 본 분석은 투자 참고용이며 투자 권유가 아닙니다.
```

### 프롬프트 생성 모듈

- `src/reports/prompt_builder.py` 신규 생성
- STEP 4(스크리닝)의 fact_daily_recommendations 결과를 기반으로 프롬프트 조립
- 각 종목의 데이터를 DB에서 조회하여 템플릿에 채워넣기
- 프롬프트의 총 토큰 수가 Claude 컨텍스트 제한을 넘지 않도록 관리

### AI 분석 결과 저장

- `src/ai/claude_analyzer.py` 신규 생성
- Claude Code CLI 호출 + 응답 파싱 담당
- AI 분석 결과를 fact_daily_recommendations의 recommendation_reason에 반영
- AI가 제외를 권고한 종목에는 ai_rejected=True 플래그 추가

### fact_daily_recommendations 컬럼 추가

| 컬럼            | 타입                   | 설명                              |
| --------------- | ---------------------- | --------------------------------- |
| ai_approved     | Boolean, default=False | Claude AI가 최종 매수 추천한 종목 |
| ai_reason       | Text, nullable         | Claude AI의 추천/제외 근거        |
| ai_target_price | Decimal, nullable      | AI가 제시한 목표가                |
| ai_stop_loss    | Decimal, nullable      | AI가 제시한 손절 기준             |

### 알림 연동

- STEP 6(알림 발송) 시 AI 분석이 포함된 최종 리포트를 이메일로 발송
- 이메일 제목: "[investmate] {날짜} 데일리 매수 추천 리포트"
- AI가 승인한 최종 3-5개 종목 + 추천 근거가 이메일 본문에 포함

### CLI 명령어 추가

```
investmate prompt latest           # 가장 최근 프롬프트를 터미널에 출력
investmate prompt show YYYY-MM-DD  # 특정 날짜 프롬프트 조회
investmate ai latest               # 가장 최근 AI 분석 결과 출력
investmate ai show YYYY-MM-DD      # 특정 날짜 AI 분석 결과 조회
investmate ai rerun                # AI 분석만 재실행 (프롬프트 재사용)
```

### 실행 환경 요구사항

- Claude Code가 설치되어 있어야 함 (`npm install -g @anthropic-ai/claude-code`)
- Claude Code에 로그인되어 있어야 함 (`claude` 실행 후 최초 1회 로그인)
- 로컬 환경에서는 바로 가능, EC2 환경에서는 SSH 접속 후 claude 로그인 필요

---

## 2. 데이터 강화 (Level 2)

### 개요

현재는 가격(OHLCV) + 기술적 지표 + 재무제표 + 뉴스만 수집한다.
추천 정확도를 높이기 위해 수급/실적/애널리스트 데이터를 추가 수집한다.
모든 데이터는 yfinance에서 추가 비용 없이 수집 가능.

### 2-1. 내부자 거래 데이터

**수집 소스:** yfinance `Ticker.insider_transactions`

**신규 테이블: fact_insider_trades**
| 컬럼 | 타입 | 설명 |
|------|------|------|
| insider_trade_id | Integer, PK | |
| stock_id | Integer, FK → dim_stocks | |
| date_id | Integer, FK → dim_date | 거래일 |
| insider_name | String | 내부자 이름 |
| insider_title | String, nullable | 직위 (CEO, CFO, Director 등) |
| transaction_type | String | Buy, Sell, Option Exercise |
| shares | BigInteger | 거래 주식 수 |
| value | Decimal, nullable | 거래 금액 ($) |
| shares_owned_after | BigInteger, nullable | 거래 후 보유 주식 수 |

**수집 시점:** STEP 1에서 전 종목 재무 데이터 수집 시 함께 수집
**수집 주기:** 매일 (SEC Form 4는 2영업일 내 공시)
**중복 방지:** (stock_id, date_id, insider_name, transaction_type) 기준 UPSERT

**스크리닝 활용:**

- 최근 3개월 내부자 순매수 금액 계산
- 내부자 순매수 > 0이면 매수 스코어에 가산점 (+0.5~1.0)
- CEO/CFO의 대규모 매수는 더 높은 가산점

### 2-2. 기관 보유 데이터

**수집 소스:** yfinance `Ticker.institutional_holders`

**신규 테이블: fact_institutional_holdings**
| 컬럼 | 타입 | 설명 |
|------|------|------|
| holding_id | Integer, PK | |
| stock_id | Integer, FK → dim_stocks | |
| date_id | Integer, FK → dim_date | 보고일 |
| institution_name | String | 기관명 |
| shares | BigInteger | 보유 주식 수 |
| value | Decimal, nullable | 보유 금액 ($) |
| pct_of_shares | Decimal, nullable | 전체 발행주 대비 비중 (%) |
| change_pct | Decimal, nullable | 전분기 대비 보유 변화율 (%) |

**수집 시점:** STEP 1에서 재무 데이터 수집 시 함께 수집
**수집 주기:** 분기별 (13F Filing 기준). 매일 실행하되, 기존 데이터와 비교하여 변경분만 저장

**스크리닝 활용:**

- 상위 기관의 보유 비중 합계 및 변화 추이
- 기관 보유 비중 증가 → 매수 스코어 가산점
- 기관 보유 비중 급감 → 경고 플래그

### 2-3. 애널리스트 컨센서스

**수집 소스:** yfinance `Ticker.recommendations`, `Ticker.analyst_price_targets`

**신규 테이블: fact_analyst_consensus**
| 컬럼 | 타입 | 설명 |
|------|------|------|
| consensus_id | Integer, PK | |
| stock_id | Integer, FK → dim_stocks | |
| date_id | Integer, FK → dim_date | 수집일 |
| strong_buy | Integer | Strong Buy 수 |
| buy | Integer | Buy 수 |
| hold | Integer | Hold 수 |
| sell | Integer | Sell 수 |
| strong_sell | Integer | Strong Sell 수 |
| target_mean | Decimal, nullable | 평균 목표가 |
| target_high | Decimal, nullable | 최고 목표가 |
| target_low | Decimal, nullable | 최저 목표가 |
| target_median | Decimal, nullable | 중간 목표가 |

**수집 시점:** STEP 1에서 매일 수집
**중복 방지:** UNIQUE(stock_id, date_id) — 일별 스냅샷 저장

**스크리닝 활용:**

- Buy 비율 (= (strong_buy + buy) / 전체) 계산
- 현재가 대비 평균 목표가 괴리율 (= (target_mean - close) / close)
- Buy 비율 70%+ & 목표가 괴리율 20%+ → 높은 가산점
- Sell 비율 30%+ → 감점 또는 필터 아웃

### 2-4. 실적 서프라이즈

**수집 소스:** yfinance `Ticker.earnings_dates`, `Ticker.earnings_history`

**신규 테이블: fact_earnings_surprises**
| 컬럼 | 타입 | 설명 |
|------|------|------|
| earnings_id | Integer, PK | |
| stock_id | Integer, FK → dim_stocks | |
| date_id | Integer, FK → dim_date | 실적 발표일 |
| period | String | 2025Q1 |
| eps_estimate | Decimal, nullable | EPS 컨센서스 예상치 |
| eps_actual | Decimal, nullable | EPS 실제치 |
| surprise_pct | Decimal, nullable | 서프라이즈 비율 (%) |
| revenue_estimate | Decimal, nullable | 매출 예상치 |
| revenue_actual | Decimal, nullable | 매출 실제치 |
| revenue_surprise_pct | Decimal, nullable | 매출 서프라이즈 비율 (%) |

**수집 시점:** STEP 1에서 재무 데이터 수집 시 함께 수집
**중복 방지:** UNIQUE(stock_id, period)

**스크리닝 활용:**

- 최근 4분기 연속 실적 서프라이즈 → 강력 매수 시그널
- 최근 실적 하회 → 감점
- 다음 실적 발표 예정일이 7일 이내면 리스크 경고 (변동성 증가)

### 2-5. 공매도 데이터

**수집 소스:** yfinance `Ticker.info`의 shortRatio, shortPercentOfFloat

**저장 방식:** 별도 테이블 없이 fact_valuations에 컬럼 추가

**fact_valuations에 추가할 컬럼:**
| 컬럼 | 타입 | 설명 |
|------|------|------|
| short_ratio | Decimal, nullable | 공매도 비율 (Days to Cover) |
| short_pct_of_float | Decimal, nullable | 유통주식 대비 공매도 비율 (%) |

**스크리닝 활용:**

- short_pct_of_float > 20% → 숏스퀴즈 가능성 (양날의 검: 높은 리스크이자 기회)
- short_ratio > 5 → 공매도 과열 경고

---

## 3. 스크리닝 스코어 업데이트

데이터 강화에 따라 스코어링 비중을 조정한다.

### 변경 전 (CLAUDE.md 기본)

| 항목               | 비중 |
| ------------------ | ---- |
| 기술적 시그널 점수 | 30%  |
| 기본적 분석 점수   | 30%  |
| 외부 요인 점수     | 20%  |
| 가격 모멘텀 점수   | 20%  |

### 변경 후 (데이터 강화 반영)

| 항목                 | 비중 | 세부                                                      |
| -------------------- | ---- | --------------------------------------------------------- |
| 기술적 시그널 점수   | 25%  | 기존과 동일                                               |
| 기본적 분석 점수     | 25%  | 실적 서프라이즈 반영                                      |
| 수급/스마트머니 점수 | 20%  | 내부자 거래 + 기관 보유 변화 + 애널리스트 컨센서스 (신규) |
| 외부 요인 점수       | 15%  | 매크로 + 뉴스 감성 + 섹터                                 |
| 가격 모멘텀 점수     | 15%  | 기존과 동일                                               |

### 수급/스마트머니 점수 (신규) 세부 배점

| 세부 항목           | 점수 기준                                        |
| ------------------- | ------------------------------------------------ |
| 내부자 거래         | 최근 3개월 순매수 > 0: +3, CEO/CFO 매수: +2 추가 |
| 기관 보유 변화      | 보유 비중 전분기 대비 증가: +2, 감소: -2         |
| 애널리스트 컨센서스 | Buy 비율 70%+: +2, 목표가 괴리율 20%+: +1        |
| 최종 합산           | 10점 만점으로 정규화                             |

---

## 4. 수집 흐름 변경

STEP 1의 수집 흐름에 추가되는 작업:

```
STEP 1 — 데이터 수집 (기존 + 강화)

  기존:
    yfinance 배치 다운로드 → fact_daily_prices
    재무 데이터 → fact_financials + fact_valuations
    매크로 지표 → fact_macro_indicators
    뉴스 → fact_news

  추가 (데이터 강화):
    내부자 거래 → fact_insider_trades
    기관 보유 → fact_institutional_holdings
    애널리스트 → fact_analyst_consensus
    실적 서프라이즈 → fact_earnings_surprises
    공매도 데이터 → fact_valuations (컬럼 추가)
```

**주의사항:**

- 내부자/기관/애널리스트 데이터는 종목별로 개별 API 호출이 필요 (배치 불가)
- 500개 종목 × 개별 호출 = 시간 소요가 큼
- 최적화: 50개씩 묶어서 처리, 각 묶음 사이 1-2초 딜레이
- 예상 추가 소요 시간: 약 10-20분
- 특정 종목 실패 시 스킵하고 로그 기록 후 계속 진행

---

## 5. 프로젝트 구조 변경

신규/수정 파일:

```
src/
├── ai/                         # 신규: AI 분석 자동화
│   ├── __init__.py
│   └── claude_analyzer.py      # Claude Code CLI 호출 + 응답 파싱
├── reports/
│   └── prompt_builder.py       # 신규: 프롬프트 생성기
└── data/
    └── enhanced_collector.py   # 신규: 강화 데이터 수집 (내부자, 기관, 애널리스트, 실적)
```

---

## 향후 확장 예정 (현재 구현 범위 밖)

- **ML 스코어링 엔진**: 데이터 3-6개월 축적 후 LightGBM/XGBoost 기반 수익률 예측 및 가중치 자동 최적화 추가 예정
- **앙상블 모델**: 여러 ML 모델의 예측을 조합하는 Stacking 구조
- **시장 국면 감지**: 강세/약세/횡보별 전략 자동 전환
- **백테스팅 엔진**: 과거 데이터로 전략 수익률 시뮬레이션
- **자기 개선 피드백 루프**: 추천 성과 기반 모델 자동 재학습
