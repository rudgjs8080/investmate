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


@router.get("/equity-curve")
def equity_curve(days: int = Query(default=365, ge=1, le=3650), db: Session = Depends(get_db)):
    """추천 종목 누적 수익률 데이터 (return_20d → 5d → 1d fallback)."""
    cutoff_id = date_to_id(date.today() - timedelta(days=days))

    recs = db.execute(
        select(FactDailyRecommendation)
        .where(FactDailyRecommendation.run_date_id >= cutoff_id)
        .order_by(FactDailyRecommendation.run_date_id)
    ).scalars().all()

    # 가용한 수익률 필드 선택 (20d → 5d → 1d fallback)
    by_date: dict[int, list[float]] = {}
    period_label = "20일"
    for r in recs:
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
            by_date.setdefault(r.run_date_id, []).append(ret)

    labels = []
    values = []
    cumulative = 0.0
    for did in sorted(by_date.keys()):
        avg_ret = sum(by_date[did]) / len(by_date[did])
        cumulative += avg_ret
        try:
            labels.append(id_to_date(did).isoformat())
        except Exception:
            labels.append(str(did))
        values.append(round(cumulative, 2))

    return {"labels": labels, "values": values, "period": period_label}


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

    return {"labels": labels, "vix": vix, "sp500": sp500, "yield_10y": yield_10y, "dollar": dollar}


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
