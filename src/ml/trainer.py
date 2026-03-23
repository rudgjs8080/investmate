"""ML 모델 학습 모듈."""

from __future__ import annotations

import logging
import pickle
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

MODEL_DIR = Path("data/models")


def train_return_model(training_data: pd.DataFrame) -> Path | None:
    """20일 수익률 예측 모델을 학습한다.

    Returns:
        모델 파일 경로, 실패 시 None
    """
    try:
        import lightgbm as lgb
    except ImportError:
        logger.warning("lightgbm 미설치, ML 학습 불가")
        return None

    if training_data.empty:
        logger.warning("학습 데이터 없음")
        return None

    # 피처/타겟 분리
    drop_cols = ["return_20d", "stock_id", "date_id"]
    feature_cols = [c for c in training_data.columns if c not in drop_cols]

    X = training_data[feature_cols].fillna(0)
    y = (training_data["return_20d"] > 0).astype(int)

    if len(y.unique()) < 2:
        logger.warning("타겟 클래스가 하나뿐: 학습 불가")
        return None

    train_set = lgb.Dataset(X, y, feature_name=feature_cols)
    params = {
        "objective": "binary",
        "metric": "auc",
        "num_leaves": 31,
        "learning_rate": 0.05,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 5,
        "verbose": -1,
        "seed": 42,
    }

    model = lgb.train(params, train_set, num_boost_round=100)

    # 저장
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = MODEL_DIR / f"lgbm_return_{ts}.pkl"

    with open(model_path, "wb") as f:
        pickle.dump({"model": model, "feature_cols": feature_cols}, f)

    best_score = model.best_score.get("training", {}).get("auc", 0)
    logger.info("모델 저장: %s (AUC: %.4f)", model_path, best_score)
    return model_path
