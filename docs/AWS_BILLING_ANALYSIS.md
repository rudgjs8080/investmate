# AWS 과금 발생 가능 지점 분석 — investmate

> **작성일**: 2026-04-11
> **대상 환경**: AWS EC2 t2.micro Free Tier 기반 단일 서버 배포 (`AWS_DEPLOYMENT.md` 구성 기준)
> **목적**: 현재 배포 구성에서 AWS 청구서에 줄로 찍힐 수 있는 모든 항목을 식별하고, 회피/모니터링 방법을 정리한다.
>
> **주의**: 이 문서는 AWS 직접 과금 항목만 다룬다. Anthropic API, Telegram, yfinance, Gmail SMTP 등은 AWS 외부 서비스라 별도 청구이며 제외했다.

---

## 0. 한눈에 보는 결론

| 시점 | 베이스라인 (서울 리전) | 주요 과금 항목 |
|---|---|---|
| **Free Tier 12개월 내** | $0 ~ $1/월 | 실수로 켜진 EIP, 누적된 S3 백업, 종료 안 한 인스턴스의 EBS |
| **Free Tier 만료 후 (정상 운영)** | 약 $14~16/월 | EC2 t2.micro + EBS 30GB + Public IPv4 |
| **함정에 빠진 경우** | $30~100+/월 | NAT Gateway, GuardDuty, CloudWatch Logs 누적, 데이터 전송 폭주 |

**가장 흔한 "왜 과금되지?" Top 3:**
1. **연결 안 된 Elastic IP** — 시간당 과금 (월 ~$3.6)
2. **종료된 인스턴스가 남긴 EBS 볼륨/스냅샷** — 인스턴스를 Stop만 하거나 Terminate 후 볼륨이 남음
3. **S3 백업이 90일 클린업 실패로 무한 누적** — 5GB 무료 한도 초과 시 GB당 과금

---

## 1. EC2 컴퓨트 (가장 큰 비중)

### 1-1. 무료 한도

| 항목 | Free Tier (12개월) | Free Tier 후 (서울) |
|---|---|---|
| t2.micro 시간 | 750시간/월 | $0.0144/시간 ≈ **$10.5/월** |
| t3.micro 시간 | 750시간/월 (2024년부터 일부 리전) | $0.013/시간 ≈ **$9.5/월** |

> 750시간은 한 달(720~744시간) 풀가동을 커버하지만 **계정당 합산** 한도다.
> EC2 인스턴스가 2개 떠 있으면 1500시간이 되어 750시간 초과분이 과금된다.

### 1-2. 과금 함정

| 시나리오 | 결과 |
|---|---|
| 테스트용으로 인스턴스를 1대 더 띄우고 잊어버림 | 750시간 초과분 즉시 과금 |
| 인스턴스를 **Stop**만 함 | EC2 시간 과금은 멈춤. 그러나 **EBS는 계속 과금** |
| 인스턴스를 **Reboot** | 같은 인스턴스, 영향 없음 |
| 인스턴스 유형을 t2.medium 이상으로 변경 | t2.micro만 무료 — **다른 유형은 100% 과금** |
| Spot 인스턴스를 켜둠 | Spot도 시간당 과금 (할인은 되지만 무료 아님) |
| Savings Plan / RI 가입 | 약정금이 매월 자동 청구 |

### 1-3. 확인 방법

```bash
# AWS 콘솔에서:
EC2 Dashboard → Instances → State 필터로 "Running" 만 확인
→ 의도하지 않은 인스턴스가 있는지 점검
→ 인스턴스 유형이 t2.micro 인지 확인
```

```bash
# CLI로:
aws ec2 describe-instances \
  --query 'Reservations[].Instances[].[InstanceId,InstanceType,State.Name,LaunchTime]' \
  --output table
```

### 1-4. Free Tier 만료 대응

`AWS_DEPLOYMENT.md` 21장에 정리된 옵션:

| 옵션 | 비용 | 비고 |
|---|---|---|
| t4g.micro (ARM, 1년 예약) | ~$5.5/월 | Python 호환 OK, 코드 변경 불필요 |
| Lightsail $5 플랜 | $5/월 | 1 vCPU/1GB/40GB SSD/2TB 전송, 고정 IP 무료 포함 |
| t3.micro On-Demand | ~$12/월 | 그냥 유지 (서울) |

> **Lightsail 추천**: investmate 같은 단일 서버 + 단일 SQLite 워크로드에 가장 깔끔하다. EIP 과금도 없다.

---

## 2. EBS (블록 스토리지)

### 2-1. 무료 한도

| 항목 | Free Tier | Free Tier 후 (서울) |
|---|---|---|
| gp3 스토리지 | 30GB | $0.0912/GB·월 → 30GB ≈ **$2.74/월** |
| Snapshot | 1GB | $0.05/GB·월 |
| IOPS (gp3 기본 3000) | 무료 | 3000 초과분만 과금 |

### 2-2. 과금 함정

| 시나리오 | 결과 |
|---|---|
| 인스턴스 Terminate 시 **"Delete on termination"** 체크 안 됨 | EBS 볼륨이 분리된 상태로 남아 계속 과금 |
| 30GB 초과해서 볼륨 확장 | 초과분 과금 (예: 50GB → 20GB × $0.0912 = $1.82/월) |
| AMI 백업을 만들면 자동으로 스냅샷 생성됨 | 스냅샷 GB당 과금 (gp3 스냅샷은 압축되어 실제 용량의 30~70%) |
| **Snapshot Lifecycle Policy** 설정 후 잊어버림 | 매일 스냅샷이 쌓여 수십~수백 GB 누적 |
| 다른 리전으로 스냅샷 복사 | 데이터 전송 + 대상 리전 스냅샷 저장료 이중 과금 |

### 2-3. investmate의 실제 디스크 사용량

`AWS_DEPLOYMENT.md:120`에 따르면 1년 후 ~8GB 예상:

| 항목 | 1년 후 추정 |
|---|---|
| OS + Python + 의존성 | ~3GB |
| `data/investmate.db` (SQLite) | ~2GB (S&P 500 × 1년 데이터 + 피처 + AI 결과) |
| `reports/daily/` | ~500MB (JSON + MD + PDF) |
| `reports/weekly/` | ~50MB |
| `logs/` | ~500MB (rotation 안 하면 더 빠르게 누적) |
| `models/` (LightGBM `.txt`) | ~50MB |
| 여유 | 22GB |

> **주의**: `logs/` 디렉터리에 logrotate가 안 걸려 있으면 1년에 5~10GB 이상 쌓일 수 있다. `find logs/ -name "*.log" -mtime +30 -delete`를 cron에 걸어야 안전하다.

### 2-4. 확인 방법

```bash
# 미사용(Available) EBS 볼륨 찾기
aws ec2 describe-volumes \
  --filters Name=status,Values=available \
  --query 'Volumes[].[VolumeId,Size,CreateTime]' \
  --output table

# 모든 스냅샷 확인 (내 계정 소유)
aws ec2 describe-snapshots --owner-ids self \
  --query 'Snapshots[].[SnapshotId,VolumeSize,StartTime]' \
  --output table
```

---

## 3. Public IPv4 / Elastic IP (가장 큰 함정)

### 3-1. 2024년 2월 정책 변경

**과거**: 인스턴스에 연결되지 않은 EIP만 시간당 과금
**현재**: **모든 퍼블릭 IPv4 주소가 시간당 $0.005 과금** (연결/미연결 무관)

| 케이스 | 월 비용 |
|---|---|
| EC2에 자동 할당된 퍼블릭 IP 1개 | $0.005 × 720 = **$3.6/월** |
| Elastic IP 할당 후 인스턴스 연결 | $3.6/월 |
| Elastic IP 할당 후 미연결 (실수) | $3.6/월 (예전과 동일) |
| EIP 2개 (예: dev/prod 분리) | $7.2/월 |

### 3-2. Free Tier 혜택

**12개월 동안 750시간 무료 퍼블릭 IPv4** 제공. 즉 인스턴스 1대 + EIP 1개 정도는 12개월간 무료.
**12개월 만료 후에는 즉시 과금 시작.**

### 3-3. 과금 함정

| 시나리오 | 결과 |
|---|---|
| EIP를 할당했지만 다른 인스턴스에 옮기느라 잠깐 분리 | 분리된 시간만큼 과금 (예전과 동일) |
| 인스턴스 Terminate 후 EIP만 남음 | **Release 안 하면 영구 과금** |
| 도메인 연결용으로 EIP 할당 후 인스턴스만 Stop | EIP 시간당 과금 + EBS 과금 동시 진행 |
| 복수 인스턴스에 각각 EIP 할당 | EIP 개수만큼 곱하기 |

### 3-4. 확인 및 회수

```bash
# 모든 EIP 확인
aws ec2 describe-addresses \
  --query 'Addresses[].[PublicIp,InstanceId,AllocationId]' \
  --output table

# 미연결 EIP는 즉시 release
aws ec2 release-address --allocation-id eipalloc-xxxxxxxx
```

> **권장**: investmate는 도메인 없이 운영 중이라면 EIP 없이 EC2 자동 할당 IP를 쓰는 것이 가장 저렴하다.
> 단, 인스턴스 재시작 시 IP가 바뀐다 — Telegram 봇/외부 통합에 IP를 하드코딩하지 말 것.

---

## 4. S3 (백업 저장소)

### 4-1. 무료 한도

| 항목 | Free Tier (12개월) | Free Tier 후 (서울) |
|---|---|---|
| Standard 저장 | 5GB | $0.025/GB·월 |
| GET 요청 | 20,000회/월 | $0.0004/1,000회 |
| PUT/POST/LIST 요청 | 2,000회/월 | $0.0045/1,000회 |
| 인터넷 데이터 전송 | 100GB/월 (전 리전 통합) | $0.09/GB |

### 4-2. investmate 백업 패턴

`AWS_DEPLOYMENT.md:1097`의 `backup_db.sh`:

- **빈도**: 매주 일요일 23시 (cron `0 23 * * 0`)
- **크기**: SQLite ~수십 MB → 90일 보관 시 ~13개 × 50MB = **~650MB**
- **요청 수**: PUT 1회/주 + LIST 1회/주 + DELETE 0~3회/주 = 월 ~20회
- **결론**: Free Tier 5GB 한도의 13% 사용. 12개월 내 사실상 $0.

### 4-3. 과금 함정

| 시나리오 | 결과 |
|---|---|
| `backup_db.sh`의 90일 클린업 로직(`aws s3 ls | rm`)이 **권한 부족 / awk 파싱 실패**로 조용히 실패 | 백업이 무한 누적 → 1년 후 ~2.5GB → 5년 후 ~13GB |
| **Versioning 활성화** | 삭제해도 버전이 남아 누적 (기본은 비활성) |
| **MFA Delete 활성화 후 키 분실** | 영구 삭제 불가 |
| `reports/daily/*.md`도 매일 백업 | 매일 PUT × 30일 = 900회 (한도 내) |
| 다른 리전 간 복사 (CRR) | 리전 간 데이터 전송료 발생 |
| **AWS CLI 실수로 큰 파일 업로드** (예: `data/` 전체 sync) | 5GB 초과 즉시 과금 |
| **Glacier로 옮긴 후 복구** | 복구 요청당 과금 + 조기 삭제 페널티 |

### 4-4. 권장 점검

```bash
# 버킷 총 용량 확인
aws s3 ls s3://investmate-backup-xxxxx --recursive --summarize \
  | tail -3

# 버전 관리 상태 확인
aws s3api get-bucket-versioning --bucket investmate-backup-xxxxx

# Lifecycle 정책 확인 (없으면 설정 권장)
aws s3api get-bucket-lifecycle-configuration --bucket investmate-backup-xxxxx
```

**권장 Lifecycle 정책** (cron 클린업의 안전망):

```json
{
  "Rules": [{
    "Id": "delete-old-backups",
    "Status": "Enabled",
    "Filter": {"Prefix": "db-backup/"},
    "Expiration": {"Days": 90}
  }]
}
```

---

## 5. CloudWatch (모니터링)

### 5-1. 무료 한도

| 항목 | Free Tier (영구) | 초과 시 과금 |
|---|---|---|
| 기본 메트릭 (CPU, 네트워크, 디스크 I/O 등) | 무료 (5분 단위) | — |
| 상세 메트릭 (1분 단위) | 유료 | $0.30/메트릭/월 |
| Custom 메트릭 | 10개 무료 | $0.30/메트릭/월 |
| 알람 | 10개 무료 | $0.10/알람/월 |
| Logs 수집 | 5GB/월 | $0.50/GB |
| Logs 저장 | 5GB | $0.03/GB·월 |
| 대시보드 | 3개 무료 | $3/대시보드/월 |

### 5-2. 과금 함정

| 시나리오 | 결과 |
|---|---|
| **CloudWatch Agent 설치 후 모든 메트릭 활성화** | mem/disk/process/network 메트릭 수십 개 발생 → 메트릭당 $0.30 |
| **상세 모니터링 활성화 (1분 단위)** | 인스턴스당 ~$2.10/월 |
| **Logs Insights 쿼리** | 스캔된 데이터 GB당 $0.005 |
| **Logs 보존 기간이 "Never expire"** (기본값) | 무한 누적 → 5GB 초과 |
| **nginx access log를 CloudWatch에 보냄** | 트래픽 많을수록 빠르게 5GB 도달 |
| **CloudWatch Synthetics (canary)** | canary 1회당 $0.0012 |
| **Container Insights** | 컨테이너당 메트릭 과금 |

### 5-3. investmate 권장 설정

investmate는 자체 Telegram 알림이 있으므로 CloudWatch는 **최소 구성**이 적절:

```bash
# Logs 보존 기간을 30일로 (없으면 무한)
aws logs put-retention-policy \
  --log-group-name /aws/ec2/investmate \
  --retention-in-days 30

# 알람: 인스턴스 status check 실패 1개만
aws cloudwatch put-metric-alarm \
  --alarm-name investmate-status-check \
  --metric-name StatusCheckFailed \
  --namespace AWS/EC2 \
  --statistic Maximum \
  --period 300 \
  --threshold 1 \
  --comparison-operator GreaterThanOrEqualToThreshold \
  --evaluation-periods 2
```

> **금지 사항**: CloudWatch Agent로 mem/disk 메트릭을 푸시하지 말 것. `free -h`, `df -h`는 SSH로 충분하다.

---

## 6. 데이터 전송 (Egress)

### 6-1. 무료 한도 (2021년부터 확장)

| 방향 | 무료 한도 |
|---|---|
| **인터넷 → EC2 (인바운드)** | **완전 무료** |
| **EC2 → 인터넷 (아웃바운드)** | 월 100GB (전 AWS 서비스 통합) |
| 같은 AZ 내 EC2 ↔ EC2 | 무료 |
| 다른 AZ 간 EC2 ↔ EC2 | $0.01/GB |
| **다른 리전 간** | $0.02~0.09/GB |

### 6-2. investmate의 트래픽 패턴

| 출처 | 방향 | 추정량 |
|---|---|---|
| yfinance API → EC2 | 인바운드 (무료) | ~500MB/일 (S&P 500 배치 다운로드) |
| Anthropic API → EC2 | 인바운드 (무료) | ~10MB/일 (분석 응답) |
| CNN F&G API → EC2 | 인바운드 (무료) | ~10KB/일 |
| Telegram/Slack 알림 → 인터넷 | 아웃바운드 | ~100KB/일 |
| 웹 대시보드 → 사용자 | 아웃바운드 | **방문자 수에 비례** |
| ECharts JS + CSS + 이미지 (페이지당) | 아웃바운드 | ~2MB/페이지 (캐시 안 될 때) |

### 6-3. 100GB 한도 시나리오 분석

| 시나리오 | 월 트래픽 |
|---|---|
| 본인만 사용 (하루 10페이지 뷰) | ~600MB/월 — 안전 |
| 가족/지인 5명 공유 (하루 50페이지 뷰) | ~3GB/월 — 안전 |
| 블로그/SNS에 공개 후 100명 접속 (하루 1000페이지) | ~60GB/월 — 한도 근접 |
| **DDoS / 봇 스크래핑** | 한도 초과 가능 |
| `/api/sparklines` 대용량 응답 폭증 | 초과 가능 |

### 6-4. 과금 함정

| 시나리오 | 결과 |
|---|---|
| 페이지에 큰 PDF (주간 리포트) 다운로드 링크 + 공개 | PDF 1MB × 1000건 = 1GB |
| **NAT Gateway** 경유 트래픽 | NAT 처리 요금 별도 ($0.045/GB) |
| **VPC Endpoint** 미사용 시 S3 트래픽이 인터넷 경유 | 같은 리전인데 100GB에서 차감됨 |
| **CloudFront** 안 쓰고 직접 서빙 | EC2 egress로 전부 청구 |

### 6-5. 방어책

```nginx
# Nginx에 정적 파일 캐싱 헤더
location ~* \.(js|css|png|jpg|svg)$ {
    expires 30d;
    add_header Cache-Control "public, immutable";
}

# Rate limiting (봇 방어)
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
location /api/ {
    limit_req zone=api burst=20 nodelay;
}
```

---

## 7. 조용히 과금되는 "의외의 함정"

### 7-1. NAT Gateway

- **조건**: VPC를 새로 만들고 Private Subnet을 구성한 경우
- **비용**: 시간당 $0.045 + 데이터 처리 GB당 $0.045
- **월 환산**: 그냥 켜져만 있어도 **$32/월** + 데이터
- **investmate 영향**: 기본 VPC만 쓰면 NAT 없음. **절대 만들지 말 것.**

### 7-2. AWS Config

- **조건**: 콘솔에서 "Set up AWS Config" 한 번 클릭하면 자동 활성화
- **비용**: 리소스당 변경 기록 $0.003 + 평가 $0.001
- **월 환산**: 작은 환경도 ~$3~10/월
- **investmate 영향**: **절대 켜지 말 것.** 컴플라이언스 서비스라 개인 프로젝트엔 불필요

### 7-3. GuardDuty

- **조건**: "Enable GuardDuty" 클릭
- **비용**: VPC Flow Logs 분석 GB당 $1.00, CloudTrail 이벤트당 $4.0/100만
- **월 환산**: 작은 환경도 **$3~5/월**, 30일 무료 평가판이 끝나면 자동 청구
- **investmate 영향**: **절대 켜지 말 것.**

### 7-4. Inspector / Security Hub / Macie

- **공통**: 한 번 활성화하면 자동 과금
- **investmate 영향**: 모두 비활성 유지

### 7-5. Route 53

- **호스팅 영역**: $0.50/월/영역
- **쿼리**: 100만 건당 $0.40
- **investmate 영향**: 도메인 연결 시에만 발생. 도메인 없이 IP로만 운영하면 $0

### 7-6. KMS

- **AWS 관리형 키** (`aws/s3` 등): **무료**
- **고객 관리형 키 (CMK)**: 키당 $1/월 + API 호출당 과금
- **investmate 영향**: 일부러 만들지 않으면 0

### 7-7. Secrets Manager

- **비용**: 시크릿당 $0.40/월 + API 호출 1만 건당 $0.05
- **investmate 영향**: `.env` 파일을 쓰는 한 0. **절대 옮기지 말 것.**

### 7-8. SSM Parameter Store (Advanced Tier)

- **Standard Tier**: 무료 (4KB 이하, 10000개)
- **Advanced Tier**: 파라미터당 $0.05/월
- **investmate 영향**: Standard만 쓰면 0

### 7-9. EBS gp2 → gp3 마이그레이션 후 옛 볼륨 미삭제

- gp2 스냅샷이 남아있으면 gp3 볼륨과 이중 과금

### 7-10. 세금/VAT

- **한국 거주자**: 모든 청구액에 **부가세 10% 추가**
- 즉, $10 청구 시 실제 결제는 $11

---

## 8. 12개월 만료 후 베이스라인 비용 시나리오

### 8-1. 시나리오 A: 현 구성 그대로 유지 (서울)

| 항목 | 월 비용 |
|---|---|
| t2.micro On-Demand (720시간) | $10.37 |
| EBS gp3 30GB | $2.74 |
| EBS gp3 IOPS/Throughput (기본 내) | $0 |
| Public IPv4 1개 | $3.60 |
| S3 백업 (~700MB) | $0.02 |
| 데이터 전송 (5GB egress) | $0 (100GB 무료 내) |
| **소계** | **$16.73** |
| 부가세 10% | $1.67 |
| **합계** | **$18.40 (~₩25,000)** |

### 8-2. 시나리오 B: t4g.micro (ARM) + 1년 예약 + EIP 제거

| 항목 | 월 비용 |
|---|---|
| t4g.micro Reserved 1y All Upfront | ~$3.0 |
| EBS gp3 30GB | $2.74 |
| Public IPv4 (자동 할당, 재시작 시 변경) | $3.60 |
| S3 백업 | $0.02 |
| **소계** | **$9.36** |
| 부가세 10% | $0.94 |
| **합계** | **$10.30 (~₩14,000)** |

### 8-3. 시나리오 C: Lightsail $5 플랜으로 이전

| 항목 | 월 비용 |
|---|---|
| Lightsail $5 (1 vCPU/1GB/40GB SSD/2TB egress, 고정 IP 포함) | $5.00 |
| S3 백업 (별도) | $0.02 |
| **소계** | **$5.02** |
| 부가세 10% | $0.50 |
| **합계** | **$5.52 (~₩7,500)** |

### 8-4. 시나리오 D: 함정에 빠진 경우 (참고)

| 추가 항목 | 월 비용 |
|---|---|
| 시나리오 A 베이스라인 | $18.40 |
| GuardDuty 실수 활성화 | +$5 |
| AWS Config 실수 활성화 | +$5 |
| 미사용 EBS 30GB 1개 + 스냅샷 10GB | +$3.24 |
| 미연결 EIP 1개 | +$3.60 |
| CloudWatch Logs 누적 (10GB) | +$0.30 |
| **합계** | **$35.54+ (~₩48,000)** |

---

## 9. 실전 점검 체크리스트

### 9-1. 매월 확인

- [ ] **Billing → Cost Explorer**에서 최근 30일 청구 내역 분류 확인
- [ ] **Billing → Free Tier** 페이지에서 12개월 만료일 확인
- [ ] **Budgets**에 $1 알림이 활성 상태인지 (`AWS_DEPLOYMENT.md:148` 가이드)
- [ ] EC2 콘솔: Running 인스턴스가 1개 뿐인지
- [ ] EC2 콘솔: Volumes 탭에 "Available" 상태 볼륨이 없는지
- [ ] EC2 콘솔: Snapshots 탭에 오래된 스냅샷이 누적되지 않았는지
- [ ] EC2 콘솔: Elastic IPs 탭에 미연결 EIP가 없는지
- [ ] S3 콘솔: 백업 버킷 용량이 의도한 범위 내인지
- [ ] CloudWatch Logs: Retention이 모두 "30일 이하"로 설정됐는지

### 9-2. 1회성 설정 (한 번만 하면 됨)

- [ ] **Billing Alerts** 활성화 (Billing preferences → Receive Free Tier Usage Alerts)
- [ ] **AWS Budgets** $1, $5, $10 3단계 알림 설정
- [ ] **IAM**: root 계정 사용 금지, MFA 활성화
- [ ] **S3 버킷 Lifecycle**: 90일 만료 정책 추가 (cron 클린업 안전망)
- [ ] **CloudWatch Logs Retention**: 모든 로그 그룹에 30일 정책
- [ ] **Cost Anomaly Detection** 활성화 (무료, 이상 청구 자동 알림)

### 9-3. 절대 하지 말 것

- ❌ AWS Config 활성화
- ❌ GuardDuty 활성화 (30일 무료 후 자동 과금)
- ❌ Inspector / Security Hub / Macie 활성화
- ❌ NAT Gateway 생성
- ❌ Secrets Manager로 시크릿 이전
- ❌ KMS Customer Managed Key 생성
- ❌ EBS 볼륨 30GB 초과 확장
- ❌ 인스턴스 유형을 t2.micro 외로 변경
- ❌ 두 번째 EC2 인스턴스 띄우기
- ❌ Elastic IP 할당 후 방치
- ❌ CloudWatch Agent로 mem/disk 메트릭 푸시
- ❌ 도메인 다수를 Route 53로 이전
- ❌ S3 Versioning 활성화 (필요할 때만)

---

## 10. 비용 모니터링 자동화

### 10-1. AWS CLI로 매월 1일 비용 리포트 받기

```bash
# 지난달 총 비용 조회
aws ce get-cost-and-usage \
  --time-period Start=2026-03-01,End=2026-04-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=DIMENSION,Key=SERVICE \
  --output table
```

### 10-2. investmate cron에 추가하기 (선택)

```bash
# /home/ec2-user/investmate/scripts/check_aws_cost.sh
#!/bin/bash
# 매월 1일 09:00 - 지난달 AWS 비용을 Telegram으로 전송

LAST_MONTH=$(date -d "1 month ago" +%Y-%m-01)
THIS_MONTH=$(date +%Y-%m-01)

COST=$(aws ce get-cost-and-usage \
  --time-period Start=${LAST_MONTH},End=${THIS_MONTH} \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --query 'ResultsByTime[0].Total.UnblendedCost.Amount' \
  --output text)

# Telegram 알림 (investmate 알림 시스템 재사용)
curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
  -d "chat_id=${TELEGRAM_CHAT_ID}" \
  -d "text=💰 AWS 청구 (${LAST_MONTH%-*}월): \$${COST}"
```

```cron
# crontab -e
0 9 1 * * /home/ec2-user/investmate/scripts/check_aws_cost.sh
```

### 10-3. Cost Anomaly Detection (무료, 강력 추천)

```bash
# 1회 설정 — 평소 대비 이상 청구 자동 감지
aws ce create-anomaly-monitor \
  --anomaly-monitor '{
    "MonitorName": "investmate-monitor",
    "MonitorType": "DIMENSIONAL",
    "MonitorDimension": "SERVICE"
  }'
```

콘솔: **Billing → Cost Anomaly Detection → Create monitor** → 알림 이메일 등록

---

## 11. 요약 — investmate 운영 황금률

1. **EC2 인스턴스는 1대만, t2.micro만, x86만**
2. **EBS는 30GB gp3 고정**, 절대 확장 금지
3. **Public IP는 자동 할당만 사용**, EIP는 도메인 연결 필요할 때만
4. **S3 백업은 Lifecycle 정책으로 90일 만료** (cron 클린업의 안전망)
5. **CloudWatch는 status check 알람 1개만**, Logs Retention 30일
6. **Config / GuardDuty / Inspector / NAT Gateway는 절대 활성화 금지**
7. **Budgets $1 알림 + Cost Anomaly Detection 활성화** (둘 다 무료)
8. **매월 1일 Cost Explorer 점검 5분**
9. **12개월 만료 전 Lightsail 이전 검토** (시나리오 C, ~$5.5/월)
10. **모든 청구액에 한국 부가세 10% 추가** 됨을 기억할 것

---

## 12. 참고 자료

| 자료 | 위치 |
|---|---|
| 배포 가이드 | `AWS_DEPLOYMENT.md` |
| Free Tier 한도 (`AWS_DEPLOYMENT.md`) | 4장, line 110-131 |
| Free Tier 만료 후 옵션 (`AWS_DEPLOYMENT.md`) | 21장, line 1421-1459 |
| 백업 스크립트 (`AWS_DEPLOYMENT.md`) | 16장, line 1043-1127 |
| 트러블슈팅 (`AWS_DEPLOYMENT.md`) | 22장, line 1463-1561 |
| AWS 공식 가격 (서울 EC2) | https://aws.amazon.com/ec2/pricing/on-demand/ |
| Public IPv4 가격 정책 변경 공지 | https://aws.amazon.com/blogs/aws/new-aws-public-ipv4-address-charge-public-ip-insights/ |
| Free Tier 100GB 데이터 전송 확장 | https://aws.amazon.com/blogs/aws/aws-free-tier-data-transfer-expansion-100-gb-from-regions-and-1-tb-from-amazon-cloudfront-per-month/ |

---

> **면책**: 이 문서의 가격은 2026-04-11 기준 ap-northeast-2 (서울) On-Demand 가격이다. AWS는 가격을 자주 갱신하므로 실제 청구는 다를 수 있다. 정확한 요금은 AWS Pricing Calculator(https://calculator.aws)에서 확인할 것.
