"""주간 리포트 웹 라우트."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter()

WEEKLY_DIR = Path("reports/weekly")


@router.get("/weekly-report")
def weekly_report_page(
    request: Request,
    year: int | None = None,
    week: int | None = None,
):
    """주간 리포트 페이지."""
    templates = request.app.state.templates

    # 특정 주차 요청 또는 최신
    report_data = None
    available_reports: list[str] = []

    if WEEKLY_DIR.exists():
        json_files = sorted(WEEKLY_DIR.glob("*.json"), reverse=True)
        available_reports = [f.stem for f in json_files]

        target_file = None
        if year is not None and week is not None:
            target_file = WEEKLY_DIR / f"{year}-W{week:02d}.json"
        elif json_files:
            target_file = json_files[0]

        if target_file and target_file.exists():
            with open(target_file, encoding="utf-8") as f:
                report_data = json.load(f)

    return templates.TemplateResponse("weekly_report.html", {
        "request": request,
        "report": report_data,
        "available_reports": available_reports,
    })
