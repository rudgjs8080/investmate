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
    data = pd.DataFrame({
        "feat1": [1.0, 2.0, 3.0],
        "feat2": [4.0, 5.0, 6.0],
        "return_20d": [0.05, 0.10, 0.03],  # 모두 양수
        "stock_id": [1, 2, 3],
        "date_id": [20250101, 20250102, 20250103],
    })
    result = train_return_model(data)
    assert result is None


def test_model_save_path(tmp_path, monkeypatch):
    """모델이 올바른 경로에 LightGBM 네이티브 형식으로 저장되는지 확인한다."""
    try:
        import lightgbm  # noqa: F401
    except ImportError:
        pytest.skip("lightgbm 미설치")

    import src.ml.trainer as trainer_mod
    monkeypatch.setattr(trainer_mod, "MODEL_DIR", tmp_path)
    # 테스트 데이터는 AUC가 낮을 수 있으므로 임계값 완화
    monkeypatch.setattr(trainer_mod, "MIN_AUC_THRESHOLD", 0.0)

    # 뚜렷한 패턴의 데이터 (feat1 높으면 양수 수익)
    data = pd.DataFrame({
        "feat1": [1.0, 2.0, 8.0, 9.0, 1.5, 2.5, 7.0, 8.5, 3.0, 7.5],
        "feat2": [4.0, 5.0, 1.0, 2.0, 4.5, 5.5, 1.5, 2.5, 5.0, 1.0],
        "return_20d": [-0.05, -0.03, 0.10, 0.08, -0.02, -0.04, 0.06, 0.12, -0.01, 0.05],
        "stock_id": list(range(1, 11)),
        "date_id": list(range(20250101, 20250111)),
    })

    result = train_return_model(data)
    assert result is not None
    assert result.exists()
    assert result.suffix == ".txt"  # 네이티브 형식
    assert "lgbm_return_" in result.name

    # 메타데이터 JSON도 생성 확인
    meta_path = result.with_suffix(".json")
    assert meta_path.exists()


def test_auc_threshold_blocks_bad_model(tmp_path, monkeypatch):
    """AUC 임계값 미달 시 모델 저장을 차단한다."""
    try:
        import lightgbm  # noqa: F401
    except ImportError:
        pytest.skip("lightgbm 미설치")

    import src.ml.trainer as trainer_mod
    monkeypatch.setattr(trainer_mod, "MODEL_DIR", tmp_path)
    # 높은 임계값 설정 — 랜덤 데이터로는 달성 불가
    monkeypatch.setattr(trainer_mod, "MIN_AUC_THRESHOLD", 0.99)

    data = pd.DataFrame({
        "feat1": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "feat2": [4.0, 5.0, 6.0, 7.0, 8.0, 9.0],
        "return_20d": [0.05, -0.03, 0.10, -0.05, 0.02, -0.01],
        "stock_id": [1, 2, 3, 4, 5, 6],
        "date_id": [20250101, 20250102, 20250103, 20250104, 20250105, 20250106],
    })

    result = train_return_model(data)
    assert result is None  # AUC 미달로 저장 안 됨
