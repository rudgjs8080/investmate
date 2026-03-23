#!/bin/bash
# 자율 반복 개선 루프 -- Claude Code가 분석->수정->테스트를 자동 반복
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

# ── 설정 ──
MAX_ITERATIONS="${1:-3}"
TURNS_PER_ITERATION="${2:-25}"
PROMPT_FILE="${3:-scripts/improve_prompts/self_judge.txt}"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
SESSION_ID="auto-improve-${TIMESTAMP}"
LOG_DIR="results/auto_improve/${TIMESTAMP}"
mkdir -p "$LOG_DIR"

echo "=== 자율 개선 루프 시작: ${TIMESTAMP} ==="
echo "세션 ID: ${SESSION_ID}"
echo "최대 반복: ${MAX_ITERATIONS}회"
echo "턴/반복: ${TURNS_PER_ITERATION}"
echo "프롬프트: ${PROMPT_FILE}"
echo "결과 디렉토리: ${LOG_DIR}"
echo ""

# ── 0단계: 현재 상태 스냅샷 ──
echo "[0/${MAX_ITERATIONS}] 현재 상태 캡처..."
uv run pytest tests/ --cov=src --cov-report=term-missing \
  > "$LOG_DIR/baseline_test.txt" 2>&1 || true

# 베이스라인 요약 추출
BASELINE_TESTS=$(grep -oP '\d+ passed' "$LOG_DIR/baseline_test.txt" | head -1 || echo "?")
BASELINE_COV=$(grep -oP 'TOTAL.*?(\d+)%' "$LOG_DIR/baseline_test.txt" | grep -oP '\d+%' || echo "?")
echo "  베이스라인: ${BASELINE_TESTS}, 커버리지 ${BASELINE_COV}"
echo ""

# ── 반복 개선 루프 ──
for i in $(seq 1 "$MAX_ITERATIONS"); do
  echo "=== 반복 ${i}/${MAX_ITERATIONS} ==="

  # 프롬프트 로드
  if [ -f "$PROMPT_FILE" ]; then
    PROMPT=$(cat "$PROMPT_FILE")
  else
    PROMPT="프로젝트를 분석하고, 테스트 커버리지가 가장 낮은 모듈에 테스트를 추가해. 수정 후 pytest를 실행해서 통과를 확인해."
  fi

  # 반복 번호를 프롬프트에 주입
  PROMPT="[반복 ${i}/${MAX_ITERATIONS}] ${PROMPT}"

  # Claude Code 실행
  echo "  Claude Code 실행 중..."
  claude -p "$PROMPT" \
    --session-id "$SESSION_ID" \
    --allowedTools "Read,Write,Edit,Bash" \
    --max-turns "$TURNS_PER_ITERATION" \
    > "$LOG_DIR/iteration_${i}_output.txt" 2>&1 || true

  echo "  완료. 출력: $LOG_DIR/iteration_${i}_output.txt"

  # ── 안전 검증 ──

  # 1) 테스트 실행
  echo "  테스트 검증 중..."
  if uv run pytest tests/ -q --tb=short > "$LOG_DIR/iteration_${i}_test.txt" 2>&1; then
    ITER_TESTS=$(grep -oP '\d+ passed' "$LOG_DIR/iteration_${i}_test.txt" | head -1 || echo "?")
    echo "  [OK] 테스트 통과: ${ITER_TESTS}"
  else
    echo "  [FAIL] 테스트 실패 -- 롤백 중..."
    git checkout . 2>/dev/null || true
    echo "  롤백 완료. 다음 반복으로 넘어감."
    continue
  fi

  # 2) 변경 범위 검증
  CHANGED_FILES=$(git diff --name-only 2>/dev/null | wc -l || echo "0")
  if [ "$CHANGED_FILES" -gt 10 ]; then
    echo "  [WARN] ${CHANGED_FILES}개 파일 변경됨 -- 범위 초과 경고"
  fi

  echo ""
done

# ── 최종 보고 ──
echo ""
echo "=== 최종 상태 ==="
uv run pytest tests/ --cov=src --cov-report=term-missing 2>&1 | tee "$LOG_DIR/final_test.txt"

FINAL_TESTS=$(grep -oP '\d+ passed' "$LOG_DIR/final_test.txt" | head -1 || echo "?")
FINAL_COV=$(grep -oP 'TOTAL.*?(\d+)%' "$LOG_DIR/final_test.txt" | grep -oP '\d+%' || echo "?")

echo ""
echo "=== 요약 ==="
echo "베이스라인: ${BASELINE_TESTS}, 커버리지 ${BASELINE_COV}"
echo "최종:       ${FINAL_TESTS}, 커버리지 ${FINAL_COV}"
echo "결과 저장:  ${LOG_DIR}/"
echo "=== 자율 개선 루프 완료 ==="
