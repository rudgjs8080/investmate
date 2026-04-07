"""JSON API 라우트 — 차트 데이터 제공."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id, id_to_date
from src.db.models import DimStock, FactDailyPrice, FactDailyRecommendation, FactMacroIndicator
from src.web.deps import get_db

router = APIRouter()


def _pick_best_return(rec: FactDailyRecommendation) -> float | None:
    """가용한 최장 기간 수익률을 반환한다 (20d→10d→5d→1d fallback)."""
    for attr in ("return_20d", "return_10d", "return_5d", "return_1d"):
        val = getattr(rec, attr, None)
        if val is not None:
            return float(val)
    return None


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    """서비스 상태 확인."""
    try:
        stock_count = db.execute(select(func.count(DimStock.stock_id))).scalar_one()
        latest_date = db.execute(select(func.max(FactDailyPrice.date_id))).scalar_one()
    except Exception:
        return JSONResponse(status_code=503, content={"status": "unhealthy"})

    return {
        "status": "ok",
        "stocks": stock_count,
        "latest_data": latest_date,
        "timestamp": datetime.now().isoformat(),
    }


# ──────────────────────────────────────────
# Equity Curve (포트폴리오 가중 + 벤치마크)
# ──────────────────────────────────────────


@router.get("/equity-curve")
def equity_curve(days: int = Query(default=365, ge=1, le=3650), db: Session = Depends(get_db)):
    """포트폴리오 가중 누적 수익률 + S&P 500 벤치마크.

    1d 수익률 기반 복리 누적. position_weight 없으면 동일 가중 fallback.
    """
    cutoff_id = date_to_id(date.today() - timedelta(days=days))

    recs = db.execute(
        select(FactDailyRecommendation)
        .where(FactDailyRecommendation.run_date_id >= cutoff_id)
        .order_by(FactDailyRecommendation.run_date_id)
    ).scalars().all()

    # 일별 가중 포트폴리오 수익률
    by_date: dict[int, list[tuple[float, float | None]]] = {}
    for r in recs:
        if r.return_1d is None:
            continue
        ret = float(r.return_1d)
        w = float(r.position_weight) if r.position_weight is not None and float(r.position_weight) > 0 else None
        by_date.setdefault(r.run_date_id, []).append((ret, w))

    labels: list[str] = []
    portfolio_values: list[float] = []
    daily_rets: list[float] = []
    weighted = False
    cumulative = 1.0

    for did in sorted(by_date):
        pairs = by_date[did]
        has_weights = all(w is not None for _, w in pairs)

        if has_weights:
            total_w = sum(w for _, w in pairs)
            if total_w > 0:
                avg_ret = sum(ret * w / total_w for ret, w in pairs)
                weighted = True
            else:
                avg_ret = sum(ret for ret, _ in pairs) / len(pairs)
        else:
            avg_ret = sum(ret for ret, _ in pairs) / len(pairs)

        daily_rets.append(avg_ret)
        cumulative *= (1 + avg_ret / 100)
        try:
            labels.append(id_to_date(did).isoformat())
        except Exception:
            labels.append(str(did))
        portfolio_values.append(round((cumulative - 1) * 100, 2))

    # S&P 500 벤치마크 시리즈
    benchmark_values = _build_sp500_equity_curve(db, cutoff_id, labels)

    return {
        "labels": labels,
        "values": portfolio_values,
        "benchmark": benchmark_values,
        "period": "1일 (복리)",
        "weighted": weighted,
    }


def _build_sp500_equity_curve(
    db: Session, cutoff_id: int, target_labels: list[str],
) -> list[float | None]:
    """S&P 500 누적 수익률 시리즈를 포트폴리오 날짜에 맞춰 반환."""
    if not target_labels:
        return []

    macros = db.execute(
        select(FactMacroIndicator.date_id, FactMacroIndicator.sp500_close)
        .where(
            FactMacroIndicator.date_id >= cutoff_id - 5,
            FactMacroIndicator.sp500_close.isnot(None),
        )
        .order_by(FactMacroIndicator.date_id)
    ).all()

    if len(macros) < 2:
        return [None] * len(target_labels)

    sp_map: dict[str, float] = {}
    prev_close = float(macros[0][1])
    cumulative = 1.0

    for did, close in macros[1:]:
        close_f = float(close)
        if prev_close > 0:
            daily_ret = (close_f / prev_close - 1)
            cumulative *= (1 + daily_ret)
        prev_close = close_f
        try:
            sp_map[id_to_date(did).isoformat()] = round((cumulative - 1) * 100, 2)
        except Exception:
            pass

    # 포트폴리오 날짜에 맞춰 매핑 (없으면 이전 값 forward fill)
    result: list[float | None] = []
    last_val: float | None = None
    for label in target_labels:
        val = sp_map.get(label)
        if val is not None:
            last_val = val
        result.append(last_val)

    return result


# ──────────────────────────────────────────
# 리스크 지표 API
# ──────────────────────────────────────────


@router.get("/risk-metrics")
def risk_metrics(days: int = Query(default=365, ge=1, le=3650), db: Session = Depends(get_db)):
    """포트폴리오 리스크 조정 지표 (Sharpe, Sortino, MDD, Calmar, Omega)."""
    from src.analysis.performance import calculate_performance

    report = calculate_performance(db, days=days)
    return {
        "sharpe": report.sharpe_ratio,
        "sortino": report.sortino_ratio,
        "max_drawdown": round(report.max_drawdown, 2) if report.max_drawdown is not None else None,
        "calmar": report.calmar_ratio,
        "omega": report.omega_ratio,
        "benchmark_cumulative": report.benchmark_return_cumulative,
        "excess_cumulative": report.excess_return_cumulative,
        "information_ratio": report.information_ratio,
        "total_recommendations": report.total_recommendations,
        "days": days,
    }


# ──────────────────────────────────────────
# 실행 비용 투명화 API
# ──────────────────────────────────────────


@router.get("/cost-transparency")
def cost_transparency(days: int = Query(default=365, ge=1, le=3650), db: Session = Depends(get_db)):
    """실행 비용 분해 (스프레드 / 시장충격 / 총비용)."""
    cutoff_id = date_to_id(date.today() - timedelta(days=days))

    recs = db.execute(
        select(FactDailyRecommendation)
        .where(FactDailyRecommendation.run_date_id >= cutoff_id)
    ).scalars().all()

    spread_vals = [float(r.spread_cost_bps) for r in recs if r.spread_cost_bps is not None]
    impact_vals = [float(r.impact_cost_bps) for r in recs if r.impact_cost_bps is not None]
    total_vals = [float(r.total_cost_bps) for r in recs if r.total_cost_bps is not None]

    def _avg(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 2) if vals else None

    # 일별 gross vs net
    by_date: list[dict] = []
    date_groups: dict[int, list[FactDailyRecommendation]] = {}
    for r in recs:
        date_groups.setdefault(r.run_date_id, []).append(r)

    for did in sorted(date_groups)[-30:]:  # 최근 30일
        group = date_groups[did]
        gross_rets = [float(r.return_1d) + (float(r.total_cost_bps) / 100 if r.total_cost_bps else 0.2)
                      for r in group if r.return_1d is not None]
        net_rets = [float(r.return_1d) for r in group if r.return_1d is not None]
        if gross_rets and net_rets:
            try:
                by_date.append({
                    "date": id_to_date(did).isoformat(),
                    "gross": round(sum(gross_rets) / len(gross_rets), 3),
                    "net": round(sum(net_rets) / len(net_rets), 3),
                })
            except Exception:
                pass

    return {
        "avg_spread_bps": _avg(spread_vals),
        "avg_impact_bps": _avg(impact_vals),
        "avg_total_bps": _avg(total_vals),
        "sample_count": len(total_vals),
        "by_date": by_date,
    }


# ──────────────────────────────────────────
# 미실현 P&L API
# ──────────────────────────────────────────


@router.get("/unrealized-pnl")
def unrealized_pnl(db: Session = Depends(get_db)):
    """최근 추천(20 거래일 이내)의 현재 미실현 P&L."""
    cutoff_id = date_to_id(date.today() - timedelta(days=30))

    recs = db.execute(
        select(FactDailyRecommendation, DimStock.ticker)
        .join(DimStock, FactDailyRecommendation.stock_id == DimStock.stock_id)
        .where(
            FactDailyRecommendation.run_date_id >= cutoff_id,
            FactDailyRecommendation.return_20d.is_(None),  # 아직 20d 미경과
        )
        .order_by(FactDailyRecommendation.run_date_id.desc())
    ).all()

    if not recs:
        return {"positions": [], "total_pnl": None, "count": 0}

    # 각 종목의 최신 종가
    stock_ids = {r.stock_id for r, _ in recs}
    latest_prices: dict[int, float] = {}
    for sid in stock_ids:
        row = db.execute(
            select(FactDailyPrice.adj_close)
            .where(FactDailyPrice.stock_id == sid)
            .order_by(FactDailyPrice.date_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            latest_prices[sid] = float(row)

    positions = []
    weighted_pnl_sum = 0.0
    total_weight = 0.0

    for r, ticker in recs:
        base = float(r.execution_price) if r.execution_price else float(r.price_at_recommendation)
        if base <= 0:
            continue
        current = latest_prices.get(r.stock_id)
        if current is None:
            continue

        pnl_pct = round((current / base - 1) * 100, 2)
        w = float(r.position_weight) if r.position_weight and float(r.position_weight) > 0 else None

        try:
            rec_date = id_to_date(r.run_date_id).isoformat()
        except Exception:
            rec_date = str(r.run_date_id)

        hold_days = (date.today() - id_to_date(r.run_date_id)).days if r.run_date_id else None

        positions.append({
            "ticker": ticker,
            "entry_price": round(base, 2),
            "current_price": round(current, 2),
            "pnl_pct": pnl_pct,
            "position_weight": round(w, 4) if w else None,
            "rec_date": rec_date,
            "hold_days": hold_days,
            "ai_stop_loss": round(float(r.ai_stop_loss), 2) if r.ai_stop_loss else None,
            "ai_target_price": round(float(r.ai_target_price), 2) if r.ai_target_price else None,
        })

        if w is not None:
            weighted_pnl_sum += pnl_pct * w
            total_weight += w

    total_pnl = round(weighted_pnl_sum / total_weight, 2) if total_weight > 0 else None

    return {"positions": positions, "total_pnl": total_pnl, "count": len(positions)}


# ──────────────────────────────────────────
# 날짜별 추천 상세 (드릴다운)
# ──────────────────────────────────────────


@router.get("/recommendations-by-date")
def recommendations_by_date(
    rec_date: str = Query(description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
):
    """특정 날짜의 추천 종목 상세."""
    try:
        target = date.fromisoformat(rec_date)
    except ValueError:
        return {"error": "Invalid date format", "recommendations": []}

    target_id = date_to_id(target)
    recs = db.execute(
        select(FactDailyRecommendation, DimStock.ticker, DimStock.name)
        .join(DimStock, FactDailyRecommendation.stock_id == DimStock.stock_id)
        .where(FactDailyRecommendation.run_date_id == target_id)
        .order_by(FactDailyRecommendation.rank)
    ).all()

    result = []
    for r, ticker, name in recs:
        result.append({
            "rank": r.rank,
            "ticker": ticker,
            "name": name or "",
            "score": float(r.total_score),
            "return_1d": float(r.return_1d) if r.return_1d is not None else None,
            "return_5d": float(r.return_5d) if r.return_5d is not None else None,
            "return_20d": float(r.return_20d) if r.return_20d is not None else None,
            "ai_approved": r.ai_approved,
            "ai_confidence": int(r.ai_confidence) if r.ai_confidence else None,
            "position_weight": round(float(r.position_weight), 4) if r.position_weight else None,
        })

    return {"date": rec_date, "recommendations": result}


# ──────────────────────────────────────────
# 기존 API (섹터, 매크로, 승률, 분포, Top/Worst, 스파크라인)
# ──────────────────────────────────────────


@router.get("/sector-heatmap")
def sector_heatmap(days: int = Query(default=365, ge=1, le=3650), db: Session = Depends(get_db)):
    """섹터별 평균 수익률 히트맵 데이터 (return fallback)."""
    cutoff_id = date_to_id(date.today() - timedelta(days=days))

    recs = db.execute(
        select(FactDailyRecommendation, DimStock)
        .join(DimStock, FactDailyRecommendation.stock_id == DimStock.stock_id)
        .where(FactDailyRecommendation.run_date_id >= cutoff_id)
    ).all()

    sector_returns: dict[str, list[float]] = {}
    for rec, stock in recs:
        ret = _pick_best_return(rec)
        if ret is None:
            continue
        sector = stock.sector.sector_name if stock.sector else "기타"
        sector_returns.setdefault(sector, []).append(ret)

    return {
        sector: {
            "avg_return": round(sum(rets) / len(rets), 2),
            "count": len(rets),
            "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
        }
        for sector, rets in sector_returns.items()
    }


@router.get("/macro-history")
def macro_history(days: int = Query(default=365, ge=1, le=3650), db: Session = Depends(get_db)):
    """매크로 지표 히스토리."""
    cutoff_id = date_to_id(date.today() - timedelta(days=days))

    macros = db.execute(
        select(FactMacroIndicator)
        .where(FactMacroIndicator.date_id >= cutoff_id)
        .order_by(FactMacroIndicator.date_id)
    ).scalars().all()

    labels = []
    vix = []
    sp500 = []
    yield_10y = []

    for m in macros:
        try:
            labels.append(id_to_date(m.date_id).isoformat())
        except Exception:
            continue
        vix.append(float(m.vix) if m.vix else None)
        sp500.append(float(m.sp500_close) if m.sp500_close else None)
        yield_10y.append(float(m.us_10y_yield) if m.us_10y_yield else None)

    dollar = [float(m.dollar_index) if m.dollar_index else None for m in macros]
    fear_greed = [float(m.fear_greed_index) if m.fear_greed_index else None for m in macros]

    return {"labels": labels, "vix": vix, "sp500": sp500, "yield_10y": yield_10y, "dollar": dollar, "fear_greed": fear_greed}


@router.get("/win-rates")
def win_rates(days: int = Query(default=365, ge=1, le=3650), db: Session = Depends(get_db)):
    """기간별 승률 데이터."""
    cutoff_id = date_to_id(date.today() - timedelta(days=days))

    recs = db.execute(
        select(FactDailyRecommendation)
        .where(FactDailyRecommendation.run_date_id >= cutoff_id)
    ).scalars().all()

    def _wr(attr):
        vals = [float(getattr(r, attr)) for r in recs if getattr(r, attr) is not None]
        if not vals:
            return None
        return round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)

    return {
        "1d": _wr("return_1d"),
        "5d": _wr("return_5d"),
        "10d": _wr("return_10d"),
        "20d": _wr("return_20d"),
    }


@router.get("/return-distribution")
def return_distribution(days: int = Query(default=365, ge=1, le=3650), db: Session = Depends(get_db)):
    """수익률 분포 히스토그램 데이터 (return fallback)."""
    cutoff_id = date_to_id(date.today() - timedelta(days=days))
    recs = db.execute(
        select(FactDailyRecommendation)
        .where(FactDailyRecommendation.run_date_id >= cutoff_id)
    ).scalars().all()

    returns = [r for r in (_pick_best_return(rec) for rec in recs) if r is not None]

    if not returns:
        return {"bins": [], "counts": []}
    # 5% 구간 히스토그램
    bins = list(range(-30, 35, 5))
    counts = [0] * (len(bins) - 1)
    for r in returns:
        for i in range(len(bins) - 1):
            if bins[i] <= r < bins[i + 1]:
                counts[i] += 1
                break
    labels = [f"{bins[i]}~{bins[i+1]}%" for i in range(len(bins) - 1)]
    return {"bins": labels, "counts": counts}


@router.get("/top-worst-picks")
def top_worst_picks(n: int = Query(default=5), days: int = Query(default=365, ge=1, le=3650), db: Session = Depends(get_db)):
    """최고/최저 수익 종목 (return_20d → 5d → 1d fallback)."""
    cutoff_id = date_to_id(date.today() - timedelta(days=days))

    recs = db.execute(
        select(FactDailyRecommendation, DimStock.ticker)
        .join(DimStock, FactDailyRecommendation.stock_id == DimStock.stock_id)
        .where(FactDailyRecommendation.run_date_id >= cutoff_id)
    ).all()

    if not recs:
        return {"top": [], "worst": [], "period": ""}

    # 가용한 수익률로 정렬
    scored: list[tuple[str, float]] = []
    period_label = "20일"
    for r, ticker in recs:
        ret = None
        if r.return_20d is not None:
            ret = float(r.return_20d)
        elif r.return_5d is not None:
            ret = float(r.return_5d)
            period_label = "5일"
        elif r.return_1d is not None:
            ret = float(r.return_1d)
            period_label = "1일"
        if ret is not None:
            scored.append((ticker, ret))

    if not scored:
        return {"top": [], "worst": [], "period": ""}

    scored.sort(key=lambda x: x[1], reverse=True)
    top = [{"ticker": t, "return": round(r, 2)} for t, r in scored[:n]]
    worst = [{"ticker": t, "return": round(r, 2)} for t, r in scored[-n:]]
    worst.reverse()

    return {"top": top, "worst": worst, "period": period_label}


@router.get("/sparkline/{ticker}")
def sparkline_data(
    ticker: str,
    days: int = Query(default=5, ge=1, le=3650),
    db: Session = Depends(get_db),
):
    """종목의 최근 N일 가격 데이터 (스파크라인용)."""
    stock = db.execute(
        select(DimStock).where(DimStock.ticker == ticker.upper())
    ).scalar_one_or_none()
    if not stock:
        return {"prices": []}

    rows = db.execute(
        select(FactDailyPrice.adj_close)
        .where(FactDailyPrice.stock_id == stock.stock_id)
        .order_by(FactDailyPrice.date_id.desc())
        .limit(days)
    ).scalars().all()

    prices = [float(p) for p in reversed(rows)]
    return {"prices": prices}


@router.get("/sparklines")
def batch_sparklines(
    tickers: str = Query(description="콤마 구분 티커 목록"),
    days: int = Query(default=5, ge=1, le=30),
    db: Session = Depends(get_db),
) -> dict[str, list[float]]:
    """여러 종목의 스파크라인 데이터를 한번에 반환한다."""
    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    if not ticker_list:
        return {}

    stocks = db.execute(
        select(DimStock).where(DimStock.ticker.in_(ticker_list))
    ).scalars().all()

    result: dict[str, list[float]] = {}
    for stock in stocks:
        rows = db.execute(
            select(FactDailyPrice.adj_close)
            .where(FactDailyPrice.stock_id == stock.stock_id)
            .order_by(FactDailyPrice.date_id.desc())
            .limit(days)
        ).scalars().all()
        result[stock.ticker] = [float(p) for p in reversed(rows)]

    return result
