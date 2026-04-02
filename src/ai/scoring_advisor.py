"""AI 적응형 스코어링 어드바이저 — 피드백 기반 가중치 자동 최적화 (Phase 5)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FactAIFeedback, FactDailyRecommendation

logger = logging.getLogger(__name__)

MIN_ADAPTIVE_SAMPLES = 30


@dataclass(frozen=True)
class AdaptiveWeights:
    """적응형 스코어링 가중치."""

    technical: float
    fundamental: float
    smart_money: float
    external: float
    momentum: float
    sample_size: int
    correlation_quality: float  # 상관관계 신뢰도 (0~1)

    def to_dict(self) -> dict[str, float]:
        """가중치 딕셔너리로 변환."""
        return {
            "technical": self.technical,
            "fundamental": self.fundamental,
            "smart_money": self.smart_money,
            "external": self.external,
            "momentum": self.momentum,
        }


def compute_adaptive_weights(
    session: Session,
    regime: str = "range",
    lookback_days: int = 60,
    min_samples: int = MIN_ADAPTIVE_SAMPLES,
) -> AdaptiveWeights | None:
    """피드백 데이터에서 각 스코어링 차원과 실제 수익률 간 상관관계를 계산하여
    적응형 가중치를 산출한다.

    각 차원(기술/기본/수급/외부/모멘텀)의 점수와 20일 수익률 간의 피어슨 상관을
    계산하고, 양의 상관이 강한 차원에 더 높은 가중치를 부여한다.

    Args:
        session: DB 세션.
        regime: 현재 시장 체제.
        lookback_days: 조회할 과거 일수.
        min_samples: 최소 샘플 수.

    Returns:
        AdaptiveWeights or None (데이터 부족 시).
    """
    from src.db.helpers import date_to_id
    from datetime import date, timedelta

    cutoff_date = date.today() - timedelta(days=lookback_days + 35)
    cutoff_id = date_to_id(cutoff_date)

    # 추천 데이터 + 20일 수익률 조회
    recs = session.execute(
        select(FactDailyRecommendation)
        .where(
            FactDailyRecommendation.return_20d.isnot(None),
            FactDailyRecommendation.run_date_id >= cutoff_id,
        )
    ).scalars().all()

    if len(recs) < min_samples:
        logger.info("적응형 가중치: 데이터 부족 (%d < %d)", len(recs), min_samples)
        return None

    # 각 차원의 점수와 수익률 수집
    dimensions = ["technical_score", "fundamental_score", "smart_money_score",
                   "external_score", "momentum_score"]
    dim_labels = ["technical", "fundamental", "smart_money", "external", "momentum"]

    scores: dict[str, list[float]] = {d: [] for d in dim_labels}
    returns: list[float] = []

    for rec in recs:
        ret = float(rec.return_20d)
        returns.append(ret)
        for dim, label in zip(dimensions, dim_labels):
            val = getattr(rec, dim, None)
            scores[label].append(float(val) if val is not None else 5.0)

    # 피어슨 상관관계 계산
    correlations: dict[str, float] = {}
    for label in dim_labels:
        corr = _pearson_correlation(scores[label], returns)
        correlations[label] = corr

    # 상관관계 기반 가중치 산출
    # 양의 상관만 반영, 음의 상관은 0으로 처리
    positive_corrs = {k: max(0.01, v) for k, v in correlations.items()}
    total = sum(positive_corrs.values())

    if total <= 0:
        return None

    weights = {k: round(v / total, 3) for k, v in positive_corrs.items()}

    # 상관관계 품질: 평균 |corr| (0~1)
    avg_abs_corr = sum(abs(c) for c in correlations.values()) / len(correlations)

    logger.info(
        "적응형 가중치 계산 완료: %s (samples=%d, quality=%.2f)",
        weights, len(recs), avg_abs_corr,
    )

    return AdaptiveWeights(
        technical=weights["technical"],
        fundamental=weights["fundamental"],
        smart_money=weights["smart_money"],
        external=weights["external"],
        momentum=weights["momentum"],
        sample_size=len(recs),
        correlation_quality=round(avg_abs_corr, 3),
    )


def _pearson_correlation(x: list[float], y: list[float]) -> float:
    """피어슨 상관계수를 계산한다."""
    n = len(x)
    if n < 3 or n != len(y):
        return 0.0

    mean_x = sum(x) / n
    mean_y = sum(y) / n

    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    var_x = sum((xi - mean_x) ** 2 for xi in x)
    var_y = sum((yi - mean_y) ** 2 for yi in y)

    denom = (var_x * var_y) ** 0.5
    if denom < 1e-10:
        return 0.0

    return round(cov / denom, 4)


def compute_feature_importance(model_path: str | None = None) -> dict[str, float]:
    """학습된 LightGBM 모델에서 피처 중요도를 추출한다.

    Args:
        model_path: 모델 pkl 파일 경로. None이면 최신 모델 자동 탐색.

    Returns:
        {feature_name: importance} 딕셔너리. 모델 없으면 빈 딕셔너리.
    """
    import pickle
    from pathlib import Path

    if model_path is None:
        model_dir = Path("data/models")
        if not model_dir.exists():
            return {}
        pkl_files = sorted(model_dir.glob("lgbm_binary_*.pkl"), reverse=True)
        if not pkl_files:
            return {}
        model_path = str(pkl_files[0])

    try:
        with open(model_path, "rb") as f:
            model_data = pickle.load(f)

        model = model_data.get("model") if isinstance(model_data, dict) else model_data
        if model is None:
            return {}

        importances = model.feature_importance(importance_type="gain")
        feature_names = model_data.get("feature_names", []) if isinstance(model_data, dict) else []

        if not feature_names:
            feature_names = [f"f{i}" for i in range(len(importances))]

        total = sum(importances) if sum(importances) > 0 else 1
        return {
            name: round(imp / total, 4)
            for name, imp in zip(feature_names, importances)
        }
    except Exception as e:
        logger.debug("피처 중요도 추출 실패: %s", e)
        return {}
