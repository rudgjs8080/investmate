"""Deep Dive 6개 분석 레이어 통합 모듈."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from src.deepdive.layers_flow import compute_layer4_flow
from src.deepdive.layers_fundamental import compute_layer1_fundamental
from src.deepdive.layers_technical import compute_layer3_technical

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
    """6개 레이어 통합 계산. 각 레이어 독립 실행."""
    result = {
        "layer1": compute_layer1_fundamental(session, stock_id),
        "layer3": compute_layer3_technical(session, stock_id, date_id),
        "layer4": compute_layer4_flow(session, stock_id),
    }

    # Phase 2 레이어 (있으면 호출)
    try:
        from src.deepdive.layers_valuation import compute_layer2_valuation

        result["layer2"] = compute_layer2_valuation(session, stock_id, sector_id)
    except ImportError:
        pass

    try:
        from src.deepdive.layers_narrative import compute_layer5_narrative

        result["layer5"] = compute_layer5_narrative(
            session, stock_id, ticker or "", reference_date or date.today(),
        )
    except ImportError:
        pass

    try:
        from src.deepdive.layers_macro import compute_layer6_macro

        result["layer6"] = compute_layer6_macro(session, stock_id, sector_id, date_id)
    except ImportError:
        pass

    return result
