"""주간 리포트 웹 라우트 — 목록 + 상세."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter()

WEEKLY_DIR = Path("reports/weekly")


def _load_report_summary(json_path: Path) -> dict | None:
    """JSON에서 요약 데이터만 추출한다."""
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        es = data.get("executive_summary", {})
        pr = data.get("performance_review", {})
        return {
            "week_id": json_path.stem,
            "year": data.get("year"),
            "week_number": data.get("week_number"),
            "week_start": data.get("week_start", ""),
            "week_end": data.get("week_end", ""),
            "trading_days": data.get("trading_days", 0),
            "sp500_return": es.get("sp500_weekly_return_pct"),
            "vix_end": es.get("vix_end"),
            "regime": es.get("regime_end", "range"),
            "win_rate": es.get("weekly_win_rate_pct"),
            "avg_return": es.get("weekly_avg_return_pct"),
            "total_picks": pr.get("total_unique_picks", 0),
            "win_count": pr.get("win_count", 0),
            "loss_count": pr.get("loss_count", 0),
            "conviction_count": len(data.get("conviction_picks", [])),
            "market_oneliner": es.get("market_oneliner", ""),
        }
    except Exception:
        return None


@router.get("/weekly-reports")
def weekly_reports_list(request: Request):
    """주간 리포트 목록 페이지."""
    templates = request.app.state.templates

    reports: list[dict] = []
    if WEEKLY_DIR.exists():
        for json_path in sorted(WEEKLY_DIR.glob("*.json"), reverse=True):
            summary = _load_report_summary(json_path)
            if summary:
                reports.append(summary)

    return templates.TemplateResponse("weekly_reports_list.html", {
        "request": request,
        "reports": reports,
    })


@router.get("/weekly-report/{week_id}")
def weekly_report_detail(request: Request, week_id: str):
    """주간 리포트 상세 페이지."""
    templates = request.app.state.templates

    report_data = None
    json_path = WEEKLY_DIR / f"{week_id}.json"
    if json_path.exists():
        with open(json_path, encoding="utf-8") as f:
            report_data = json.load(f)

    # prev/next 계산
    prev_id, next_id = None, None
    if WEEKLY_DIR.exists():
        all_ids = sorted([f.stem for f in WEEKLY_DIR.glob("*.json")])
        if week_id in all_ids:
            idx = all_ids.index(week_id)
            if idx > 0:
                prev_id = all_ids[idx - 1]
            if idx < len(all_ids) - 1:
                next_id = all_ids[idx + 1]

    # conviction_technicals를 ticker로 매핑
    tech_map = {}
    if report_data and report_data.get("conviction_technicals"):
        for ct in report_data["conviction_technicals"]:
            tech_map[ct.get("ticker", "")] = ct

    return templates.TemplateResponse("weekly_report.html", {
        "request": request,
        "report": report_data,
        "week_id": week_id,
        "prev_id": prev_id,
        "next_id": next_id,
        "tech_map": tech_map,
    })


@router.get("/weekly-report")
def weekly_report_redirect(year: int | None = None, week: int | None = None):
    """하위 호환 — 기존 URL을 새 라우트로 리다이렉트."""
    if year is not None and week is not None:
        return RedirectResponse(f"/weekly-report/{year}-W{week:02d}", status_code=302)
    return RedirectResponse("/weekly-reports", status_code=302)
