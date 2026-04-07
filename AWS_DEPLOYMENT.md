# Investmate — AWS Free Tier 배포 가이드

> S&P 500 AI 투자 가이드를 AWS Free Tier만으로 운영하기 위한 완전 초보자용 배포 가이드.
> AWS 계정만 있으면 이 문서를 위에서 아래로 따라하면 됩니다.

---

## 목차

1. [시작하기 전에 — 이 문서에서 하는 일](#1-시작하기-전에--이-문서에서-하는-일)
2. [사전 준비물](#2-사전-준비물)
3. [전체 아키텍처 한눈에 보기](#3-전체-아키텍처-한눈에-보기)
4. [AWS Free Tier 이해하기](#4-aws-free-tier-이해하기)
5. [STEP 1 — AWS 계정 생성 및 초기 보안 설정](#5-step-1--aws-계정-생성-및-초기-보안-설정)
6. [STEP 2 — EC2 인스턴스 생성](#6-step-2--ec2-인스턴스-생성)
7. [STEP 3 — SSH로 서버 접속하기](#7-step-3--ssh로-서버-접속하기)
8. [STEP 4 — 서버 기초 설정](#8-step-4--서버-기초-설정)
9. [STEP 5 — Investmate 설치](#9-step-5--investmate-설치)
10. [STEP 6 — 환경 변수 설정](#10-step-6--환경-변수-설정)
11. [STEP 7 — DB 초기화 및 첫 실행](#11-step-7--db-초기화-및-첫-실행)
12. [STEP 8 — 웹 대시보드 서비스 등록](#12-step-8--웹-대시보드-서비스-등록)
13. [STEP 9 — Nginx 리버스 프록시 설정](#13-step-9--nginx-리버스-프록시-설정)
14. [STEP 10 — 배치 파이프라인 자동 실행 (cron)](#14-step-10--배치-파이프라인-자동-실행-cron)
15. [STEP 11 — 모니터링 및 알림 설정](#15-step-11--모니터링-및-알림-설정)
16. [STEP 12 — 백업 설정](#16-step-12--백업-설정)
17. [STEP 13 — 보안 강화](#17-step-13--보안-강화)
18. [STEP 14 — 최종 검증 체크리스트](#18-step-14--최종-검증-체크리스트)
19. [운영 가이드 — 일상적인 관리](#19-운영-가이드--일상적인-관리)
20. [업데이트 및 배포 자동화](#20-업데이트-및-배포-자동화)
21. [Free Tier 만료 후 비용 최적화](#21-free-tier-만료-후-비용-최적화)
22. [트러블슈팅 — 문제가 생겼을 때](#22-트러블슈팅--문제가-생겼을-때)
23. [용어 사전](#23-용어-사전)

---

## 1. 시작하기 전에 — 이 문서에서 하는 일

이 가이드를 완료하면 다음이 동작합니다:

```
[매일 자동]
  오전 6:30 (한국 시간) → 미국 주식 데이터 수집 → 분석 → 추천 종목 선정 → 리포트 생성

[언제든 접속]
  브라우저에서 http://내서버IP/ → 웹 대시보드로 결과 확인
```

**소요 시간**: 약 1~2시간 (처음이면 2시간, 익숙하면 30분)
**비용**: 12개월간 $0 (AWS Free Tier)
**난이도**: 터미널에 명령어를 복사-붙여넣기 할 수 있으면 충분합니다

---

## 2. 사전 준비물

시작하기 전에 아래 3가지를 준비하세요:

| 준비물                  | 설명                                              | 없으면?                                                                                 |
| ----------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------- |
| **AWS 계정**            | 신용카드 등록 필요 (Free Tier 내에서는 과금 없음) | [STEP 1](#5-step-1--aws-계정-생성-및-초기-보안-설정)에서 만듭니다                       |
| **SSH 터미널**          | 서버에 접속할 프로그램                            | Windows: [MobaXterm](https://mobaxterm.mobatek.net/) 또는 PowerShell / Mac: 기본 터미널 |
| **프로젝트 Git 저장소** | investmate 코드가 올라간 GitHub/GitLab 등         | 미리 `git push` 해두세요                                                                |

> **참고**: 텔레그램 알림을 받고 싶다면 텔레그램 봇 토큰도 준비하세요 (선택사항).

---

## 3. 전체 아키텍처 한눈에 보기

```
                         여러분의 브라우저
                              │
                     http://EC2-공인IP/
                              │
                              ▼
┌──────────────────────────────────────────────────────────┐
│              AWS EC2 서버 (t2.micro, 무료)                │
│                                                          │
│   ┌─────────┐         ┌──────────────────────┐           │
│   │  Nginx  │────────▶│  FastAPI 웹 대시보드  │           │
│   │ (포트80) │         │  (포트 8000)          │           │
│   └─────────┘         └──────────┬───────────┘           │
│                                  │                        │
│   ┌──────────────────┐           │ 같은 DB를 읽음         │
│   │  배치 파이프라인   │           │                        │
│   │  (매일 자동 실행)  │───────────┤                        │
│   └──────────────────┘           │                        │
│                           ┌──────▼──────┐                 │
│                           │  SQLite DB  │                 │
│                           │ (EBS 30GB)  │                 │
│                           └─────────────┘                 │
│                                                          │
│   ┌─────────┐  ┌────────────┐  ┌────────────────┐       │
│   │ S3 백업 │  │ CloudWatch │  │ cron 스케줄러   │       │
│   │ (5GB)   │  │ (모니터링)  │  │ (매일 06:30)   │       │
│   └─────────┘  └────────────┘  └────────────────┘       │
└──────────────────────────────────────────────────────────┘
         │
    외부 인터넷 통신
         │
         ├── yfinance (주식 데이터, 무료)
         ├── Telegram Bot API (알림, 무료)
         └── Gmail SMTP (이메일 알림, 무료)
```

**한줄 요약**: EC2 서버 1대에 **웹 서버 + 배치 작업**을 모두 올리고, **SQLite** 파일 하나로 DB를 운영합니다.

---

## 4. AWS Free Tier 이해하기

> **Free Tier란?** AWS에 처음 가입하면 12개월 동안 일부 서비스를 무료로 쓸 수 있는 프로그램입니다.
> 한도를 넘기지 않는 한 요금이 청구되지 않습니다.

### 우리가 사용할 무료 서비스

| 서비스                    | 무료 한도         | 우리가 쓸 양    | 남는 양   |
| ------------------------- | ----------------- | --------------- | --------- |
| **EC2** (서버)            | 매월 750시간      | ~720시간 (24/7) | 여유      |
| **EBS** (디스크)          | 30GB              | ~8GB (1년 후)   | 22GB 여유 |
| **S3** (파일 저장소)      | 5GB               | ~1GB            | 4GB 여유  |
| **CloudWatch** (모니터링) | 10 지표, 5GB 로그 | 3~5 지표        | 여유      |
| **SNS** (알림)            | 이메일 1,000건/월 | ~30건/월        | 여유      |

### 비용이 발생하는 실수를 피하려면

- EC2 인스턴스를 **1개만** 만드세요 (2개 이상은 750시간 초과)
- EBS 볼륨을 **30GB 이하**로 설정하세요
- 사용하지 않는 EC2 인스턴스는 **종료(Terminate)**하세요 (중지만 하면 EBS 과금)
- AWS 콘솔에서 **Billing → Budgets**에 $1 예산 알림을 설정해두세요

---

## 5. STEP 1 — AWS 계정 생성 및 초기 보안 설정

> 이미 AWS 계정이 있다면 [STEP 2](#6-step-2--ec2-인스턴스-생성)로 건너뛰세요.

### 1-1. 계정 생성

1. https://aws.amazon.com/ 접속
2. **"AWS 계정 생성"** 클릭
3. 이메일, 비밀번호, 계정 이름 입력
4. 연락처 정보 입력 (개인 선택)
5. **신용카드 등록** (검증용 $1 임시 결제 후 환불됨)
6. 전화번호 인증
7. **Basic (무료)** 플랜 선택

### 1-2. 예산 알림 설정 (실수 방지)

> **왜 하나요?** 실수로 Free Tier를 초과하면 요금이 부과됩니다. 미리 알림을 걸어두면 안전합니다.

1. AWS 콘솔 상단 검색창에 `Billing` 입력 → **Billing and Cost Management** 클릭
2. 왼쪽 메뉴에서 **Budgets** 클릭
3. **Create budget** 클릭
4. **Monthly cost budget** 선택 → **Budget amount**: `1` (USD) 입력
5. **Alert threshold**: `80%` → 이메일 주소 입력
6. **Create** 클릭

이제 월 $0.80 이상 발생하면 이메일로 경고가 옵니다.

### 1-3. IAM 사용자 생성 (권장)

> **왜 하나요?** root 계정 대신 별도 사용자를 만들어 쓰면 보안이 강화됩니다.

1. AWS 콘솔 검색창에 `IAM` 입력 → IAM 대시보드 이동
2. 왼쪽 **Users** → **Create user**
3. 이름: `investmate-admin` → **Next**
4. **Attach policies directly** → `AdministratorAccess` 체크 → **Next** → **Create user**
5. 생성된 사용자 클릭 → **Security credentials** 탭 → **Enable console access**
6. 비밀번호 설정 후, 이후로는 이 사용자로 로그인

---

## 6. STEP 2 — EC2 인스턴스 생성

> **EC2란?** Amazon의 가상 서버입니다. 우리가 원격으로 접속해서 프로그램을 실행할 수 있는 컴퓨터 1대를 빌리는 것입니다.

### 2-1. EC2 대시보드로 이동

1. AWS 콘솔 상단 검색창에 `EC2` 입력 → **EC2 Dashboard** 클릭
2. **오른쪽 상단 리전**을 확인하세요:
   - 한국에서 접속: `Asia Pacific (Seoul) ap-northeast-2` 선택
   - 미국 기준으로 운영: `US East (N. Virginia) us-east-1` 선택
3. **Launch instance** (주황색 버튼) 클릭

### 2-2. 인스턴스 설정

아래 항목을 하나씩 설정합니다:

#### Name (이름)

```
investmate-server
```

#### Application and OS Images (AMI 선택)

- **Amazon Linux 2023 AMI** 선택 (Free Tier eligible 표시 확인)
- Architecture: **64-bit (x86)** 선택

> **주의**: ARM(arm64)은 t2.micro에서 지원하지 않습니다. 반드시 **x86**을 선택하세요.

#### Instance type (인스턴스 유형)

```
t2.micro  —  Free tier eligible
```

> 목록에서 `t2.micro`를 선택하면 옆에 "Free tier eligible" 라벨이 보입니다. 이것을 선택하세요.

#### Key pair (로그인용 키)

서버에 SSH로 접속할 때 사용하는 비밀 키 파일입니다.

1. **Create new key pair** 클릭
2. Key pair name: `investmate-key`
3. Key pair type: **RSA**
4. Private key file format:
   - Windows (PuTTY/MobaXterm): `.ppk`
   - Mac/Linux 또는 Windows PowerShell: `.pem`
5. **Create key pair** 클릭 → 파일이 다운로드됩니다

> **중요**: 이 파일(`investmate-key.pem`)은 **절대 분실하면 안 됩니다**. 안전한 곳에 보관하세요.
> 분실하면 서버에 접속할 방법이 없어서 인스턴스를 새로 만들어야 합니다.

#### Network settings (네트워크 설정)

**Edit** 버튼을 클릭한 후:

- **Auto-assign public IP**: `Enable` (외부에서 접속할 수 있도록 공인 IP 부여)
- **Create security group** 선택
- Security group name: `investmate-sg`

**인바운드 규칙 (Inbound rules):**

| Type | Port | Source               | 설명                          |
| ---- | ---- | -------------------- | ----------------------------- |
| SSH  | 22   | My IP                | 내 컴퓨터에서만 SSH 접속 허용 |
| HTTP | 80   | 0.0.0.0/0 (Anywhere) | 웹 대시보드 접속 허용         |

> **보안 팁**: SSH는 반드시 "My IP"로 제한하세요. "Anywhere"로 열면 전 세계에서 무차별 대입 공격을 받습니다.
> IP가 바뀌면 AWS 콘솔에서 Security Group을 수정하면 됩니다.

**규칙 추가 방법:**

1. 기본으로 SSH 규칙이 1개 있습니다 → Source type을 `My IP`로 변경
2. **Add security group rule** 클릭 → Type: `HTTP`, Source: `Anywhere-IPv4`

#### Configure storage (스토리지 설정)

```
30 GiB    gp3    (Free Tier 최대)
```

> 기본값이 8GB일 수 있습니다. **반드시 30으로 변경**하세요. 30GB까지 무료입니다.

### 2-3. 인스턴스 실행

1. 오른쪽 **Summary** 패널에서 설정을 확인:
   - Instance type: t2.micro
   - Storage: 30 GiB gp3
2. **Launch instance** 클릭
3. 초록색 "Success" 메시지가 나오면 성공

### 2-4. 공인 IP 확인

1. EC2 Dashboard → **Instances** 클릭
2. `investmate-server` 인스턴스 클릭
3. **Public IPv4 address** 값을 메모하세요 (예: `3.35.xxx.xxx`)

> 이 IP는 인스턴스를 중지(Stop)했다가 다시 시작하면 바뀔 수 있습니다.
> 고정 IP가 필요하면 [Elastic IP](#elastic-ip-고정-ip-선택)를 참고하세요.

---

## 7. STEP 3 — SSH로 서버 접속하기

> **SSH란?** 원격 서버에 터미널(명령줄)로 접속하는 방법입니다. 마치 서버 앞에 앉아서 키보드를 치는 것과 같습니다.

### Windows (PowerShell)

```powershell
# 1. 키 파일이 있는 폴더로 이동 (보통 다운로드 폴더)
cd C:\Users\사용자이름\Downloads

# 2. SSH 접속 (IP는 위에서 메모한 공인 IP로 교체)
ssh -i investmate-key.pem ec2-user@3.35.xxx.xxx
```

처음 접속 시 `Are you sure you want to continue connecting?` 질문이 나오면 `yes` 입력.

### Windows (MobaXterm)

1. MobaXterm 실행 → **Session** → **SSH**
2. Remote host: `3.35.xxx.xxx` (메모한 IP)
3. Specify username: `ec2-user`
4. Advanced SSH settings → Use private key: `investmate-key.pem` 파일 선택
5. **OK** 클릭

### Mac / Linux

```bash
# 1. 키 파일 권한 설정 (최초 1회)
chmod 400 ~/Downloads/investmate-key.pem

# 2. SSH 접속
ssh -i ~/Downloads/investmate-key.pem ec2-user@3.35.xxx.xxx
```

### 접속 성공 확인

아래와 비슷한 화면이 나오면 성공입니다:

```
   ,     #_
   ~\_  ####_        Amazon Linux 2023
  ~~  \_#####\
  ~~     \###|
  ~~       \#/ ___
   ~~       V~' '->
    ~~~         /
      ~~._.   _/
         _/ _/
       _/m/'
[ec2-user@ip-172-31-xx-xx ~]$
```

> **접속이 안 되면?**
>
> - `Connection timed out`: Security Group에서 SSH(22번 포트)가 내 IP로 열려 있는지 확인
> - `Permission denied`: 키 파일(.pem)이 올바른지, 사용자 이름이 `ec2-user`인지 확인
> - 키 파일 권한 에러: `chmod 400 investmate-key.pem` 실행

---

## 8. STEP 4 — 서버 기초 설정

SSH로 접속한 상태에서 아래 명령어를 **위에서 아래로 순서대로** 실행합니다.

### 4-1. 시스템 업데이트

```bash
sudo dnf update -y
```

> 2~3분 걸릴 수 있습니다. 완료될 때까지 기다리세요.

### 4-2. Swap 메모리 설정 (필수)

> **Swap이란?** 디스크의 일부를 메모리처럼 사용하는 기술입니다.
> t2.micro는 RAM이 1GB뿐이라 배치 파이프라인 실행 시 메모리가 부족할 수 있습니다.
> Swap을 설정하면 디스크를 보조 메모리로 활용하여 이 문제를 방지합니다.

```bash
# 2GB swap 파일 생성
sudo fallocate -l 2G /swapfile

# 보안을 위해 root만 접근 가능하도록 설정
sudo chmod 600 /swapfile

# swap 영역으로 포맷
sudo mkswap /swapfile

# swap 활성화
sudo swapon /swapfile

# 재부팅해도 유지되도록 등록
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# swap 사용 빈도 조정 (60 = 적극적으로 swap 사용)
echo 'vm.swappiness=60' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
```

**확인:**

```bash
free -h
```

아래처럼 Swap 행에 2.0G가 보이면 성공:

```
              total        used        free
Mem:          949Mi       ...         ...
Swap:         2.0Gi       0B          2.0Gi
```

### 4-3. 필수 패키지 설치

```bash
# Python 3.11, pip, git, Nginx 한번에 설치
sudo dnf install python3.11 python3.11-pip python3.11-devel git nginx gcc -y
```

> `gcc`와 `python3.11-devel`은 일부 Python 패키지(lightgbm 등) 컴파일에 필요합니다.

### 4-4. 타임존 설정 (한국 시간)

```bash
sudo timedatectl set-timezone Asia/Seoul
```

**확인:**

```bash
date
```

한국 시간(KST)이 표시되면 성공입니다.

---

## 9. STEP 5 — Investmate 설치

### 5-1. 프로젝트 코드 가져오기

```bash
# 홈 디렉토리로 이동
cd /home/ec2-user

# Git 저장소에서 코드 복제 (본인의 저장소 URL로 교체)
git clone https://github.com/YOUR_USERNAME/investmate.git

# 프로젝트 폴더로 이동
cd investmate
```

> **Git 저장소가 private인 경우:**
>
> ```bash
> # HTTPS + 개인 액세스 토큰 사용
> git clone https://YOUR_TOKEN@github.com/YOUR_USERNAME/investmate.git
> ```
>
> GitHub → Settings → Developer settings → Personal access tokens에서 토큰을 발급하세요.

### 5-2. Python 가상환경 생성 및 패키지 설치

```bash
# 가상환경 생성 (.venv 폴더가 만들어짐)
python3.11 -m venv .venv

# 가상환경 활성화 (프롬프트 앞에 (.venv)가 붙으면 성공)
source .venv/bin/activate

# Investmate 패키지 설치 (의존성 포함, 3~5분 소요)
pip install -e .
```

**확인:**

```bash
investmate --help
```

아래와 비슷한 도움말이 나오면 설치 성공:

```
Usage: investmate [OPTIONS] COMMAND [ARGS]...

  Investmate — AI 주식 투자 가이드

Options:
  --help  Show this message and exit.

Commands:
  ai        AI 분석 관리
  backtest  백테스트 실행
  config    설정 관리
  db        DB 관리
  history   히스토리 조회
  ml        ML 모델 관리
  prompt    AI 프롬프트 조회
  report    리포트 조회
  run       데일리 파이프라인 실행
  stock     종목 상세 조회
  web       웹 대시보드 실행
```

### 5-3. 필요한 디렉토리 생성

```bash
# 로그, 리포트, 데이터 디렉토리 확인/생성
mkdir -p logs reports/daily reports/prompts reports/ai_analysis data
```

---

## 10. STEP 6 — 환경 변수 설정

> **환경 변수란?** 프로그램에 전달하는 설정값입니다.
> 비밀번호나 경로 같은 것들을 코드에 직접 쓰지 않고, `.env` 파일에 모아둡니다.

### 6-1. .env 파일 생성

```bash
cat << 'EOF' > /home/ec2-user/investmate/.env
# ============================================
# Investmate 환경 설정
# ============================================

# --- 기본 설정 ---
INVESTMATE_ENV=prod
INVESTMATE_DB_PATH=/home/ec2-user/investmate/data/investmate.db
INVESTMATE_TOP_N=10
INVESTMATE_HISTORY_PERIOD=2y
INVESTMATE_BATCH_SIZE=50
INVESTMATE_NEWS_COUNT=20

# --- 스크리너 설정 ---
INVESTMATE_MIN_DATA_DAYS=60
INVESTMATE_MIN_VOLUME=100000

# --- AI 분석 (Free Tier에서는 비활성화 권장) ---
INVESTMATE_AI_ENABLED=false
# INVESTMATE_AI_TIMEOUT=300
# INVESTMATE_AI_STYLE=balanced

# --- 알림 (선택: 필요한 것만 주석 해제) ---
# INVESTMATE_NOTIFY_CHANNELS=telegram

# 텔레그램 알림 (선택)
# INVESTMATE_TELEGRAM_TOKEN=여기에_봇_토큰_입력
# INVESTMATE_TELEGRAM_CHAT_ID=여기에_채팅_ID_입력

# 이메일 알림 (선택)
# INVESTMATE_SMTP_USER=your-email@gmail.com
# INVESTMATE_SMTP_PASS=your-gmail-app-password
# INVESTMATE_EMAIL_TO=recipient@example.com

# 슬랙 알림 (선택)
# INVESTMATE_SLACK_WEBHOOK=https://hooks.slack.com/services/...
EOF
```

### 6-2. .env 파일 보안 설정

```bash
# 소유자만 읽기/쓰기 가능하도록 권한 설정
chmod 600 /home/ec2-user/investmate/.env
```

### 6-3. 알림 설정하기 (선택)

텔레그램 알림을 받고 싶다면:

1. 텔레그램에서 `@BotFather`에게 `/newbot` 명령으로 봇 생성
2. 받은 토큰을 `.env`의 `INVESTMATE_TELEGRAM_TOKEN`에 입력
3. 봇과 대화를 시작한 후 `https://api.telegram.org/bot<TOKEN>/getUpdates` 에서 chat_id 확인
4. `.env` 파일 수정:

```bash
# nano 편집기로 .env 파일 열기
nano /home/ec2-user/investmate/.env
```

주석(`#`)을 제거하고 값을 입력한 후 `Ctrl+X` → `Y` → `Enter`로 저장합니다.

```
INVESTMATE_NOTIFY_CHANNELS=telegram
INVESTMATE_TELEGRAM_TOKEN=1234567890:ABCdefGhIJKlmnOPQRstUVwxyz
INVESTMATE_TELEGRAM_CHAT_ID=9876543210
```

---

## 11. STEP 7 — DB 초기화 및 첫 실행

### 7-1. 가상환경 활성화 확인

```bash
# 프롬프트 앞에 (.venv)가 없으면 활성화
cd /home/ec2-user/investmate
source .venv/bin/activate
```

### 7-2. DB 초기화

```bash
investmate db init
```

이 명령은 다음을 수행합니다:

- SQLite 데이터베이스 파일 생성 (`data/investmate.db`)
- 테이블 스키마 생성 (Dimension + Fact 테이블)
- S&P 500 전 종목 (~500개) 시딩
- 기술적 지표, 시그널 타입 정의 시딩
- 날짜 디멘션(2015~2030) 시딩

**확인:**

```bash
investmate db status
```

종목 수, 테이블 현황 등이 표시되면 성공입니다.

### 7-3. 첫 파이프라인 실행 (테스트)

> **주의**: 첫 실행은 2년치 히스토리를 가져오므로 **15~30분** 정도 걸립니다.
> SSH 연결이 끊어지면 실행도 중단되므로, `nohup`을 사용합니다.

```bash
# 백그라운드에서 첫 실행 (SSH 끊어져도 계속 실행)
nohup investmate run > logs/first_run.log 2>&1 &

# 실행 상태 확인 (실시간 로그 보기)
tail -f logs/first_run.log
```

> `tail -f`를 보다가 멈추려면 `Ctrl+C`를 누르세요 (파이프라인은 계속 실행됩니다).

**실행 완료 확인:**

```bash
# 프로세스가 끝났는지 확인
jobs

# 또는 리포트가 생성되었는지 확인
investmate report latest
```

리포트가 터미널에 출력되면 첫 실행 성공입니다.

---

## 12. STEP 8 — 웹 대시보드 서비스 등록

> **systemd란?** Linux에서 서비스(프로그램)를 자동으로 시작/종료/관리하는 시스템입니다.
> 여기서는 FastAPI 웹 서버가 서버 부팅 시 자동으로 시작되도록 등록합니다.

### 8-1. 서비스 파일 생성

```bash
sudo tee /etc/systemd/system/investmate-web.service << 'EOF'
[Unit]
Description=Investmate Web Dashboard
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/investmate
Environment=PATH=/home/ec2-user/investmate/.venv/bin:/usr/bin:/bin
EnvironmentFile=/home/ec2-user/investmate/.env
ExecStart=/home/ec2-user/investmate/.venv/bin/investmate web --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

# 메모리 제한 (배치 파이프라인과 공존을 위해)
MemoryMax=512M
MemoryHigh=400M

# 로그 설정
StandardOutput=journal
StandardError=journal
SyslogIdentifier=investmate-web

[Install]
WantedBy=multi-user.target
EOF
```

### 8-2. 서비스 시작

```bash
# systemd에 새 서비스 파일 인식시키기
sudo systemctl daemon-reload

# 부팅 시 자동 시작 활성화
sudo systemctl enable investmate-web

# 지금 바로 시작
sudo systemctl start investmate-web
```

### 8-3. 동작 확인

```bash
# 서비스 상태 확인
sudo systemctl status investmate-web
```

`Active: active (running)` 이 보이면 성공입니다.

```bash
# 웹 서버 응답 확인
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/
```

`200`이 출력되면 웹 서버가 정상 동작 중입니다.

> **실패했다면?** 로그를 확인하세요:
>
> ```bash
> sudo journalctl -u investmate-web -n 50 --no-pager
> ```

---

## 13. STEP 9 — Nginx 리버스 프록시 설정

> **Nginx란?** 고성능 웹 서버입니다. 여기서는 "프록시" 역할을 합니다.
> 사용자가 포트 80(http://서버IP/)으로 접속하면, Nginx가 그 요청을 내부의 FastAPI(포트 8000)로 전달합니다.
>
> **왜 필요한가요?**
>
> - 포트 80은 브라우저에서 `:80`을 안 붙여도 됩니다 (편의성)
> - Nginx가 정적 파일(CSS, JS)을 직접 처리해서 FastAPI 부하를 줄입니다
> - 보안 헤더를 자동으로 추가합니다

### 9-1. Nginx 설정 파일 생성

```bash
sudo tee /etc/nginx/conf.d/investmate.conf << 'EOF'
server {
    listen 80;
    server_name _;

    # === 보안 헤더 ===
    add_header X-Frame-Options DENY;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";

    # === 접속 제한 (선택) ===
    # 본인 IP만 허용하려면 아래 2줄의 주석(#)을 제거하고 IP를 입력하세요:
    # allow 123.456.789.0/32;
    # deny all;

    # === FastAPI로 요청 전달 ===
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket 지원 (채팅 기능)
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        # 타임아웃 (배치 중 웹이 느려질 수 있으므로 여유있게)
        proxy_connect_timeout 60s;
        proxy_read_timeout 120s;
    }

    # === 정적 파일 직접 서빙 (Nginx가 처리 → 더 빠름) ===
    location /static/ {
        alias /home/ec2-user/investmate/src/web/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
EOF
```

### 9-2. 기본 Nginx 설정과 충돌 방지

```bash
# 기본 설정의 server 블록이 충돌할 수 있으므로 비활성화
sudo sed -i '/^[^#]*server {/,/^[^#]*}/s/^/#/' /etc/nginx/nginx.conf 2>/dev/null || true
```

> 위 명령이 에러나도 무시해도 됩니다. Amazon Linux 2023은 보통 기본 server 블록이 없습니다.

### 9-3. 설정 테스트 및 시작

```bash
# Nginx 설정 문법 검사
sudo nginx -t
```

`syntax is ok`와 `test is successful`이 나와야 합니다.

```bash
# Nginx 시작 및 부팅 시 자동 시작 등록
sudo systemctl enable nginx
sudo systemctl start nginx
```

### 9-4. 브라우저에서 확인

본인 컴퓨터의 브라우저에서:

```
http://3.35.xxx.xxx/
```

> `3.35.xxx.xxx`는 [STEP 2-4](#2-4-공인-ip-확인)에서 메모한 공인 IP입니다.

Investmate 대시보드가 보이면 성공입니다.

> **접속이 안 되면?**
>
> 1. Security Group에서 HTTP(80) 포트가 열려있는지 확인
> 2. `sudo systemctl status nginx` 로 Nginx 상태 확인
> 3. `sudo systemctl status investmate-web` 로 FastAPI 상태 확인
> 4. `curl http://localhost:8000/` 로 FastAPI가 직접 응답하는지 확인

---

## 14. STEP 10 — 배치 파이프라인 자동 실행 (cron)

> **cron이란?** 정해진 시간에 자동으로 명령을 실행하는 Linux의 스케줄러입니다.
> 매일 장 마감 후 `investmate run`이 자동 실행되도록 설정합니다.

### 10-1. 배치 실행 스크립트 생성

단순히 cron에 `investmate run`을 넣는 대신, 래퍼 스크립트를 만들어서
로깅, 에러 처리, S3 백업까지 한번에 처리합니다.

```bash
cat << 'SCRIPT' > /home/ec2-user/investmate/scripts/run_batch.sh
#!/bin/bash
# ============================================
# Investmate 데일리 배치 실행 스크립트
# cron에서 매일 자동으로 호출됩니다.
# ============================================
set -euo pipefail

PROJECT_DIR="/home/ec2-user/investmate"
LOG_DIR="${PROJECT_DIR}/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/${TODAY}_batch.log"

# 로그 디렉토리 확인
mkdir -p "${LOG_DIR}"

# 가상환경 활성화
source "${PROJECT_DIR}/.venv/bin/activate"
cd "${PROJECT_DIR}"

echo "========================================" >> "${LOG_FILE}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 배치 시작" >> "${LOG_FILE}"
echo "========================================" >> "${LOG_FILE}"

# 파이프라인 실행
if investmate run >> "${LOG_FILE}" 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 배치 성공" >> "${LOG_FILE}"
else
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 배치 실패 (exit=${EXIT_CODE})" >> "${LOG_FILE}"
fi

# S3 백업 (S3 설정이 되어있을 때만 실행, 실패해도 무시)
if command -v aws &> /dev/null; then
    BUCKET="investmate-backup-$(whoami)"
    aws s3 cp "${PROJECT_DIR}/reports/daily/${TODAY}.md" \
        "s3://${BUCKET}/reports/${TODAY}.md" 2>/dev/null || true
    aws s3 cp "${PROJECT_DIR}/reports/daily/${TODAY}.json" \
        "s3://${BUCKET}/reports/${TODAY}.json" 2>/dev/null || true
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 스크립트 종료" >> "${LOG_FILE}"
SCRIPT

# 실행 권한 부여
chmod +x /home/ec2-user/investmate/scripts/run_batch.sh
```

### 10-2. cron 등록

```bash
# crontab 편집기 열기
crontab -e
```

> 처음 실행하면 편집기를 선택하라고 합니다. `nano`를 추천합니다 (번호 입력 후 Enter).

파일 맨 아래에 다음 한 줄을 추가합니다:

```
# 매일 평일(월-금) 오전 6:30 KST에 배치 실행
# (미국 장 마감 = EST 16:30 = KST 06:30 다음날)
30 6 * * 1-5 /home/ec2-user/investmate/scripts/run_batch.sh
```

`Ctrl+X` → `Y` → `Enter`로 저장합니다.

### 10-3. cron 등록 확인

```bash
crontab -l
```

위에서 입력한 줄이 보이면 성공입니다.

### 10-4. 수동 테스트

```bash
# 스크립트가 정상 동작하는지 직접 실행해봅니다
/home/ec2-user/investmate/scripts/run_batch.sh

# 로그 확인
cat /home/ec2-user/investmate/logs/$(date +%Y-%m-%d)_batch.log
```

### 10-5. 주간 리포트 배치 스크립트

주간 리포트도 자동으로 실행되도록 래퍼 스크립트를 등록합니다.

> 주간 리포트 스크립트(`scripts/run_weekly.sh`)는 이미 리포지토리에 포함되어 있습니다.
> `git pull` 후 실행 권한만 부여하면 됩니다.

```bash
chmod +x /home/ec2-user/investmate/scripts/run_weekly.sh
```

### 10-6. 주간 cron 등록

```bash
crontab -e
```

기존 줄 아래에 추가:

```
# 매주 일요일 오전 9시 KST에 주간 리포트 실행
0 9 * * 0 /home/ec2-user/investmate/scripts/run_weekly.sh
```

### 10-7. 주간 배치 수동 테스트

```bash
# 스크립트가 정상 동작하는지 직접 실행해봅니다
/home/ec2-user/investmate/scripts/run_weekly.sh

# 로그 확인
cat /home/ec2-user/investmate/logs/$(date +%Y-%m-%d)_weekly.log

# 생성된 리포트 확인
ls -la /home/ec2-user/investmate/reports/weekly/
```

---

## 15. STEP 11 — 모니터링 및 알림 설정

### 11-1. 헬스체크 스크립트 (웹 서버 자동 복구)

> 웹 서버가 죽으면 5분 이내에 자동으로 재시작합니다.

```bash
cat << 'SCRIPT' > /home/ec2-user/investmate/scripts/healthcheck.sh
#!/bin/bash
# ============================================
# 웹 서비스 헬스체크 — 5분마다 cron으로 실행
# 응답이 없으면 자동 재시작
# ============================================

LOG="/home/ec2-user/investmate/logs/healthcheck.log"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://localhost:8000/ 2>/dev/null || echo "000")

if [ "${HTTP_CODE}" != "200" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 웹 서비스 이상 (HTTP ${HTTP_CODE}), 재시작 시도" >> "${LOG}"
    sudo systemctl restart investmate-web
    sleep 5

    # 재시작 후 재확인
    HTTP_CODE_AFTER=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 http://localhost:8000/ 2>/dev/null || echo "000")
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 재시작 후 상태: HTTP ${HTTP_CODE_AFTER}" >> "${LOG}"
fi
SCRIPT

chmod +x /home/ec2-user/investmate/scripts/healthcheck.sh
```

cron에 등록:

```bash
crontab -e
```

기존 줄 아래에 추가:

```
# 5분마다 웹 서비스 헬스체크
*/5 * * * * /home/ec2-user/investmate/scripts/healthcheck.sh
```

### 11-2. 디스크/메모리 모니터링 스크립트

```bash
cat << 'SCRIPT' > /home/ec2-user/investmate/scripts/system_check.sh
#!/bin/bash
# ============================================
# 시스템 리소스 점검 — 매시간 cron으로 실행
# 디스크 90% 이상 또는 메모리 부족 시 경고
# ============================================

LOG="/home/ec2-user/investmate/logs/system_check.log"

# 디스크 사용률 확인
DISK_USAGE=$(df / | tail -1 | awk '{print $5}' | tr -d '%')
if [ "${DISK_USAGE}" -gt 90 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 디스크 경고: ${DISK_USAGE}% 사용 중" >> "${LOG}"
fi

# 메모리 사용률 확인
MEM_AVAILABLE=$(free -m | awk '/^Mem:/{print $7}')
if [ "${MEM_AVAILABLE}" -lt 100 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 메모리 경고: 가용 ${MEM_AVAILABLE}MB" >> "${LOG}"
fi

# 웹 서비스 메모리 사용량 기록
WEB_MEM=$(systemctl show investmate-web --property=MemoryCurrent 2>/dev/null | cut -d= -f2)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 시스템 정상 | 디스크: ${DISK_USAGE}% | 가용 메모리: ${MEM_AVAILABLE}MB | 웹: ${WEB_MEM:-N/A}" >> "${LOG}"
SCRIPT

chmod +x /home/ec2-user/investmate/scripts/system_check.sh
```

cron에 등록 (`crontab -e`):

```
# 매시간 시스템 리소스 점검
0 * * * * /home/ec2-user/investmate/scripts/system_check.sh
```

### 11-3. 로그 로테이션 (디스크 꽉 참 방지)

> 로그 파일이 계속 커지면 디스크가 가득 찹니다. 30일 이상 된 로그를 자동 삭제합니다.

```bash
sudo tee /etc/logrotate.d/investmate << 'EOF'
/home/ec2-user/investmate/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 0644 ec2-user ec2-user
}
EOF
```

---

## 16. STEP 12 — 백업 설정

> **왜 백업이 필요한가요?** 서버에 문제가 생기면 축적된 주식 데이터를 모두 잃을 수 있습니다.
> S3에 정기 백업을 해두면 새 서버에서 빠르게 복구할 수 있습니다.

### 12-1. S3 버킷 생성

```bash
# AWS CLI 설정 (최초 1회)
aws configure
```

아래 정보를 입력합니다:

```
AWS Access Key ID: (IAM 사용자의 액세스 키)
AWS Secret Access Key: (IAM 사용자의 시크릿 키)
Default region name: ap-northeast-2  (서울 리전인 경우)
Default output format: json
```

> **액세스 키 발급 방법:**
> AWS 콘솔 → IAM → Users → `investmate-admin` → Security credentials → Create access key
> → Command Line Interface (CLI) 선택 → 키 발급 → 안전한 곳에 메모

```bash
# S3 버킷 생성 (버킷 이름은 전세계에서 유일해야 함)
aws s3 mb s3://investmate-backup-$(date +%s)
```

> 버킷 이름을 메모해두세요. 아래 스크립트에서 사용합니다.

### 12-2. 주간 DB 백업 스크립트

```bash
cat << 'SCRIPT' > /home/ec2-user/investmate/scripts/backup_db.sh
#!/bin/bash
# ============================================
# SQLite DB를 S3에 백업 — 매주 일요일 cron으로 실행
# ============================================

PROJECT_DIR="/home/ec2-user/investmate"
DB_FILE="${PROJECT_DIR}/data/investmate.db"
BACKUP_NAME="investmate-$(date +%Y%m%d).db"
LOG="${PROJECT_DIR}/logs/backup.log"

# 여기에 본인의 S3 버킷 이름을 입력하세요
S3_BUCKET="investmate-backup-여기에버킷이름"

if [ -f "${DB_FILE}" ]; then
    # SQLite 안전 백업 (WAL 모드 대응)
    sqlite3 "${DB_FILE}" ".backup /tmp/${BACKUP_NAME}"

    # S3에 업로드
    if aws s3 cp "/tmp/${BACKUP_NAME}" "s3://${S3_BUCKET}/db-backup/${BACKUP_NAME}"; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] DB 백업 성공: ${BACKUP_NAME}" >> "${LOG}"
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] DB 백업 실패: S3 업로드 에러" >> "${LOG}"
    fi

    # 임시 파일 삭제
    rm -f "/tmp/${BACKUP_NAME}"

    # S3에서 90일 이상 된 백업 삭제 (용량 관리)
    aws s3 ls "s3://${S3_BUCKET}/db-backup/" | while read -r line; do
        FILE_DATE=$(echo "${line}" | awk '{print $1}')
        FILE_NAME=$(echo "${line}" | awk '{print $4}')
        if [ -n "${FILE_DATE}" ] && [ "$(date -d "${FILE_DATE}" +%s 2>/dev/null || echo 0)" -lt "$(date -d '90 days ago' +%s)" ]; then
            aws s3 rm "s3://${S3_BUCKET}/db-backup/${FILE_NAME}" 2>/dev/null || true
        fi
    done
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] DB 파일 없음: ${DB_FILE}" >> "${LOG}"
fi
SCRIPT

chmod +x /home/ec2-user/investmate/scripts/backup_db.sh
```

cron에 등록 (`crontab -e`):

```
# 매주 일요일 밤 11시에 DB 백업
0 23 * * 0 /home/ec2-user/investmate/scripts/backup_db.sh
```

---

## 17. STEP 13 — 보안 강화

### 13-1. SSH 보안 설정

```bash
# SSH 설정 파일 백업
sudo cp /etc/ssh/sshd_config /etc/ssh/sshd_config.backup

# 비밀번호 로그인 비활성화 (키 파일로만 접속 가능)
sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config

# root 직접 로그인 비활성화
sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

# SSH 서비스 재시작
sudo systemctl restart sshd
```

> **주의**: 이 설정을 적용한 후에는 키 파일(.pem) 없이는 접속할 수 없습니다.
> 키 파일을 안전한 곳에 백업해두세요.

### 13-2. 자동 보안 업데이트

```bash
# dnf-automatic 설치 (보안 패치 자동 적용)
sudo dnf install dnf-automatic -y

# 자동 설치 활성화
sudo sed -i 's/apply_updates = no/apply_updates = yes/' /etc/dnf/automatic.conf

# 타이머 활성화
sudo systemctl enable dnf-automatic-install.timer
sudo systemctl start dnf-automatic-install.timer
```

### 13-3. fail2ban 설치 (SSH 무차별 대입 공격 방지)

```bash
sudo dnf install fail2ban -y

sudo tee /etc/fail2ban/jail.local << 'EOF'
[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/secure
maxretry = 5
bantime = 3600
findtime = 600
EOF

sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

> 5회 비밀번호 틀리면 1시간 동안 해당 IP를 차단합니다.

---

## 18. STEP 14 — 최종 검증 체크리스트

모든 설정이 끝났습니다. 아래 항목을 하나씩 확인하세요.

```bash
# ============================================
# 최종 검증 스크립트 — 모두 OK가 나와야 합니다
# ============================================

echo "=== 1. Swap 메모리 ==="
swapon --show | grep -q swapfile && echo "OK: Swap 활성화됨" || echo "FAIL: Swap 없음"

echo ""
echo "=== 2. Python 버전 ==="
/home/ec2-user/investmate/.venv/bin/python --version

echo ""
echo "=== 3. Investmate CLI ==="
/home/ec2-user/investmate/.venv/bin/investmate --help > /dev/null 2>&1 && echo "OK: CLI 동작" || echo "FAIL: CLI 에러"

echo ""
echo "=== 4. DB 상태 ==="
source /home/ec2-user/investmate/.venv/bin/activate
investmate db status 2>/dev/null | head -5

echo ""
echo "=== 5. 웹 서비스 ==="
sudo systemctl is-active investmate-web && echo "OK" || echo "FAIL: 서비스 비활성"

echo ""
echo "=== 6. FastAPI 응답 ==="
HTTP=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost:8000/)
[ "${HTTP}" = "200" ] && echo "OK: HTTP ${HTTP}" || echo "FAIL: HTTP ${HTTP}"

echo ""
echo "=== 7. Nginx ==="
sudo systemctl is-active nginx && echo "OK" || echo "FAIL: Nginx 비활성"

echo ""
echo "=== 8. Nginx → FastAPI 프록시 ==="
HTTP2=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 http://localhost/)
[ "${HTTP2}" = "200" ] && echo "OK: HTTP ${HTTP2}" || echo "FAIL: HTTP ${HTTP2}"

echo ""
echo "=== 9. cron 등록 ==="
crontab -l 2>/dev/null | grep -q "run_batch" && echo "OK: 배치 cron 등록됨" || echo "FAIL: 배치 cron 없음"
crontab -l 2>/dev/null | grep -q "run_weekly" && echo "OK: 주간 cron 등록됨" || echo "FAIL: 주간 cron 없음"
crontab -l 2>/dev/null | grep -q "healthcheck" && echo "OK: 헬스체크 cron 등록됨" || echo "FAIL: 헬스체크 cron 없음"

echo ""
echo "=== 10. 디스크 여유 ==="
df -h / | tail -1 | awk '{print "사용: "$3" / "$2" ("$5" 사용)"}'

echo ""
echo "=== 11. 메모리 상태 ==="
free -h | grep -E "Mem|Swap"
```

위 스크립트를 한 번에 실행하려면:

```bash
bash -c '위의_전체_스크립트'
```

또는 하나씩 수동으로 확인하세요. 모든 항목이 OK면 배포 완료입니다.

---

## 19. 운영 가이드 — 일상적인 관리

### 자주 쓰는 명령어 모음

```bash
# ── SSH 접속 ──
ssh -i investmate-key.pem ec2-user@서버IP

# ── 가상환경 활성화 (접속 후 항상 먼저 실행) ──
cd /home/ec2-user/investmate && source .venv/bin/activate

# ── 오늘의 리포트 확인 ──
investmate report latest

# ── 특정 날짜 리포트 확인 ──
investmate report show 2026-03-20

# ── 개별 종목 상세 분석 ──
investmate stock AAPL

# ── 배치를 수동으로 즉시 실행 ──
investmate run

# ── 과거 추천 성과 확인 ──
investmate history recommendations

# ── 웹 서비스 재시작 ──
sudo systemctl restart investmate-web

# ── 웹 서비스 로그 보기 ──
sudo journalctl -u investmate-web -f

# ── 배치 실행 로그 보기 ──
tail -f /home/ec2-user/investmate/logs/$(date +%Y-%m-%d)_batch.log

# ── DB 상태 확인 ──
investmate db status

# ── 디스크 사용량 확인 ──
df -h /

# ── 메모리 사용량 확인 ──
free -h
```

### 주간 점검 사항

| 점검 항목 | 명령어                                 | 정상 상태             |
| --------- | -------------------------------------- | --------------------- |
| 웹 서비스 | `sudo systemctl status investmate-web` | active (running)      |
| 배치 로그 | `ls -la logs/`                         | 평일마다 새 로그 파일 |
| 디스크    | `df -h /`                              | 80% 미만              |
| DB 크기   | `ls -lh data/investmate.db`            | 매주 조금씩 증가      |
| 리포트    | `ls reports/daily/ \| tail -5`         | 평일마다 생성         |

---

## 20. 업데이트 및 배포 자동화

### 20-1. 수동 업데이트 (코드 변경 시)

```bash
cd /home/ec2-user/investmate
source .venv/bin/activate

# 최신 코드 가져오기
git pull origin main

# 의존성 업데이트 (새 패키지 추가 시)
pip install -e .

# DB 스키마 마이그레이션 (새 컬럼 추가 시)
investmate db init

# 웹 서비스 재시작 (코드 변경 반영)
sudo systemctl restart investmate-web

# 확인
investmate db status
```

### 20-2. 원클릭 배포 스크립트

```bash
cat << 'SCRIPT' > /home/ec2-user/investmate/scripts/deploy.sh
#!/bin/bash
# ============================================
# 원클릭 배포 스크립트
# 코드 업데이트 → 의존성 설치 → DB 마이그레이션 → 서비스 재시작
# ============================================
set -e

PROJECT_DIR="/home/ec2-user/investmate"
cd "${PROJECT_DIR}"

echo "[1/5] 코드 업데이트..."
git pull origin main

echo "[2/5] 가상환경 활성화..."
source .venv/bin/activate

echo "[3/5] 의존성 설치..."
pip install -e . --quiet

echo "[4/5] DB 마이그레이션..."
investmate db init 2>/dev/null || true

echo "[5/5] 웹 서비스 재시작..."
sudo systemctl restart investmate-web

echo ""
echo "=== 배포 완료 ==="
sudo systemctl status investmate-web --no-pager | head -5
echo ""
investmate db status 2>/dev/null | head -3
SCRIPT

chmod +x /home/ec2-user/investmate/scripts/deploy.sh
```

사용법:

```bash
/home/ec2-user/investmate/scripts/deploy.sh
```

### 20-3. GitHub Actions 자동 배포 (선택)

> GitHub에 코드를 push하면 자동으로 EC2에 배포됩니다.

**GitHub 리포지토리 설정:**

1. GitHub → 리포지토리 → Settings → Secrets and variables → Actions
2. 아래 시크릿 추가:
   - `EC2_HOST`: EC2 공인 IP (예: `3.35.xxx.xxx`)
   - `EC2_SSH_KEY`: `investmate-key.pem` 파일의 **전체 내용** 복사-붙여넣기

**워크플로우 파일 생성:**

리포지토리에 `.github/workflows/deploy.yml` 파일 생성:

```yaml
name: Deploy to EC2

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy via SSH
        uses: appleboy/ssh-action@v1
        with:
          host: ${{ secrets.EC2_HOST }}
          username: ec2-user
          key: ${{ secrets.EC2_SSH_KEY }}
          script: /home/ec2-user/investmate/scripts/deploy.sh
```

---

## 21. Free Tier 만료 후 비용 최적화

12개월 Free Tier가 만료되면 과금이 시작됩니다. 최소 비용으로 계속 운영하는 방법:

### Option A: 가장 저렴한 EC2 유지 (~$5/월)

```
t4g.micro (ARM, 2 vCPU, 1GB RAM) — 서울 리전 기준
  인스턴스: ~$3/월 (1년 예약 시)
  EBS 30GB: ~$2.40/월
  합계: 약 $5.50/월 (₩7,500)
```

> t4g는 ARM 기반이므로 코드 호환성 확인이 필요하지만, Python은 문제없이 동작합니다.

### Option B: Lightsail ($5/월)

```
$5/월 플랜: 1 vCPU, 1GB RAM, 40GB SSD, 2TB 전송
→ EC2보다 관리가 쉽고, 고정 IP 무료 포함
```

### Option C: 배치만 클라우드 + 웹은 로컬 (~$2/월)

```
배치: EC2 Spot 또는 Lambda (하루 30분만 실행)
웹: 본인 PC에서 investmate web 실행
DB: S3에 동기화
합계: 약 $2/월 (₩2,700)
```

### Option D: 전부 로컬 ($0/월)

```
본인 PC에서 모든 것을 실행
배치: Windows 작업 스케줄러로 자동화
웹: localhost:8000으로만 접속
DB: 로컬 파일 그대로 사용
```

---

## 22. 트러블슈팅 — 문제가 생겼을 때

### SSH 접속이 안 돼요

| 증상                   | 원인                                  | 해결                                                 |
| ---------------------- | ------------------------------------- | ---------------------------------------------------- |
| `Connection timed out` | Security Group에서 포트 22가 막혀있음 | AWS 콘솔 → EC2 → Security Groups → SSH(22) 규칙 확인 |
| `Connection refused`   | SSH 데몬이 실행되지 않음              | EC2 콘솔에서 인스턴스 재시작                         |
| `Permission denied`    | 키 파일이 맞지 않음                   | 올바른 .pem 파일 사용 확인                           |
| `bad permissions`      | 키 파일 권한이 너무 개방적            | `chmod 400 investmate-key.pem`                       |
| 내 IP가 바뀜           | Security Group이 이전 IP만 허용       | Security Group에서 SSH 규칙의 IP 업데이트            |

### 웹 대시보드가 안 보여요

```bash
# 1단계: FastAPI가 실행 중인가?
sudo systemctl status investmate-web

# 2단계: FastAPI가 응답하는가?
curl http://localhost:8000/

# 3단계: Nginx가 실행 중인가?
sudo systemctl status nginx

# 4단계: Nginx 설정에 에러가 있나?
sudo nginx -t

# 5단계: Security Group에서 HTTP(80) 포트가 열려있나?
# → AWS 콘솔에서 확인

# 6단계: 웹 서비스 로그 확인
sudo journalctl -u investmate-web -n 30 --no-pager
```

### 배치가 실행되지 않아요

```bash
# 1단계: cron이 등록되어있나?
crontab -l

# 2단계: 배치 로그 확인
ls -la /home/ec2-user/investmate/logs/

# 3단계: 수동으로 실행해보기
cd /home/ec2-user/investmate
source .venv/bin/activate
investmate run

# 4단계: 권한 문제 확인
ls -la /home/ec2-user/investmate/scripts/run_batch.sh
# → -rwxr-xr-x 여야 함 (x = 실행 권한)
```

### 메모리 부족 (Out of Memory)

```bash
# Swap이 활성화되어있는지 확인
free -h

# Swap이 없으면 다시 설정
sudo swapon /swapfile

# 웹 서비스 메모리 사용량 확인
sudo systemctl show investmate-web --property=MemoryCurrent

# 메모리를 많이 먹는 프로세스 확인
top -o %MEM
```

### 디스크 부족

```bash
# 디스크 사용량 확인
df -h /

# 어디서 많이 쓰고 있는지 확인
du -sh /home/ec2-user/investmate/*

# 오래된 로그 삭제
find /home/ec2-user/investmate/logs/ -name "*.log" -mtime +30 -delete

# 오래된 리포트 삭제
find /home/ec2-user/investmate/reports/daily/ -name "*.md" -mtime +90 -delete
```

### DB가 손상된 것 같아요

```bash
# DB 무결성 검사
sqlite3 /home/ec2-user/investmate/data/investmate.db "PRAGMA integrity_check;"
# → "ok"가 나오면 정상

# DB 재초기화 (데이터 손실!)
# 정말 필요한 경우에만:
mv data/investmate.db data/investmate.db.broken
investmate db init
investmate run  # 데이터 재수집
```

---

## 23. 용어 사전

| 용어               | 설명                                                                            |
| ------------------ | ------------------------------------------------------------------------------- |
| **EC2**            | Elastic Compute Cloud. AWS의 가상 서버. 원격으로 접속해서 사용하는 컴퓨터       |
| **EBS**            | Elastic Block Store. EC2에 연결된 디스크(하드드라이브). 서버를 꺼도 데이터 유지 |
| **S3**             | Simple Storage Service. 파일 저장소. 백업 용도로 사용                           |
| **AMI**            | Amazon Machine Image. 운영체제(OS)가 설치된 서버 이미지. "기본 세팅된 컴퓨터"   |
| **Security Group** | 가상 방화벽. 어떤 포트를 누구에게 열어줄지 결정하는 규칙                        |
| **SSH**            | Secure Shell. 암호화된 원격 접속 방법. 터미널로 서버를 조작                     |
| **Nginx**          | 웹 서버/리버스 프록시. 외부 요청을 내부 앱으로 전달하는 중간 다리 역할          |
| **systemd**        | Linux 서비스 관리자. 프로그램을 자동 시작/종료/재시작                           |
| **cron**           | Linux 작업 스케줄러. 정해진 시간에 자동으로 명령 실행                           |
| **Swap**           | 디스크를 메모리처럼 사용하는 기술. RAM이 부족할 때 보조 역할                    |
| **Free Tier**      | AWS 가입 후 12개월간 일부 서비스를 무료로 쓸 수 있는 프로그램                   |
| **t2.micro**       | EC2 인스턴스 유형. 1 vCPU + 1GB RAM. Free Tier 대상                             |
| **gp3**            | EBS 볼륨 유형. 범용 SSD. 성능과 비용의 균형이 좋음                              |
| **WAL 모드**       | Write-Ahead Logging. SQLite의 동시 접근 최적화 모드                             |
| **리버스 프록시**  | 외부 요청을 내부 서버로 전달하는 중간 서버. Nginx가 이 역할을 함                |
| **Elastic IP**     | EC2에 연결할 수 있는 고정 공인 IP. 재시작해도 IP가 바뀌지 않음                  |

---

## 부록

### Elastic IP (고정 IP, 선택)

EC2를 재시작하면 공인 IP가 바뀝니다. 고정 IP가 필요하면:

1. EC2 Dashboard → 왼쪽 메뉴 **Elastic IPs** → **Allocate Elastic IP address**
2. **Allocate** 클릭
3. 할당된 IP 선택 → **Actions** → **Associate Elastic IP address**
4. Instance: `investmate-server` 선택 → **Associate**

> **주의**: Elastic IP를 할당했지만 인스턴스에 연결하지 않으면 시간당 과금됩니다.
> 사용하지 않을 때는 반드시 Release하세요.

### cron 시간 설정 참고

```
* * * * *  명령어
│ │ │ │ │
│ │ │ │ └─ 요일 (0=일, 1=월, ..., 6=토)
│ │ │ └─── 월 (1-12)
│ │ └───── 일 (1-31)
│ └─────── 시 (0-23)
└───────── 분 (0-59)

예시:
30 6 * * 1-5  → 평일 오전 6시 30분
0 */2 * * *   → 2시간마다
0 0 * * 0     → 매주 일요일 자정
```

### t2.micro 리소스 제약 참고

| 항목       | 수치                    | Investmate 사용량            | 여유             |
| ---------- | ----------------------- | ---------------------------- | ---------------- |
| vCPU       | 1개                     | 배치 시 100%, 웹 시 5-10%    | 배치는 하루 30분 |
| RAM        | 1GB                     | 웹 300MB + 배치 800MB (동시) | Swap 2GB 보완    |
| CPU 크레딧 | 6크레딧/시간 (최대 144) | 배치 30분: ~30크레딧         | 하루 1회면 충분  |
| 네트워크   | Low~Moderate            | 배치: ~50MB, 웹: ~1MB/요청   | 여유             |
| EBS IOPS   | 3,000 (gp3 기본)        | SQLite I/O: ~100 IOPS        | 충분             |

> **CPU 크레딧이란?** t2 인스턴스는 기본 CPU 사용률(10%)을 초과하면 "크레딧"을 소모합니다.
> 크레딧은 사용하지 않는 동안 자동 충전됩니다. 하루 1회 30분 배치면 크레딧이 충분합니다.
> 크레딧이 고갈되면 CPU가 10%로 제한되어 웹이 느려질 수 있습니다.
