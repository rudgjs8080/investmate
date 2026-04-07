.PHONY: test coverage lint run improve clean report help

help: ## 도움말 표시
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

test: ## pytest 실행
	uv run pytest tests/ -x -q

coverage: ## 테스트 커버리지 리포트
	uv run pytest tests/ --cov=src --cov-report=term-missing

lint: ## ruff 린트 실행
	uv run ruff check src/ tests/ || echo "[참고] ruff 미설치시: uv pip install ruff"

run: ## investmate run 파이프라인 실행
	uv run python -m src.main run

run-step5: ## 리포트만 재생성 (step 5)
	uv run python -m src.main run --step 5

report: ## 최신 리포트 출력
	uv run python -m src.main report latest

weekly: ## 주간 리포트 생성
	uv run python -m src.main report weekly

improve: ## 자율 반복 개선 루프 (기본 3회)
	bash scripts/auto_improve.sh 3 25

improve-1: ## 자율 개선 1회 실행
	bash scripts/auto_improve.sh 1 25

improve-coverage: ## 커버리지 집중 개선
	bash scripts/auto_improve.sh 3 25 scripts/improve_prompts/coverage.txt

improve-quality: ## 코드 품질 집중 개선
	bash scripts/auto_improve.sh 3 25 scripts/improve_prompts/quality.txt

pipeline: ## 파이프라인 래퍼 (로깅 포함)
	bash scripts/run_pipeline.sh

history: ## 추천 이력 조회
	uv run python -m src.main history recommendations

stock: ## 개별 종목 조회 (사용법: make stock TICKER=AAPL)
	uv run python -m src.main stock $(TICKER)

db-status: ## DB 상태 확인
	uv run python -m src.main db status

clean: ## 캐시/임시 파일 정리
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .coverage htmlcov 2>/dev/null || true
	@echo "정리 완료"
