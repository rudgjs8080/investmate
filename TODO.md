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

## 미래 과제 (백로그)
- [ ] 섹터별 PER/PBR 보정 (IT PER 25 vs Utilities PER 12)
- [ ] EV/EBITDA 스코어링
- [ ] 현금흐름 분석 (영업현금흐름 vs 순이익)
- [ ] ML 모듈 실제 구현 (60일 데이터 축적 후)
- [ ] 상대 강도 (Relative Strength) 지표
- [ ] 지지/저항 수준 감지
- [ ] 매크로 추세 분석 (전일 대비 VIX/금리 변화)
- [ ] 리포트 히스토리 비교 (어제 vs 오늘)
- [ ] 포트폴리오 시뮬레이션
- [ ] 뉴스 감성 고도화 (NLP 기반)
- [ ] return_10d 터미널 출력
