"""S&P 500 시장 히트맵 라우트."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.data.kr_names import get_kr_name
from src.db.helpers import date_to_id
from src.db.models import (
    DimSector,
    DimStock,
    FactDailyPrice,
    FactDailyRecommendation,
    FactValuation,
)
from src.web.deps import get_db

router = APIRouter()


@router.get("/heatmap")
def heatmap_page(request: Request):
    """시장 히트맵 페이지."""
    templates = request.app.state.templates
    return templates.TemplateResponse("heatmap.html", {"request": request})


@router.get("/api/heatmap")
def heatmap_data(period: str = Query(default="1d"), db: Session = Depends(get_db)):
    """S&P 500 히트맵 데이터 (섹터별 트리맵).

    Returns:
        sectors: 섹터별 종목 트리맵 데이터
        summary: 시장 요약 통계
        recommended_tickers: 오늘의 추천 종목
        period: 요청된 기간
    """
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
        return {"sectors": [], "summary": None, "recommended_tickers": [], "period": period}

    latest_id = latest_dates[0]
    compare_id = latest_dates[min(lookback, len(latest_dates) - 1)]

    # 전 종목 조회
    stocks = db.execute(
        select(DimStock).where(DimStock.is_sp500 == True).where(DimStock.is_active == True)
    ).scalars().all()

    # 배치 가격 조회
    latest_prices = dict(db.execute(
        select(FactDailyPrice.stock_id, FactDailyPrice.close)
        .where(FactDailyPrice.date_id == latest_id)
    ).all())

    compare_prices = dict(db.execute(
        select(FactDailyPrice.stock_id, FactDailyPrice.close)
        .where(FactDailyPrice.date_id == compare_id)
    ).all())

    # 시총 — 종목별 최신 date_id만 조회 (최적화)
    latest_val_sq = (
        select(
            FactValuation.stock_id,
            func.max(FactValuation.date_id).label("max_date_id"),
        )
        .where(FactValuation.market_cap.isnot(None))
        .group_by(FactValuation.stock_id)
        .subquery()
    )
    market_caps = dict(db.execute(
        select(FactValuation.stock_id, FactValuation.market_cap)
        .join(latest_val_sq, (
            (FactValuation.stock_id == latest_val_sq.c.stock_id)
            & (FactValuation.date_id == latest_val_sq.c.max_date_id)
        ))
    ).all())
    market_caps = {sid: float(mc) for sid, mc in market_caps.items()}

    # 추가 지표: RSI, PER (배치 로드)
    rsi_map = _load_latest_rsi(db, latest_id)
    per_map = _load_latest_per(db)

    # 오늘의 추천 종목
    recommended = _load_recommended_tickers(db, latest_id)

    # 섹터별 그룹핑 + 수익률 계산
    sector_data: dict[str, list] = {}
    all_returns: list[float] = []

    for stock in stocks:
        sid = stock.stock_id
        latest = latest_prices.get(sid)
        compare = compare_prices.get(sid)
        if not latest or not compare or float(compare) == 0:
            continue

        ret_pct = round((float(latest) - float(compare)) / float(compare) * 100, 2)
        sector = stock.sector.sector_name if stock.sector else "기타"
        mc = market_caps.get(sid, 1e9)

        all_returns.append(ret_pct)
        sector_data.setdefault(sector, []).append({
            "ticker": stock.ticker,
            "name": get_kr_name(stock.ticker, stock.name),
            "return_pct": ret_pct,
            "market_cap": mc,
            "price": float(latest),
            "volume": 0,
            "rsi": rsi_map.get(sid),
            "per": per_map.get(sid),
            "is_recommended": stock.ticker in recommended,
            "rec_rank": recommended.get(stock.ticker),
        })

    # 시장 요약 통계
    summary = _build_summary(all_returns, sector_data)

    # ECharts treemap 포맷
    sectors = []
    for sector_name, stock_list in sorted(sector_data.items()):
        children = [
            {
                "name": f"{s['ticker']}\n{s['return_pct']:+.1f}%",
                "value": max(s["market_cap"] / 1e9, 0.1),
                "return_pct": s["return_pct"],
                "ticker": s["ticker"],
                "stock_name": s["name"],
                "price": s["price"],
                "rsi": s["rsi"],
                "per": s["per"],
                "is_recommended": s["is_recommended"],
                "rec_rank": s["rec_rank"],
            }
            for s in sorted(stock_list, key=lambda x: -x["market_cap"])
        ]
        sector_return = sum(s["return_pct"] for s in stock_list) / len(stock_list) if stock_list else 0
        sectors.append({
            "name": sector_name,
            "return_pct": round(sector_return, 2),
            "children": children,
        })

    return {
        "sectors": sectors,
        "summary": summary,
        "recommended_tickers": list(recommended.keys()),
        "period": period,
    }


def _load_latest_rsi(db: Session, latest_date_id: int) -> dict[int, float]:
    """최신 RSI_14 값을 배치 로드한다."""
    from src.db.models import DimIndicatorType, FactIndicatorValue

    rsi_type = db.execute(
        select(DimIndicatorType.indicator_type_id)
        .where(DimIndicatorType.code == "RSI_14")
    ).scalar_one_or_none()

    if rsi_type is None:
        return {}

    rows = db.execute(
        select(FactIndicatorValue.stock_id, FactIndicatorValue.value)
        .where(
            FactIndicatorValue.indicator_type_id == rsi_type,
            FactIndicatorValue.date_id <= latest_date_id,
            FactIndicatorValue.date_id >= latest_date_id - 5,
        )
        .order_by(FactIndicatorValue.date_id.desc())
    ).all()

    result: dict[int, float] = {}
    for sid, val in rows:
        if sid not in result:
            result[sid] = round(float(val), 1)
    return result


def _load_latest_per(db: Session) -> dict[int, float]:
    """최신 PER 값을 배치 로드한다."""
    latest_sq = (
        select(
            FactValuation.stock_id,
            func.max(FactValuation.date_id).label("max_date_id"),
        )
        .where(FactValuation.per.isnot(None))
        .group_by(FactValuation.stock_id)
        .subquery()
    )
    rows = db.execute(
        select(FactValuation.stock_id, FactValuation.per)
        .join(latest_sq, (
            (FactValuation.stock_id == latest_sq.c.stock_id)
            & (FactValuation.date_id == latest_sq.c.max_date_id)
        ))
    ).all()
    return {sid: round(float(per), 1) for sid, per in rows if per is not None}


def _load_recommended_tickers(db: Session, latest_date_id: int) -> dict[str, int]:
    """오늘의 추천 종목 {ticker: rank} 매핑을 로드한다."""
    # 최신 추천 날짜 조회
    latest_rec_date = db.execute(
        select(FactDailyRecommendation.run_date_id)
        .order_by(FactDailyRecommendation.run_date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    if latest_rec_date is None:
        return {}

    rows = db.execute(
        select(DimStock.ticker, FactDailyRecommendation.rank)
        .join(DimStock, DimStock.stock_id == FactDailyRecommendation.stock_id)
        .where(FactDailyRecommendation.run_date_id == latest_rec_date)
        .order_by(FactDailyRecommendation.rank)
    ).all()

    return {ticker: rank for ticker, rank in rows}


def _build_summary(
    all_returns: list[float],
    sector_data: dict[str, list],
) -> dict | None:
    """시장 요약 통계를 생성한다."""
    if not all_returns:
        return None

    up = sum(1 for r in all_returns if r > 0)
    down = sum(1 for r in all_returns if r < 0)
    unchanged = len(all_returns) - up - down
    avg_return = round(sum(all_returns) / len(all_returns), 2)

    # 섹터별 평균 수익률
    sector_avgs = {}
    for sector, stocks in sector_data.items():
        if stocks:
            sector_avgs[sector] = round(
                sum(s["return_pct"] for s in stocks) / len(stocks), 2
            )

    best_sector = max(sector_avgs, key=sector_avgs.get) if sector_avgs else None
    worst_sector = min(sector_avgs, key=sector_avgs.get) if sector_avgs else None

    return {
        "total_stocks": len(all_returns),
        "up_count": up,
        "down_count": down,
        "unchanged": unchanged,
        "avg_return": avg_return,
        "market_breadth": round(up / len(all_returns) * 100, 1),
        "best_sector": best_sector,
        "best_sector_return": sector_avgs.get(best_sector, 0) if best_sector else 0,
        "worst_sector": worst_sector,
        "worst_sector_return": sector_avgs.get(worst_sector, 0) if worst_sector else 0,
    }
