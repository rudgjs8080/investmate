"""AI API 비용 추적 — 일별 토큰 사용량 및 비용 집계.

모든 AI API 호출의 토큰 사용량과 비용을 기록하여
일별·모델별·용도별로 집계한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

logger = logging.getLogger(__name__)

# 모델별 가격 (USD per 1M tokens) — 2025-05 기준
MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
}
DEFAULT_PRICING = {"input": 3.0, "output": 15.0}


@dataclass(frozen=True)
class APICallRecord:
    """단일 API 호출 기록."""

    timestamp: datetime
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    purpose: str  # "debate_r1_bull", "debate_r3_synth", "retrospective", "chat" 등


class CostTracker:
    """일별 AI API 비용 추적기.

    싱글톤 패턴 없이, 파이프라인 실행당 하나의 인스턴스를 생성하여 사용한다.
    """

    def __init__(self) -> None:
        self._records: list[APICallRecord] = []

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        purpose: str,
    ) -> APICallRecord:
        """API 호출 비용을 기록한다."""
        pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
        cost = (
            input_tokens * pricing["input"] / 1_000_000
            + output_tokens * pricing["output"] / 1_000_000
        )

        record = APICallRecord(
            timestamp=datetime.now(),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            purpose=purpose,
        )
        self._records.append(record)

        logger.info(
            "AI비용: $%.4f (%s, in=%d, out=%d, %s)",
            cost, model.split("-")[1] if "-" in model else model,
            input_tokens, output_tokens, purpose,
        )
        return record

    def daily_summary(self) -> dict:
        """일별 비용 요약을 반환한다."""
        total_cost = sum(r.cost_usd for r in self._records)
        total_input = sum(r.input_tokens for r in self._records)
        total_output = sum(r.output_tokens for r in self._records)

        by_model: dict[str, float] = {}
        by_purpose: dict[str, float] = {}
        for r in self._records:
            by_model[r.model] = by_model.get(r.model, 0) + r.cost_usd
            by_purpose[r.purpose] = by_purpose.get(r.purpose, 0) + r.cost_usd

        return {
            "date": date.today().isoformat(),
            "total_cost_usd": round(total_cost, 4),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "call_count": len(self._records),
            "by_model": {k: round(v, 4) for k, v in by_model.items()},
            "by_purpose": {k: round(v, 4) for k, v in by_purpose.items()},
        }

    def check_budget(self, daily_limit: float = 5.0) -> bool:
        """일일 예산 초과 여부를 확인한다.

        Returns:
            True면 예산 내, False면 초과.
        """
        total = sum(r.cost_usd for r in self._records)
        if total > daily_limit:
            logger.warning(
                "일일 AI 예산 초과: $%.2f / $%.2f (%.0f%%)",
                total, daily_limit, total / daily_limit * 100,
            )
            return False
        return True

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self._records)

    @property
    def call_count(self) -> int:
        return len(self._records)
