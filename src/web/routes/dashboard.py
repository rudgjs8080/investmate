"""메인 대시보드 라우트."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.repository import RecommendationRepository, MacroRepository
from src.web.deps import get_db

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
        from src.db.helpers import id_to_date
        latest_date = id_to_date(macro.date_id)
        run_date_id = macro.date_id

        recs = RecommendationRepository.get_by_date(db, run_date_id)
        from src.db.models import DimStock
        from sqlalchemy import select

        for rec in recs:
            stock = db.execute(
                select(DimStock).where(DimStock.stock_id == rec.stock_id)
            ).scalar_one_or_none()
            if stock:
                sector = stock.sector.sector_name if stock.sector else "기타"
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
                from src.data.kr_names import get_kr_name
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

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "date": latest_date,
        "macro": {
            "vix": float(macro.vix) if macro and macro.vix else None,
            "market_score": market_score,
            "sp500": float(macro.sp500_close) if macro and macro.sp500_close else None,
            "dollar": float(macro.dollar_index) if macro and macro.dollar_index else None,
            "yield_10y": float(macro.us_10y_yield) if macro and macro.us_10y_yield else None,
        } if macro else {},
        "recommendations": recommendations,
        "sector_counts": sector_counts,
        "market_mood": market_mood,
        "market_score": market_score,
        "buy_signal_count": buy_signal_count,
        "sell_signal_count": sell_signal_count,
    })
