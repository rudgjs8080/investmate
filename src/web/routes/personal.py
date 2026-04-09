"""개인 분석 (Deep Dive) 라우트 — /personal."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import id_to_date
from src.db.models import DimStock, FactDailyPrice
from src.db.repository import DeepDiveRepository, WatchlistRepository
from src.web.deps import get_db

logger = logging.getLogger(__name__)

_GRADE_SCORES = {
    "A": 9, "B": 7, "C": 5, "D": 3, "F": 1,
    "Cheap": 9, "Fair": 6, "Rich": 3, "Extreme": 1,
    "Bullish": 9, "Neutral": 5, "Bearish": 2,
    "Accumulation": 9, "Distribution": 2,
    "Positive": 9, "Negative": 2,
    "Favorable": 9, "Headwind": 2,
}

router = APIRouter(tags=["personal"])


@router.get("/personal")
def personal_dashboard(request: Request, db: Session = Depends(get_db)):
    """워치리스트 카드 그리드 페이지."""
    templates = request.app.state.templates

    # 1. 워치리스트 조회
    watchlist = WatchlistRepository.get_active(db)
    holdings = WatchlistRepository.get_all_holdings(db)

    # 2. 최신 보고서
    reports = DeepDiveRepository.get_latest_reports_all(db)
    report_map = {r.ticker: r for r in reports}

    # 2.5. 오늘 변경 건수 (카드 배지용)
    from src.db.helpers import date_to_id
    from datetime import date as date_type
    from collections import Counter

    today_date_id = date_to_id(date_type.today())
    today_changes = DeepDiveRepository.get_changes_by_date(db, today_date_id)
    change_counts: dict[str, int] = Counter(c.ticker for c in today_changes)

    # 3. 종목 정보 배치 로드
    tickers = [w.ticker for w in watchlist]
    stocks = {}
    if tickers:
        rows = db.execute(
            select(DimStock).where(DimStock.ticker.in_(tickers))
        ).scalars().all()
        stocks = {s.ticker: s for s in rows}

    # 4. 최신 가격 배치 로드
    stock_ids = [s.stock_id for s in stocks.values()] if stocks else []
    prices = {}
    if stock_ids:
        for sid in stock_ids:
            price_rows = list(
                db.execute(
                    select(FactDailyPrice)
                    .where(FactDailyPrice.stock_id == sid)
                    .order_by(FactDailyPrice.date_id.desc())
                    .limit(2)
                ).scalars().all()
            )
            if price_rows:
                stock = next((s for s in stocks.values() if s.stock_id == sid), None)
                if stock:
                    current = float(price_rows[0].close)
                    prev = float(price_rows[1].close) if len(price_rows) > 1 else current
                    change = ((current - prev) / prev * 100) if prev > 0 else 0.0
                    prices[stock.ticker] = {"current": current, "change": round(change, 2)}

    # 5. 카드 데이터 조합
    cards = []
    for w in watchlist:
        stock = stocks.get(w.ticker)
        report = report_map.get(w.ticker)
        holding = holdings.get(w.ticker)
        price_info = prices.get(w.ticker, {"current": 0, "change": 0})

        card = {
            "ticker": w.ticker,
            "name": stock.name if stock else w.ticker,
            "current_price": price_info["current"],
            "daily_change": price_info["change"],
            "has_report": report is not None,
            "action_grade": report.action_grade if report else None,
            "conviction": report.conviction if report else None,
            "uncertainty": report.uncertainty if report else None,
            "has_holding": holding is not None,
            "holding_shares": holding.shares if holding else None,
            "holding_avg_cost": float(holding.avg_cost) if holding else None,
            "holding_pnl_pct": None,
            "holding_pnl_amount": None,
            "change_count": change_counts.get(w.ticker, 0),
        }

        # P&L 계산
        if holding and price_info["current"] > 0:
            avg = float(holding.avg_cost)
            if avg > 0:
                card["holding_pnl_pct"] = round(
                    (price_info["current"] - avg) / avg * 100, 2,
                )
                card["holding_pnl_amount"] = round(
                    (price_info["current"] - avg) * holding.shares, 2,
                )

        cards.append(card)

    return templates.TemplateResponse(
        "personal.html",
        {
            "request": request,
            "current_path": "/personal",
            "cards": cards,
            "stock_count": len(cards),
        },
    )


@router.get("/personal/forecasts")
def personal_forecasts(request: Request, db: Session = Depends(get_db)):
    """예측 정확도 리더보드."""
    from src.deepdive.forecast_evaluator import compute_accuracy_scores

    templates = request.app.state.templates

    forecasts = DeepDiveRepository.get_all_evaluated_forecasts(db)
    accuracy_scores = compute_accuracy_scores(forecasts)
    accuracy_scores.sort(key=lambda a: a.overall_score, reverse=True)

    chart_data = [
        {
            "ticker": a.ticker,
            "hit_rate": round(a.hit_rate * 100, 1),
            "direction": round(a.direction_accuracy * 100, 1),
            "overall": round(a.overall_score * 100, 1),
        }
        for a in accuracy_scores
    ]

    # 시나리오별 요약
    scenario_summary: dict[str, dict] = {}
    for f in forecasts:
        s = f.scenario
        if s not in scenario_summary:
            scenario_summary[s] = {"count": 0, "hits": 0, "prob_sum": 0.0}
        scenario_summary[s]["count"] += 1
        if f.hit_range is True:
            scenario_summary[s]["hits"] += 1
        if f.probability is not None:
            scenario_summary[s]["prob_sum"] += float(f.probability)

    return templates.TemplateResponse(
        "personal_forecasts.html",
        {
            "request": request,
            "current_path": "/personal/forecasts",
            "accuracy_scores": accuracy_scores,
            "chart_data": json.dumps(chart_data, ensure_ascii=False),
            "scenario_summary": scenario_summary,
        },
    )


@router.get("/personal/{ticker}")
def personal_detail(ticker: str, request: Request, db: Session = Depends(get_db)):
    """종목 상세 분석 페이지 — 6레이어 + 토론 + 시나리오."""
    templates = request.app.state.templates
    ticker = ticker.upper()

    stock = db.execute(
        select(DimStock).where(DimStock.ticker == ticker)
    ).scalar_one_or_none()

    report = None
    layers_data = {}
    forecasts = []
    scenario_chart = "{}"
    radar_data = "{}"
    current_price = 0.0
    daily_change = 0.0

    if stock:
        report = DeepDiveRepository.get_latest_report(db, stock.stock_id)

        if report:
            try:
                rd = json.loads(report.report_json)
                layers_data = rd.get("layers", {})
            except (json.JSONDecodeError, TypeError):
                pass

            forecasts = DeepDiveRepository.get_forecasts_by_report(db, report.report_id)

        # 가격
        price_rows = list(
            db.execute(
                select(FactDailyPrice)
                .where(FactDailyPrice.stock_id == stock.stock_id)
                .order_by(FactDailyPrice.date_id.desc())
                .limit(2)
            ).scalars().all()
        )
        if price_rows:
            current_price = float(price_rows[0].close)
            if len(price_rows) > 1:
                prev = float(price_rows[1].close)
                daily_change = round(((current_price - prev) / prev * 100) if prev > 0 else 0.0, 2)

        scenario_chart = json.dumps(_build_scenario_chart(forecasts, current_price), ensure_ascii=False)
        radar_data = json.dumps(_build_radar(layers_data), ensure_ascii=False)

    holding = WatchlistRepository.get_holding(db, ticker)
    holding_pnl_pct = None
    if holding and current_price > 0 and float(holding.avg_cost) > 0:
        holding_pnl_pct = round((current_price - float(holding.avg_cost)) / float(holding.avg_cost) * 100, 2)

    # 변경사항 (오늘)
    changes = DeepDiveRepository.get_changes_by_ticker(db, ticker, limit=10)

    return templates.TemplateResponse(
        "personal_detail.html",
        {
            "request": request,
            "current_path": f"/personal/{ticker}",
            "ticker": ticker,
            "stock": stock,
            "report": report,
            "layers": layers_data,
            "forecasts": forecasts,
            "scenario_chart": scenario_chart,
            "radar_data": radar_data,
            "holding": holding,
            "holding_pnl_pct": holding_pnl_pct,
            "current_price": current_price,
            "daily_change": daily_change,
            "changes": changes,
        },
    )


@router.get("/personal/{ticker}/history")
def personal_history(ticker: str, request: Request, db: Session = Depends(get_db)):
    """과거 분석 회고 페이지."""
    templates = request.app.state.templates
    ticker = ticker.upper()

    stock = db.execute(
        select(DimStock).where(DimStock.ticker == ticker)
    ).scalar_one_or_none()

    reports = DeepDiveRepository.get_reports_by_ticker(db, ticker, limit=60)
    actions = DeepDiveRepository.get_actions_by_ticker(db, ticker, limit=60)
    changes = DeepDiveRepository.get_changes_by_ticker(db, ticker, limit=60)
    evaluated_forecasts = DeepDiveRepository.get_evaluated_forecasts_by_ticker(db, ticker)

    # 타임라인 차트 데이터 (시간순)
    actions_reversed = list(reversed(actions))
    timeline_data = {
        "dates": [id_to_date(a.date_id).isoformat() for a in actions_reversed],
        "convictions": [a.conviction for a in actions_reversed],
        "grades": [a.action_grade for a in actions_reversed],
    } if actions_reversed else None

    return templates.TemplateResponse(
        "personal_history.html",
        {
            "request": request,
            "current_path": f"/personal/{ticker}/history",
            "ticker": ticker,
            "stock": stock,
            "reports": reports,
            "actions": actions,
            "changes": changes,
            "evaluated_forecasts": evaluated_forecasts,
            "timeline_data": json.dumps(timeline_data, ensure_ascii=False) if timeline_data else None,
            "id_to_date": id_to_date,
        },
    )


def _build_scenario_chart(forecasts, current_price: float) -> dict:
    if not forecasts:
        return {}
    horizons = []
    for h in ("1M", "3M", "6M"):
        h_forecasts = [f for f in forecasts if f.horizon == h]
        if not h_forecasts:
            continue
        entry = {"label": h}
        for f in h_forecasts:
            key = f.scenario.lower()
            entry[key] = {"low": float(f.price_low), "high": float(f.price_high), "prob": float(f.probability)}
        horizons.append(entry)
    return {"currentPrice": current_price, "horizons": horizons}


def _build_radar(layers: dict) -> dict:
    mapping = [
        ("layer1", "fundamental", "health_grade"),
        ("layer2", "valuation", "valuation_grade"),
        ("layer3", "technical", "technical_grade"),
        ("layer4", "flow", "flow_grade"),
        ("layer5", "narrative", "narrative_grade"),
        ("layer6", "macro", "macro_grade"),
    ]
    result = {}
    for layer_key, score_key, grade_attr in mapping:
        layer = layers.get(layer_key)
        if isinstance(layer, dict):
            grade = layer.get(grade_attr, "Neutral")
        else:
            grade = "Neutral"
        result[score_key] = _GRADE_SCORES.get(grade, 5)
    return result
