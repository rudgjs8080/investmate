"""팩터 대시보드 라우트."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.web.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/factors")
def factors_page(request: Request, db: Session = Depends(get_db)):
    """팩터 대시보드 페이지."""
    templates = request.app.state.templates

    factor_data = {}
    try:
        from src.analysis.factor_returns import get_factor_momentum
        momentum = get_factor_momentum(db, date.today())
        factor_data["momentum"] = {
            name: {
                "1m": round(fm.momentum_1m, 2),
                "3m": round(fm.momentum_3m, 2),
                "6m": round(fm.momentum_6m, 2),
            }
            for name, fm in momentum.items()
        }
    except Exception as e:
        logger.warning("팩터 모멘텀 조회 실패: %s", e)

    return templates.TemplateResponse("factors.html", {
        "request": request,
        "factor_data": factor_data,
    })


@router.get("/api/factors/returns")
def factor_returns_data(
    db: Session = Depends(get_db),
    factor: str | None = Query(default=None),
    days: int = Query(default=252, ge=30, le=1095),
):
    """팩터별 누적 수익률 시계열."""
    from src.db.helpers import date_to_id, id_to_date
    from src.db.repository import FactorReturnRepository

    start_date = date.today() - timedelta(days=days)
    start_id = date_to_id(start_date)

    if factor:
        records = FactorReturnRepository.get_by_factor(db, factor, start_date_id=start_id)
    else:
        records = FactorReturnRepository.get_all_factors(db, start_date_id=start_id)

    # 팩터별 누적 수익률
    series: dict[str, list[dict]] = {}
    cumulative: dict[str, float] = {}

    for r in records:
        name = r.factor_name
        if name not in cumulative:
            cumulative[name] = 0.0
            series[name] = []
        cumulative[name] += float(r.spread)
        try:
            d = id_to_date(r.date_id)
            series[name].append({
                "date": d.isoformat(),
                "cumulative": round(cumulative[name], 4),
                "spread": round(float(r.spread), 4),
            })
        except Exception:
            continue

    return {"factors": series}


@router.get("/api/factors/ic")
def factor_ic_data(
    db: Session = Depends(get_db),
    factor: str = Query(default="value"),
    days: int = Query(default=252, ge=30, le=1095),
):
    """IC 추이 시계열."""
    from src.analysis.factor_returns import get_factor_ic

    ic_series = get_factor_ic(db, factor, lookback_days=days)

    return {
        "factor": factor,
        "ic": [{"date": d.isoformat(), "ic": ic} for d, ic in ic_series],
    }


@router.get("/api/factors/correlation")
def factor_correlation_data(
    db: Session = Depends(get_db),
    days: int = Query(default=252, ge=30, le=1095),
):
    """팩터 간 상관관계 행렬."""
    import numpy as np

    from src.db.helpers import date_to_id
    from src.db.repository import FactorReturnRepository

    start_date = date.today() - timedelta(days=days)
    start_id = date_to_id(start_date)
    records = FactorReturnRepository.get_all_factors(db, start_date_id=start_id)

    # 팩터별 스프레드 시계열 수집
    spreads: dict[str, list[float]] = {}
    dates_by_factor: dict[str, list[int]] = {}
    for r in records:
        spreads.setdefault(r.factor_name, []).append(float(r.spread))
        dates_by_factor.setdefault(r.factor_name, []).append(r.date_id)

    factor_names = sorted(spreads.keys())
    if len(factor_names) < 2:
        return {"factors": factor_names, "correlation": []}

    # 날짜 정렬된 스프레드 배열
    min_len = min(len(v) for v in spreads.values())
    if min_len < 10:
        return {"factors": factor_names, "correlation": []}

    matrix_data = np.array([spreads[f][-min_len:] for f in factor_names])
    corr = np.corrcoef(matrix_data)

    return {
        "factors": factor_names,
        "correlation": [
            [round(float(corr[i][j]), 3) for j in range(len(factor_names))]
            for i in range(len(factor_names))
        ],
    }
