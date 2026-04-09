#!/usr/bin/env bash
# Deep Dive daily pipeline cron wrapper
# Crontab:
#   0 7 * * 1-5 /path/to/investmate/scripts/run_deepdive.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

# 가상환경 활성화
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
elif [ -f .venv/Scripts/activate ]; then
    source .venv/Scripts/activate
fi

# 로그 디렉토리
mkdir -p logs

# 실행
python -m src.main deepdive run --date "$(date +%Y-%m-%d)" \
    2>&1 | tee -a "logs/deepdive_$(date +%Y%m%d).log"
