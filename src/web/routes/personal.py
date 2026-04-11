"""개인 분석 (Deep Dive) 라우트 — /personal."""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass
from datetime import date as date_type

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import id_to_date
from src.db.models import DimStock, FactDailyPrice
from src.db.repository import AlertRepository, DeepDiveRepository, WatchlistRepository
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

        # Phase 5: report_json에서 execution_guide 추출
        guide = None
        if report:
            try:
                rd = json.loads(report.report_json)
                guide = rd.get("execution_guide")
            except (json.JSONDecodeError, TypeError):
                pass

        ev_3m = None
        rr = None
        rr_label = None
        entry_distance_pct = None
        action_hint = None
        if guide:
            ev_values = guide.get("expected_value_pct") or {}
            ev_3m = ev_values.get("3M")
            rr = guide.get("risk_reward_ratio")
            rr_label = guide.get("risk_reward_label")
            action_hint = guide.get("action_hint")
            # 진입 존 대비 현재가 거리
            bz_low = guide.get("buy_zone_low")
            bz_high = guide.get("buy_zone_high")
            cur = price_info["current"]
            if cur and bz_low and bz_high:
                if bz_low <= cur <= bz_high:
                    entry_distance_pct = 0.0
                elif cur < bz_low:
                    entry_distance_pct = round((cur - bz_low) / bz_low * 100, 1)
                else:
                    entry_distance_pct = round((cur - bz_high) / bz_high * 100, 1)

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
            "ev_3m": ev_3m,
            "rr": rr,
            "rr_label": rr_label,
            "entry_distance_pct": entry_distance_pct,
            "action_hint": action_hint,
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

    # Phase 11c: 리밸런싱 제안 계산 (보유 종목이 있을 때만)
    rebalance_plan = _build_rebalance_plan_for_dashboard(
        holdings, stocks, prices, report_map, db,
    )

    return templates.TemplateResponse(
        "personal.html",
        {
            "request": request,
            "current_path": "/personal",
            "cards": cards,
            "stock_count": len(cards),
            "rebalance_plan": rebalance_plan,
        },
    )


def _build_rebalance_plan_for_dashboard(
    holdings_map,
    stocks_map,
    prices_map,
    report_map,
    db: Session,
):
    """Phase 11c: /personal 대시보드용 리밸런싱 플랜.

    현재 보유 종목 기준으로 ExecutionGuide들을 모아 순수 함수에 전달.
    """
    if not holdings_map:
        return None
    try:
        from src.config import get_settings
        from src.db.models import DimSector
        from src.deepdive.rebalance_advisor import Holding, build_rebalance_plan

        settings = get_settings()
        holdings_list: list[Holding] = []
        guides: dict = {}

        for ticker, holding in holdings_map.items():
            stock = stocks_map.get(ticker)
            if stock is None:
                continue
            price_info = prices_map.get(ticker)
            if not price_info or price_info.get("current", 0) <= 0:
                continue

            sector_name = None
            if stock.sector_id:
                sector = db.get(DimSector, stock.sector_id)
                sector_name = sector.name if sector else None

            holdings_list.append(
                Holding(
                    ticker=ticker,
                    shares=float(holding.shares),
                    avg_cost=float(holding.avg_cost),
                    current_price=float(price_info["current"]),
                    sector=sector_name,
                )
            )

            # 가이드 추출
            report = report_map.get(ticker)
            if report is None:
                continue
            try:
                rd = json.loads(report.report_json)
                guide_dict = rd.get("execution_guide")
                if guide_dict is None:
                    continue
                guides[ticker] = _GuideFromDict(
                    suggested_position_pct=float(
                        guide_dict.get("suggested_position_pct", 0.0),
                    ),
                    expected_value_pct=guide_dict.get("expected_value_pct") or {},
                    risk_reward_ratio=guide_dict.get("risk_reward_ratio"),
                    portfolio_fit_warnings=tuple(
                        guide_dict.get("portfolio_fit_warnings") or ()
                    ),
                )
            except (json.JSONDecodeError, TypeError):
                continue

        if not holdings_list:
            return None

        plan = build_rebalance_plan(
            holdings_list,
            guides,
            max_sector_weight=float(settings.max_sector_weight_pct),
            max_single_stock_pct=float(settings.max_single_stock_pct),
            tx_cost_bps=float(settings.transaction_cost_bps),
        )

        if not plan.suggestions:
            return None

        return {
            "suggestions": [
                {
                    "ticker": s.ticker,
                    "current_pct": round(s.current_weight * 100, 1),
                    "target_pct": round(s.target_weight * 100, 1),
                    "delta_pct": s.delta_pct,
                    "delta_shares": s.delta_shares,
                    "delta_dollar": s.delta_dollar,
                    "net_ev_pct": s.net_ev_pct,
                    "rationale": s.rationale,
                }
                for s in plan.suggestions
            ],
            "total_turnover_pct": plan.total_turnover_pct,
            "cash_weight_pct": round(plan.cash_weight_after * 100, 1),
            "blocked_sectors": list(plan.blocked_by_sector_cap),
            "warnings": list(plan.warnings),
        }
    except Exception as e:  # pragma: no cover - 로그 후 세션 연속
        import logging

        logging.getLogger(__name__).warning("리밸런싱 플랜 계산 실패: %s", e)
        return None


@dataclass
class _GuideFromDict:
    suggested_position_pct: float
    expected_value_pct: dict
    risk_reward_ratio: float | None
    portfolio_fit_warnings: tuple


# ────────────────────────────────────────────────────────────────────────
# Phase 12d: 종목 직접 비교 — /personal/compare
# /personal/{ticker} 캐치올보다 먼저 등록되어야 함.
# ────────────────────────────────────────────────────────────────────────


_COMPARE_MAX = 4
_COMPARE_ROWS = [
    ("action_grade", "AI 액션", "text"),
    ("conviction", "확신도", "int10"),
    ("uncertainty", "불확실도", "text"),
    ("ev_3m", "3M EV", "pct"),
    ("rr", "R/R", "ratio"),
    ("entry_distance", "진입존 거리", "pct"),
    ("suggested_position", "제안 비중", "pct"),
    ("layer1", "Layer1 펀더", "grade"),
    ("layer2", "Layer2 밸류", "grade"),
    ("layer3", "Layer3 기술", "grade"),
    ("layer4", "Layer4 수급", "grade"),
    ("layer5", "Layer5 내러", "grade"),
    ("layer6", "Layer6 매크로", "grade"),
]


@router.get("/personal/compare")
def personal_compare(
    request: Request,
    tickers: str = "",
    db: Session = Depends(get_db),
):
    """선택한 2~4개 종목의 6레이어/AI/EV를 사이드 바이 사이드 비교."""
    templates = request.app.state.templates

    watchlist_all = WatchlistRepository.get_active(db)
    watchlist_tickers = [w.ticker for w in watchlist_all]

    raw = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    raw = list(dict.fromkeys(raw))[:_COMPARE_MAX]  # dedup + cap

    columns: list[dict] = []
    for ticker in raw:
        validated = _validate_ticker(ticker)
        if validated is None:
            continue
        stock = db.execute(
            select(DimStock).where(DimStock.ticker == validated)
        ).scalar_one_or_none()
        if stock is None:
            columns.append({
                "ticker": validated,
                "name": validated,
                "missing": True,
                "metrics": {},
                "radar": {},
            })
            continue

        report = DeepDiveRepository.get_latest_report(db, stock.stock_id)
        metrics, radar = _extract_compare_metrics(report)

        # 현재가 (1일치)
        price_row = db.execute(
            select(FactDailyPrice)
            .where(FactDailyPrice.stock_id == stock.stock_id)
            .order_by(FactDailyPrice.date_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        current_price = float(price_row.close) if price_row else 0.0

        # 진입존 거리 계산
        if report:
            try:
                rd = json.loads(report.report_json)
                guide = rd.get("execution_guide") or {}
                bz_low = guide.get("buy_zone_low")
                bz_high = guide.get("buy_zone_high")
                if current_price > 0 and bz_low and bz_high:
                    if bz_low <= current_price <= bz_high:
                        metrics["entry_distance"] = 0.0
                    elif current_price < bz_low:
                        metrics["entry_distance"] = round(
                            (current_price - bz_low) / bz_low * 100, 2,
                        )
                    else:
                        metrics["entry_distance"] = round(
                            (current_price - bz_high) / bz_high * 100, 2,
                        )
            except (json.JSONDecodeError, TypeError):
                pass

        columns.append({
            "ticker": validated,
            "name": stock.name,
            "current_price": current_price,
            "missing": report is None,
            "metrics": metrics,
            "radar": radar,
        })

    # 각 행에 대해 최고/최저 셀 표시용 우열 계산
    best_worst = _compute_best_worst(columns)

    radar_data = {
        col["ticker"]: col["radar"] for col in columns if col.get("radar")
    }

    return templates.TemplateResponse(
        "personal_compare.html",
        {
            "request": request,
            "current_path": "/personal/compare",
            "columns": columns,
            "rows": _COMPARE_ROWS,
            "best_worst": best_worst,
            "watchlist_tickers": watchlist_tickers,
            "selected_tickers": raw,
            "radar_data": json.dumps(radar_data, ensure_ascii=False),
        },
    )


def _extract_compare_metrics(report) -> tuple[dict, dict]:
    """FactDeepDiveReport → 비교용 metrics dict + radar dict."""
    metrics: dict = {}
    radar: dict = {}
    if report is None:
        return metrics, radar
    try:
        rd = json.loads(report.report_json)
    except (json.JSONDecodeError, TypeError):
        return metrics, radar

    ai = rd.get("ai_result") or {}
    guide = rd.get("execution_guide") or {}
    layers = rd.get("layers") or {}

    metrics["action_grade"] = report.action_grade
    metrics["conviction"] = report.conviction
    metrics["uncertainty"] = report.uncertainty
    metrics["ev_3m"] = (guide.get("expected_value_pct") or {}).get("3M")
    metrics["rr"] = guide.get("risk_reward_ratio")
    metrics["suggested_position"] = guide.get("suggested_position_pct")

    # 6레이어 그레이드 추출
    layer_grades = [
        ("layer1", "health_grade"),
        ("layer2", "valuation_grade"),
        ("layer3", "technical_grade"),
        ("layer4", "flow_grade"),
        ("layer5", "narrative_grade"),
        ("layer6", "macro_grade"),
    ]
    for key, grade_attr in layer_grades:
        layer = layers.get(key) or {}
        grade = layer.get(grade_attr) if isinstance(layer, dict) else None
        metrics[key] = grade or "-"
        radar[key] = _GRADE_SCORES.get(grade, 5)

    return metrics, radar


def _compute_best_worst(columns: list[dict]) -> dict:
    """각 행에 대해 최고/최저 컬럼 인덱스 (색상 강조용)."""
    result: dict = {}
    if len(columns) < 2:
        return result

    for row_key, _label, row_type in _COMPARE_ROWS:
        values: list[tuple[int, float | None]] = []
        for i, col in enumerate(columns):
            raw = col["metrics"].get(row_key)
            score: float | None = None
            if row_type in ("pct", "ratio", "int10"):
                if isinstance(raw, (int, float)):
                    score = float(raw)
            elif row_type == "grade":
                score = float(_GRADE_SCORES.get(raw, 5)) if raw else None
            elif row_type == "text" and row_key == "action_grade":
                score = {"ADD": 3, "HOLD": 2, "TRIM": 1, "EXIT": 0}.get(raw)
            values.append((i, score))

        valid = [(i, v) for i, v in values if v is not None]
        if len(valid) < 2:
            continue

        # entry_distance는 0에 가까울수록 좋음 → 절대값 반전
        if row_key == "entry_distance":
            sorted_vals = sorted(valid, key=lambda x: abs(x[1]))
            best = sorted_vals[0][0]
            worst = sorted_vals[-1][0]
        else:
            sorted_vals = sorted(valid, key=lambda x: x[1])
            best = sorted_vals[-1][0]
            worst = sorted_vals[0][0]

        if best != worst:
            result[row_key] = {"best": best, "worst": worst}

    return result


# ────────────────────────────────────────────────────────────────────────
# Phase 12b: 알림 센터 — /personal/alerts
# /personal/{ticker} 캐치올보다 먼저 등록되어야 함.
# ────────────────────────────────────────────────────────────────────────


_SEVERITY_ICON = {"critical": "🔴", "warning": "🟡", "info": "🔵"}


@router.get("/personal/alerts")
def personal_alerts_page(
    request: Request,
    days: int = 30,
    severity: str | None = None,
    ack: str | None = None,
    ticker: str | None = None,
    db: Session = Depends(get_db),
):
    """알림 히스토리 페이지 (필터 + 페이지네이션)."""
    templates = request.app.state.templates

    days = max(1, min(days, 90))
    alerts = AlertRepository.get_recent(
        db,
        days=days,
        severity_min=severity if severity in ("critical", "warning", "info") else None,
        ack_filter=ack if ack in ("unread", "read") else None,
        ticker=ticker.upper() if ticker else None,
        limit=200,
    )
    unread_count = AlertRepository.count_unread(db, days=days)

    # 타임라인 그룹화: 오늘/어제/지난주/이전
    from datetime import date as date_type, timedelta

    today = date_type.today()
    groups: dict[str, list] = {"오늘": [], "어제": [], "지난 7일": [], "이전": []}
    for a in alerts:
        d = id_to_date(a.date_id)
        delta = (today - d).days
        if delta <= 0:
            bucket = "오늘"
        elif delta == 1:
            bucket = "어제"
        elif delta <= 7:
            bucket = "지난 7일"
        else:
            bucket = "이전"
        groups[bucket].append({
            "alert_id": a.alert_id,
            "ticker": a.ticker,
            "date": d.isoformat(),
            "trigger_type": a.trigger_type,
            "severity": a.severity,
            "severity_icon": _SEVERITY_ICON.get(a.severity, "•"),
            "message": a.message,
            "current_price": float(a.current_price) if a.current_price else None,
            "acknowledged": bool(a.acknowledged),
        })

    return templates.TemplateResponse(
        "personal_alerts.html",
        {
            "request": request,
            "current_path": "/personal/alerts",
            "groups": groups,
            "total_count": len(alerts),
            "unread_count": unread_count,
            "filter_days": days,
            "filter_severity": severity or "",
            "filter_ack": ack or "",
            "filter_ticker": ticker or "",
        },
    )


@router.get("/personal/alerts/unread-count")
def personal_alerts_unread_count(db: Session = Depends(get_db)):
    """헤더 뱃지용 JSON (htmx/fetch 폴링)."""
    return _ok({"count": AlertRepository.count_unread(db)})


@router.post("/personal/alerts/{alert_id}/ack")
def personal_alerts_ack(alert_id: int, db: Session = Depends(get_db)):
    """단일 알림 확인."""
    if alert_id <= 0:
        return _err("alert_id가 올바르지 않습니다")
    ok = AlertRepository.acknowledge(db, alert_id)
    if not ok:
        return _err(f"alert_id {alert_id}를 찾을 수 없습니다", status=404)
    return _ok({"alert_id": alert_id})


@router.post("/personal/alerts/ack-all")
async def personal_alerts_ack_all(
    request: Request, db: Session = Depends(get_db),
):
    """모든 미확인 알림 확인. body={date_id?} 주어지면 해당 날짜만."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    date_id = body.get("date_id") if isinstance(body, dict) else None
    count = AlertRepository.acknowledge_all(
        db, date_id=int(date_id) if date_id else None,
    )
    return _ok({"acknowledged_count": count})


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
    """종목 상세 분석 페이지 — 실행 가이드 + 6레이어 + 토론 + 시나리오."""
    templates = request.app.state.templates
    ticker = ticker.upper()

    stock = db.execute(
        select(DimStock).where(DimStock.ticker == ticker)
    ).scalar_one_or_none()

    report = None
    layers_data: dict = {}
    forecasts: list = []
    scenario_chart = "{}"
    radar_data = "{}"
    current_price = 0.0
    daily_change = 0.0
    ai_result_data: dict = {}
    execution_guide: dict | None = None
    pair_comparisons: list = []

    if stock:
        report = DeepDiveRepository.get_latest_report(db, stock.stock_id)

        if report:
            try:
                rd = json.loads(report.report_json)
                layers_data = rd.get("layers", {}) or {}
                ai_result_data = rd.get("ai_result", {}) or {}
                execution_guide = rd.get("execution_guide")
                pair_comparisons = rd.get("pair_comparisons", []) or []
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

        scenario_chart = json.dumps(
            _build_scenario_chart(forecasts, current_price, execution_guide),
            ensure_ascii=False,
        )
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
            "ai_result": ai_result_data,
            "execution_guide": execution_guide,
            "pair_comparisons": pair_comparisons,
            "evidence_refs": ai_result_data.get("evidence_refs") or [],
            "invalidation_conditions": ai_result_data.get("invalidation_conditions") or [],
            "next_review_trigger": ai_result_data.get("next_review_trigger"),
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


def _build_scenario_chart(
    forecasts, current_price: float, execution_guide: dict | None = None,
) -> dict:
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

    overlays: dict = {}
    if execution_guide:
        overlays = {
            "buy_zone_low": execution_guide.get("buy_zone_low"),
            "buy_zone_high": execution_guide.get("buy_zone_high"),
            "stop_loss": execution_guide.get("stop_loss"),
            "target_1m": execution_guide.get("target_1m"),
            "target_3m": execution_guide.get("target_3m"),
            "target_6m": execution_guide.get("target_6m"),
        }
    return {"currentPrice": current_price, "horizons": horizons, "overlays": overlays}


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


# ────────────────────────────────────────────────────────────────────────
# Phase 12a: 보유/워치리스트 CRUD API
# 모든 응답은 {success, data?, error?} envelope 형식.
# ────────────────────────────────────────────────────────────────────────


_MAX_TICKER_LEN = 10
_MAX_SHARES = 10_000_000
_MAX_AVG_COST = 1_000_000.0
_CSV_MAX_ROWS = 500


def _ok(data=None, status: int = 200) -> JSONResponse:
    return JSONResponse({"success": True, "data": data}, status_code=status)


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        {"success": False, "error": message}, status_code=status,
    )


def _validate_ticker(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    t = raw.strip().upper()
    if not t or len(t) > _MAX_TICKER_LEN:
        return None
    if not all(c.isalnum() or c in {"-", "."} for c in t):
        return None
    return t


def _parse_opened_at(raw: object) -> date_type | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, date_type):
        return raw
    if isinstance(raw, str):
        try:
            return date_type.fromisoformat(raw.strip())
        except ValueError:
            return None
    return None


@router.post("/personal/watchlist")
async def personal_add_watchlist(
    request: Request, db: Session = Depends(get_db),
):
    """워치리스트에 종목 추가. body={ticker, note?}."""
    try:
        body = await request.json()
    except Exception:
        return _err("잘못된 JSON 형식입니다", status=400)

    ticker = _validate_ticker(body.get("ticker"))
    if ticker is None:
        return _err("올바른 티커를 입력하세요 (영문/숫자, 최대 10자)")

    note_raw = body.get("note")
    note = note_raw.strip() if isinstance(note_raw, str) and note_raw.strip() else None

    # DimStock 미존재 시 자동 등록 (yfinance .info 1회 호출)
    from src.deepdive.watchlist_manager import ensure_stock_registered

    try:
        stock = ensure_stock_registered(db, ticker)
    except Exception as exc:  # pragma: no cover - 네트워크 실패 케이스
        logger.warning("자동 종목 등록 실패 (%s): %s", ticker, exc)
        return _err(f"종목 정보를 가져올 수 없습니다: {ticker}", status=502)

    WatchlistRepository.add_ticker(db, ticker, note=note)
    return _ok({
        "ticker": ticker,
        "name": stock.name,
        "sector_id": stock.sector_id,
        "is_sp500": bool(stock.is_sp500),
        "note": note,
    }, status=201)


@router.delete("/personal/watchlist/{ticker}")
def personal_remove_watchlist(
    ticker: str, db: Session = Depends(get_db),
):
    """워치리스트에서 종목 soft-delete."""
    normalized = _validate_ticker(ticker)
    if normalized is None:
        return _err("올바른 티커를 입력하세요")

    removed = WatchlistRepository.remove_ticker(db, normalized)
    if not removed:
        return _err(f"{normalized}은(는) 워치리스트에 없습니다", status=404)
    # 보유정보도 함께 정리 (사용자 기대 동작: 워치리스트 제거 시 보유도 정리)
    WatchlistRepository.delete_holding(db, normalized)
    return _ok({"ticker": normalized})


@router.post("/personal/holdings")
async def personal_upsert_holding(
    request: Request, db: Session = Depends(get_db),
):
    """보유 정보 UPSERT. body={ticker, shares, avg_cost, opened_at?}."""
    try:
        body = await request.json()
    except Exception:
        return _err("잘못된 JSON 형식입니다", status=400)

    ticker = _validate_ticker(body.get("ticker"))
    if ticker is None:
        return _err("올바른 티커를 입력하세요")

    # 워치리스트에 없는 종목은 먼저 추가해야 함 (명시적 오류)
    watch = WatchlistRepository.get_active(db)
    if not any(w.ticker == ticker for w in watch):
        return _err(
            f"{ticker}은(는) 워치리스트에 없습니다. 먼저 워치리스트에 추가하세요.",
            status=404,
        )

    try:
        shares = int(body.get("shares"))
        avg_cost = float(body.get("avg_cost"))
    except (TypeError, ValueError):
        return _err("shares(정수)와 avg_cost(실수)가 필요합니다")

    if shares <= 0 or shares > _MAX_SHARES:
        return _err(f"shares는 1~{_MAX_SHARES} 범위여야 합니다")
    if avg_cost <= 0 or avg_cost > _MAX_AVG_COST:
        return _err(f"avg_cost는 0~{_MAX_AVG_COST} 범위여야 합니다")

    opened_at = _parse_opened_at(body.get("opened_at"))
    if body.get("opened_at") and opened_at is None:
        return _err("opened_at은 YYYY-MM-DD 형식이어야 합니다")

    holding = WatchlistRepository.set_holding(
        db, ticker, shares=shares, avg_cost=avg_cost, opened_at=opened_at,
    )
    return _ok({
        "ticker": holding.ticker,
        "shares": holding.shares,
        "avg_cost": float(holding.avg_cost),
        "opened_at": holding.opened_at.isoformat() if holding.opened_at else None,
    })


@router.delete("/personal/holdings/{ticker}")
def personal_delete_holding(
    ticker: str, db: Session = Depends(get_db),
):
    """보유 정보 삭제 (워치리스트는 유지)."""
    normalized = _validate_ticker(ticker)
    if normalized is None:
        return _err("올바른 티커를 입력하세요")
    removed = WatchlistRepository.delete_holding(db, normalized)
    if not removed:
        return _err(f"{normalized}의 보유정보가 없습니다", status=404)
    return _ok({"ticker": normalized})


_CSV_TEMPLATE = "ticker,shares,avg_cost,opened_at\nAAPL,100,150.25,2024-01-15\n"


@router.get("/personal/holdings/csv-template")
def personal_holdings_csv_template():
    """빈 CSV 템플릿 다운로드."""
    return PlainTextResponse(
        _CSV_TEMPLATE,
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="holdings_template.csv"',
        },
    )


# ────────────────────────────────────────────────────────────────────────
# Phase 12c: What-if 시뮬레이터
# ────────────────────────────────────────────────────────────────────────


@router.post("/personal/simulate")
async def personal_simulate(
    request: Request, db: Session = Depends(get_db),
):
    """보유 변경을 가상 적용해 before/after 리밸런싱 플랜을 반환."""
    try:
        body = await request.json()
    except Exception:
        return _err("잘못된 JSON 형식입니다", status=400)

    raw_mods = body.get("modifications") or []
    if not isinstance(raw_mods, list) or not raw_mods:
        return _err("modifications 리스트가 필요합니다")

    from src.config import get_settings
    from src.db.models import DimSector
    from src.deepdive.rebalance_advisor import Holding
    from src.deepdive.whatif_simulator import (
        Modification,
        StockInfo,
        simulate_holdings_change,
    )

    settings = get_settings()

    # 1) modifications 파싱/검증
    modifications: list[Modification] = []
    for i, m in enumerate(raw_mods):
        if not isinstance(m, dict):
            return _err(f"modifications[{i}]는 객체여야 합니다")
        ticker = _validate_ticker(m.get("ticker"))
        if ticker is None:
            return _err(f"modifications[{i}].ticker가 올바르지 않습니다")

        shares_raw = m.get("shares")
        delta_raw = m.get("shares_delta")
        if (shares_raw is None) == (delta_raw is None):
            return _err(
                f"modifications[{i}]는 shares 또는 shares_delta 중 정확히 하나여야 합니다",
            )

        try:
            shares = int(shares_raw) if shares_raw is not None else None
            shares_delta = int(delta_raw) if delta_raw is not None else None
        except (TypeError, ValueError):
            return _err(f"modifications[{i}] 숫자 변환 실패")

        try:
            modifications.append(
                Modification(
                    ticker=ticker, shares=shares, shares_delta=shares_delta,
                ),
            )
        except ValueError as exc:
            return _err(f"modifications[{i}]: {exc}")

    # 2) 현재 보유 + universe 구성 (워치리스트 전체 종목의 가격/섹터)
    holdings_map = WatchlistRepository.get_all_holdings(db)
    watchlist = WatchlistRepository.get_active(db)

    stock_tickers = {w.ticker for w in watchlist}
    stock_tickers.update(m.ticker for m in modifications)

    stocks = db.execute(
        select(DimStock).where(DimStock.ticker.in_(stock_tickers))
    ).scalars().all()
    stocks_by_ticker = {s.ticker: s for s in stocks}

    # 가격 배치 로드 (최신 종가)
    prices: dict[str, float] = {}
    for s in stocks:
        row = db.execute(
            select(FactDailyPrice)
            .where(FactDailyPrice.stock_id == s.stock_id)
            .order_by(FactDailyPrice.date_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row:
            prices[s.ticker] = float(row.close)

    # 섹터명 lookup
    def _sector_name(stock) -> str | None:
        if not stock.sector_id:
            return None
        sector = db.get(DimSector, stock.sector_id)
        return sector.sector_name if sector else None

    # 현재 보유 리스트
    current_holdings: list[Holding] = []
    for ticker, holding in holdings_map.items():
        stock = stocks_by_ticker.get(ticker)
        if stock is None:
            continue
        price = prices.get(ticker)
        if price is None or price <= 0:
            continue
        current_holdings.append(
            Holding(
                ticker=ticker,
                shares=float(holding.shares),
                avg_cost=float(holding.avg_cost),
                current_price=price,
                sector=_sector_name(stock),
            )
        )

    # universe (수정 대상이 현재 보유에 없을 때를 위한 가격/섹터 정보)
    universe: dict[str, StockInfo] = {}
    for ticker in stock_tickers:
        stock = stocks_by_ticker.get(ticker)
        if stock is None:
            continue
        price = prices.get(ticker)
        if price is None or price <= 0:
            continue
        universe[ticker] = StockInfo(
            current_price=price, sector=_sector_name(stock),
        )

    # 3) 가이드 구성 (최신 보고서 기반)
    report_map = DeepDiveRepository.get_latest_reports_all(db)
    guides: dict = {}
    for report in report_map:
        try:
            rd = json.loads(report.report_json)
            guide_dict = rd.get("execution_guide")
            if guide_dict is None:
                continue
            guides[report.ticker] = _GuideFromDict(
                suggested_position_pct=float(
                    guide_dict.get("suggested_position_pct", 0.0),
                ),
                expected_value_pct=guide_dict.get("expected_value_pct") or {},
                risk_reward_ratio=guide_dict.get("risk_reward_ratio"),
                portfolio_fit_warnings=tuple(
                    guide_dict.get("portfolio_fit_warnings") or ()
                ),
            )
        except (json.JSONDecodeError, TypeError):
            continue

    # 4) 시뮬레이션 실행
    try:
        result = simulate_holdings_change(
            current=current_holdings,
            modifications=modifications,
            guides=guides,
            universe=universe,
            max_sector_weight=float(settings.max_sector_weight_pct),
            max_single_stock_pct=float(settings.max_single_stock_pct),
            tx_cost_bps=float(settings.transaction_cost_bps),
        )
    except Exception as exc:
        logger.warning("시뮬레이션 실패: %s", exc)
        return _err(f"시뮬레이션 실패: {exc}", status=500)

    # 5) 응답 직렬화
    def _plan_to_dict(plan) -> dict:
        return {
            "suggestions": [
                {
                    "ticker": s.ticker,
                    "current_pct": round(s.current_weight * 100, 2),
                    "target_pct": round(s.target_weight * 100, 2),
                    "delta_pct": s.delta_pct,
                    "delta_shares": s.delta_shares,
                    "delta_dollar": s.delta_dollar,
                    "net_ev_pct": s.net_ev_pct,
                    "rationale": s.rationale,
                }
                for s in plan.suggestions
            ],
            "total_turnover_pct": plan.total_turnover_pct,
            "cash_weight_pct": round(plan.cash_weight_after * 100, 2),
            "blocked_sectors": list(plan.blocked_by_sector_cap),
            "warnings": list(plan.warnings),
        }

    return _ok({
        "before": _plan_to_dict(result.before_plan),
        "after": _plan_to_dict(result.after_plan),
        "before_sector_weights": [
            {"sector": s, "pct": round(w * 100, 2)}
            for s, w in result.before_sector_weights
        ],
        "after_sector_weights": [
            {"sector": s, "pct": round(w * 100, 2)}
            for s, w in result.after_sector_weights
        ],
        "before_total_value": result.before_total_value,
        "after_total_value": result.after_total_value,
        "modified_tickers": list(result.modified_tickers),
        "warnings": list(result.warnings),
        "violations": list(result.violations),
    })


@router.post("/personal/holdings/import")
async def personal_holdings_import(
    file: UploadFile = File(...), db: Session = Depends(get_db),
):
    """CSV 일괄 UPSERT. 헤더: ticker,shares,avg_cost,opened_at"""
    from src.deepdive.watchlist_manager import ensure_stock_registered

    try:
        raw = await file.read()
    except Exception:
        return _err("파일을 읽을 수 없습니다", status=400)
    if len(raw) == 0:
        return _err("빈 파일입니다")
    if len(raw) > 256 * 1024:
        return _err("파일이 너무 큽니다 (최대 256KB)")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _err("UTF-8 인코딩만 지원합니다")

    reader = csv.DictReader(io.StringIO(text))
    required = {"ticker", "shares", "avg_cost"}
    if not required.issubset({h.strip() for h in (reader.fieldnames or [])}):
        return _err(
            "헤더는 ticker,shares,avg_cost[,opened_at] 이어야 합니다",
        )

    imported: list[dict] = []
    errors: list[dict] = []

    for idx, row in enumerate(reader, start=2):  # 2 = 데이터 첫 줄(헤더 다음)
        if idx > _CSV_MAX_ROWS + 1:
            errors.append({
                "line": idx,
                "error": f"최대 {_CSV_MAX_ROWS}행까지만 지원합니다",
            })
            break

        ticker = _validate_ticker(row.get("ticker"))
        if ticker is None:
            errors.append({"line": idx, "error": "잘못된 ticker"})
            continue

        try:
            shares = int(float(row.get("shares") or 0))
            avg_cost = float(row.get("avg_cost") or 0)
        except (TypeError, ValueError):
            errors.append({
                "line": idx, "ticker": ticker,
                "error": "shares/avg_cost 숫자 변환 실패",
            })
            continue

        if shares <= 0 or shares > _MAX_SHARES:
            errors.append({
                "line": idx, "ticker": ticker,
                "error": "shares 범위 오류",
            })
            continue
        if avg_cost <= 0 or avg_cost > _MAX_AVG_COST:
            errors.append({
                "line": idx, "ticker": ticker,
                "error": "avg_cost 범위 오류",
            })
            continue

        opened_at = _parse_opened_at(row.get("opened_at"))

        try:
            ensure_stock_registered(db, ticker)
            WatchlistRepository.add_ticker(db, ticker)
            WatchlistRepository.set_holding(
                db, ticker, shares=shares, avg_cost=avg_cost, opened_at=opened_at,
            )
            imported.append({"ticker": ticker, "shares": shares, "avg_cost": avg_cost})
        except Exception as exc:  # pragma: no cover
            logger.warning("CSV import 실패 %s: %s", ticker, exc)
            errors.append({
                "line": idx, "ticker": ticker,
                "error": f"DB 저장 실패: {exc}",
            })

    return _ok({
        "imported_count": len(imported),
        "error_count": len(errors),
        "imported": imported,
        "errors": errors,
    })
