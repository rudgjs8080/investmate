"""추천 성과 분석 모듈 -- 과거 추천의 수익률 통계를 집계한다."""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.risk_metrics import (
    calculate_calmar,
    calculate_max_drawdown,
    calculate_omega,
    calculate_sharpe,
    calculate_sortino,
)
from src.config import get_settings
from src.db.helpers import date_to_id, id_to_date
from src.db.models import (
    DimDate,
    DimStock,
    FactDailyPrice,
    FactDailyRecommendation,
    FactMacroIndicator,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PerformanceReport:
    """추천 성과 요약."""

    total_recommendations: int = 0
    with_return_data: int = 0
    # 단순 평균
    win_rate_1d: float | None = None
    win_rate_5d: float | None = None
    win_rate_10d: float | None = None
    win_rate_20d: float | None = None
    avg_return_1d: float | None = None
    avg_return_5d: float | None = None
    avg_return_10d: float | None = None
    avg_return_20d: float | None = None
    # 가중 평균 (position_weight 기반)
    weighted_avg_return_1d: float | None = None
    weighted_avg_return_5d: float | None = None
    weighted_avg_return_10d: float | None = None
    weighted_avg_return_20d: float | None = None
    # 벤치마크 비교
    benchmark_return_cumulative: float | None = None
    excess_return_cumulative: float | None = None
    information_ratio: float | None = None
    # 리스크 조정 지표
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    max_drawdown: float | None = None
    calmar_ratio: float | None = None
    omega_ratio: float | None = None
    # 기존 필드
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

    # ── 각 기간별 통계 (단순 + 가중) ──
    stats: dict[str, float | None] = {}
    for period, attr in [("1d", "return_1d"), ("5d", "return_5d"), ("10d", "return_10d"), ("20d", "return_20d")]:
        values = [float(getattr(r, attr)) for r in recs if getattr(r, attr) is not None]
        if values:
            wins = sum(1 for v in values if v > 0)
            stats[f"win_rate_{period}"] = round(wins / len(values) * 100, 1)
            stats[f"avg_return_{period}"] = round(sum(values) / len(values), 2)
        else:
            stats[f"win_rate_{period}"] = None
            stats[f"avg_return_{period}"] = None

        # 가중 평균 수익률 (position_weight 기반)
        weighted_pairs = [
            (float(getattr(r, attr)), float(r.position_weight))
            for r in recs
            if getattr(r, attr) is not None and r.position_weight is not None and float(r.position_weight) > 0
        ]
        if weighted_pairs:
            total_w = sum(w for _, w in weighted_pairs)
            stats[f"weighted_avg_return_{period}"] = round(
                sum(ret * w for ret, w in weighted_pairs) / total_w, 2,
            )
        else:
            stats[f"weighted_avg_return_{period}"] = None

    # ── 일별 가중 포트폴리오 수익률 시계열 (리스크 지표 계산용) ──
    daily_returns = _build_daily_portfolio_returns(recs)

    risk_sharpe = None
    risk_sortino = None
    risk_mdd = None
    risk_calmar = None
    risk_omega = None

    if len(daily_returns) >= 2:
        risk_sharpe = calculate_sharpe(daily_returns, period_days=1)
        risk_sortino = calculate_sortino(daily_returns, period_days=1)
        risk_mdd = calculate_max_drawdown(daily_returns)
        risk_calmar = calculate_calmar(daily_returns, risk_mdd, period_days=1)
        risk_omega = calculate_omega(daily_returns)

    # ── 벤치마크 비교 ──
    bm_cumulative = None
    excess_cumulative = None
    ir = None
    benchmark_data = _calculate_benchmark_returns(session, recs)
    if benchmark_data and daily_returns:
        bm_cumulative = benchmark_data["cumulative"]
        portfolio_cum = sum(daily_returns)
        excess_cumulative = round(portfolio_cum - bm_cumulative, 2)

        excess_series = benchmark_data.get("excess_series", [])
        if len(excess_series) >= 2:
            mean_ex = statistics.mean(excess_series)
            std_ex = statistics.stdev(excess_series)
            if std_ex > 0:
                ir = round(mean_ex / std_ex * (252 ** 0.5), 3)

    # ── 종목 정보 배치 로드 (N+1 방지) ──
    all_stock_ids = {r.stock_id for r in recs}
    stock_map = {
        s.stock_id: s
        for s in session.execute(
            select(DimStock).where(DimStock.stock_id.in_(all_stock_ids))
        ).scalars()
    } if all_stock_ids else {}

    # ── 최고/최저 종목 ──
    scored = []
    for r in recs:
        ret = _best_return(r)
        if ret is not None:
            stock = stock_map.get(r.stock_id)
            ticker = stock.ticker if stock else f"#{r.stock_id}"
            d = id_to_date(r.run_date_id).isoformat()
            scored.append((ticker, ret, d))

    best_pick = None
    worst_pick = None
    if scored:
        scored.sort(key=lambda x: x[1], reverse=True)
        best_pick = scored[0]
        worst_pick = scored[-1]

    # ── 섹터별 평균 수익률 ──
    sector_returns: dict[str, list[float]] = {}
    for r in recs:
        ret = _best_return(r)
        if ret is None:
            continue
        stock = stock_map.get(r.stock_id)
        if stock and stock.sector:
            sector_returns.setdefault(stock.sector.sector_name, []).append(ret)

    by_sector = {
        s: round(sum(v) / len(v), 2)
        for s, v in sector_returns.items()
        if v
    }

    # ── AI 승인 vs 전체 ──
    ai_approved_returns = [_best_return(r) for r in recs if r.ai_approved is True and _best_return(r) is not None]
    all_returns = [_best_return(r) for r in recs if _best_return(r) is not None]

    ai_approved_avg = round(sum(ai_approved_returns) / len(ai_approved_returns), 2) if ai_approved_returns else None
    all_avg = round(sum(all_returns) / len(all_returns), 2) if all_returns else None

    # ── 최근 30개 ──
    recent = []
    for r in recs[:30]:
        stock = stock_map.get(r.stock_id)
        recent.append({
            "date": id_to_date(r.run_date_id).isoformat(),
            "rank": r.rank,
            "ticker": stock.ticker if stock else f"#{r.stock_id}",
            "name": stock.name if stock else "",
            "score": float(r.total_score),
            "return_1d": float(r.return_1d) if r.return_1d is not None else None,
            "return_5d": float(r.return_5d) if r.return_5d is not None else None,
            "return_10d": float(r.return_10d) if r.return_10d is not None else None,
            "return_20d": float(r.return_20d) if r.return_20d is not None else None,
            "ai_approved": r.ai_approved,
            "ai_confidence": int(r.ai_confidence) if r.ai_confidence else None,
            "position_weight": round(float(r.position_weight), 4) if r.position_weight else None,
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
        weighted_avg_return_1d=stats["weighted_avg_return_1d"],
        weighted_avg_return_5d=stats["weighted_avg_return_5d"],
        weighted_avg_return_10d=stats["weighted_avg_return_10d"],
        weighted_avg_return_20d=stats["weighted_avg_return_20d"],
        benchmark_return_cumulative=bm_cumulative,
        excess_return_cumulative=excess_cumulative,
        information_ratio=ir,
        sharpe_ratio=risk_sharpe,
        sortino_ratio=risk_sortino,
        max_drawdown=risk_mdd,
        calmar_ratio=risk_calmar,
        omega_ratio=risk_omega,
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


def _build_daily_portfolio_returns(recs: list[FactDailyRecommendation]) -> list[float]:
    """추천을 run_date_id 기준으로 그룹핑 → 일별 가중 포트폴리오 수익률(1d) 시계열.

    position_weight가 없으면 동일 가중 fallback.
    return_1d가 없는 추천은 제외.
    """
    by_date: dict[int, list[tuple[float, float]]] = {}
    for r in recs:
        if r.return_1d is None:
            continue
        ret = float(r.return_1d)
        w = float(r.position_weight) if r.position_weight is not None and float(r.position_weight) > 0 else None
        by_date.setdefault(r.run_date_id, []).append((ret, w))

    daily_rets: list[float] = []
    for did in sorted(by_date):
        pairs = by_date[did]
        has_weights = all(w is not None for _, w in pairs)
        if has_weights:
            total_w = sum(w for _, w in pairs)
            if total_w > 0:
                daily_rets.append(sum(ret * w / total_w for ret, w in pairs))
            else:
                daily_rets.append(sum(ret for ret, _ in pairs) / len(pairs))
        else:
            daily_rets.append(sum(ret for ret, _ in pairs) / len(pairs))
    return daily_rets


def _calculate_benchmark_returns(
    session: Session, recs: list[FactDailyRecommendation],
) -> dict | None:
    """추천 기간의 S&P 500 일별 수익률과 누적 수익률을 계산한다."""
    if not recs:
        return None

    date_ids = sorted({r.run_date_id for r in recs})
    if len(date_ids) < 2:
        return None

    min_did = min(date_ids) - 5  # 전일 종가 필요
    max_did = max(date_ids) + 1

    macros = session.execute(
        select(FactMacroIndicator.date_id, FactMacroIndicator.sp500_close)
        .where(
            FactMacroIndicator.date_id >= min_did,
            FactMacroIndicator.date_id <= max_did,
            FactMacroIndicator.sp500_close.isnot(None),
        )
        .order_by(FactMacroIndicator.date_id)
    ).all()

    if len(macros) < 2:
        return None

    sp500_map = {did: float(close) for did, close in macros}
    sorted_dids = sorted(sp500_map)

    # 일별 S&P 500 수익률
    sp500_daily: list[float] = []
    for i in range(1, len(sorted_dids)):
        prev = sp500_map[sorted_dids[i - 1]]
        curr = sp500_map[sorted_dids[i]]
        if prev > 0:
            sp500_daily.append((curr / prev - 1) * 100)

    if not sp500_daily:
        return None

    # 일별 포트폴리오 수익률 (date_ids 기준)
    portfolio_daily = _build_daily_portfolio_returns(recs)

    # 동일 기간 맞추기: 최소 공통 길이
    min_len = min(len(sp500_daily), len(portfolio_daily))
    sp500_aligned = sp500_daily[:min_len]
    port_aligned = portfolio_daily[:min_len]

    cumulative_sp = sum(sp500_aligned)
    excess_series = [p - s for p, s in zip(port_aligned, sp500_aligned)]

    return {
        "cumulative": round(cumulative_sp, 2),
        "excess_series": excess_series,
    }


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
