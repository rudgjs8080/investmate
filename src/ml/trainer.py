"""ML 모델 학습 모듈 — 시간 기반 split + early stopping + 네이티브 저장."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

MODEL_DIR = Path("data/models")

# AUC 임계값: 이 이하면 모델을 저장하지 않음 (나쁜 모델로 규칙 기반 점수 오염 방지)
MIN_AUC_THRESHOLD = 0.55


def train_return_model(
    training_data: pd.DataFrame,
    test_ratio: float = 0.2,
) -> Path | None:
    """20일 수익률 예측 모델을 학습한다.

    시간순으로 정렬하여 뒤쪽 test_ratio 비율을 검증 셋으로 사용한다.
    early stopping으로 과적합을 방지하고, LightGBM 네이티브 형식으로 저장한다.

    Returns:
        모델 파일 경로, 실패 시 None.
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

    # 시간순 정렬 (금융 데이터는 무작위 셔플 금지 — 미래 데이터 누수 방지)
    df = training_data.sort_values("date_id").reset_index(drop=True)

    X = df[feature_cols]  # NaN 유지 — LightGBM 네이티브 처리
    y = (df["return_20d"] > 0).astype(int)

    if len(y.unique()) < 2:
        logger.warning("타겟 클래스가 하나뿐: 학습 불가")
        return None

    # 시간 기반 train/test split
    split_idx = int(len(df) * (1 - test_ratio))
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    logger.info("학습 데이터: train=%d, test=%d (%.0f%%)",
                len(X_train), len(X_test), test_ratio * 100)

    train_set = lgb.Dataset(X_train, y_train, feature_name=feature_cols)
    valid_set = lgb.Dataset(X_test, y_test, reference=train_set)

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

    model = lgb.train(
        params,
        train_set,
        num_boost_round=500,
        valid_sets=[valid_set],
        callbacks=[lgb.early_stopping(20), lgb.log_evaluation(50)],
    )

    # 검증 AUC
    try:
        from sklearn.metrics import roc_auc_score
        y_pred = model.predict(X_test)
        test_auc = roc_auc_score(y_test, y_pred)
    except Exception:
        test_auc = model.best_score.get("valid_0", {}).get("auc", 0)

    train_auc = model.best_score.get("training", {}).get("auc", 0)

    logger.info("학습 완료: train_auc=%.4f, test_auc=%.4f, best_iteration=%d",
                train_auc, test_auc, model.best_iteration)

    # AUC 임계값 미달 시 저장 안 함
    if test_auc < MIN_AUC_THRESHOLD:
        logger.warning(
            "test AUC(%.4f) < 임계값(%.2f) — 모델 저장 건너뜀 (나쁜 모델 방지)",
            test_auc, MIN_AUC_THRESHOLD,
        )
        return None

    # 피처 중요도 추출
    importance = dict(zip(feature_cols, model.feature_importance(importance_type="gain")))
    top_features = sorted(importance.items(), key=lambda x: -x[1])[:10]
    logger.info("피처 중요도 TOP 10: %s",
                ", ".join(f"{k}={v:.0f}" for k, v in top_features))

    # 저장 — LightGBM 네이티브 형식 + JSON 메타데이터
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    model_path = MODEL_DIR / f"lgbm_return_{ts}.txt"
    meta_path = MODEL_DIR / f"lgbm_return_{ts}.json"

    model.save_model(str(model_path))
    metadata = {
        "feature_cols": feature_cols,
        "train_auc": round(train_auc, 4),
        "test_auc": round(test_auc, 4),
        "best_iteration": model.best_iteration,
        "train_samples": len(X_train),
        "test_samples": len(X_test),
        "feature_importance": {k: round(v, 2) for k, v in importance.items()},
        "params": params,
        "trained_at": ts,
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    logger.info("모델 저장: %s (test AUC: %.4f)", model_path, test_auc)
    return model_path
