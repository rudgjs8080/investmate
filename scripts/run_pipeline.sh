#!/bin/bash
# 파이프라인 실행 래퍼 -- 로깅, 시간 측정, 에러 처리
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

DATE="${1:-$(date +%Y-%m-%d)}"
LOG_FILE="logs/${DATE}.log"
mkdir -p logs

echo "=== investmate 파이프라인 시작: ${DATE} ===" | tee -a "$LOG_FILE"
START_TIME=$(date +%s)

# 파이프라인 실행
if uv run python -m src.main run --date "$DATE" 2>&1 | tee -a "$LOG_FILE"; then
  STATUS="SUCCESS"
else
  STATUS="FAILED (exit code: $?)"
fi

END_TIME=$(date +%s)
DURATION=$((END_TIME - START_TIME))
MINS=$((DURATION / 60))
SECS=$((DURATION % 60))

echo "" | tee -a "$LOG_FILE"
echo "=== 파이프라인 종료: ${STATUS} (${MINS}분 ${SECS}초) ===" | tee -a "$LOG_FILE"

# 리포트 존재 확인
if [ -f "reports/${DATE}.md" ]; then
  echo "리포트: reports/${DATE}.md" | tee -a "$LOG_FILE"
fi
if [ -f "reports/${DATE}.json" ]; then
  echo "JSON:   reports/${DATE}.json" | tee -a "$LOG_FILE"
fi
