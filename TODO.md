# TODO

## 완료 (5 루프, 17 사이클)

### 루프 1 (Cycle 0-9)
- [x] DB 스키마 마이그레이션 유틸리티
- [x] 추적 파일 생성 (CHANGELOG, TODO, METRICS)
- [x] 공매도 비율 → Smart Money 점수
- [x] 실적 서프라이즈 → Fundamental 점수
- [x] 스토캐스틱 K/D → Technical 점수
- [x] RSI 과매도 strength 공식 수정
- [x] 내부자 거래 시간 감쇠
- [x] 데이터 누락 시 감점 (5.0→3.5)
- [x] SMA120 필터 완화
- [x] 애널리스트 목표가 비대칭 보정
- [x] 백테스트 엔진 + 가중치 비교
- [x] Smart Money 상한 +3.0
- [x] 모멘텀 선형 보간
- [x] 기관 보유 스코어링
- [x] yfinance 실패 티커 추적
- [x] 뉴스 datetime fallback 수정
- [x] 매크로 배치화
- [x] 뉴스 감성 단어 경계 매칭
- [x] 매크로 완전성 검증
- [x] 섹터 모멘텀 플랫 마켓 감쇄
- [x] 날짜 매핑 배치 캐시
- [x] RSI 임계값 상수화
- [x] AI 티커 파싱 오류 수정
- [x] 알림 내용 보강

### 루프 2 (Cycle 10-11)
- [x] 달러 인덱스 스코어링
- [x] 배당수익률 스코어링
- [x] 스토캐스틱 K-D 시그널
- [x] 시그널 중복 제거 강도 우선
- [x] 샤프 비율 연환산
- [x] volume=0 필터링

### 루프 3 (Cycle 12-13)
- [x] 스토캐스틱 시그널 DB seed
- [x] 스토캐스틱 시그널 가중치
- [x] 스토캐스틱 한국어 번역 + 초보자 설명
- [x] 스크리너 date_map 캐시
- [x] MacroRepository.get_previous()

### 루프 4 (Cycle 14-15)
- [x] 추천 이유에 스토캐스틱 추가
- [x] 빈 추천 early return
- [x] date_map 캐시 테스트

### 루프 5 (Cycle 16)
- [x] format_utils 테스트
- [x] fail_under 50→60 상향

### 루프 6 (2026-03-24) — 백로그 4사이클
- [x] EV/EBITDA 스코어링 (fundamental.py 가중치 10%)
- [x] 매크로 추세 분석 (전일 대비 VIX/금리/달러/S&P 변화 보정)
- [x] return_10d 터미널 출력
- [x] 상대 강도 (Relative Strength) 지표 (63일 S&P 500 대비)
- [x] 리포트 히스토리 비교 (어제 vs 오늘 comparator.py)
- [x] ML 파이프라인 연결 (자동 리랭킹 + 평가기 구현)

## 이전 세션에서 이미 구현됨 (확인 완료)
- [x] 섹터별 PER/PBR 보정 (fundamental.py build_sector_medians)
- [x] 현금흐름 분석 (fundamental.py _score_fcf)
- [x] 지지/저항 수준 감지 (support_resistance.py)
- [x] 포트폴리오 시뮬레이션 (portfolio/ 모듈)
- [x] 뉴스 감성 고도화 (sentiment.py LLM 통합)

## 미래 과제 (백로그)
- [ ] ML 모델 자동 학습 트리거 (7일 경과 시 재학습)
- [ ] Walk-Forward 교차검증 강화
- [ ] 리포트 마크다운에 "vs 어제" 비교 섹션 추가
- [ ] 리포트 터미널에 RS 백분위 표시
