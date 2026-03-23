"""추천 성과 분석 모듈 -- 과거 추천의 수익률 통계를 집계한다."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.helpers import date_to_id, id_to_date
from src.db.models import DimDate, DimStock, FactDailyPrice, FactDailyRecommendation

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PerformanceReport:
    """추천 성과 요약."""

    total_recommendations: int = 0
    with_return_data: int = 0
    win_rate_1d: float | None = None
    win_rate_5d: float | None = None
    win_rate_10d: float | None = None
    win_rate_20d: float | None = None
    avg_return_1d: float | None = None
    avg_return_5d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    best_pick: tuple[str, float, str] | None = None  # (ticker, return%, date)
    worst_pick: tuple[str, float, str] | None = None
    by_sector: dict[str, float] = field(default_factory=dict)
    ai_approved_avg_20d: float | None = None
    all_avg_20d: float | None = None
    recent_picks: tuple[dict, ...] = ()


def calculate_performance(
    session: Session, days: int = 90,
) -> PerformanceReport:
    """과거 추천의 성과를 집계한다.

    Args:
        session: DB 세션
        days: 조회 기간 (최근 N일)

    Returns:
        성과 요약 리포트
    """
    cutoff_date_id = date_to_id(date.today() - timedelta(days=days))

    stmt = (
        select(FactDailyRecommendation)
        .where(FactDailyRecommendation.run_date_id >= cutoff_date_id)
        .order_by(FactDailyRecommendation.run_date_id.desc())
    )
    recs = list(session.execute(stmt).scalars().all())

    if not recs:
        return PerformanceReport()

    # 수익률 데이터가 있는 추천만
    with_data = [r for r in recs if r.return_1d is not None or r.return_20d is not None]

    # 각 기간별 통계
    stats = {}
    for period, attr in [("1d", "return_1d"), ("5d", "return_5d"), ("10d", "return_10d"), ("20d", "return_20d")]:
        values = [float(getattr(r, attr)) for r in recs if getattr(r, attr) is not None]
        if values:
            wins = sum(1 for v in values if v > 0)
            stats[f"win_rate_{period}"] = round(wins / len(values) * 100, 1)
            stats[f"avg_return_{period}"] = round(sum(values) / len(values), 2)
        else:
            stats[f"win_rate_{period}"] = None
            stats[f"avg_return_{period}"] = None

    # 종목 정보 배치 로드 (N+1 방지)
    all_stock_ids = {r.stock_id for r in recs}
    stock_map = {
        s.stock_id: s
        for s in session.execute(
            select(DimStock).where(DimStock.stock_id.in_(all_stock_ids))
        ).scalars()
    } if all_stock_ids else {}

    # 최고/최저 종목 (20일 기준, 없으면 5일)
    best_pick = None
    worst_pick = None
    scored = []
    for r in recs:
        ret = _best_return(r)
        if ret is not None:
            stock = stock_map.get(r.stock_id)
            ticker = stock.ticker if stock else f"#{r.stock_id}"
            d = id_to_date(r.run_date_id).isoformat()
            scored.append((ticker, ret, d))

    if scored:
        scored.sort(key=lambda x: x[1], reverse=True)
        best_pick = scored[0]
        worst_pick = scored[-1]

    # 섹터별 평균 수익률 (20일)
    sector_returns: dict[str, list[float]] = {}
    for r in recs:
        if r.return_20d is None:
            continue
        stock = stock_map.get(r.stock_id)
        if stock and stock.sector:
            sector_returns.setdefault(stock.sector.sector_name, []).append(float(r.return_20d))

    by_sector = {
        s: round(sum(v) / len(v), 2)
        for s, v in sector_returns.items()
        if v
    }

    # AI 승인 종목 vs 전체
    ai_approved_returns = [float(r.return_20d) for r in recs if r.ai_approved and r.return_20d is not None]
    all_returns_20d = [float(r.return_20d) for r in recs if r.return_20d is not None]

    ai_approved_avg = round(sum(ai_approved_returns) / len(ai_approved_returns), 2) if ai_approved_returns else None
    all_avg = round(sum(all_returns_20d) / len(all_returns_20d), 2) if all_returns_20d else None

    # 최근 10개
    recent = []
    for r in recs[:10]:
        stock = stock_map.get(r.stock_id)
        recent.append({
            "date": id_to_date(r.run_date_id).isoformat(),
            "rank": r.rank,
            "ticker": stock.ticker if stock else f"#{r.stock_id}",
            "score": float(r.total_score),
            "return_1d": float(r.return_1d) if r.return_1d is not None else None,
            "return_5d": float(r.return_5d) if r.return_5d is not None else None,
            "return_10d": float(r.return_10d) if r.return_10d is not None else None,
            "return_20d": float(r.return_20d) if r.return_20d is not None else None,
            "ai_approved": r.ai_approved,
        })

    return PerformanceReport(
        total_recommendations=len(recs),
        with_return_data=len(with_data),
        win_rate_1d=stats["win_rate_1d"],
        win_rate_5d=stats["win_rate_5d"],
        win_rate_10d=stats["win_rate_10d"],
        win_rate_20d=stats["win_rate_20d"],
        avg_return_1d=stats["avg_return_1d"],
        avg_return_5d=stats["avg_return_5d"],
        avg_return_10d=stats["avg_return_10d"],
        avg_return_20d=stats["avg_return_20d"],
        best_pick=best_pick,
        worst_pick=worst_pick,
        by_sector=by_sector,
        ai_approved_avg_20d=ai_approved_avg,
        all_avg_20d=all_avg,
        recent_picks=tuple(recent),
    )


def _best_return(rec: FactDailyRecommendation) -> float | None:
    """가장 긴 기간의 수익률을 반환한다."""
    for attr in ("return_20d", "return_10d", "return_5d", "return_1d"):
        val = getattr(rec, attr)
        if val is not None:
            return float(val)
    return None


# ──────────────────────────────────────────
# T+1 실행가격 자동 채우기
# ──────────────────────────────────────────


def fill_execution_prices(session: Session) -> int:
    """execution_price가 NULL인 추천에 T+1 시가(open)를 채운다.

    추천일(run_date_id) 다음 거래일의 시가를 execution_price로 설정한다.
    실제 거래는 장 마감 후 추천 생성 -> 다음 날 시가에 진입하므로
    이 가격이 현실적인 체결가에 해당한다.

    Returns:
        업데이트된 레코드 수
    """
    stmt = (
        select(FactDailyRecommendation)
        .where(FactDailyRecommendation.execution_price.is_(None))
    )
    pending = list(session.execute(stmt).scalars().all())

    if not pending:
        return 0

    stock_ids = {r.stock_id for r in pending}
    min_date_id = min(r.run_date_id for r in pending)

    # 각 종목의 가격 데이터 배치 로드 (open 포함)
    prices_stmt = (
        select(
            FactDailyPrice.stock_id,
            FactDailyPrice.date_id,
            FactDailyPrice.open,
        )
        .where(
            FactDailyPrice.stock_id.in_(stock_ids),
            FactDailyPrice.date_id > min_date_id,
        )
        .order_by(FactDailyPrice.stock_id, FactDailyPrice.date_id)
    )
    price_rows = session.execute(prices_stmt).all()

    # stock_id -> [(date_id, open_price), ...] 정렬됨
    stock_open_map: dict[int, list[tuple[int, float]]] = {}
    for sid, did, open_price in price_rows:
        stock_open_map.setdefault(sid, []).append((did, float(open_price)))

    updated = 0
    for rec in pending:
        entries = stock_open_map.get(rec.stock_id, [])
        # 추천일 다음 거래일의 시가 찾기
        for did, open_price in entries:
            if did > rec.run_date_id:
                rec.execution_price = open_price
                updated += 1
                break

    if updated > 0:
        session.flush()
        logger.info("execution_price 채움: %d건", updated)

    return updated


# ──────────────────────────────────────────
# 수익률 자동 업데이트
# ──────────────────────────────────────────

# (기간 이름, 필요 거래일 수, 모델 속성명)
_RETURN_PERIODS: tuple[tuple[str, int, str], ...] = (
    ("1d", 1, "return_1d"),
    ("5d", 5, "return_5d"),
    ("10d", 10, "return_10d"),
    ("20d", 20, "return_20d"),
)


def update_recommendation_returns(session: Session) -> int:
    """과거 추천의 수익률(return_1d/5d/10d/20d)을 자동 계산하여 업데이트한다.

    - NULL인 수익률 필드가 하나라도 있는 추천을 조회
    - 추천일 이후 N 거래일의 종가(adj_close)로 수익률 계산
    - 배치 쿼리로 N+1 문제 방지

    Args:
        session: DB 세션

    Returns:
        업데이트된 레코드 수
    """
    # 1) NULL 수익률이 하나라도 있는 추천 조회
    stmt = (
        select(FactDailyRecommendation)
        .where(
            (FactDailyRecommendation.return_1d.is_(None))
            | (FactDailyRecommendation.return_5d.is_(None))
            | (FactDailyRecommendation.return_10d.is_(None))
            | (FactDailyRecommendation.return_20d.is_(None))
        )
    )
    pending_recs = list(session.execute(stmt).scalars().all())

    if not pending_recs:
        logger.info("업데이트할 추천 수익률 없음")
        return 0

    # 2) 필요한 stock_id 및 run_date_id 수집
    stock_ids = {r.stock_id for r in pending_recs}
    run_date_ids = {r.run_date_id for r in pending_recs}

    # 3) 추천일 이후 거래일 목록 배치 조회 (stock별 date_id → adj_close)
    #    최대 20 거래일 + 여유분으로 충분한 범위 조회
    min_run_date_id = min(run_date_ids)
    prices_stmt = (
        select(
            FactDailyPrice.stock_id,
            FactDailyPrice.date_id,
            FactDailyPrice.adj_close,
        )
        .where(
            FactDailyPrice.stock_id.in_(stock_ids),
            FactDailyPrice.date_id >= min_run_date_id,
        )
        .order_by(FactDailyPrice.stock_id, FactDailyPrice.date_id)
    )
    price_rows = session.execute(prices_stmt).all()

    # stock_id → [date_id, ...] 정렬된 거래일 목록
    # stock_id → {date_id: adj_close}
    trading_days_map: dict[int, list[int]] = {}
    price_map: dict[int, dict[int, float]] = {}
    for sid, did, adj in price_rows:
        trading_days_map.setdefault(sid, []).append(did)
        price_map.setdefault(sid, {})[did] = float(adj)

    # 4) 거래 비용 설정 로드
    tx_cost_pct = get_settings().transaction_cost_bps / 100  # bps → %

    # 5) 각 추천에 대해 수익률 계산
    updated_count = 0

    for rec in pending_recs:
        stock_days = trading_days_map.get(rec.stock_id, [])
        stock_prices = price_map.get(rec.stock_id, {})

        if not stock_days:
            continue

        # execution_price 우선, 없으면 price_at_recommendation (하위 호환)
        base_price = float(rec.execution_price) if rec.execution_price else float(rec.price_at_recommendation)
        if base_price <= 0:
            continue

        # 추천일 이후 거래일만 필터 (추천일 자체는 제외)
        future_days = [d for d in stock_days if d > rec.run_date_id]

        changed = False
        for _label, n_days, attr_name in _RETURN_PERIODS:
            # 이미 계산된 필드는 스킵
            if getattr(rec, attr_name) is not None:
                continue

            # N 거래일이 아직 경과하지 않았으면 스킵
            if len(future_days) < n_days:
                continue

            # N번째 거래일의 종가
            target_date_id = future_days[n_days - 1]
            price_later = stock_prices.get(target_date_id)
            if price_later is None:
                continue

            return_pct = round((price_later / base_price - 1) * 100 - tx_cost_pct, 2)
            setattr(rec, attr_name, return_pct)
            changed = True

        if changed:
            updated_count += 1

    if updated_count > 0:
        session.flush()
        logger.info("추천 수익률 업데이트 완료: %d건", updated_count)

    return updated_count
