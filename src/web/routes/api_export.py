"""CSV/JSON 내보내기 API."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.data.kr_names import get_kr_name
from src.db.helpers import date_to_id, id_to_date
from src.db.models import DimStock, FactDailyRecommendation
from src.db.repository import MacroRepository, RecommendationRepository
from src.web.deps import get_db

router = APIRouter()


@router.get("/export/recommendations")
def export_recommendations(
    report_date: str = Query(default=None),
    db: Session = Depends(get_db),
):
    """추천 종목 CSV 내보내기."""
    # 날짜 결정
    if report_date:
        try:
            d = date.fromisoformat(report_date)
            run_date_id = date_to_id(d)
        except ValueError:
            return {"error": "잘못된 날짜 형식"}
    else:
        macro = MacroRepository.get_latest(db)
        if not macro:
            return {"error": "데이터 없음"}
        run_date_id = macro.date_id
        d = id_to_date(run_date_id)

    recs = RecommendationRepository.get_by_date(db, run_date_id)
    if not recs:
        return {"error": "추천 데이터 없음"}

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "순위", "티커", "종목명", "한글명", "섹터", "현재가",
        "종합점수", "기술점수", "기본점수", "수급점수", "외부점수", "모멘텀점수",
        "AI승인", "AI신뢰도", "AI목표가", "AI손절가", "추천근거",
    ])

    for rec in recs:
        stock = db.execute(
            select(DimStock).where(DimStock.stock_id == rec.stock_id)
        ).scalar_one_or_none()
        if not stock:
            continue

        sector = stock.sector.sector_name if stock.sector else ""
        ai_status = "추천" if rec.ai_approved is True else ("제외" if rec.ai_approved is False else "미실행")

        writer.writerow([
            rec.rank,
            stock.ticker,
            stock.name,
            get_kr_name(stock.ticker, stock.name),
            sector,
            f"{float(rec.price_at_recommendation):.2f}",
            f"{float(rec.total_score):.2f}",
            f"{float(rec.technical_score):.2f}",
            f"{float(rec.fundamental_score):.2f}",
            f"{float(rec.smart_money_score):.2f}",
            f"{float(rec.external_score):.2f}",
            f"{float(rec.momentum_score):.2f}",
            ai_status,
            int(rec.ai_confidence) if rec.ai_confidence else "",
            f"{float(rec.ai_target_price):.2f}" if rec.ai_target_price else "",
            f"{float(rec.ai_stop_loss):.2f}" if rec.ai_stop_loss else "",
            rec.recommendation_reason or "",
        ])

    output.seek(0)
    filename = f"investmate_{d.isoformat()}.csv"
    return StreamingResponse(
        iter(["\ufeff" + output.getvalue()]),  # BOM for Excel 한글
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
