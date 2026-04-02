"""P&L 추적 라우트."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.web.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/performance")
def performance_page(request: Request, days: int = Query(default=90), db: Session = Depends(get_db)):
    """P&L 추적 페이지."""
    templates = request.app.state.templates

    perf: dict = {}
    try:
        from src.analysis.performance import calculate_performance
        report = calculate_performance(db, days=days)
        if report and report.total_recommendations > 0:
            perf = {
                "total": report.total_recommendations,
                "with_data": report.with_return_data,
                # 단순 평균
                "win_rate_1d": report.win_rate_1d,
                "win_rate_5d": report.win_rate_5d,
                "win_rate_10d": report.win_rate_10d,
                "win_rate_20d": report.win_rate_20d,
                "avg_return_1d": report.avg_return_1d,
                "avg_return_5d": report.avg_return_5d,
                "avg_return_10d": report.avg_return_10d,
                "avg_return_20d": report.avg_return_20d,
                # 가중 평균
                "weighted_avg_return_1d": report.weighted_avg_return_1d,
                "weighted_avg_return_5d": report.weighted_avg_return_5d,
                "weighted_avg_return_10d": report.weighted_avg_return_10d,
                "weighted_avg_return_20d": report.weighted_avg_return_20d,
                # 벤치마크
                "benchmark_return": report.benchmark_return_cumulative,
                "excess_return": report.excess_return_cumulative,
                "information_ratio": report.information_ratio,
                # 리스크
                "sharpe": report.sharpe_ratio,
                "sortino": report.sortino_ratio,
                "max_drawdown": report.max_drawdown,
                "calmar": report.calmar_ratio,
                "omega": report.omega_ratio,
                # 기존
                "best_pick": report.best_pick,
                "worst_pick": report.worst_pick,
                "by_sector": report.by_sector or {},
                "ai_approved_avg": report.ai_approved_avg_20d,
                "all_avg": report.all_avg_20d,
                "recent_picks": list(report.recent_picks),
            }
    except Exception as e:
        logger.warning("성과 계산 실패: %s", e)

    return templates.TemplateResponse("performance.html", {
        "request": request,
        "days": days,
        "perf": perf,
    })
