"""ML 트레이너 테스트."""

from __future__ import annotations

import pandas as pd
import pytest

from src.ml.trainer import MODEL_DIR, train_return_model


def test_train_empty_data():
    """빈 DataFrame이면 None을 반환한다."""
    result = train_return_model(pd.DataFrame())
    assert result is None


def test_train_single_class():
    """타겟 클래스가 하나뿐이면 None을 반환한다."""
    # 모든 return_20d > 0 → 단일 클래스
    data = pd.DataFrame({
        "feat1": [1.0, 2.0, 3.0],
        "feat2": [4.0, 5.0, 6.0],
        "return_20d": [0.05, 0.10, 0.03],  # 모두 양수
        "stock_id": [1, 2, 3],
        "date_id": [20250101, 20250102, 20250103],
    })
    result = train_return_model(data)
    # lightgbm 미설치 시 None, 설치되어 있어도 단일 클래스면 None
    # lightgbm 미설치 → ImportError → None
    # lightgbm 설치 + 단일 클래스 → None
    assert result is None


def test_model_save_path(tmp_path, monkeypatch):
    """모델이 올바른 경로에 저장되는지 확인한다."""
    try:
        import lightgbm  # noqa: F401
    except ImportError:
        pytest.skip("lightgbm 미설치")

    # MODEL_DIR을 tmp_path로 변경
    import src.ml.trainer as trainer_mod
    monkeypatch.setattr(trainer_mod, "MODEL_DIR", tmp_path)

    data = pd.DataFrame({
        "feat1": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "feat2": [4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
        "return_20d": [0.05, -0.03, 0.10, -0.05, 0.02, -0.01],
        "stock_id": [1, 2, 3, 4, 5, 6],
        "date_id": [20250101, 20250102, 20250103, 20250104, 20250105, 20250106],
    })

    result = train_return_model(data)
    assert result is not None
    assert result.exists()
    assert result.suffix == ".pkl"
    assert "lgbm_return_" in result.name
