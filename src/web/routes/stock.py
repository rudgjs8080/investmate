"""개별 종목 상세 라우트 — 뉴스, 수급, 시그널, 지표 포함."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.data.kr_names import get_kr_name
from src.db.helpers import date_to_id, id_to_date
from src.db.models import (
    DimIndicatorType, DimStock, FactDailyPrice, FactDailyRecommendation,
    FactIndicatorValue, FactValuation,
)
from src.web.deps import get_db

router = APIRouter()


@router.get("/stock/{ticker}")
def stock_detail(ticker: str, request: Request, db: Session = Depends(get_db)):
    """개별 종목 상세 페이지."""
    templates = request.app.state.templates

    stock = db.execute(
        select(DimStock).where(DimStock.ticker == ticker.upper())
    ).scalar_one_or_none()

    if not stock:
        return templates.TemplateResponse("stock.html", {
            "request": request, "stock": None, "error": f"종목 '{ticker}'을 찾을 수 없습니다.",
            "rec_history": [], "news": [], "smart_money": {}, "signals": [], "explanation": None,
        })

    # 최신 밸류에이션
    val = db.execute(
        select(FactValuation).where(FactValuation.stock_id == stock.stock_id)
        .order_by(FactValuation.date_id.desc()).limit(1)
    ).scalar_one_or_none()

    # 최신 가격
    latest_price = db.execute(
        select(FactDailyPrice).where(FactDailyPrice.stock_id == stock.stock_id)
        .order_by(FactDailyPrice.date_id.desc()).limit(1)
    ).scalar_one_or_none()

    # 추천 이력
    recs = db.execute(
        select(FactDailyRecommendation).where(FactDailyRecommendation.stock_id == stock.stock_id)
        .order_by(FactDailyRecommendation.run_date_id.desc()).limit(20)
    ).scalars().all()

    rec_history = []
    for r in recs:
        try:
            d = id_to_date(r.run_date_id)
        except Exception:
            d = None
        rec_history.append({
            "date": d.isoformat() if d else str(r.run_date_id),
            "rank": r.rank,
            "total_score": float(r.total_score),
            "ai_approved": r.ai_approved,
            "ai_confidence": int(r.ai_confidence) if r.ai_confidence else None,
            "return_1d": float(r.return_1d) if r.return_1d is not None else None,
            "return_5d": float(r.return_5d) if r.return_5d is not None else None,
            "return_20d": float(r.return_20d) if r.return_20d is not None else None,
        })

    # 뉴스
    news = []
    try:
        from src.db.repository import NewsRepository
        news_items = NewsRepository.get_by_stock(db, stock.stock_id, limit=10)
        for n in news_items:
            news.append({
                "title": n.title,
                "source": n.source,
                "url": n.url,
                "published_at": n.published_at.strftime("%Y-%m-%d") if n.published_at else None,
                "sentiment": float(n.sentiment_score) if n.sentiment_score is not None else 0,
            })
    except Exception:
        pass

    # 수급/스마트머니
    smart_money = {}
    try:
        from src.db.repository import AnalystConsensusRepository, InstitutionalHoldingRepository, InsiderTradeRepository
        # 애널리스트
        consensus = AnalystConsensusRepository.get_latest(db, stock.stock_id)
        if consensus:
            total = consensus.strong_buy + consensus.buy + consensus.hold + consensus.sell + consensus.strong_sell
            smart_money["analyst"] = {
                "buy": consensus.strong_buy + consensus.buy,
                "hold": consensus.hold,
                "sell": consensus.sell + consensus.strong_sell,
                "total": total,
                "target_mean": float(consensus.target_mean) if consensus.target_mean else None,
            }
        # 기관
        holdings = InstitutionalHoldingRepository.get_by_stock(db, stock.stock_id, limit=5)
        if holdings:
            smart_money["institutions"] = [
                {"name": h.institution_name, "value": float(h.value) if h.value else None}
                for h in holdings[:3]
            ]
        # 내부자
        trades = InsiderTradeRepository.get_by_stock(db, stock.stock_id, limit=10)
        if trades:
            buys = sum(1 for t in trades if t.transaction_type in ("Buy", "Purchase"))
            sells = len(trades) - buys
            smart_money["insider"] = {"buys": buys, "sells": sells, "total": len(trades)}
    except Exception:
        pass

    # 시그널
    signals = []
    try:
        from src.db.repository import SignalRepository
        from src.reports.explainer import _translate_signals
        sig_items = SignalRepository.get_by_stock_and_date(db, stock.stock_id, date_to_id(date.today()))
        for s in sig_items:
            sig_type_map = SignalRepository.get_signal_type_map(db)
            reverse_map = {v: k for k, v in sig_type_map.items()}
            code = reverse_map.get(s.signal_type_id, "unknown")
            kr_name = _translate_signals([code])[0]
            direction = "BUY" if "buy" in code or "oversold" in code or "bullish" in code or "golden" in code or "lower" in code else "SELL"
            signals.append({"code": code, "name_kr": kr_name, "direction": direction, "strength": s.strength})
    except Exception:
        pass

    # 종목 설명
    explanation = None
    try:
        if recs:
            from src.reports.assembler import assemble_enriched_report
            latest_rec = recs[0]
            report = assemble_enriched_report(db, date.today(), latest_rec.run_date_id)
            for rec_detail in report.recommendations:
                if rec_detail.ticker == stock.ticker:
                    from src.reports.explainer import explain_stock
                    explanation = {
                        "headline": explain_stock(rec_detail).headline,
                        "why": explain_stock(rec_detail).why_recommended,
                        "risk": explain_stock(rec_detail).risk_simple,
                    }
                    break
    except Exception:
        pass

    # 지지/저항 수준
    sr_levels = {"supports": [], "resistances": []}
    try:
        from src.analysis.support_resistance import find_support_resistance
        prices_for_sr = db.execute(
            select(FactDailyPrice).where(FactDailyPrice.stock_id == stock.stock_id)
            .order_by(FactDailyPrice.date_id).limit(800)
        ).scalars().all()
        if prices_for_sr:
            import pandas as pd
            sr_df = pd.DataFrame([{"close": float(p.close), "high": float(p.high), "low": float(p.low)} for p in prices_for_sr])
            sr = find_support_resistance(sr_df)
            sr_levels["supports"] = [{"price": s.price, "strength": s.strength} for s in sr.supports]
            sr_levels["resistances"] = [{"price": r.price, "strength": r.strength} for r in sr.resistances]
    except Exception:
        pass

    stock_data = {
        "ticker": stock.ticker,
        "name": stock.name,
        "name_kr": stock.name_kr or get_kr_name(stock.ticker, stock.name),
        "sector": stock.sector.sector_name if stock.sector else "기타",
        "price": float(latest_price.close) if latest_price else None,
        "per": float(val.per) if val and val.per else None,
        "pbr": float(val.pbr) if val and val.pbr else None,
        "roe": float(val.roe) if val and val.roe else None,
        "dividend_yield": float(val.dividend_yield) if val and val.dividend_yield else None,
        "debt_ratio": float(val.debt_ratio) if val and val.debt_ratio else None,
        "market_cap": float(val.market_cap) if val and val.market_cap else None,
    }

    return templates.TemplateResponse("stock.html", {
        "request": request,
        "stock": stock_data,
        "rec_history": rec_history,
        "news": news,
        "smart_money": smart_money,
        "signals": signals,
        "explanation": explanation,
        "sr_levels": sr_levels,
        "error": None,
    })


@router.get("/api/stock/{ticker}/prices")
def stock_prices(ticker: str, days: int = Query(default=1095), db: Session = Depends(get_db)):
    """종목 OHLCV + SMA 데이터."""
    stock = db.execute(
        select(DimStock).where(DimStock.ticker == ticker.upper())
    ).scalar_one_or_none()
    if not stock:
        return {"dates": [], "open": [], "high": [], "low": [], "close": [], "volume": [], "sma5": [], "sma20": [], "sma60": []}

    cutoff_id = date_to_id(date.today() - timedelta(days=days))

    # OHLCV
    prices = db.execute(
        select(FactDailyPrice).where(FactDailyPrice.stock_id == stock.stock_id)
        .where(FactDailyPrice.date_id >= cutoff_id).order_by(FactDailyPrice.date_id)
    ).scalars().all()

    # SMA 지표
    sma_codes = {"SMA_5": "sma5", "SMA_20": "sma20", "SMA_60": "sma60"}
    type_map = {}
    for row in db.execute(select(DimIndicatorType.indicator_type_id, DimIndicatorType.code)).all():
        if row[1] in sma_codes:
            type_map[row[0]] = sma_codes[row[1]]

    indicators = db.execute(
        select(FactIndicatorValue).where(FactIndicatorValue.stock_id == stock.stock_id)
        .where(FactIndicatorValue.date_id >= cutoff_id)
        .where(FactIndicatorValue.indicator_type_id.in_(type_map.keys()))
        .order_by(FactIndicatorValue.date_id)
    ).scalars().all()

    # date_id별 SMA 매핑
    sma_by_date: dict[int, dict[str, float]] = {}
    for ind in indicators:
        key = type_map.get(ind.indicator_type_id)
        if key:
            sma_by_date.setdefault(ind.date_id, {})[key] = float(ind.value)

    dates, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    sma5, sma20, sma60 = [], [], []

    for p in prices:
        try:
            dates.append(id_to_date(p.date_id).isoformat())
        except Exception:
            continue
        opens.append(float(p.open))
        highs.append(float(p.high))
        lows.append(float(p.low))
        closes.append(float(p.close))
        volumes.append(int(p.volume))
        sma = sma_by_date.get(p.date_id, {})
        sma5.append(sma.get("sma5"))
        sma20.append(sma.get("sma20"))
        sma60.append(sma.get("sma60"))

    return {
        "dates": dates, "open": opens, "high": highs, "low": lows,
        "close": closes, "volume": volumes,
        "sma5": sma5, "sma20": sma20, "sma60": sma60,
    }


@router.get("/stock/search")
def stock_search(q: str, request: Request):
    """종목 검색 → 리다이렉트."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/stock/{q.strip().upper()}")
