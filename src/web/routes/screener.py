"""인터랙티브 스크리너 라우트 (Finviz 스타일)."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.data.kr_names import get_kr_name
from src.db.helpers import date_to_id
from src.db.models import (
    DimSector,
    DimStock,
    FactDailyPrice,
    FactIndicatorValue,
    FactSignal,
    FactValuation,
    DimIndicatorType,
    DimSignalType,
)
from src.web.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/screener")
def screener_page(request: Request, db: Session = Depends(get_db)):
    """스크리너 페이지."""
    templates = request.app.state.templates

    # 섹터 목록 (필터용)
    sectors = db.execute(
        select(DimSector.sector_name).distinct().order_by(DimSector.sector_name)
    ).scalars().all()

    return templates.TemplateResponse("screener.html", {
        "request": request,
        "sectors": list(sectors),
    })


@router.get("/api/screener")
def screener_data(
    db: Session = Depends(get_db),
    sector: str | None = None,
    rsi_min: float | None = None,
    rsi_max: float | None = None,
    per_min: float | None = None,
    per_max: float | None = None,
    pbr_min: float | None = None,
    pbr_max: float | None = None,
    roe_min: float | None = None,
    div_min: float | None = None,
    signal: str | None = None,
    sort_by: str = "ticker",
    sort_dir: str = "asc",
    limit: int = 100,
):
    """스크리너 API — 전 종목 필터링 + 정렬."""
    # S&P 500 종목 로드
    stock_query = (
        select(DimStock)
        .where(DimStock.is_sp500.is_(True), DimStock.is_active.is_(True))
    )
    if sector:
        sector_list = str(sector).split(",")
        sector_ids = db.execute(
            select(DimSector.sector_id).where(DimSector.sector_name.in_(sector_list))
        ).scalars().all()
        if sector_ids:
            stock_query = stock_query.where(DimStock.sector_id.in_(sector_ids))

    stocks = list(db.execute(stock_query).scalars().all())
    if not stocks:
        return {"results": [], "total": 0}

    stock_map = {s.stock_id: s for s in stocks}
    stock_ids = list(stock_map.keys())

    # 최신 밸류에이션 배치 로드
    val_subq = (
        select(
            FactValuation.stock_id,
            func.max(FactValuation.date_id).label("max_date"),
        )
        .where(FactValuation.stock_id.in_(stock_ids))
        .group_by(FactValuation.stock_id)
        .subquery()
    )
    vals = db.execute(
        select(FactValuation)
        .join(val_subq, (FactValuation.stock_id == val_subq.c.stock_id) & (FactValuation.date_id == val_subq.c.max_date))
    ).scalars().all()
    val_map = {v.stock_id: v for v in vals}

    # 최신 RSI 배치 로드
    rsi_type = db.execute(
        select(DimIndicatorType.indicator_type_id).where(DimIndicatorType.code == "RSI_14")
    ).scalar_one_or_none()

    rsi_map: dict[int, float] = {}
    if rsi_type:
        rsi_subq = (
            select(
                FactIndicatorValue.stock_id,
                func.max(FactIndicatorValue.date_id).label("max_date"),
            )
            .where(
                FactIndicatorValue.stock_id.in_(stock_ids),
                FactIndicatorValue.indicator_type_id == rsi_type,
            )
            .group_by(FactIndicatorValue.stock_id)
            .subquery()
        )
        rsi_rows = db.execute(
            select(FactIndicatorValue.stock_id, FactIndicatorValue.value)
            .join(rsi_subq, (FactIndicatorValue.stock_id == rsi_subq.c.stock_id) & (FactIndicatorValue.date_id == rsi_subq.c.max_date))
            .where(FactIndicatorValue.indicator_type_id == rsi_type)
        ).all()
        rsi_map = {sid: float(v) for sid, v in rsi_rows}

    # 최신 가격 배치 로드
    price_subq = (
        select(
            FactDailyPrice.stock_id,
            func.max(FactDailyPrice.date_id).label("max_date"),
        )
        .where(FactDailyPrice.stock_id.in_(stock_ids))
        .group_by(FactDailyPrice.stock_id)
        .subquery()
    )
    price_rows = db.execute(
        select(FactDailyPrice.stock_id, FactDailyPrice.close, FactDailyPrice.volume)
        .join(price_subq, (FactDailyPrice.stock_id == price_subq.c.stock_id) & (FactDailyPrice.date_id == price_subq.c.max_date))
    ).all()
    price_map = {sid: (float(c), int(v)) for sid, c, v in price_rows}

    # 시그널 필터 (최근 5일 이내)
    signal_stocks: set[int] | None = None
    if signal:
        sig_code = str(signal)
        sig_type = db.execute(
            select(DimSignalType.signal_type_id).where(DimSignalType.code == sig_code)
        ).scalar_one_or_none()
        if sig_type:
            recent_dates = db.execute(
                select(FactDailyPrice.date_id).distinct().order_by(FactDailyPrice.date_id.desc()).limit(5)
            ).scalars().all()
            if recent_dates:
                sig_rows = db.execute(
                    select(FactSignal.stock_id).distinct()
                    .where(
                        FactSignal.signal_type_id == sig_type,
                        FactSignal.date_id >= min(recent_dates),
                    )
                ).scalars().all()
                signal_stocks = set(sig_rows)

    # 필터링 + 결과 조립
    results = []
    for sid, stock in stock_map.items():
        rsi = rsi_map.get(sid)
        val = val_map.get(sid)
        price_info = price_map.get(sid)

        # RSI 필터
        if rsi_min is not None and (rsi is None or rsi < rsi_min):
            continue
        if rsi_max is not None and (rsi is None or rsi > rsi_max):
            continue

        # 밸류에이션 필터
        per = float(val.per) if val and val.per else None
        pbr = float(val.pbr) if val and val.pbr else None
        roe = float(val.roe) if val and val.roe else None
        div_yield = float(val.dividend_yield) if val and val.dividend_yield else None
        market_cap = float(val.market_cap) if val and val.market_cap else None

        if per_min is not None and (per is None or per < per_min):
            continue
        if per_max is not None and (per is None or per > per_max):
            continue
        if pbr_min is not None and (pbr is None or pbr < pbr_min):
            continue
        if pbr_max is not None and (pbr is None or pbr > pbr_max):
            continue
        if roe_min is not None and (roe is None or roe < roe_min):
            continue
        if div_min is not None and (div_yield is None or div_yield < div_min):
            continue

        # 시그널 필터
        if signal_stocks is not None and sid not in signal_stocks:
            continue

        price = price_info[0] if price_info else None
        volume = price_info[1] if price_info else None

        sector_name = stock.sector.sector_name if stock.sector else "기타"

        results.append({
            "ticker": stock.ticker,
            "name": get_kr_name(stock.ticker, stock.name),
            "sector": sector_name,
            "price": price,
            "volume": volume,
            "rsi": round(rsi, 1) if rsi else None,
            "per": round(per, 1) if per else None,
            "pbr": round(pbr, 2) if pbr else None,
            "roe": round(roe * 100, 1) if roe and abs(roe) < 10 else (round(roe, 1) if roe else None),
            "dividend_yield": round(div_yield * 100, 2) if div_yield and abs(div_yield) < 1 else (round(div_yield, 2) if div_yield else None),
            "market_cap": market_cap,
        })

    # 정렬
    sort_key = sort_by if sort_by in ("ticker", "price", "rsi", "per", "pbr", "roe", "dividend_yield", "market_cap", "volume") else "ticker"
    reverse = sort_dir == "desc"

    results.sort(
        key=lambda r: (r.get(sort_key) is None, r.get(sort_key) or 0),
        reverse=reverse,
    )

    total = len(results)
    results = results[:limit]

    return {"results": results, "total": total}
