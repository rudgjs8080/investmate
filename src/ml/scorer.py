"""ML 스코어링 — 2차 필터 (데이터 충분 시 자동 활성화)."""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import FactDailyRecommendation
from src.ml.features import build_features_for_stock

logger = logging.getLogger(__name__)

MIN_TRAINING_DAYS = 60
MODEL_DIR = Path("data/models")

# 블렌딩 비율 기본값 (rule 70% + ML 30%)
DEFAULT_ML_BLEND_RATIO = 0.3


class MLScorer:
    """ML 기반 2차 스코어링.

    LightGBM 모델을 캐싱하여 반복 로딩을 방지한다.
    """

    def __init__(self) -> None:
        self._cached_model = None
        self._cached_features: list[str] | None = None
        self._cached_path: Path | None = None

    def is_ready(self, session: Session) -> bool:
        """ML 모델 사용 가능 여부를 확인한다."""
        count = session.execute(
            select(func.count(distinct(FactDailyRecommendation.run_date_id)))
        ).scalar_one()

        if count < MIN_TRAINING_DAYS:
            return False

        if not MODEL_DIR.exists():
            return False

        # 네이티브(.txt) 또는 레거시(.pkl) 모델 탐색
        model_files = list(MODEL_DIR.glob("lgbm_*.txt")) + list(MODEL_DIR.glob("lgbm_*.pkl"))
        return len(model_files) > 0

    def _load_model(self):
        """최신 모델을 로드한다 (캐싱 적용)."""
        # 네이티브 형식 우선, 레거시 pkl 폴백
        txt_files = sorted(MODEL_DIR.glob("lgbm_*.txt"), reverse=True)
        pkl_files = sorted(MODEL_DIR.glob("lgbm_*.pkl"), reverse=True)

        if txt_files:
            model_path = txt_files[0]
            meta_path = model_path.with_suffix(".json")

            # 캐시 히트: 동일 모델이면 재로딩 불필요
            if self._cached_path == model_path and self._cached_model is not None:
                return self._cached_model, self._cached_features

            try:
                import lightgbm as lgb
                model = lgb.Booster(model_file=str(model_path))
                if meta_path.exists():
                    with open(meta_path, encoding="utf-8") as f:
                        metadata = json.load(f)
                    feature_cols = metadata["feature_cols"]
                else:
                    feature_cols = model.feature_name()

                self._cached_model = model
                self._cached_features = feature_cols
                self._cached_path = model_path
                return model, feature_cols
            except Exception as e:
                logger.warning("네이티브 모델 로드 실패: %s", e)

        # 레거시 pkl 폴백
        if pkl_files:
            try:
                import pickle
                with open(pkl_files[0], "rb") as f:
                    model_data = pickle.load(f)  # noqa: S301
                return model_data["model"], model_data["feature_cols"]
            except Exception as e:
                logger.warning("레거시 모델 로드 실패: %s", e)

        return None, None

    def rank(
        self, session: Session, candidates: list[dict],
    ) -> list[dict]:
        """ML 모델로 후보를 재랭킹한다."""
        if not self.is_ready(session):
            logger.info("ML 모델 미준비, 규칙 기반 폴백")
            return candidates

        model, feature_cols = self._load_model()
        if model is None or feature_cols is None:
            return candidates

        today_id = date_to_id(date.today())
        ml_ratio = DEFAULT_ML_BLEND_RATIO
        rule_ratio = 1.0 - ml_ratio

        for cand in candidates:
            try:
                features = build_features_for_stock(
                    session, cand["stock_id"], today_id,
                )
                X = pd.DataFrame(
                    [{col: features.get(col) for col in feature_cols}]
                )
                prob = model.predict(X)[0]

                rule_score = cand["total_score"]
                ml_score = prob * 10  # 0~1 확률 → 0~10 스케일
                adjusted = rule_score * rule_ratio + ml_score * ml_ratio
                cand["total_score"] = round(adjusted, 4)
                cand["ml_probability"] = round(float(prob), 4)
            except (KeyError, ValueError) as e:
                logger.debug("ML 스코어링 실패 [%s]: %s", cand.get("ticker"), e)
            except Exception as e:
                logger.warning("ML 스코어링 예외 [%s]: %s", cand.get("ticker"), e)

        candidates.sort(key=lambda x: x["total_score"], reverse=True)
        logger.info("ML 스코어링 적용: %d 종목 (blend=%.0f%%)", len(candidates), ml_ratio * 100)
        return candidates

    def get_status(self, session: Session) -> dict:
        """ML 모델 상태를 반환한다."""
        data_days = session.execute(
            select(func.count(distinct(FactDailyRecommendation.run_date_id)))
        ).scalar_one()

        model_files = (
            list(MODEL_DIR.glob("lgbm_*.txt")) + list(MODEL_DIR.glob("lgbm_*.pkl"))
            if MODEL_DIR.exists() else []
        )

        return {
            "data_days": data_days,
            "min_required": MIN_TRAINING_DAYS,
            "is_ready": data_days >= MIN_TRAINING_DAYS and len(model_files) > 0,
            "models_count": len(model_files),
            "status": (
                "활성"
                if self.is_ready(session)
                else f"데이터 축적 중 ({data_days}/{MIN_TRAINING_DAYS}일)"
            ),
        }
