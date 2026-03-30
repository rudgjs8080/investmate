prompt_builder.py와 AI 분석 체계를 전면 개편해줘.
핵심 목표: AI가 피드백/캘리브레이션/시장 체제 데이터를 "참고"가 아닌 "강제 규칙"으로 따르게 만들어서, 실제 수익으로 이어지는 분석을 수행하게 하는 것.

아래 계획대로 구현해줘.

---

## 배경 및 근본 문제

현재 AI는 피드백을 "보기만" 하고 "따르지" 않는 구조야.
예시: "승률이 40%입니다" → Claude: "알겠습니다" → 신뢰도 7 부여 → 손실
개선: "승률이 40%이므로 신뢰도 최대 5, Tech 섹터 추천 금지" → 강제 적용

9가지 근본 원인:

1. 피드백이 "참고"일 뿐 → "규칙"으로 강제
2. 신뢰도 상한 없음 → VIX별 상한 적용
3. 시장 체제 반영 약함 → 체제별 추천 수 제한
4. 약점 섹터 차단 안 됨 → 승률 40% 미만 섹터 자동 제외
5. 캘리브레이션 사후 적용 → 프롬프트에 직접 삽입
6. AI 스타일 고정 → VIX/승률 기반 자동 전환
7. 목표가 과대추정 → 보수적 목표가 규칙화
8. 검증 허술 → 사후 강제 조정
9. 페르소나 과신 → "리스크 매니저" 페르소나로 변경

---

## 수정 대상 파일 및 상세 구현

### 파일 1: prompt_builder.py — 프롬프트 전면 개편

시스템 프롬프트를 아래 내용으로 교체해. 기존 프롬프트 구조를 완전히 대체하는 거야.

#### 시스템 프롬프트 내용:

```
<role>
너는 "리스크 매니저 겸 퀀트 애널리스트"다.
너의 최우선 원칙은 손실 회피이며, 수익 기회보다 리스크 제어를 항상 먼저 판단한다.
자신감이 높을수록 좋은 것이 아니다. 근거 없는 확신은 손실이다.
</role>

<hard_rules>
아래 규칙은 "참고사항"이 아니라 반드시 따라야 하는 강제 규칙이다.
규칙을 위반한 추천은 무효로 간주된다.

## 규칙 1: 신뢰도 상한 (절대 초과 금지)
- VIX 30 이상 (위기): 신뢰도 최대 5
- VIX 25~30 (주의): 신뢰도 최대 6
- VIX 20~25 (보통): 신뢰도 최대 7
- VIX 20 미만 (안정): 신뢰도 최대 8
- 어떤 경우에도 신뢰도 9 이상은 부여하지 마라.

## 규칙 2: 추천 수 제한
- 위기 체제: 최대 3개 종목
- 약세 체제: 최대 5개 종목
- 보통 체제: 최대 7개 종목
- 강세 체제: 최대 10개 종목

## 규칙 3: 약점 섹터 차단
blocked_sectors 태그 안에 나열된 섹터의 종목은 절대 추천하지 마라.
해당 섹터에 좋아 보이는 종목이 있더라도 예외 없이 제외한다.

## 규칙 4: 캘리브레이션 보정
calibration 태그에 과거 신뢰도별 실제 승률이 제공된다.
너의 직관적 신뢰도를 먼저 산출한 뒤, 캘리브레이션 테이블에 따라 반드시 하향 조정하라.
예: 직관적으로 7점이지만 과거 7점의 실제 승률이 40%였다면 → 5점으로 하향

## 규칙 5: 목표가 보수적 설정
- 목표 수익률은 과거 동일 섹터 평균 수익률의 80%를 초과하지 마라.
- "대박 시나리오"는 제시하지 마라. 현실적 시나리오만 제시하라.

## 규칙 6: 피드백 규칙 강제 적용
feedback_rules 태그 안의 지시는 단순 참고가 아니라 강제 명령이다.
"~하세요"로 끝나는 지시는 모두 반드시 이행하라.
</hard_rules>

<output_format>
반드시 아래 JSON 구조로만 응답하라. JSON 외의 텍스트를 출력하지 마라.

{
  "market_regime": "위기|주의|보통|강세",
  "analysis_style": "방어적|균형|공격적",
  "recommendations": [
    {
      "ticker": "종목코드",
      "name": "종목명",
      "sector": "섹터",
      "raw_confidence": 직관적_신뢰도(정수),
      "calibrated_confidence": 보정_후_신뢰도(정수),
      "calibration_reason": "7→5 하향: 과거 7점 승률 40%",
      "entry_price": 진입가,
      "target_price": 목표가,
      "stop_loss": 손절가,
      "risk_reward_ratio": 손익비(소수),
      "rationale": "추천 근거 (3문장 이내)",
      "risk_factors": ["리스크1", "리스크2"]
    }
  ],
  "excluded_sectors": ["제외된 섹터와 제외 사유"],
  "rule_compliance": {
    "max_confidence_cap": "적용된 상한값",
    "total_recommendations": "추천 수 / 허용 상한",
    "blocked_sectors_checked": true,
    "calibration_applied": true
  }
}
</output_format>

<reasoning_process>
추천을 생성하기 전에 반드시 다음 순서로 사고하라:

1단계 - 시장 체제 판단: VIX, 시장 지표를 보고 현재 체제를 결정
2단계 - 분석 스타일 결정: 체제와 피드백 규칙에 따라 방어적/균형/공격적 선택
3단계 - 차단 섹터 확인: blocked_sectors 목록을 먼저 확인하고 해당 섹터 완전 배제
4단계 - 후보 종목 분석: 남은 섹터에서 후보 선정
5단계 - 직관적 신뢰도 산출: 각 종목에 대한 초기 신뢰도 부여
6단계 - 캘리브레이션 보정: 테이블 참조하여 신뢰도 하향 조정
7단계 - 상한 검증: VIX 기반 상한 초과 여부 확인, 초과 시 절삭
8단계 - 추천 수 검증: 체제별 허용 수 초과 시 하위 신뢰도 종목부터 제거
9단계 - 목표가 검증: 과거 평균 수익률 80% 초과 여부 확인
</reasoning_process>
```

#### 유저 메시지 템플릿:

prompt_builder.py에서 유저 메시지를 아래 구조로 동적 생성하도록 구현해.
각 변수는 pipeline에서 주입받는다.

```
<market_data>
현재 날짜: {current_date}
VIX: {vix_value}
S&P 500 변화율(1일): {sp500_1d}%
S&P 500 변화율(1주): {sp500_1w}%
시장 체제 판단: {market_regime}
{additional_market_data}
</market_data>

<calibration>
과거 신뢰도별 실제 승률:
- 신뢰도 10: 실제 승률 {cal_10}%
- 신뢰도 9: 실제 승률 {cal_9}%
- 신뢰도 8: 실제 승률 {cal_8}%
- 신뢰도 7: 실제 승률 {cal_7}%
- 신뢰도 6: 실제 승률 {cal_6}%
- 신뢰도 5: 실제 승률 {cal_5}%

보정 기준: 실제 승률이 기대보다 낮은 구간은 해당 승률에 맞는 점수로 하향하라.
</calibration>

<blocked_sectors>
{blocked_sectors_list}
(위 섹터는 최근 승률 40% 미만. 절대 추천 금지.)
</blocked_sectors>

<feedback_rules>
{feedback_rules_list}
</feedback_rules>

<sector_performance>
{sector_performance_data}
</sector_performance>

<candidate_stocks>
{candidate_stocks_data}
</candidate_stocks>

위 데이터를 기반으로 hard_rules와 reasoning_process를 엄격히 준수하여 종목 추천을 수행하라.
```

#### prompt_builder.py 구현 요구사항:

- 시스템 프롬프트는 상수 문자열로 관리
- 유저 메시지는 build_user_message() 함수에서 동적 조립
- VIX 값으로 confidence_cap과 max_recommendations를 계산하는 헬퍼 함수 추가
- market_regime을 판단하는 로직이 이미 있다면 그대로 활용, 없으면 VIX 기반으로 신규 구현

---

### 파일 2: feedback.py — 제약 규칙 자동 생성

피드백을 "관찰 문장"이 아니라 "명령 문장"으로 변환하는 함수를 추가해.

#### 핵심 로직:

- generate_feedback_rules() 함수 신규 생성 또는 기존 함수 수정
- 입력: 과거 성과 데이터 (섹터별 승률, 신뢰도별 승률 등)
- 출력: 명령형 문장 리스트

#### 변환 규칙:

- 승률 40% 미만 섹터 → "{섹터} 종목은 추천하지 마세요. 승률 {승률}%로 차단 기준(40%) 미만입니다."
- 특정 신뢰도 구간 과대평가 → "신뢰도 {N}점은 실제 승률 {실제}%이므로 {보정값}점 이하로 부여하세요."
- 특정 조건에서 손실 반복 → "{조건} 상황에서는 추천을 자제하세요. 최근 {N}회 연속 손실입니다."
- 모든 출력 문장은 반드시 "~하세요" 명령형으로 끝나야 함

---

### 파일 3: validator.py — 사후 검증 강화

AI 응답을 받은 후 rule_compliance 필드와 실제 데이터를 교차 검증하는 로직 추가.

#### 검증 항목:

1. 신뢰도 상한 검증: calibrated_confidence가 VIX 기반 상한을 초과하면 상한값으로 절삭
2. 추천 수 검증: 체제별 허용 수 초과 시 calibrated_confidence 하위 종목부터 제거
3. 차단 섹터 검증: blocked_sectors에 해당하는 종목이 포함되어 있으면 자동 제거
4. 목표가 검증: target_price 기반 수익률이 섹터 평균의 80%를 초과하면 목표가를 80% 선으로 재조정
5. rule_compliance 필드에서 calibration_applied가 false이면 경고 로그 출력

#### 구현 방식:

- validate_ai_response(response, market_context) 함수 신규 생성 또는 기존 함수 확장
- 검증 실패 시 자동 수정 후 수정 로그 반환
- 수정이 3건 이상 발생하면 warning 로그 출력 (AI 프롬프트 조정 필요 신호)

---

### 파일 4: pipeline.py — AI 호출 전 데이터 주입

AI API 호출 전에 시장 체제, 피드백 규칙, 캘리브레이션 데이터를 prompt_builder로 전달하는 파이프라인 수정.

#### 호출 순서:

1. 시장 데이터 수집 (기존 로직 유지)
2. feedback.py의 generate_feedback_rules() 호출 → feedback_rules_list 생성
3. 캘리브레이션 데이터 로드 → calibration 변수들 생성
4. 차단 섹터 리스트 생성 (승률 40% 미만 필터링)
5. prompt_builder.build_user_message()에 위 데이터 모두 전달
6. AI API 호출
7. validator.validate_ai_response()로 사후 검증
8. 검증 통과된 최종 결과 반환

#### 주의사항:

- 기존 파이프라인 구조를 최대한 유지하면서 데이터 주입 단계만 추가
- 각 단계에서 실패 시 적절한 에러 핸들링 구현
- 디버깅을 위해 주입된 프롬프트 전문을 로그로 남기는 옵션 추가

---

## 구현 순서

1. feedback.py 먼저 (다른 파일의 의존성)
2. prompt_builder.py (프롬프트 전면 교체)
3. validator.py (사후 검증)
4. pipeline.py (전체 연결)

각 파일 수정 후 기존 테스트가 있다면 실행해서 깨지는 부분 확인하고 수정해줘.
먼저 현재 프로젝트 구조를 탐색한 뒤 위 계획을 기존 코드에 맞게 적용해.
