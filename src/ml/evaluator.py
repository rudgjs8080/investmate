"""모델 성능 평가 — Walk-Forward Validation."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def evaluate_model(session) -> dict:  # noqa: ANN001
    """모델 성능을 평가한다.

    TODO: 데이터 축적 후 구현.
    """
    logger.info("모델 평가 — 미구현 (데이터 축적 필요)")
    return {"status": "데이터 부족", "message": "최소 60거래일 데이터 필요"}
