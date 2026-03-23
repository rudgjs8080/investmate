"""추천 종목 상세 라우트."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.web.deps import get_db

router = APIRouter()


@router.get("/recommendations/{report_date}")
def recommendations_detail(report_date: str, request: Request, db: Session = Depends(get_db)):
    """특정 날짜의 추천 종목 상세."""
    templates = request.app.state.templates

    try:
        run_date = date.fromisoformat(report_date)
    except ValueError:
        return templates.TemplateResponse("dashboard.html", {
            "request": request, "error": "잘못된 날짜 형식",
            "date": None, "macro": {}, "recommendations": [], "sector_counts": {},
        })

    run_date_id = date_to_id(run_date)

    from src.reports.assembler import assemble_enriched_report
    try:
        report = assemble_enriched_report(db, run_date, run_date_id)
    except Exception:
        return templates.TemplateResponse("recommendations.html", {
            "request": request, "date": run_date, "recommendations": [],
        })

    recs = []
    for rec in report.recommendations:
        recs.append({
            "rank": rec.rank,
            "ticker": rec.ticker,
            "name": rec.name,
            "sector": rec.sector,
            "price": rec.price,
            "price_change": rec.price_change_pct,
            "total_score": rec.total_score,
            "technical_score": rec.technical_score,
            "fundamental_score": rec.fundamental_score,
            "smart_money_score": rec.smart_money_score,
            "external_score": rec.external_score,
            "momentum_score": rec.momentum_score,
            "reason": rec.recommendation_reason,
            "rsi": rec.technical.rsi,
            "macd_status": rec.technical.macd_status,
            "sma_alignment": rec.technical.sma_alignment,
            "per": rec.fundamental.per,
            "roe": rec.fundamental.roe,
            "dividend_yield": rec.fundamental.dividend_yield,
            "ai_approved": rec.ai_approved,
            "ai_confidence": rec.ai_confidence,
            "ai_reason": rec.ai_reason,
            "ai_target_price": rec.ai_target_price,
            "ai_stop_loss": rec.ai_stop_loss,
            "ai_entry_strategy": rec.ai_entry_strategy,
            "ai_exit_strategy": rec.ai_exit_strategy,
            "ai_risk_level": rec.ai_risk_level,
            "signals": [
                {"type": s.signal_type, "direction": s.direction, "strength": s.strength}
                for s in rec.technical.signals
            ],
            "risk_factors": list(rec.risk_factors),
        })

    return templates.TemplateResponse("recommendations.html", {
        "request": request,
        "date": run_date,
        "recommendations": recs,
        "macro": {
            "mood": report.macro.mood,
            "market_score": report.macro.market_score,
            "vix": report.macro.vix,
        },
    })
