"""메인 대시보드 라우트."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.data.kr_names import get_kr_name
from src.db.helpers import date_to_id, id_to_date
from src.db.models import DimStock
from src.db.repository import MacroRepository, RecommendationRepository
from src.web.deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    """메인 대시보드 페이지."""
    templates = request.app.state.templates

    # 최신 추천 데이터
    macro = MacroRepository.get_latest(db)
    latest_date = None
    recommendations = []
    sector_counts: dict[str, int] = {}

    if macro:
        latest_date = id_to_date(macro.date_id)
        run_date_id = macro.date_id

        recs = RecommendationRepository.get_by_date(db, run_date_id)

        # 배치 프리로딩 — N+1 방지
        stock_ids = [rec.stock_id for rec in recs]
        stocks = {
            s.stock_id: s
            for s in db.execute(
                select(DimStock).where(DimStock.stock_id.in_(stock_ids))
            ).scalars().all()
        } if stock_ids else {}

        for rec in recs:
            stock = stocks.get(rec.stock_id)
            if stock:
                sector = stock.sector.sector_name if stock.sector else "기타"
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
                recommendations.append({
                    "rank": rec.rank,
                    "ticker": stock.ticker,
                    "name": stock.name,
                    "name_kr": stock.name_kr or get_kr_name(stock.ticker, stock.name),
                    "sector": sector,
                    "total_score": float(rec.total_score),
                    "technical_score": float(rec.technical_score),
                    "fundamental_score": float(rec.fundamental_score),
                    "smart_money_score": float(rec.smart_money_score),
                    "external_score": float(rec.external_score),
                    "momentum_score": float(rec.momentum_score),
                    "price": float(rec.price_at_recommendation),
                    "ai_approved": rec.ai_approved,
                    "ai_confidence": int(rec.ai_confidence) if rec.ai_confidence else None,
                    "ai_reason": rec.ai_reason,
                })

    # 시장 분위기 및 시그널 카운트 계산
    market_score = macro.market_score if macro else None
    if market_score is not None:
        if market_score >= 7:
            market_mood = "강세"
        elif market_score <= 3:
            market_mood = "약세"
        else:
            market_mood = "보통"
    else:
        market_mood = None

    buy_signal_count = 0
    sell_signal_count = 0
    if macro:
        from src.db.models import FactSignal, DimSignalType
        signals = db.execute(
            select(DimSignalType.direction)
            .join(FactSignal, FactSignal.signal_type_id == DimSignalType.signal_type_id)
            .where(FactSignal.date_id == macro.date_id)
        ).scalars().all()
        buy_signal_count = sum(1 for d in signals if d == "BUY")
        sell_signal_count = sum(1 for d in signals if d == "SELL")

    # 최근 성과 요약
    perf_summary = {}
    try:
        from src.analysis.performance import calculate_performance
        perf = calculate_performance(db, days=30)
        perf_summary = {
            "win_rate_1d": perf.win_rate_1d,
            "win_rate_5d": perf.win_rate_5d,
            "avg_return_1d": perf.avg_return_1d,
            "avg_return_5d": perf.avg_return_5d,
            "total": perf.total_recommendations,
            "with_data": perf.with_return_data,
        }
    except Exception as e:
        logger.debug("성과 요약 계산 실패: %s", e)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "date": latest_date,
        "macro": {
            "vix": float(macro.vix) if macro and macro.vix else None,
            "market_score": market_score,
            "sp500": float(macro.sp500_close) if macro and macro.sp500_close else None,
            "dollar": float(macro.dollar_index) if macro and macro.dollar_index else None,
            "yield_10y": float(macro.us_10y_yield) if macro and macro.us_10y_yield else None,
            "fear_greed_index": float(macro.fear_greed_index) if macro and macro.fear_greed_index else None,
            "fear_greed_rating": macro.fear_greed_rating if macro else None,
        } if macro else {},
        "recommendations": recommendations,
        "sector_counts": sector_counts,
        "market_mood": market_mood,
        "market_score": market_score,
        "buy_signal_count": buy_signal_count,
        "sell_signal_count": sell_signal_count,
        "perf_summary": perf_summary,
    })
