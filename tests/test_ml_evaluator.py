"""ML 평가기 테스트."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.ml.evaluator import evaluate_model


class TestEvaluateModel:
    def test_no_data_returns_insufficient(self):
        """return_20d 데이터 없으면 '데이터 부족'."""
        session = MagicMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        session.execute.return_value = mock_result

        result = evaluate_model(session)
        assert result["status"] == "데이터 부족"

    def test_with_data_returns_accuracy(self):
        """데이터가 있으면 정확도 계산."""
        session = MagicMock()

        # 4개 추천: 3개 양수, 1개 음수 → accuracy = 75%
        mock_recs = []
        for i, (ret, score) in enumerate([
            (5.0, 8.0), (3.0, 7.5), (-2.0, 7.0), (1.0, 6.5),
        ]):
            rec = MagicMock()
            rec.return_20d = ret
            rec.total_score = score
            rec.run_date_id = 20260320  # 같은 날짜
            mock_recs.append(rec)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_recs
        session.execute.return_value = mock_result

        result = evaluate_model(session)
        assert result["status"] == "평가 완료"
        assert result["accuracy"] == 75.0  # 3/4
        assert result["total_predictions"] == 4
        assert result["data_days"] == 1

    def test_precision_at_10(self):
        """상위 10개 중 양수 비율 계산."""
        session = MagicMock()

        mock_recs = []
        # 5개 중 3개 양수, 2개 음수
        for ret, score, date_id in [
            (5.0, 9.0, 20260320),
            (3.0, 8.0, 20260320),
            (-1.0, 7.0, 20260320),
            (2.0, 6.0, 20260320),
            (-4.0, 5.0, 20260320),
        ]:
            rec = MagicMock()
            rec.return_20d = ret
            rec.total_score = score
            rec.run_date_id = date_id
            mock_recs.append(rec)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_recs
        session.execute.return_value = mock_result

        result = evaluate_model(session)
        assert result["precision_at_10"] == 60.0  # 3/5
