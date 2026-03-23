"""AI 예측 정확도 대시보드 라우트."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FactAIFeedback, FactDailyRecommendation
from src.web.deps import get_db

router = APIRouter()


@router.get("/ai-accuracy")
def ai_accuracy_page(request: Request, db: Session = Depends(get_db)):
    """AI 예측 정확도 대시보드."""
    templates = request.app.state.templates

    perf = None
    try:
        from src.ai.feedback import calculate_ai_performance
        perf = calculate_ai_performance(db)
    except Exception:
        pass

    data = {}
    data_days = 0
    if perf and perf.total_predictions > 0:
        data = {
            "total": perf.total_predictions,
            "approved_count": perf.ai_approved_count,
            "excluded_count": perf.ai_excluded_count,
            "win_rate_approved": perf.win_rate_approved,
            "win_rate_excluded": perf.win_rate_excluded,
            "avg_return_approved": perf.avg_return_approved,
            "avg_return_excluded": perf.avg_return_excluded,
            "direction_accuracy": perf.direction_accuracy,
            "overestimate_rate": perf.overestimate_rate,
            "sector_accuracy": perf.sector_accuracy or {},
            "confidence_calibration": perf.confidence_calibration or {},
        }

    if not data:
        try:
            from src.db.models import FactDailyRecommendation
            from sqlalchemy import func
            count = db.execute(
                select(func.count(func.distinct(FactDailyRecommendation.run_date_id)))
                .where(FactDailyRecommendation.ai_approved.isnot(None))
            ).scalar() or 0
            data_days = count
        except Exception:
            pass

    return templates.TemplateResponse("ai_accuracy.html", {
        "request": request,
        "data": data,
        "data_days": data_days,
    })


@router.get("/api/ai-accuracy/calibration")
def ai_calibration(db: Session = Depends(get_db)):
    """신뢰도별 실제 승률 (교정 곡선 데이터)."""
    try:
        from src.ai.feedback import calculate_ai_performance
        perf = calculate_ai_performance(db)
        if perf.confidence_calibration:
            labels = sorted(perf.confidence_calibration.keys())
            values = [perf.confidence_calibration[k] for k in labels]
            return {"labels": labels, "values": values}
    except Exception:
        pass
    return {"labels": [], "values": []}


@router.get("/api/ai-accuracy/sector")
def ai_sector_accuracy(db: Session = Depends(get_db)):
    """섹터별 AI 승률."""
    try:
        from src.ai.feedback import calculate_ai_performance
        perf = calculate_ai_performance(db)
        if perf.sector_accuracy:
            return perf.sector_accuracy
    except Exception:
        pass
    return {}


@router.get("/api/ai-calibration-curve")
def calibration_curve_api(db: Session = Depends(get_db)):
    """AI 신뢰도 캘리브레이션 커브 데이터."""
    try:
        from src.ai.feedback import compute_calibration_curve, compute_ece
        curve = compute_calibration_curve(db)
        ece = compute_ece(curve)
        return {"curve": curve, "ece": ece}
    except Exception:
        return {"curve": {}, "ece": 0.0}


@router.get("/api/ai-monthly-trend")
def monthly_trend_api(db: Session = Depends(get_db)):
    """월별 AI 성과 추이."""
    try:
        feedbacks = list(
            db.execute(
                select(FactAIFeedback).where(
                    FactAIFeedback.return_20d.isnot(None)
                )
            )
            .scalars()
            .all()
        )

        monthly: dict[str, dict] = {}
        for f in feedbacks:
            if f.return_20d is None:
                continue
            rec_date_id = db.execute(
                select(FactDailyRecommendation.run_date_id).where(
                    FactDailyRecommendation.recommendation_id
                    == f.recommendation_id
                )
            ).scalar_one_or_none()
            if rec_date_id:
                month_key = str(rec_date_id)[:6]  # YYYYMM
                if month_key not in monthly:
                    monthly[month_key] = {
                        "wins": 0, "total": 0, "returns": [],
                    }
                monthly[month_key]["total"] += 1
                ret = float(f.return_20d)
                monthly[month_key]["returns"].append(ret)
                if ret > 0:
                    monthly[month_key]["wins"] += 1

        result = []
        for month, data in sorted(monthly.items()):
            avg_ret = (
                round(sum(data["returns"]) / len(data["returns"]), 2)
                if data["returns"]
                else 0
            )
            win_rate = (
                round(data["wins"] / data["total"] * 100, 1)
                if data["total"]
                else 0
            )
            result.append({
                "month": month,
                "win_rate": win_rate,
                "avg_return": avg_ret,
                "count": data["total"],
            })

        return {"months": result}
    except Exception:
        return {"months": []}
