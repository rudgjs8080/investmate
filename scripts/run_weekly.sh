#!/bin/bash
# ============================================
# Investmate 주간 리포트 배치 실행 스크립트
# cron에서 매주 일요일 자동으로 호출됩니다.
# ============================================
set -euo pipefail

PROJECT_DIR="/home/ec2-user/investmate"
LOG_DIR="${PROJECT_DIR}/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="${LOG_DIR}/${TODAY}_weekly.log"

# 로그 디렉토리 확인
mkdir -p "${LOG_DIR}"

# 가상환경 활성화
source "${PROJECT_DIR}/.venv/bin/activate"
cd "${PROJECT_DIR}"

echo "========================================" >> "${LOG_FILE}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] 주간 배치 시작" >> "${LOG_FILE}"
echo "========================================" >> "${LOG_FILE}"

# 주간 파이프라인 실행
if investmate report weekly >> "${LOG_FILE}" 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 주간 배치 성공" >> "${LOG_FILE}"
else
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 주간 배치 실패 (exit=${EXIT_CODE})" >> "${LOG_FILE}"
fi

# S3 백업 (설정되어있을 때만, 실패해도 무시)
if command -v aws &> /dev/null; then
    BUCKET="investmate-backup-$(whoami)"
    WEEK_ID=$(date -d "yesterday" +%G-W%V)
    aws s3 cp "${PROJECT_DIR}/reports/weekly/${WEEK_ID}.md" \
        "s3://${BUCKET}/reports/weekly/${WEEK_ID}.md" 2>/dev/null || true
    aws s3 cp "${PROJECT_DIR}/reports/weekly/${WEEK_ID}.pdf" \
        "s3://${BUCKET}/reports/weekly/${WEEK_ID}.pdf" 2>/dev/null || true
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] 주간 스크립트 종료" >> "${LOG_FILE}"
