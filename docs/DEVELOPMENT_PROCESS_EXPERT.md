# Claude Code 기반 개발 프로세스 — 전문가 딥다이브

> investmate 프로젝트가 Claude Code 에코시스템 위에서 어떻게 구동되는지를 기술한 아키텍처 문서.
> 모든 레이어의 내부 구조, 의사결정 흐름, 에이전트 오케스트레이션 로직을 포함한다.

---

## 목차

1. [전체 아키텍처](#1-전체-아키텍처)
2. [컨텍스트 조립 과정](#2-컨텍스트-조립-과정)
3. [Rules 시스템 상세](#3-rules-시스템-상세)
4. [Agent 시스템 상세](#4-agent-시스템-상세)
5. [Memory 시스템 상세](#5-memory-시스템-상세)
6. [Settings 및 권한 시스템](#6-settings-및-권한-시스템)
7. [Skill 시스템](#7-skill-시스템)
8. [개발 워크플로우 5단계](#8-개발-워크플로우-5단계)
9. [Plan Mode 동작 원리](#9-plan-mode-동작-원리)
10. [자동 개선 루프](#10-자동-개선-루프-autoimprovesh)
11. [테스트 인프라](#11-테스트-인프라)
12. [상태 표시 시스템](#12-상태-표시-시스템-statusline)
13. [모델 라우팅 전략](#13-모델-라우팅-전략)
14. [배포 프로세스](#14-배포-프로세스)
15. [실제 세션 흐름 재현](#15-실제-세션-흐름-재현)
16. [파일 경로 레퍼런스](#16-파일-경로-레퍼런스)

---

## 1. 전체 아키텍처

### 1.1 레이어 구조

```
┌──────────────────────────────────────────────────────────────────┐
│                     사용자 프롬프트 입력                           │
└──────────────────────────────┬───────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────┐
│                  Claude Code Runtime (Opus 4.6, 1M context)       │
│                                                                   │
│  ┌─────────────────────── Context Assembly ─────────────────────┐ │
│  │                                                               │ │
│  │  Layer 1: CLAUDE.md (프로젝트 명세)                            │ │
│  │  Layer 2: Rules (11개 .md — common/ + python/)                │ │
│  │  Layer 3: Memory (6개 .md — user/feedback/project)            │ │
│  │  Layer 4: Settings (global + local .json)                     │ │
│  │  Layer 5: Git Status (현재 브랜치, 최근 커밋)                  │ │
│  │                                                               │ │
│  └───────────────────────────┬───────────────────────────────────┘ │
│                              │                                     │
│  ┌───────────────────────────▼───────────────────────────────────┐ │
│  │                    의사결정 엔진                                │ │
│  │                                                               │ │
│  │  Rules 제약 평가 → Memory 참조 → 도구/에이전트 선택            │ │
│  │                                                               │ │
│  └──────┬──────────────┬──────────────┬─────────────────────────┘ │
│         │              │              │                            │
│    ┌────▼────┐   ┌─────▼─────┐  ┌────▼─────┐                     │
│    │ 직접    │   │ Agent     │  │ Skill    │                      │
│    │ 도구    │   │ Dispatch  │  │ Invoke   │                      │
│    │ 실행    │   │ (18종)    │  │ (130+종) │                      │
│    └─────────┘   └───────────┘  └──────────┘                      │
│                                                                   │
│    도구: Read, Write, Edit, Bash, Grep, Glob, Agent, Skill        │
└───────────────────────────────────────────────────────────────────┘
```

### 1.2 데이터 흐름

```
사용자 입력
    ↓
[Context Assembly] CLAUDE.md + Rules + Memory + Settings + Git Status
    ↓
[의사결정] "이 요청에 어떤 도구/에이전트/스킬이 필요한가?"
    ↓
[실행] Read/Edit/Bash → 코드 탐색/수정/테스트
    ↓
[서브에이전트] 필요시 Agent 호출 (별도 컨텍스트에서 실행)
    ↓
[결과 통합] 서브에이전트 결과를 메인 컨텍스트로 반환
    ↓
[Memory 갱신] 학습 사항이 있으면 Memory 파일 업데이트
    ↓
사용자에게 응답
```

---

## 2. 컨텍스트 조립 과정

매 세션 시작 시 Claude Code는 다음 순서로 컨텍스트를 조립한다.

### Layer 1: CLAUDE.md (프로젝트 명세)

`D:\project\investmate\CLAUDE.md` — 프로젝트 루트의 이 파일이 **가장 먼저** 로드된다.

포함 내용:
- 프로젝트 개요 (S&P 500 퀀트 파이프라인)
- 기술 스택 (Python 3.11+, FastAPI, SQLAlchemy, LightGBM, Anthropic SDK)
- 파이프라인 상세 (STEP 0~6 + 4.5~4.7)
- AI 시스템 아키텍처 (10개 모듈)
- 스코어링 + 리스크 관리
- DB 설계 (Star Schema, Dimension 6 + Fact 17)
- 웹 대시보드 (16개 라우트)
- 설정 전체 (27개 환경변수)
- CLI 명령어

`CLAUDE_FULL.md`는 상세 참조용으로 존재하며, 필요시 `CLAUDE.md`에서 링크로 참조.

**효과:** Claude Code가 "return_20d가 뭔 컬럼인지", "pipeline.py의 step4_5가 뭘 하는지" 질문 없이 바로 코딩 가능.

### Layer 2: Rules (행동 제약)

`~/.claude/rules/` 디렉토리의 모든 `.md` 파일이 로드된다.

```
rules/
├── common/                     # 언어 비종속 (9개)
│   ├── agents.md               # 에이전트 오케스트레이션
│   ├── coding-style.md         # 불변성, 파일/함수 크기, 에러 처리
│   ├── development-workflow.md # 5단계 기능 구현 파이프라인
│   ├── git-workflow.md         # conventional commits, PR 형식
│   ├── hooks.md                # PreToolUse/PostToolUse/Stop
│   ├── patterns.md             # Repository 패턴, API envelope
│   ├── performance.md          # 모델 라우팅, 컨텍스트 관리, Extended Thinking
│   ├── security.md             # OWASP 체크리스트, 시크릿 관리
│   └── testing.md              # TDD 강제, 80% 커버리지 하한
└── python/                     # Python 특화 (5개, common 오버라이드)
    ├── coding-style.md         # PEP 8, black/ruff/isort, frozen dataclass
    ├── hooks.md                # PostToolUse: black 자동 포맷
    ├── patterns.md             # Protocol, NamedTuple
    ├── security.md             # bandit, os.environ
    └── testing.md              # pytest, pytest-cov
```

**우선순위:** python/ 규칙이 common/ 규칙을 오버라이드 (specific > general).

### Layer 3: Memory (세션 간 지속 상태)

`~/.claude/projects/D--project-investmate/memory/MEMORY.md` (인덱스)가 항상 로드되며, 인덱스에 링크된 개별 메모리 파일의 이름/설명으로 관련성을 판단한다.

### Layer 4: Settings (런타임 구성)

`~/.claude/settings.json` (글로벌) + `D:\project\investmate\.claude\settings.local.json` (프로젝트)

### Layer 5: Git Status

현재 브랜치, 최근 커밋 5개, 작업 상태(clean/dirty)가 자동 주입된다.

---

## 3. Rules 시스템 상세

### 3.1 coding-style.md — 코드 품질 제약

| 제약 | 강제 수준 | 구체적 기준 |
|------|----------|-----------|
| **불변성** | CRITICAL | 기존 객체 변경 금지. `dataclass(frozen=True)`, 새 객체 반환 |
| **파일 크기** | HARD | 200-400줄 전형, 800줄 MAX. 초과 시 분리 |
| **함수 크기** | HARD | 50줄 미만. 초과 시 추출 |
| **중첩 깊이** | HARD | 4레벨 미만. 초과 시 early return 패턴 |
| **에러 처리** | ALWAYS | 모든 레벨에서 명시적 처리. 절대 무시 금지 |
| **입력 검증** | ALWAYS | 시스템 경계에서 스키마 기반 검증. 외부 데이터 불신 |
| **하드코딩** | NEVER | 매직 넘버 → 상수/설정으로 추출 |

### 3.2 testing.md — 테스트 강제

```
TDD MANDATORY Workflow:
1. Write test first (RED)      ← 테스트가 실패하는 것을 확인
2. Run test — should FAIL      ← 실패 확인 필수
3. Write minimal code (GREEN)  ← 최소한의 구현
4. Run test — should PASS      ← 통과 확인 필수
5. Refactor (IMPROVE)          ← 코드 정리
6. Verify coverage (80%+)      ← 커버리지 검증
```

테스트 유형 3가지 모두 필수: Unit + Integration + E2E.

### 3.3 security.md — 보안 체크리스트

커밋 전 필수 체크:
- 하드코딩된 시크릿 없음 (API 키, 비밀번호, 토큰)
- 모든 사용자 입력 검증
- SQL 인젝션 방지 (매개변수화된 쿼리)
- XSS 방지 (HTML 새니타이징)
- CSRF 보호 활성화
- 인증/인가 검증
- 속도 제한 적용
- 에러 메시지에 민감 데이터 미포함

보안 이슈 발견 시 프로토콜:
1. 즉시 중지
2. security-reviewer 에이전트 호출
3. CRITICAL 이슈 해결 전까지 다른 작업 금지
4. 노출된 시크릿 즉시 로테이션

### 3.4 agents.md — 에이전트 오케스트레이션 규칙

**즉시 호출 (사용자 프롬프트 불필요):**
1. 복잡한 기능 요청 → **planner** 에이전트
2. 코드 작성/수정 직후 → **code-reviewer** 에이전트
3. 버그 수정/새 기능 → **tdd-guide** 에이전트
4. 아키텍처 결정 → **architect** 에이전트

**병렬 실행 규칙:**
```
# GOOD: 독립 작업은 병렬
Agent 1: 보안 분석 (auth 모듈)
Agent 2: 성능 검토 (cache 시스템)     ← 동시 실행
Agent 3: 타입 체크 (utilities)

# BAD: 의존 관계 있으면 순차
Agent 1: 계획 수립 (planner)
Agent 2: 구현 (tdd-guide)           ← 1번 결과 필요하므로 순차
```

**다중 관점 분석** (복잡한 문제):
- Factual reviewer
- Senior engineer
- Security expert
- Consistency reviewer
- Redundancy checker

### 3.5 development-workflow.md — 5단계 파이프라인

```
Step 0: Research & Reuse (필수 — 코딩 전)
  ├── gh search repos / gh search code ← GitHub 기존 구현 검색
  ├── Library docs (Context7 / 벤더 문서) ← API 동작 확인
  ├── Exa (광범위 웹 검색) ← 위 두 가지로 불충분할 때만
  └── Package registries (npm, PyPI) ← 유틸 직접 작성 전 라이브러리 검색
  
  원칙: 검증된 기존 구현 채택 > 새로 작성

Step 1: Plan First
  └── planner 에이전트 호출 → PRD, architecture, task_list 문서 생성

Step 2: TDD Approach
  └── tdd-guide 에이전트 호출 → RED→GREEN→REFACTOR, 80%+ 커버리지

Step 3: Code Review
  └── code-reviewer 에이전트 호출 → CRITICAL/HIGH 필수 해결

Step 4: Commit & Push
  └── conventional commits 형식, git-workflow.md 준수
```

### 3.6 performance.md — 모델 선택 및 컨텍스트 관리

모델 선택 전략:

| 모델 | 능력 | 적합 용도 |
|------|------|----------|
| **Haiku 4.5** | Sonnet의 90%, 3x 비용 절감 | 경량 에이전트, 빈번 호출, 워커 |
| **Sonnet 4.6** | 최고 코딩 모델 | 메인 개발, 다중 에이전트 조정 |
| **Opus 4.6** | 최심 추론 | 복잡한 아키텍처, 연구, 분석 |

컨텍스트 윈도우 관리:
- 마지막 20%에서 대규모 리팩토링 회피
- Extended Thinking 기본 활성화 (31,999 토큰 예약)
- Alt+T로 토글, `MAX_THINKING_TOKENS=10000`으로 제한 가능

---

## 4. Agent 시스템 상세

### 4.1 에이전트 정의 구조

`~/.claude/agents/` 디렉토리에 18개의 `.md` 파일이 존재. 각 파일은 YAML frontmatter로 구성:

```yaml
---
model: opus | sonnet | haiku
description: 에이전트 설명
tools: [Read, Write, Edit, Bash, Grep, Glob]
---
상세 지시 (프롬프트)
```

### 4.2 핵심 에이전트 7종 — 내부 동작

#### planner (Opus)

```
도구: Read, Grep, Glob (읽기 전용)
트리거: 복잡한 기능 요청, 리팩토링, 아키텍처 변경

내부 프로세스:
1. Requirements Analysis
   - 요구사항을 명확한 목록으로 분해
   - 암묵적 요구사항 도출
   
2. Architecture Review
   - 기존 코드 구조 탐색 (Grep, Glob)
   - 영향받는 파일 목록 작성
   - 기존 패턴과 규칙 파악
   
3. Step Breakdown
   - Phase별 구현 단계 (각 단계에 파일 경로, 함수명, 변경 내용 명시)
   - 의존성 그래프 (어떤 단계가 먼저 완료되어야 하는지)
   
4. Output Format
   - Overview (2-3 문장)
   - Requirements (리스트)
   - Architecture Changes
   - Implementation Steps (Phase별)
   - Testing Strategy
   - Risks & Mitigations
   - Success Criteria
   
원칙:
- 구체적이어야 함 (파일 경로, 라인 번호)
- 엣지 케이스 고려
- 변경 최소화
- 기존 패턴 유지
- 테스트 용이성 확보
- 점진적 구현 (한번에 모든 것을 바꾸지 않음)
```

#### code-reviewer (Sonnet)

```
도구: Read, Grep, Glob, Bash
트리거: 모든 코드 변경 직후 (필수)

내부 워크플로:
1. git diff 스캔 → 변경된 파일 식별
2. 변경 파일의 전체 컨텍스트 읽기 (주변 코드 이해)
3. 4단계 체크리스트 적용

심각도 분류:
- CRITICAL (보안): SQL 인젝션, XSS, 하드코딩 자격증명, 인증 우회, 경로 탐색
- HIGH (품질): 50줄+ 함수, 800줄+ 파일, 4레벨+ 중첩, 에러 처리 부재, 뮤테이션, 테스트 부족
- MEDIUM (성능): 비효율 알고리즘, N+1 쿼리, 캐싱 부재, 동기 I/O
- LOW (모범사례): TODO/FIXME, 이름 지정, 매직 넘버

필터링 규칙:
- 80% 이상 확신도만 보고
- 스타일 선호도(따옴표 타입 등) 생략
- 기존 패턴과 일관된 코드는 플래그하지 않음
- AI 생성 코드 추가 체크: 동작 회귀, 보안 가정, 숨겨진 결합
```

#### tdd-guide (Sonnet)

```
도구: Read, Write, Edit, Bash, Grep (쓰기 가능)
트리거: 새 기능, 버그 수정, 리팩토링

강제 프로세스:
1. 인터페이스 먼저 정의 (함수 시그니처, 타입)
2. 테스트 작성 (RED)
   - 정상 경로
   - 엣지 케이스: null/undefined, empty, invalid types, boundary values
   - 에러 경로: 예외, 타임아웃, 네트워크 실패
   - 동시성: race condition, 대량 데이터 (10K+)
3. 테스트 실행 → 실패 확인
4. 최소 구현 (GREEN) — 테스트를 통과시키는 최소한의 코드
5. 리팩토링 (IMPROVE)
6. 커버리지 검증 → 80%+ 미달 시 추가 테스트

Anti-patterns 감지:
- 구현 세부사항 테스트 (내부 상태 직접 검증)
- 공유 상태 의존
- 부족한 assertion
- 외부 의존성 미모킹
```

#### architect (Opus)

```
도구: Read, Grep, Glob (읽기 전용)
트리거: 시스템 설계, 확장성 결정, 기술 선택

프로세스:
1. Current State Analysis — 기존 아키텍처 파악
2. Requirements Gathering — 기능/비기능 요구사항
3. Design Proposal — 구체적 설계안
4. Trade-Off Analysis — Pros/Cons/Alternatives

원칙:
- Modularity & SoC (단일 책임)
- Scalability (수평 확장, stateless)
- Maintainability (일관된 패턴)
- Security (심층 방어, 최소 권한)
- Performance (효율적 알고리즘, 캐싱)

출력: ADR(Architecture Decision Records) 템플릿
```

#### security-reviewer (Sonnet)

```
도구: Read, Write, Edit, Bash, Grep, Glob (쓰기 가능)
트리거: 사용자 입력 처리, 인증, API 엔드포인트, 민감 데이터 코드

OWASP Top 10 체크리스트:
A1: Injection (SQL, Command, LDAP)
A2: Broken Authentication
A3: Sensitive Data Exposure
A4: XML External Entities
A5: Broken Access Control
A6: Security Misconfiguration
A7: Cross-Site Scripting
A8: Insecure Deserialization
A9: Known Vulnerabilities
A10: Insufficient Logging

즉시 플래그 패턴:
- CRITICAL: 하드코딩 시크릿, 셸 명령 주입, SQL 문자열 연결, 평문 비밀번호, 인증 미확인
- HIGH: innerHTML, fetch(사용자 URL), 속도 제한 없음
- MEDIUM: 로그 시크릿
```

#### python-reviewer (Sonnet)

```
도구: Read, Grep, Glob, Bash
트리거: Python 코드 변경 시

체크 우선순위:
1. CRITICAL: 보안 (f-string SQL → 매개변수화)
2. CRITICAL: 에러 처리 (bare except → 구체적 예외)
3. HIGH: 타입 힌트 (모든 함수 시그니처)
4. HIGH: Pythonic 패턴 (list comp, isinstance, Enum, join)
5. HIGH: 코드 품질 (PEP 8, 이름 규칙)
6. HIGH: 동시성 (공유 상태 잠금, with 컨텍스트)
7. MEDIUM: 모범사례

진단 명령 실행:
- mypy src/ (타입 체크)
- ruff check src/ (린트)
- black --check src/ (포맷)
- bandit -r src/ (보안 스캔)
- pytest --cov (커버리지)
```

#### build-error-resolver (Sonnet)

```
도구: Read, Write, Edit, Bash, Grep, Glob (쓰기 가능)
트리거: 빌드 실패, 타입 에러, 린터 경고

원칙: 최소 변경. 리팩토링 X, 아키텍처 변경 X, 로직 변경 X.

허용 범위:
- 타입 어노테이션 수정
- null 체크 추가
- import/export 수정
- 의존성 수정
- 타입 정의 수정
- 설정 수정

금지 범위:
- 변수명 변경
- 새 기능 추가
- 코드 구조 변경
- 성능 최적화

워크플로: 에러 전체 수집 → 최소 수정 전략 → 검증 → 반복
```

### 4.3 에이전트 호출 메커니즘

에이전트는 **독립된 서브프로세스**로 실행된다. 메인 Claude Code 컨텍스트와 분리된 자체 컨텍스트를 가진다.

```
메인 Claude Code (Opus, 1M context)
    │
    ├── Agent(subagent_type="Explore", prompt="...")
    │   └── 별도 컨텍스트에서 코드 탐색 → 결과 반환
    │
    ├── Agent(subagent_type="Plan", prompt="...")
    │   └── 별도 컨텍스트에서 설계 → 계획 반환
    │
    └── Agent(subagent_type="code-reviewer", prompt="...")
        └── 별도 컨텍스트에서 검토 → 이슈 목록 반환
```

핵심 특성:
- 서브에이전트는 메인 대화를 모름 → prompt에 충분한 컨텍스트 전달 필수
- 서브에이전트 결과는 메인에게만 보임 → 사용자에게 직접 표시 안 됨
- 독립 작업은 단일 메시지에 다중 Agent 호출로 **병렬 실행**
- foreground (결과 대기) vs background (비동기) 모드 선택 가능
- `isolation: "worktree"` 옵션으로 git worktree 격리 실행 가능

### 4.4 특수 에이전트

| 유형 | 이름 | 역할 |
|------|------|------|
| **탐색 전용** | Explore | 코드베이스 탐색. thoroughness: quick/medium/very thorough |
| **설계 전용** | Plan | 구현 설계. 탐색 결과를 받아 상세 계획 반환 |
| **범용** | general-purpose | 복잡한 멀티스텝 작업. 모든 도구 접근 |

---

## 5. Memory 시스템 상세

### 5.1 저장 위치와 구조

```
~/.claude/projects/D--project-investmate/memory/
├── MEMORY.md                          # 인덱스 (항상 컨텍스트 로드, 200줄 제한)
├── user_role.md                       # type: user
├── feedback_autonomous.md             # type: feedback
├── feedback_no_cost_statusline.md     # type: feedback
├── feedback_workdir_restriction.md    # type: feedback
└── project_report_improvement.md      # type: project
```

### 5.2 메모리 타입별 역할

#### user (사용자 프로필)

**저장 시점:** 사용자의 역할, 목표, 전문성을 학습했을 때
**활용 시점:** 응답의 난이도, 용어 수준, 설명 방식 결정

현재 내용 (`user_role.md`):
```yaml
---
name: user_role
description: 30년차 시니어 퀀트 트레이더 — 전문 지표/포트폴리오 관점 우선
type: user
---
30년차 시니어 퀀트 트레이더. Sharpe, Sortino, IR, MDD 등 전문 지표에 익숙하며,
포트폴리오 레벨 사고를 기본으로 함. 수익률 트래킹에 대한 높은 기준을 가지고 있음.
```

**행동 변경:** Sharpe ratio를 "위험 대비 수익 지표"라고 풀어쓰지 않음. IR, MDD 같은 약어 직접 사용.

#### feedback (교정/확인 기록)

**저장 시점:** 사용자가 접근 방식을 교정하거나 비정상적 접근을 확인했을 때
**활용 시점:** 동일한 실수 반복 방지

현재 3개:

1. `feedback_autonomous.md` — "자율 개발 선호"
```
사용자는 개발 작업 시 중간에 멈추지 않고 자율적으로 계속 진행하기를 원함.
**Why:** "계속할까요?" 질문이 작업 흐름을 끊음
**How to apply:** 코드 수정→테스트→결과 검토를 자동으로 연속 진행. 불확실한 경우만 질문.
```

2. `feedback_no_cost_statusline.md` — "비용 표시 불필요"
```
Pro Max 사용자이므로 statusline에서 비용(💰) 항목 불필요.
```

3. `feedback_workdir_restriction.md` — "작업 디렉토리 제한"
```
D:\project\investmate 디렉토리 안에서만 파일 수정 허용.
예외: ~/.claude/projects/D--project-investmate/memory/
**Why:** --dangerously-skip-permissions 사용 중이므로 안전 가드레일 필요.
```

#### project (프로젝트 학습)

**저장 시점:** 진행 중인 작업, 목표, 마감, 기술적 결정을 학습했을 때
**활용 시점:** 사용자 요청의 맥락 이해

현재 내용 (`project_report_improvement.md`):
```
2026-03-19 리포트 시스템 전면 개편 (25라운드 반복).
DB repository date_id 범위 조회로 변경, 두괄식 리포트 구조,
explainer.py 신규 (초보자 친화 한국어 설명), yfinance v2 뉴스 API 대응.
**How to apply:** 리포트 관련 작업 시 explainer.py 활용, 새 지표 시 초보자 설명 필수.
```

### 5.3 메모리 저장 안 하는 것

- 코드 패턴/아키텍처 (코드에서 파생 가능)
- Git 히스토리 (`git log`로 조회)
- 디버깅 솔루션 (커밋 메시지에 기록)
- CLAUDE.md에 이미 문서화된 내용
- 임시 작업 상태 (현재 세션에서만 유효)

### 5.4 메모리 검증 프로토콜

메모리가 "파일 X에 함수 Y가 있다"고 기록했더라도, 실제 사용 전에 검증한다:
- 파일 경로 메모리 → 파일 존재 확인 (Glob)
- 함수/플래그 메모리 → grep으로 검색
- 저장소 상태 요약 → `git log`와 비교

"메모리가 X가 존재한다고 말하는 것"과 "X가 지금 존재하는 것"은 다르다.

---

## 6. Settings 및 권한 시스템

### 6.1 글로벌 설정 (`~/.claude/settings.json`)

```json
{
  "statusLine": {
    "type": "command",
    "command": "python /c/Users/user/.claude/statusline-command.py"
  },
  "enabledPlugins": {
    "skill-creator@claude-plugins-official": true,
    "ralph-loop@claude-plugins-official": true
  },
  "effortLevel": "high",
  "skipDangerousModePermissionPrompt": true
}
```

| 키 | 값 | 효과 |
|----|-----|------|
| `statusLine` | 커스텀 Python 스크립트 | 모델명, 컨텍스트 사용률, 토큰, 코드 변경량, 시간, 프로젝트, 브랜치 표시 |
| `enabledPlugins` | skill-creator, ralph-loop | 스킬 자동 생성 + 반복 루프 실행 플러그인 |
| `effortLevel` | high | 모든 응답에서 깊이 있는 분석 수행 |
| `skipDangerousModePermissionPrompt` | true | 위험 모드 권한 확인 생략 |

### 6.2 프로젝트 설정 (`D:\project\investmate\.claude\settings.local.json`)

```json
{
  "permissions": {
    "allow": [
      "Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit",
      "Bash(uv:*)", "Bash(pip:*)", "Bash(python:*)", "Bash(pytest:*)",
      "Bash(alembic:*)", "Bash(investmate:*)", "Bash(git:*)",
      "Bash(ls:*)", "Bash(cd:*)", "Bash(rm:*)", "Bash(mkdir:*)",
      "Bash(cp:*)", "Bash(mv:*)", "Bash(cat:*)", "Bash(head:*)",
      "Bash(tail:*)", "Bash(wc:*)", "Bash(find:*)", "Bash(grep:*)",
      "Bash(echo:*)", "Bash(touch:*)", "Bash(diff:*)",
      "Bash(taskkill:*)", "Bash(tasklist:*)",
      "Bash(ssh:*)",
      "Skill(update-config)", "Skill(keybindings-help)"
    ],
    "defaultMode": "auto"
  }
}
```

**권한 구조:**
- `Bash(패턴:*)` — 특정 명령어만 화이트리스트 허용
- `defaultMode: "auto"` — 화이트리스트에 있는 도구는 사용자 승인 없이 자동 실행
- 화이트리스트에 없는 Bash 명령 → 사용자에게 승인 요청

---

## 7. Skill 시스템

### 7.1 Skill이란

사용자가 `/명령어`로 호출하는 특화 기능. Rules(규칙)와 달리 **구체적 실행 절차**를 정의한다.

```
Rule  = "무엇을 해야 하는가" (예: "80% 커버리지 달성")
Skill = "어떻게 하는가" (예: "/tdd로 pytest 기반 TDD 실행")
Agent = "누가 하는가" (예: "tdd-guide 에이전트가 실행")
```

### 7.2 주요 Skill 목록

| Skill | 트리거 | 내부 동작 |
|-------|--------|----------|
| `/plan` | 구현 전 계획 | 요구사항 재정리 → 리스크 평가 → 단계별 계획 → 사용자 CONFIRM 대기 |
| `/tdd` | TDD 개발 | 인터페이스 스캐폴딩 → 테스트 먼저 → 최소 구현 → 80%+ 커버리지 |
| `/code-review` | 코드 검토 | code-reviewer 에이전트 호출 |
| `/commit` | Git 커밋 | git status + diff 분석 → 커밋 메시지 자동 생성 |
| `/build-fix` | 빌드 에러 | build-error-resolver 에이전트 호출 |
| `/verify` | 종합 검증 | 빌드 + 테스트 + 린트 + 보안 스캔 |
| `/update-docs` | 문서 갱신 | CLAUDE.md, README, 코드맵 자동 업데이트 |
| `/update-config` | 설정 수정 | settings.json/hooks 설정 변경 |
| `/simplify` | 코드 단순화 | 변경된 코드의 재사용성, 품질, 효율 검토 후 개선 |
| `/python-review` | Python 리뷰 | python-reviewer 에이전트 호출 |

### 7.3 Skill vs Agent 관계

많은 Skill은 내부적으로 Agent를 호출한다:
```
/code-review → code-reviewer agent (Sonnet)
/tdd → tdd-guide agent (Sonnet)
/plan → planner agent (Opus)
/build-fix → build-error-resolver agent (Sonnet)
```

---

## 8. 개발 워크플로우 5단계

### 전체 흐름도

```
사용자 요청
    │
    ▼
┌─ Step 0: Research & Reuse ─────────────────────────────────────┐
│  gh search code "키워드" → 기존 구현 확인                       │
│  PyPI/npm 검색 → 라이브러리 존재 확인                           │
│  Library docs → API 동작 확인                                   │
│                                                                 │
│  원칙: 80%+ 해결하는 기존 구현이 있으면 채택                    │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
    ▼
┌─ Step 1: Plan First ───────────────────────────────────────────┐
│  planner 에이전트 호출 (Opus)                                   │
│                                                                 │
│  OR Plan Mode 활성화:                                           │
│  ├── Explore agents (병렬) → 코드베이스 탐색                    │
│  ├── Plan agent → 구현 설계                                     │
│  └── Plan File 작성 → ExitPlanMode → 사용자 승인                │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
    ▼
┌─ Step 2: TDD Approach ────────────────────────────────────────┐
│  tdd-guide 에이전트 호출 (Sonnet)                               │
│                                                                 │
│  1. 인터페이스 정의 (함수 시그니처, 타입)                       │
│  2. 테스트 작성 (RED) → pytest 실행 → 실패 확인                │
│  3. 최소 구현 (GREEN) → pytest 실행 → 통과 확인                │
│  4. 리팩토링 (IMPROVE)                                          │
│  5. 커버리지 검증 (80%+)                                       │
│                                                                 │
│  memory: feedback_autonomous → 중단 없이 연속 실행              │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
    ▼
┌─ Step 3: Code Review ─────────────────────────────────────────┐
│  code-reviewer 에이전트 호출 (Sonnet)                           │
│                                                                 │
│  git diff 스캔 → 4단계 체크리스트 → 이슈 분류                  │
│  CRITICAL/HIGH → 수정 필수                                     │
│  MEDIUM → 가능하면 수정                                        │
│  LOW → 선택                                                    │
│                                                                 │
│  + python-reviewer (Python 프로젝트)                            │
│  + security-reviewer (보안 관련 코드)                           │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
    ▼
┌─ Step 4: Commit & Push ───────────────────────────────────────┐
│  git status → git diff → git log (최근 스타일 참조)            │
│  커밋 메시지 자동 생성 (conventional commits)                   │
│  git add (특정 파일만, -A 지양) → git commit                   │
│                                                                 │
│  규칙: 새 커밋 생성 (amend 지양), hooks 건너뛰기 금지           │
└────────────────────────────────────────────────────────────────┘
```

---

## 9. Plan Mode 동작 원리

Plan Mode는 Claude Code가 **코드를 수정하지 않고** 계획만 수립하는 특수 모드이다.

### 활성화

사용자가 Plan Mode를 요청하면 system-reminder로 다음 제약이 주입된다:
- Edit, Write, Bash(비읽기) 등 변경 도구 사용 금지
- 유일하게 수정 가능한 파일: `~/.claude/plans/{session-id}.md`

### 5단계 워크플로

```
Phase 1: Initial Understanding (탐색)
    ├── Explore agents 최대 3개 병렬 실행
    ├── 코드베이스, 기존 함수, 패턴 파악
    └── 재사용 가능한 기존 구현 식별

Phase 2: Design (설계)
    ├── Plan agent 실행 (최대 1개)
    ├── Phase 1 결과를 기반으로 구현 설계
    └── 대안 비교, 트레이드오프 분석

Phase 3: Review (검토)
    ├── 핵심 파일 직접 읽어서 이해 심화
    ├── 계획이 사용자 의도와 일치하는지 확인
    └── 불명확한 점은 AskUserQuestion으로 질문

Phase 4: Final Plan (계획서 작성)
    ├── Context 섹션 (왜 이 변경이 필요한가)
    ├── 수정 파일 목록 + 기존 함수 재사용 명시
    ├── 검증 방법 (테스트, MCP 도구, 수동 확인)
    └── Plan File에 기록

Phase 5: ExitPlanMode (승인 요청)
    └── 사용자가 승인하면 구현 시작
```

---

## 10. 자동 개선 루프 (auto_improve.sh)

### 호출 방법

```bash
make improve           # 3회 반복, 회당 25턴, self_judge.txt
make improve-1         # 1회만
make improve-coverage  # coverage.txt (커버리지 집중)
make improve-quality   # quality.txt (코드 품질 집중)
```

### 내부 구조

```bash
#!/bin/bash
# 자율 반복 개선 루프 -- Claude Code가 분석→수정→테스트를 자동 반복

MAX_ITERATIONS=${1:-3}
TURNS=${2:-25}
PROMPT_FILE=${3:-scripts/improve_prompts/self_judge.txt}
```

### 실행 흐름

```
0단계: 베이스라인 측정
    └── pytest --cov → 현재 테스트 수 + 커버리지 기록

반복 1..N:
    ├── 프롬프트 파일 로드 (self_judge.txt / coverage.txt / quality.txt)
    │
    ├── Claude Code 실행
    │   claude -p "$PROMPT" \
    │     --session-id "$SESSION_ID" \
    │     --allowedTools "Read,Write,Edit,Bash" \
    │     --max-turns $TURNS
    │
    ├── 테스트 검증
    │   ├── pytest -x -q → 통과 → 다음 반복
    │   └── pytest -x -q → 실패 → git checkout . (롤백)
    │
    └── 범위 검증
        └── 변경 파일 >10개 → 경고 (과도한 변경)

최종: 베이스라인 vs 최종 비교 리포트
```

### 프롬프트 파일 3종

**self_judge.txt** (기본 — 자율 판단):
```
판단 기준 (우선순위):
1. 실패 테스트 수정 (최우선)
2. 런타임 에러 가능성 제거
3. 가장 낮은 커버리지 모듈 개선
4. 성능 병목 해소
5. 코드 품질 (리팩토링, 타입 힌트)

안전 규칙:
- 한 번에 하나의 이슈만 수정
- 수정 후 반드시 pytest 실행
- 기존 통과 테스트 깨뜨리기 금지
- 10개 이상 파일 동시 수정 금지
```

**coverage.txt** (커버리지 집중):
```
가장 낮은 커버리지 모듈 3개에 대해:
1. 미테스트 코드 경로 파악
2. edge case + error case 테스트 작성
3. pytest 통과 확인
4. 커버리지 상승 확인
```

**quality.txt** (코드 품질 집중):
```
6가지 기준:
1. 50줄+ 함수 → 분리
2. 하드코딩 매직 넘버 → 상수 추출
3. 3레벨+ 중첩 → early return 평탄화
4. try-except Exception → 구체적 예외
5. 타임아웃 없는 HTTP 요청 → timeout=30
6. 불필요한 import 제거
```

---

## 11. 테스트 인프라

### pyproject.toml 설정

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[tool.coverage.run]
source = ["src"]

[tool.coverage.report]
show_missing = true
fail_under = 65
```

### conftest.py Fixture 체계

```python
@pytest.fixture
def engine():
    """In-memory SQLite 엔진. 테스트별 격리."""
    eng = create_engine("sqlite://", echo=False)
    # PRAGMA foreign_keys=ON (외래키 활성화)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()

@pytest.fixture
def session(engine):
    """테스트 세션. 트랜잭션 격리."""
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()

@pytest.fixture
def seeded_session(engine):
    """Dimension 데이터 사전 로드."""
    seed_dimensions(engine)  # 시장, 섹터, 날짜 등 초기화
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.close()

@pytest.fixture
def sample_stock(seeded_session) -> dict:
    """AAPL 샘플 데이터."""
    return {"id": ..., "ticker": "AAPL", "name": "Apple Inc"}
```

### 테스트 규모 (현재)

- **95개** 테스트 파일 (`tests/test_*.py`)
- **1,120개** 개별 테스트 케이스
- **커버리지** 65% 하한 (`fail_under`)

---

## 12. 상태 표시 시스템 (Statusline)

`~/.claude/statusline-command.py`가 Claude Code 세션 상태를 터미널에 표시한다.

### 입력 (JSON, stdin)

```json
{
  "model": {"display_name": "Claude Opus 4.6"},
  "workspace": {"current_dir": "/d/project/investmate"},
  "context_window": {"used_percentage": 45, "total_input_tokens": 125000, "total_output_tokens": 8000},
  "cost": {"total_duration_ms": 323000, "total_lines_added": 50, "total_lines_removed": 3}
}
```

### 출력 형식

```
🤖 Opus 4.6 | ▓▓▓▓░░░░░░ 45% | 📊 133K | +50 -3 | ⏱ 5m23s | 📁 investmate | 🌿 main
```

| 항목 | 의미 |
|------|------|
| 🤖 | 현재 모델 |
| ▓▓▓▓░░░░░░ | 컨텍스트 윈도우 사용률 (90%+ 빨강, 70%+ 노란, 그 외 초록) |
| 📊 | 총 토큰 사용량 |
| +50 -3 | 코드 변경량 (추가/삭제 줄 수) |
| ⏱ | 세션 경과 시간 |
| 📁 | 프로젝트명 |
| 🌿 | Git 브랜치 |

**참고:** `feedback_no_cost_statusline.md`에 의해 비용(💰) 항목은 제거됨.

---

## 13. 모델 라우팅 전략

### 개발 도구 레벨

| 상황 | 모델 | 이유 |
|------|------|------|
| 메인 세션 (사용자 대화) | **Opus 4.6** | 1M context, 최심 추론 |
| planner, architect agent | **Opus** | 복잡한 설계 결정 |
| code-reviewer, tdd-guide, security-reviewer | **Sonnet** | 최적 코딩 모델 |
| python-reviewer, build-error-resolver | **Sonnet** | 코드 분석 최적 |
| Explore, Plan agent | **상속** (메인 모델) | 별도 지정 없으면 Opus |

### 프로덕션 레벨 (investmate AI 분석)

| 용도 | 모델 | 설정 |
|------|------|------|
| 분석/코멘터리 | `claude-sonnet-4-20250514` | INVESTMATE_AI_MODEL_ANALYSIS |
| 채팅/감성 | `claude-haiku-4-5-20251001` | INVESTMATE_AI_MODEL_CHAT |

---

## 14. 배포 프로세스

### 배포 명령 (단일 SSH)

```bash
ssh -i ~/Downloads/investmate-key.pem ec2-user@54.116.125.164 \
  "cd /home/ec2-user/investmate && source .venv/bin/activate && \
   git pull origin main && pip install -e . --quiet && \
   python -c 'from src.db.migrate import ensure_schema; ...' && \
   sudo systemctl restart investmate-web && \
   echo '=== 배포 완료 ===' && sudo systemctl status investmate-web --no-pager | head -5"
```

### EC2 아키텍처

```
브라우저 → Nginx (80) → FastAPI (8000) → SQLite (EBS 30GB)

cron: 화~토 08:00 KST → investmate run (배치 파이프라인)
systemd: investmate-web.service (MemoryMax=512M, RestartSec=5)
백업: S3 (5GB)
모니터링: CloudWatch + Telegram
```

---

## 15. 실제 세션 흐름 재현

### 이번 세션 (수익률 트래킹 고도화) 전체 흐름

```
[1] 사용자: "수익률 트래킹 시스템을 보완해야해"
    │
    ├── Memory 참조:
    │   user_role.md → "30년차 퀀트" → 전문 용어 사용
    │   feedback_autonomous.md → 중단 없이 자율 진행
    │
    ▼
[2] Explore Agent 3개 병렬 실행 (조사)
    │
    ├── Agent 1: 추천 수 설정 + 스크리닝 로직
    │   → config.py, feedback.py, screener.py, pipeline.py 탐색
    │   → TOP_N=10, 체제별 상한 (crisis:3 ~ bull:10) 파악
    │
    ├── Agent 2: 수익률 트래킹 현재 구현
    │   → performance.py, api.py, templates, models.py 탐색
    │   → Gap 발견: 벤치마크 부재, 가중 수익률 부재, 리스크 지표 미통합
    │
    └── Agent 3: 신뢰성 시스템
        → calibrator.py, feedback.py, counterfactual.py 탐색
        → 캘리브레이션 곡선에 신뢰 구간 부재, 감사 추적 부재 발견
    │
    ▼
[3] Plan Agent 실행 (설계)
    │
    └── Explore 결과를 종합하여 구현 계획 수립
        → Phase 1~4, P0/P1/P2 우선순위, 수정 파일 목록
        → 의존성 그래프, 검증 계획
    │
    ▼
[4] Plan Mode: 계획서 작성
    │
    └── ~/.claude/plans/merry-fluttering-panda.md 에 저장
        → Context, Phase별 상세, 파일 목록, 검증 방법
    │
    ▼
[5] ExitPlanMode → 사용자 승인
    │
    ▼
[6] 구현 (Write/Edit/Bash 도구 실행)
    │
    ├── Write: src/analysis/risk_metrics.py (신규)
    ├── Edit: src/backtest/engine.py (리스크 함수 위임)
    ├── Write: src/analysis/performance.py (전면 재작성)
    ├── Write: src/web/routes/api.py (전면 재작성)
    ├── Write: src/web/routes/performance.py (확장)
    ├── Write: src/web/templates/performance.html (전면 재설계)
    ├── Edit: src/db/models.py (감사 추적 컬럼 + 품질 테이블)
    ├── Edit: src/ai/feedback.py (Wilson 신뢰구간)
    ├── Edit: src/ai/calibrator.py (샘플 가드)
    │
    ├── Bash: pytest tests/ -x -q → 1120 passed (첫 시도)
    ├── (calibrator 테스트 1개 실패 → sample_size 임계값 조정)
    └── Bash: pytest tests/ -x -q → 1120 passed (최종)
    │
    ▼
[7] Memory 갱신
    │
    └── user_role.md 생성 (30년차 퀀트 트레이더 정보 저장)
    │
    ▼
[8] 커밋 + Push
    │
    └── git add (9개 파일) → git commit → git push origin main
    │
    ▼
[9] EC2 배포
    │
    └── SSH → git pull → pip install → DB migrate → systemctl restart
    │
    ▼
[10] 검증
    │
    ├── /api/health → {"status": "ok", "stocks": 507}
    └── /api/risk-metrics → {"sharpe": -3.55, "max_drawdown": 14.39, ...}
```

---

## 16. 파일 경로 레퍼런스

### Claude Code 설정

| 경로 | 용도 | 항목 수 |
|------|------|--------|
| `~/.claude/settings.json` | 글로벌 설정 | 4개 키 |
| `~/.claude/rules/common/*.md` | 공통 규칙 | 9개 파일 |
| `~/.claude/rules/python/*.md` | Python 규칙 | 5개 파일 |
| `~/.claude/agents/*.md` | 에이전트 정의 | 18개 파일 |
| `~/.claude/statusline-command.py` | 상태 표시 스크립트 | 1개 |

### 프로젝트 설정

| 경로 | 용도 |
|------|------|
| `D:/project/investmate/CLAUDE.md` | 프로젝트 명세 (15KB) |
| `D:/project/investmate/CLAUDE_FULL.md` | 상세 명세 (61KB) |
| `D:/project/investmate/.claude/settings.local.json` | 프로젝트 권한 |
| `~/.claude/projects/D--project-investmate/memory/` | 프로젝트 메모리 (6개) |
| `~/.claude/plans/*.md` | 세션별 계획 파일 |

### 자동화

| 경로 | 용도 |
|------|------|
| `D:/project/investmate/Makefile` | 자동화 명령 (13개) |
| `D:/project/investmate/scripts/auto_improve.sh` | AI 자율 개선 루프 |
| `D:/project/investmate/scripts/run_pipeline.sh` | 파이프라인 래퍼 |
| `D:/project/investmate/scripts/improve_prompts/self_judge.txt` | 자율 판단 프롬프트 |
| `D:/project/investmate/scripts/improve_prompts/coverage.txt` | 커버리지 프롬프트 |
| `D:/project/investmate/scripts/improve_prompts/quality.txt` | 품질 프롬프트 |

### Git 히스토리 (23개 커밋)

```
8d1e9c0 feat: 수익률 트래킹 시스템 전면 고도화
4ee1a92 feat: 웹 차트 UI/UX 전면 개선
4f63a4c feat: AI 고도화 — 멀티 호라이즌 피드백 + 반사실 분석
cfd03c6 feat: AI 고도화 — 멀티 에이전트 토론 + 자기학습
78de442 feat: AI 분석 체계 전면 개편
ea00d04 feat: 백로그 4사이클 — EV/EBITDA, RS, ML 연결
b4b046a feat: investmate v1.0
8feb66b Initial commit
```

패턴: `feat:` 65%, `fix:` 20%, `docs:` 15%. 단일 커밋이 대규모 변경을 포함하는 "전면 고도화" 스타일.
