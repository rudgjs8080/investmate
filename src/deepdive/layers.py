"""Deep Dive 6개 분석 레이어 통합 모듈."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from src.deepdive.layers_flow import compute_layer4_flow
from src.deepdive.layers_fundamental import compute_layer1_fundamental
from src.deepdive.layers_technical import compute_layer3_technical

logger = logging.getLogger(__name__)

# 후방 호환: 기존 import 경로 유지
__all__ = [
    "compute_layer1_fundamental",
    "compute_layer3_technical",
    "compute_layer4_flow",
    "compute_all_layers",
]


def compute_all_layers(
    session: Session,
    stock_id: int,
    date_id: int,
    sector_id: int | None = None,
    ticker: str | None = None,
    reference_date: date | None = None,
) -> dict:
    """6개 레이어 통합 계산. 각 레이어 독립 실행.

    레이어 2/5/6 import/계산 실패는 logger.error로 명시. 결과 dict에 해당 키 누락.
    """
    result: dict = {}

    # Core layers (1/3/4) — 실패 시 로그 후 해당 키 누락.
    result.update(_safe_call("layer1", compute_layer1_fundamental, session, stock_id))
    result.update(
        _safe_call("layer3", compute_layer3_technical, session, stock_id, date_id)
    )
    result.update(_safe_call("layer4", compute_layer4_flow, session, stock_id))

    # Optional layers — import 자체가 실패할 수 있음 (개발 중 모듈 부재).
    try:
        from src.deepdive.layers_valuation import compute_layer2_valuation
    except ImportError as e:
        logger.error("layer2 모듈 import 실패: %s", e)
    else:
        result.update(
            _safe_call("layer2", compute_layer2_valuation, session, stock_id, sector_id)
        )

    try:
        from src.deepdive.layers_narrative import compute_layer5_narrative
    except ImportError as e:
        logger.error("layer5 모듈 import 실패: %s", e)
    else:
        result.update(
            _safe_call(
                "layer5",
                compute_layer5_narrative,
                session,
                stock_id,
                ticker or "",
                reference_date or date.today(),
            )
        )

    try:
        from src.deepdive.layers_macro import compute_layer6_macro
    except ImportError as e:
        logger.error("layer6 모듈 import 실패: %s", e)
    else:
        result.update(
            _safe_call(
                "layer6",
                compute_layer6_macro,
                session,
                stock_id,
                sector_id,
                date_id,
            )
        )

    return result


def _safe_call(layer_key: str, fn, *args, **kwargs) -> dict:
    """레이어 계산을 감싸고 실패 시 로그 후 빈 dict 반환."""
    try:
        return {layer_key: fn(*args, **kwargs)}
    except Exception as e:
        logger.error("%s 계산 실패: %s", layer_key, e, exc_info=True)
        return {}
