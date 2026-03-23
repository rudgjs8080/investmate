"""ML 스코어러 테스트."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.ml.scorer import MLScorer


class _FakeModel:
    """pickle 가능한 가짜 ML 모델."""

    def predict(self, X):  # noqa: ANN001, N803
        return [0.8]


def test_scorer_not_ready(seeded_session):
    """ML 모델 미준비 시 후보를 그대로 반환한다."""
    scorer = MLScorer()
    candidates = [
        {"stock_id": 1, "ticker": "AAPL", "total_score": 8.0},
        {"stock_id": 2, "ticker": "MSFT", "total_score": 7.5},
    ]

    result = scorer.rank(seeded_session, candidates)

    assert result == candidates
    assert result[0]["total_score"] == 8.0
    assert result[1]["total_score"] == 7.5


def test_scorer_is_ready_insufficient_data(seeded_session):
    """데이터 부족 시 is_ready가 False."""
    scorer = MLScorer()
    assert scorer.is_ready(seeded_session) is False


def test_scorer_get_status(seeded_session):
    """상태 반환이 올바른 구조를 갖는다."""
    scorer = MLScorer()
    status = scorer.get_status(seeded_session)

    assert "data_days" in status
    assert "min_required" in status
    assert "is_ready" in status
    assert "models_count" in status
    assert "status" in status
    assert status["is_ready"] is False


def test_scorer_adjusts_scores(seeded_session, tmp_path, monkeypatch):
    """모델이 있으면 점수가 70% rule + 30% ML로 조정된다."""
    import pickle

    import src.ml.scorer as scorer_mod

    # MODEL_DIR을 tmp_path로 설정
    monkeypatch.setattr(scorer_mod, "MODEL_DIR", tmp_path)

    model_data = {"model": _FakeModel(), "feature_cols": ["feat1", "feat2"]}
    model_path = tmp_path / "lgbm_return_20250320_120000.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(model_data, f)

    # is_ready를 True로 강제
    scorer = MLScorer()
    monkeypatch.setattr(scorer, "is_ready", lambda session: True)

    # build_features_for_stock을 mock
    with patch(
        "src.ml.features.build_features_for_stock",
        return_value={"feat1": 1.0, "feat2": 2.0},
    ):
        candidates = [
            {"stock_id": 1, "ticker": "AAPL", "total_score": 8.0},
            {"stock_id": 2, "ticker": "MSFT", "total_score": 7.0},
        ]
        result = scorer.rank(seeded_session, candidates)

    # 8.0 * 0.7 + (0.8 * 10) * 0.3 = 5.6 + 2.4 = 8.0
    assert result[0]["total_score"] == 8.0
    assert result[0]["ml_probability"] == 0.8
    # 7.0 * 0.7 + 8.0 * 0.3 = 4.9 + 2.4 = 7.3
    assert result[1]["total_score"] == 7.3
