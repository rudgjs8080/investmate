"""포트폴리오 최적화 라우트."""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.data.kr_names import get_kr_name
from src.db.helpers import date_to_id
from src.db.models import DimStock, FactDailyPrice, FactDailyRecommendation
from src.web.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/portfolio")
def portfolio_page(request: Request, db: Session = Depends(get_db)):
    """포트폴리오 최적화 페이지."""
    templates = request.app.state.templates

    # 가장 최근 추천 종목 가져오기
    latest_date_id = db.execute(
        select(FactDailyRecommendation.run_date_id)
        .order_by(FactDailyRecommendation.run_date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    tickers = []
    if latest_date_id:
        recs = db.execute(
            select(FactDailyRecommendation)
            .where(FactDailyRecommendation.run_date_id == latest_date_id)
            .order_by(FactDailyRecommendation.rank)
        ).scalars().all()

        # 배치 로드 (N+1 방지)
        rec_stock_ids = [rec.stock_id for rec in recs]
        stock_map = {
            s.stock_id: s
            for s in db.execute(
                select(DimStock).where(DimStock.stock_id.in_(rec_stock_ids))
            ).scalars()
        } if rec_stock_ids else {}

        for rec in recs:
            stock = stock_map.get(rec.stock_id)
            if stock:
                tickers.append({
                    "ticker": stock.ticker,
                    "name": get_kr_name(stock.ticker, stock.name),
                    "price": float(rec.price_at_recommendation or 0),
                    "score": float(rec.total_score),
                })

    return templates.TemplateResponse("portfolio.html", {
        "request": request,
        "tickers": tickers,
    })


_VALID_STRATEGIES = {"max_sharpe", "min_variance", "risk_parity", "equal_weight"}


@router.get("/api/portfolio/optimize")
def optimize_api(
    db: Session = Depends(get_db),
    tickers: str = "",
    strategy: str = "max_sharpe",
    investment: float = 10000.0,
    days: int = 252,
):
    """포트폴리오 최적화 API."""
    from src.portfolio.optimizer import optimize_portfolio
    from src.portfolio.efficient_frontier import compute_efficient_frontier

    # 전략 검증
    strategy = str(strategy)
    if strategy not in _VALID_STRATEGIES:
        strategy = "max_sharpe"

    ticker_list = [t.strip().upper() for t in str(tickers).split(",") if t.strip()]
    if not ticker_list:
        return {"error": "종목을 선택하세요", "result": None, "frontier": []}

    # 가격 데이터 로드 — 배치 종목 조회 (N+1 방지)
    lookback = date.today() - timedelta(days=days)
    cutoff_id = date_to_id(lookback)

    stocks = db.execute(
        select(DimStock).where(DimStock.ticker.in_(ticker_list))
    ).scalars().all()
    ticker_to_stock = {s.ticker: s for s in stocks}

    price_data: dict[str, pd.Series] = {}
    for ticker in ticker_list:
        stock = ticker_to_stock.get(ticker)
        if not stock:
            continue

        rows = db.execute(
            select(FactDailyPrice.date_id, FactDailyPrice.adj_close)
            .where(
                FactDailyPrice.stock_id == stock.stock_id,
                FactDailyPrice.date_id >= cutoff_id,
            )
            .order_by(FactDailyPrice.date_id)
        ).all()

        if rows and len(rows) >= 20:
            price_data[ticker] = pd.Series(
                [float(r.adj_close) for r in rows],
                index=[r.date_id for r in rows],
            )

    if len(price_data) < 2:
        from src.portfolio.optimizer import equal_weight
        result = equal_weight(list(price_data.keys()) or ticker_list, investment)
        return {
            "result": _result_to_dict(result),
            "frontier": [],
            "error": "2개 이상 종목의 가격 데이터가 필요합니다" if len(price_data) < 2 else None,
        }

    # 최적화
    result = optimize_portfolio(price_data, strategy=strategy, investment=investment)

    # 효율적 프런티어 (최대 20점)
    frontier = compute_efficient_frontier(price_data, n_points=20)

    # 4가지 전략 모두 계산 (비교용)
    all_strategies = {}
    for s in ["equal_weight", "max_sharpe", "min_variance", "risk_parity"]:
        r = optimize_portfolio(price_data, strategy=s, investment=investment)
        all_strategies[s] = _result_to_dict(r)

    return {
        "result": _result_to_dict(result),
        "all_strategies": all_strategies,
        "frontier": [
            {
                "return": fp.expected_return,
                "volatility": fp.volatility,
                "sharpe": fp.sharpe_ratio,
            }
            for fp in frontier
        ],
        "error": None,
    }


def _result_to_dict(result) -> dict:
    """PortfolioResult → JSON 직렬화."""
    return {
        "strategy": result.strategy,
        "allocations": result.allocations,
        "expected_return": result.expected_return,
        "volatility": result.volatility,
        "sharpe_ratio": result.sharpe_ratio,
        "amounts": result.amounts,
    }
