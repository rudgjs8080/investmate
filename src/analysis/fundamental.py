"""기본적 분석 모듈 — 재무 지표 점수화."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.data.schemas import FinancialRecord, ValuationRecord

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class FundamentalScore:
    """기본적 분석 점수."""

    per_score: float
    pbr_score: float
    roe_score: float
    debt_score: float
    growth_score: float
    composite_score: float

    @property
    def summary(self) -> str:
        if self.composite_score >= 7:
            return "우수"
        if self.composite_score >= 5:
            return "보통"
        return "주의"


# 가중치
_WEIGHTS = {
    "per": 0.15,
    "peg": 0.10,
    "pbr": 0.10,
    "roe": 0.18,
    "debt": 0.10,
    "dividend": 0.07,
    "growth": 0.12,
    "fcf": 0.18,
}


def _score_per(per: float | None) -> float:
    """PER 점수 (낮을수록 좋음). 1-10."""
    if per is None:
        return 3.5  # 데이터 누락 감점
    if per < 0:
        return 2.0  # 적자
    if per < 10:
        return 9.0
    if per < 15:
        return 8.0
    if per < 20:
        return 7.0
    if per < 25:
        return 6.0
    if per < 30:
        return 5.0
    if per < 40:
        return 4.0
    return 3.0


def _score_pbr(pbr: float | None) -> float:
    """PBR 점수 (낮을수록 좋음). 1-10."""
    if pbr is None:
        return 3.5  # 데이터 누락 감점
    if pbr < 0:
        return 2.0
    if pbr < 1.0:
        return 9.0
    if pbr < 2.0:
        return 7.0
    if pbr < 5.0:
        return 5.0
    if pbr < 10.0:
        return 4.0
    return 3.0


def _score_roe(roe: float | None) -> float:
    """ROE 점수 (높을수록 좋음). 1-10."""
    if roe is None:
        return 3.5  # 데이터 누락 감점
    # yfinance ROE는 소수점 형태 (0.15 = 15%)
    roe_pct = roe * 100 if abs(roe) < 1 else roe
    if roe_pct < 0:
        return 2.0
    if roe_pct >= 25:
        return 9.0
    if roe_pct >= 20:
        return 8.0
    if roe_pct >= 15:
        return 7.0
    if roe_pct >= 10:
        return 6.0
    if roe_pct >= 5:
        return 5.0
    return 4.0


def _score_debt(total_assets: float | None, total_liabilities: float | None) -> float:
    """부채비율 점수 (낮을수록 좋음). 1-10."""
    if total_assets is None or total_liabilities is None or total_assets == 0:
        return 3.5  # 데이터 누락 감점
    ratio = total_liabilities / total_assets
    if ratio < 0.2:
        return 9.0
    if ratio < 0.3:
        return 8.0
    if ratio < 0.4:
        return 7.0
    if ratio < 0.5:
        return 6.0
    if ratio < 0.6:
        return 5.0
    if ratio < 0.7:
        return 4.0
    return 3.0


def _score_dividend_yield(dividend_yield: float | None) -> float:
    """배당수익률 점수 (적정 배당 선호). 1-10."""
    if dividend_yield is None:
        return 5.0  # 배당 없는 성장주도 많으므로 중립
    dy = dividend_yield * 100 if abs(dividend_yield) < 1 else dividend_yield
    if dy >= 5:
        return 8.0  # 고배당
    if dy >= 3:
        return 7.0
    if dy >= 1.5:
        return 6.0
    if dy > 0:
        return 5.0
    return 4.0  # 무배당


def _score_growth(
    revenues: list[float | None],
) -> float:
    """매출 성장률 점수. 1-10."""
    valid = [r for r in revenues if r is not None and r > 0]
    if len(valid) < 2:
        return 5.0

    # YoY (4분기 전 비교) 우선, QoQ fallback
    if len(valid) >= 5:
        growth_rate = (valid[0] - valid[4]) / valid[4] * 100  # YoY
    elif len(valid) >= 2:
        growth_rate = (valid[0] - valid[1]) / valid[1] * 100  # QoQ fallback
    if growth_rate >= 30:
        return 9.0
    if growth_rate >= 20:
        return 8.0
    if growth_rate >= 10:
        return 7.0
    if growth_rate >= 5:
        return 6.0
    if growth_rate >= 0:
        return 5.0
    if growth_rate >= -10:
        return 4.0
    return 3.0


def _score_fcf(
    operating_cashflow: float | None,
    net_income: float | None,
    total_assets: float | None,
    sector_fcf_median: float | None = None,
) -> float:
    """FCF 품질 점수 (1-10). Operating CF가 NI보다 크면 고품질."""
    if operating_cashflow is None or total_assets is None or total_assets == 0:
        return 5.0

    fcf_margin = operating_cashflow / total_assets

    # 섹터 상대 스코어링 (sector_fcf_median 제공 시)
    if sector_fcf_median and sector_fcf_median > 0:
        relative = fcf_margin / sector_fcf_median
        score = 5.0
        if relative > 1.5:
            score += 2.0
        elif relative > 1.0:
            score += 1.0
        elif relative < 0.5:
            score -= 1.5
        # 품질 보너스: CF > NI
        if net_income is not None and operating_cashflow > net_income:
            score += 0.5
        return max(1.0, min(10.0, score))

    # 기존 절대 스코어링
    score = 5.0
    if operating_cashflow > 0:
        score += 1.5
    if net_income is not None and operating_cashflow > net_income:
        score += 1.0  # Cash > Accruals = quality
    if fcf_margin > 0.10:
        score += 1.5
    elif fcf_margin > 0.05:
        score += 0.5
    elif fcf_margin < 0:
        score -= 1.5

    return max(1.0, min(10.0, score))


def _score_peg(per: float | None, growth_rate: float) -> float:
    """PEG 비율 점수. PEG < 1.0이 저평가."""
    if per is None or growth_rate <= 0 or per <= 0:
        return 5.0
    peg = per / growth_rate
    if peg < 0.5:
        return 9.0
    if peg < 1.0:
        return 8.0
    if peg < 1.5:
        return 7.0
    if peg < 2.0:
        return 5.0
    return 3.0


def _score_relative(value: float | None, sector_median: float | None) -> float | None:
    """섹터 중앙값 대비 상대 점수. 적용 불가 시 None 반환."""
    if value is None or sector_median is None or sector_median == 0:
        return None
    ratio = value / sector_median
    if ratio < 0.5:
        return 9.0
    if ratio < 0.75:
        return 8.0
    if ratio < 1.0:
        return 7.0
    if ratio < 1.25:
        return 5.0
    if ratio < 1.5:
        return 4.0
    return 3.0


def _score_per_relative(per: float | None, sector_median: float | None) -> float:
    """섹터 상대 PER 점수 (낮을수록 좋음). 1-10."""
    if per is not None and per < 0:
        return 2.0  # 적자는 항상 2.0
    rel = _score_relative(per, sector_median)
    if rel is not None:
        return rel
    return _score_per(per)


def _score_pbr_relative(pbr: float | None, sector_median: float | None) -> float:
    """섹터 상대 PBR 점수 (낮을수록 좋음). 1-10."""
    if pbr is not None and pbr < 0:
        return 2.0
    rel = _score_relative(pbr, sector_median)
    if rel is not None:
        return rel
    return _score_pbr(pbr)


def _score_roe_relative(roe: float | None, sector_median: float | None) -> float:
    """섹터 상대 ROE 점수 (높을수록 좋음 — 역비율). 1-10."""
    if roe is not None and roe < 0:
        return 2.0
    if roe is None or sector_median is None or sector_median == 0:
        return _score_roe(roe)
    # ROE는 높을수록 좋으므로 역비율: median/value
    ratio = sector_median / roe if roe != 0 else 999.0
    if ratio < 0.5:
        return 9.0
    if ratio < 0.75:
        return 8.0
    if ratio < 1.0:
        return 7.0
    if ratio < 1.25:
        return 5.0
    if ratio < 1.5:
        return 4.0
    return 3.0


def build_sector_medians(session: Session) -> dict[str, dict[str, float]]:
    """섹터별 PER/PBR/ROE/FCF 중앙값을 계산한다.

    각 종목의 최신 밸류에이션(max date_id)만 사용.
    FCF는 최신 재무제표의 operating_cashflow / total_assets로 계산.

    Returns:
        {"Information Technology": {"per": 28.5, "pbr": 8.2, "roe": 0.22, "fcf": 0.08}, ...}
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy import select

    from src.db.models import DimSector, DimStock, FactFinancial, FactValuation

    # 종목별 최신 date_id 서브쿼리
    latest_sq = (
        select(
            FactValuation.stock_id,
            sa_func.max(FactValuation.date_id).label("max_date_id"),
        )
        .group_by(FactValuation.stock_id)
        .subquery()
    )

    # 최신 밸류에이션 + 섹터 조인
    stmt = (
        select(
            DimSector.sector_name,
            FactValuation.per,
            FactValuation.pbr,
            FactValuation.roe,
        )
        .join(latest_sq, (
            (FactValuation.stock_id == latest_sq.c.stock_id)
            & (FactValuation.date_id == latest_sq.c.max_date_id)
        ))
        .join(DimStock, DimStock.stock_id == FactValuation.stock_id)
        .join(DimSector, DimSector.sector_id == DimStock.sector_id)
    )

    rows = session.execute(stmt).all()

    # 섹터별 그룹핑
    sector_data: dict[str, dict[str, list[float]]] = {}
    for sector_name, per, pbr, roe in rows:
        if sector_name not in sector_data:
            sector_data[sector_name] = {"per": [], "pbr": [], "roe": [], "fcf": []}
        if per is not None and per > 0:
            sector_data[sector_name]["per"].append(float(per))
        if pbr is not None and pbr > 0:
            sector_data[sector_name]["pbr"].append(float(pbr))
        if roe is not None:
            sector_data[sector_name]["roe"].append(float(roe))

    # FCF 마진 계산: 종목별 최신 재무제표에서 operating_cashflow / total_assets
    latest_fin_sq = (
        select(
            FactFinancial.stock_id,
            sa_func.max(FactFinancial.financial_id).label("max_fin_id"),
        )
        .group_by(FactFinancial.stock_id)
        .subquery()
    )

    fin_stmt = (
        select(
            DimSector.sector_name,
            FactFinancial.operating_cashflow,
            FactFinancial.total_assets,
        )
        .join(latest_fin_sq, (
            FactFinancial.financial_id == latest_fin_sq.c.max_fin_id
        ))
        .join(DimStock, DimStock.stock_id == FactFinancial.stock_id)
        .join(DimSector, DimSector.sector_id == DimStock.sector_id)
    )

    fin_rows = session.execute(fin_stmt).all()
    for sector_name, op_cf, total_assets in fin_rows:
        if (op_cf is not None and total_assets is not None
                and float(total_assets) > 0):
            fcf_margin = float(op_cf) / float(total_assets)
            if sector_name not in sector_data:
                sector_data[sector_name] = {"per": [], "pbr": [], "roe": [], "fcf": []}
            sector_data[sector_name].setdefault("fcf", []).append(fcf_margin)

    # 중앙값 계산
    result: dict[str, dict[str, float]] = {}
    for sector_name, metrics in sector_data.items():
        medians: dict[str, float] = {}
        for key, values in metrics.items():
            if values:
                medians[key] = statistics.median(values)
        if medians:
            result[sector_name] = medians

    return result


def analyze_fundamentals(
    financials: list[FinancialRecord],
    valuation: ValuationRecord | None = None,
    *,
    sector_medians: dict[str, float] | None = None,
) -> FundamentalScore:
    """재무 데이터를 분석하여 점수를 산출한다.

    Args:
        financials: 원본 재무제표 리스트 (최신순).
        valuation: 최신 밸류에이션 데이터 (PER, PBR, ROE 등).
        sector_medians: 섹터 중앙값 {"per": 28.5, "pbr": 8.2, "roe": 0.22}.
            제공 시 섹터 상대 점수로 PER/PBR/ROE를 평가한다.
    """
    if not financials:
        return FundamentalScore(
            per_score=5.0, pbr_score=5.0, roe_score=5.0,
            debt_score=5.0, growth_score=5.0, composite_score=5.0,
        )

    latest_fin = financials[0]

    per_val = valuation.per if valuation else None
    pbr_val = valuation.pbr if valuation else None
    roe_val = valuation.roe if valuation else None

    if sector_medians is not None:
        per_score = _score_per_relative(per_val, sector_medians.get("per"))
        pbr_score = _score_pbr_relative(pbr_val, sector_medians.get("pbr"))
        roe_score = _score_roe_relative(roe_val, sector_medians.get("roe"))
    else:
        per_score = _score_per(per_val)
        pbr_score = _score_pbr(pbr_val)
        roe_score = _score_roe(roe_val)
    debt_score = _score_debt(latest_fin.total_assets, latest_fin.total_liabilities)
    growth_score = _score_growth([f.revenue for f in financials])
    dividend_score = _score_dividend_yield(valuation.dividend_yield if valuation else None)
    sector_fcf_med = sector_medians.get("fcf") if sector_medians else None
    fcf_score = _score_fcf(
        latest_fin.operating_cashflow, latest_fin.net_income, latest_fin.total_assets,
        sector_fcf_median=sector_fcf_med,
    )

    # PEG 점수: growth_rate 계산 (YoY 우선, QoQ fallback)
    revenues = [f.revenue for f in financials]
    valid_rev = [r for r in revenues if r is not None and r > 0]
    if len(valid_rev) >= 5:
        peg_growth_rate = (valid_rev[0] - valid_rev[4]) / valid_rev[4] * 100
    elif len(valid_rev) >= 2:
        peg_growth_rate = (valid_rev[0] - valid_rev[1]) / valid_rev[1] * 100
    else:
        peg_growth_rate = 0.0
    peg_score = _score_peg(per_val, peg_growth_rate)

    composite = (
        per_score * _WEIGHTS["per"]
        + peg_score * _WEIGHTS["peg"]
        + pbr_score * _WEIGHTS["pbr"]
        + roe_score * _WEIGHTS["roe"]
        + debt_score * _WEIGHTS["debt"]
        + growth_score * _WEIGHTS["growth"]
        + dividend_score * _WEIGHTS["dividend"]
        + fcf_score * _WEIGHTS["fcf"]
    )

    return FundamentalScore(
        per_score=per_score,
        pbr_score=pbr_score,
        roe_score=roe_score,
        debt_score=debt_score,
        growth_score=growth_score,
        composite_score=round(composite, 1),
    )
