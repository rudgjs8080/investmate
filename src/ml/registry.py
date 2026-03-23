"""모델 버전 관리."""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

MODEL_DIR = Path("data/models")
REGISTRY_FILE = MODEL_DIR / "model_registry.json"


def get_registry() -> list[dict]:
    """모델 레지스트리를 로드한다."""
    if not REGISTRY_FILE.exists():
        return []

    with open(REGISTRY_FILE, encoding="utf-8") as f:
        return json.load(f)


def get_latest_model_path(model_type: str = "lgbm_return5d") -> Path | None:
    """최신 모델 파일 경로를 반환한다."""
    if not MODEL_DIR.exists():
        return None

    files = sorted(MODEL_DIR.glob(f"{model_type}_*.pkl"), reverse=True)
    return files[0] if files else None
