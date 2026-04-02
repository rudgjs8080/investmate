"""ML 모델 드리프트 감지 시스템 (Phase 7)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import FactDailyRecommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DriftReport:
    """모델 드리프트 검사 결과."""

    is_drifted: bool
    accuracy_current: float  # 최근 윈도우 정확도 (%)
    accuracy_baseline: float  # 학습기 기준 정확도 (%)
    accuracy_delta: float  # current - baseline
    sample_count_current: int
    sample_count_baseline: int
    recommended_action: str  # "none" | "monitor" | "retrain"


def _win_rate(returns: list[float]) -> float:
    """양수 수익률 비율을 계산한다."""
    if not returns:
        return 0.0
    wins = sum(1 for r in returns if float(r) > 0)
    return wins / len(returns)


def detect_drift(
    session: Session,
    window_days: int = 20,
    baseline_days: int = 60,
    threshold: float = 0.10,
    *,
    reference_date: date | None = None,
) -> DriftReport:
    """최근 모델 정확도를 기준 기간과 비교하여 드리프트를 감지한다.

    Uses FactDailyRecommendation return_20d to compute win rates for
    recent window vs baseline period.

    Args:
        session: SQLAlchemy DB 세션.
        window_days: 최근 비교 기간 (거래일).
        baseline_days: 기준 기간 (거래일).
        threshold: 정확도 하락 임계값 (0.10 = 10%p).
        reference_date: 기준일 (기본: 오늘).

    Returns:
        DriftReport with drift detection results.
    """
    today = reference_date or date.today()

    # Calendar-day approximation: multiply trading days by ~1.5
    window_cutoff = date_to_id(today - timedelta(days=int(window_days * 1.5)))
    baseline_cutoff = date_to_id(today - timedelta(days=int(baseline_days * 1.5)))
    today_id = date_to_id(today)

    # Recent window: [window_cutoff, today]
    recent_recs = list(
        session.execute(
            select(FactDailyRecommendation.return_20d).where(
                FactDailyRecommendation.return_20d.isnot(None),
                FactDailyRecommendation.run_date_id >= window_cutoff,
                FactDailyRecommendation.run_date_id <= today_id,
            )
        )
        .scalars()
        .all()
    )

    # Baseline: [baseline_cutoff, window_cutoff)
    baseline_recs = list(
        session.execute(
            select(FactDailyRecommendation.return_20d).where(
                FactDailyRecommendation.return_20d.isnot(None),
                FactDailyRecommendation.run_date_id >= baseline_cutoff,
                FactDailyRecommendation.run_date_id < window_cutoff,
            )
        )
        .scalars()
        .all()
    )

    acc_current = _win_rate(recent_recs)
    acc_baseline = _win_rate(baseline_recs)
    delta = acc_current - acc_baseline

    # Need minimum sample size for meaningful comparison
    min_samples = 5
    is_drifted = delta < -threshold and len(recent_recs) >= min_samples

    if is_drifted:
        action = "retrain"
    elif delta < -threshold / 2 and len(recent_recs) >= min_samples:
        action = "monitor"
    else:
        action = "none"

    logger.info(
        "드리프트 감지: current=%.1f%% baseline=%.1f%% delta=%.1f%% → %s",
        acc_current * 100,
        acc_baseline * 100,
        delta * 100,
        action,
    )

    return DriftReport(
        is_drifted=is_drifted,
        accuracy_current=round(acc_current * 100, 1),
        accuracy_baseline=round(acc_baseline * 100, 1),
        accuracy_delta=round(delta * 100, 1),
        sample_count_current=len(recent_recs),
        sample_count_baseline=len(baseline_recs),
        recommended_action=action,
    )
