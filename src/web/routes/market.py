"""시장 환경 라우트."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from src.db.repository import MacroRepository
from src.web.deps import get_db

router = APIRouter()


@router.get("/market")
def market_page(request: Request, db: Session = Depends(get_db)):
    """시장 환경 페이지."""
    templates = request.app.state.templates

    macro = MacroRepository.get_latest(db)
    data = {}
    if macro:
        from src.db.helpers import id_to_date
        data = {
            "date": id_to_date(macro.date_id).isoformat(),
            "vix": float(macro.vix) if macro.vix else None,
            "sp500": float(macro.sp500_close) if macro.sp500_close else None,
            "sp500_sma20": float(macro.sp500_sma20) if macro.sp500_sma20 else None,
            "yield_10y": float(macro.us_10y_yield) if macro.us_10y_yield else None,
            "yield_13w": float(macro.us_13w_yield) if macro.us_13w_yield else None,
            "dollar": float(macro.dollar_index) if macro.dollar_index else None,
            "market_score": macro.market_score,
        }

    return templates.TemplateResponse("market.html", {
        "request": request,
        "macro": data,
    })
