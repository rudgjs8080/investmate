"""ML 스코어링 — 2차 필터 (데이터 충분 시 자동 활성화)."""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import FactDailyRecommendation

logger = logging.getLogger(__name__)

MIN_TRAINING_DAYS = 60  # 3개월 축적 후 활성화
MODEL_DIR = Path("data/models")


class MLScorer:
    """ML 기반 2차 스코어링."""

    def __init__(self) -> None:
        self._model = None

    def is_ready(self, session: Session) -> bool:
        """ML 모델 사용 가능 여부를 확인한다.

        조건: fact_daily_recommendations에 60거래일 이상 데이터 + 학습된 모델 존재.
        """
        from sqlalchemy import distinct

        count = session.execute(
            select(func.count(distinct(FactDailyRecommendation.run_date_id)))
        ).scalar_one()

        if count < MIN_TRAINING_DAYS:
            return False

        if not MODEL_DIR.exists():
            return False

        model_files = list(MODEL_DIR.glob("lgbm_*.pkl"))
        return len(model_files) > 0

    def rank(
        self, session: Session, candidates: list[dict],
    ) -> list[dict]:
        """ML 모델로 후보를 재랭킹한다.

        데이터 부족 시 입력을 그대로 반환 (폴백).
        """
        if not self.is_ready(session):
            logger.info("ML 모델 미준비, 규칙 기반 폴백")
            return candidates

        # 최신 모델 로드
        model_files = sorted(MODEL_DIR.glob("lgbm_*.pkl"), reverse=True)
        if not model_files:
            return candidates

        try:
            with open(model_files[0], "rb") as f:
                model_data = pickle.load(f)  # noqa: S301
            model = model_data["model"]
            feature_cols = model_data["feature_cols"]
        except Exception as e:
            logger.warning("모델 로드 실패: %s", e)
            return candidates

        # 각 후보에 ML 확률 보정 적용
        from datetime import date

        import pandas as pd

        from src.db.helpers import date_to_id
        from src.ml.features import build_features_for_stock

        today_id = date_to_id(date.today())

        for cand in candidates:
            try:
                features = build_features_for_stock(
                    session, cand["stock_id"], today_id,
                )
                X = pd.DataFrame(
                    [{col: features.get(col, 0) for col in feature_cols}]
                )
                prob = model.predict(X)[0]

                rule_score = cand["total_score"]
                ml_score = prob * 10  # 0-1 probability -> 0-10 scale
                adjusted = rule_score * 0.7 + ml_score * 0.3  # 30% ML weight
                cand["total_score"] = round(adjusted, 4)
                cand["ml_probability"] = round(float(prob), 4)
            except Exception as e:
                logger.debug(
                    "ML 스코어링 실패 [%s]: %s", cand.get("ticker"), e,
                )

        # Re-sort by adjusted score
        candidates.sort(key=lambda x: x["total_score"], reverse=True)
        logger.info("ML 스코어링 적용: %d 종목", len(candidates))
        return candidates

    def get_status(self, session: Session) -> dict:
        """ML 모델 상태를 반환한다."""
        from sqlalchemy import distinct

        data_days = session.execute(
            select(func.count(distinct(FactDailyRecommendation.run_date_id)))
        ).scalar_one()

        model_files = (
            list(MODEL_DIR.glob("lgbm_*.pkl")) if MODEL_DIR.exists() else []
        )

        return {
            "data_days": data_days,
            "min_required": MIN_TRAINING_DAYS,
            "is_ready": data_days >= MIN_TRAINING_DAYS
            and len(model_files) > 0,
            "models_count": len(model_files),
            "status": (
                "활성"
                if self.is_ready(session)
                else f"데이터 축적 중 ({data_days}/{MIN_TRAINING_DAYS}일)"
            ),
        }
