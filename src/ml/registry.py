"""모델 버전 관리."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_DIR = Path("data/models")


def get_latest_model_path(model_type: str = "lgbm_return") -> Path | None:
    """최신 모델 파일 경로를 반환한다.

    네이티브(.txt) 형식 우선, 레거시(.pkl) 폴백.
    """
    if not MODEL_DIR.exists():
        return None

    # 네이티브 형식 우선
    txt_files = sorted(MODEL_DIR.glob(f"{model_type}_*.txt"), reverse=True)
    if txt_files:
        return txt_files[0]

    # 레거시 pkl 폴백
    pkl_files = sorted(MODEL_DIR.glob(f"{model_type}_*.pkl"), reverse=True)
    return pkl_files[0] if pkl_files else None


def list_models(model_type: str = "lgbm_return") -> list[dict]:
    """저장된 모델 목록과 메타데이터를 반환한다."""
    if not MODEL_DIR.exists():
        return []

    models = []
    for model_path in sorted(MODEL_DIR.glob(f"{model_type}_*.txt"), reverse=True):
        meta_path = model_path.with_suffix(".json")
        metadata = {}
        if meta_path.exists():
            try:
                with open(meta_path, encoding="utf-8") as f:
                    metadata = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        models.append({
            "path": str(model_path),
            "name": model_path.stem,
            "test_auc": metadata.get("test_auc"),
            "train_auc": metadata.get("train_auc"),
            "trained_at": metadata.get("trained_at"),
            "train_samples": metadata.get("train_samples"),
        })
    return models
