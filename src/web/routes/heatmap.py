"""S&P 500 시장 히트맵 라우트."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.data.kr_names import get_kr_name
from src.db.helpers import date_to_id
from src.db.models import DimSector, DimStock, FactDailyPrice, FactValuation
from src.web.deps import get_db

router = APIRouter()


@router.get("/heatmap")
def heatmap_page(request: Request):
    """시장 히트맵 페이지."""
    templates = request.app.state.templates
    return templates.TemplateResponse("heatmap.html", {"request": request})


@router.get("/api/heatmap")
def heatmap_data(period: str = Query(default="1d"), db: Session = Depends(get_db)):
    """S&P 500 히트맵 데이터 (섹터별 트리맵)."""
    # 최근 2일 가격 (1d), 5일, 20일 기준
    days_map = {"1d": 2, "5d": 6, "1m": 22}
    lookback = days_map.get(period, 2)

    # 최근 날짜들 가져오기
    latest_dates = db.execute(
        select(FactDailyPrice.date_id)
        .distinct()
        .order_by(FactDailyPrice.date_id.desc())
        .limit(lookback + 5)
    ).scalars().all()

    if len(latest_dates) < 2:
        return {"sectors": []}

    latest_id = latest_dates[0]
    compare_id = latest_dates[min(lookback, len(latest_dates) - 1)]

    # 전 종목 최신 가격 + 비교 가격
    stocks = db.execute(
        select(DimStock).where(DimStock.is_sp500 == True).where(DimStock.is_active == True)
    ).scalars().all()

    # 배치로 가격 조회
    latest_prices = dict(db.execute(
        select(FactDailyPrice.stock_id, FactDailyPrice.close)
        .where(FactDailyPrice.date_id == latest_id)
    ).all())

    compare_prices = dict(db.execute(
        select(FactDailyPrice.stock_id, FactDailyPrice.close)
        .where(FactDailyPrice.date_id == compare_id)
    ).all())

    # 시총 (최신)
    market_caps = {}
    vals = db.execute(
        select(FactValuation.stock_id, FactValuation.market_cap)
        .where(FactValuation.market_cap.isnot(None))
    ).all()
    for sid, mc in vals:
        market_caps[sid] = float(mc)

    # 섹터별 그룹핑
    sector_data: dict[str, list] = {}
    for stock in stocks:
        sid = stock.stock_id
        latest = latest_prices.get(sid)
        compare = compare_prices.get(sid)
        if not latest or not compare or float(compare) == 0:
            continue

        ret_pct = round((float(latest) - float(compare)) / float(compare) * 100, 2)
        sector = stock.sector.sector_name if stock.sector else "기타"
        mc = market_caps.get(sid, 1e9)  # 기본 10억

        sector_data.setdefault(sector, []).append({
            "ticker": stock.ticker,
            "name": get_kr_name(stock.ticker, stock.name),
            "return_pct": ret_pct,
            "market_cap": mc,
            "price": float(latest),
        })

    # ECharts treemap 포맷
    sectors = []
    for sector_name, stock_list in sorted(sector_data.items()):
        children = [
            {
                "name": f"{s['ticker']}\n{s['return_pct']:+.1f}%",
                "value": max(s["market_cap"] / 1e9, 0.1),  # 10억 단위
                "return_pct": s["return_pct"],
                "ticker": s["ticker"],
                "stock_name": s["name"],
                "price": s["price"],
            }
            for s in sorted(stock_list, key=lambda x: -x["market_cap"])
        ]
        sector_return = sum(s["return_pct"] for s in stock_list) / len(stock_list) if stock_list else 0
        sectors.append({
            "name": sector_name,
            "return_pct": round(sector_return, 2),
            "children": children,
        })

    return {"sectors": sectors, "period": period}
