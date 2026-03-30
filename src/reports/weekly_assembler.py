"""주간 리포트 데이터 조립기 — DB에서 월~금 데이터를 쿼리하고 WeeklyReport로 조립."""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from datetime import date, datetime
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.analysis.regime import MarketRegime, detect_regime
from src.config import get_settings
from src.db.helpers import date_to_id, id_to_date
from src.db.models import (
    DimDate,
    DimSector,
    DimSignalType,
    DimStock,
    FactDailyPrice,
    FactDailyRecommendation,
    FactIndicatorValue,
    FactMacroIndicator,
    FactSignal,
)
from src.reports.weekly_models import (
    ConvictionPick,
    ConvictionTechnical,
    RiskDashboard,
    SectorRotationEntry,
    WeeklyActionItem,
    WeeklyAIAccuracy,
    WeeklyBestWorstDetail,
    WeeklyExecutiveSummary,
    WeeklyMacroSummary,
    WeeklyOutlook,
    WeeklyPerformanceReview,
    WeeklyPickPerformance,
    WeeklyReport,
    WeeklySignalTrend,
    WeekOverWeekChange,
    WinRateTrend,
)

logger = logging.getLogger(__name__)


def assemble_weekly_report(
    session: Session, year: int, week_number: int,
) -> WeeklyReport:
    """주간 리포트 데이터를 조립한다."""
    date_ids, dates = _get_week_trading_days(session, year, week_number)
    trading_days = len(date_ids)

    # 이전 주 date_ids (비교용)
    prev_date_ids, _ = _get_prev_week_trading_days(session, year, week_number)

    week_start = dates[0].isoformat() if dates else ""
    week_end = dates[-1].isoformat() if dates else ""

    # 주간 종료 시점 regime 감지
    regime_end_obj = detect_regime(session)

    # 각 섹션 빌드
    perf_review = _build_performance_review(session, date_ids)
    sector_rotation = _build_sector_rotation(session, date_ids, prev_date_ids)
    macro_summary = _build_macro_summary(session, date_ids)

    executive_summary = _build_executive_summary(
        session, date_ids, macro_summary, perf_review, regime_end_obj,
    )
    conviction_picks = _build_conviction_picks(session, date_ids, trading_days)
    signal_trend = _build_signal_trend(session, date_ids, prev_date_ids)
    ai_accuracy = _build_ai_accuracy(session, date_ids)
    outlook = _build_outlook(regime_end_obj, sector_rotation)

    # 고도화 섹션
    best_worst = _build_best_worst_detail(session, perf_review, date_ids)
    risk_dashboard = _build_risk_dashboard(perf_review, macro_summary, sector_rotation)
    win_rate_trend = _build_win_rate_trend(session, year, week_number)
    conviction_techs = _build_conviction_technicals(session, conviction_picks, date_ids)
    wow = _build_week_over_week(perf_review, prev_date_ids, session)
    action_items = _build_action_items(
        regime_end_obj, conviction_picks, sector_rotation, perf_review,
    )

    return WeeklyReport(
        year=year,
        week_number=week_number,
        week_start=week_start,
        week_end=week_end,
        trading_days=trading_days,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        executive_summary=executive_summary,
        performance_review=perf_review,
        conviction_picks=conviction_picks,
        sector_rotation=sector_rotation,
        macro_summary=macro_summary,
        signal_trend=signal_trend,
        ai_accuracy=ai_accuracy,
        outlook=outlook,
        best_worst_detail=best_worst,
        risk_dashboard=risk_dashboard,
        win_rate_trend=win_rate_trend,
        conviction_technicals=conviction_techs,
        week_over_week=wow,
        action_items=action_items,
    )


# ──────────────────────────────────────────
# 날짜 유틸리티
# ──────────────────────────────────────────


def _get_week_trading_days(
    session: Session, year: int, week: int,
) -> tuple[list[int], list[date]]:
    """해당 주의 거래일 date_id 목록과 date 목록을 반환한다."""
    rows = (
        session.execute(
            select(DimDate.date_id, DimDate.date)
            .where(DimDate.year == year, DimDate.week_of_year == week)
            .order_by(DimDate.date_id)
        )
        .all()
    )
    if not rows:
        return [], []
    date_ids = [r.date_id for r in rows]
    dates = [r.date for r in rows]
    return date_ids, dates


def _get_prev_week_trading_days(
    session: Session, year: int, week: int,
) -> tuple[list[int], list[date]]:
    """이전 주의 거래일 date_id 목록을 반환한다."""
    if week > 1:
        return _get_week_trading_days(session, year, week - 1)
    # 연도 경계: 이전 해의 마지막 주
    return _get_week_trading_days(session, year - 1, 52)


# ──────────────────────────────────────────
# Executive Summary
# ──────────────────────────────────────────


def _build_executive_summary(
    session: Session,
    date_ids: list[int],
    macro: WeeklyMacroSummary,
    perf: WeeklyPerformanceReview,
    regime: MarketRegime,
) -> WeeklyExecutiveSummary:
    """1분 브리핑 — 주간 시장 총평."""
    if not date_ids:
        return WeeklyExecutiveSummary(
            market_oneliner="데이터 없음",
            sp500_weekly_return_pct=None,
            vix_start=None, vix_end=None, vix_high=None, vix_low=None,
            regime_start="range", regime_end="range", regime_changed=False,
            weekly_win_rate_pct=None, weekly_avg_return_pct=None,
        )

    # S&P 500 주간 수익률: 월요일 종가 → 금요일 종가
    first_macro = session.execute(
        select(FactMacroIndicator)
        .where(FactMacroIndicator.date_id == date_ids[0])
    ).scalar_one_or_none()
    last_macro = session.execute(
        select(FactMacroIndicator)
        .where(FactMacroIndicator.date_id == date_ids[-1])
    ).scalar_one_or_none()

    sp500_return = None
    if first_macro and last_macro and first_macro.sp500_close and last_macro.sp500_close:
        sp500_return = round(
            (float(last_macro.sp500_close) / float(first_macro.sp500_close) - 1) * 100, 2,
        )

    # VIX 범위
    vix_values = [v for _, v in macro.vix_series if v is not None]
    vix_start = vix_values[0] if vix_values else None
    vix_end = vix_values[-1] if vix_values else None
    vix_high = max(vix_values) if vix_values else None
    vix_low = min(vix_values) if vix_values else None

    # 시장 분위기 한줄 요약
    regime_end_str = regime.regime
    oneliner = _generate_market_oneliner(sp500_return, vix_end, regime_end_str, perf)

    return WeeklyExecutiveSummary(
        market_oneliner=oneliner,
        sp500_weekly_return_pct=sp500_return,
        vix_start=vix_start,
        vix_end=vix_end,
        vix_high=vix_high,
        vix_low=vix_low,
        regime_start=regime_end_str,  # 현재 구현: 주간 시작 regime 별도 감지 미지원
        regime_end=regime_end_str,
        regime_changed=False,
        weekly_win_rate_pct=perf.win_rate_pct,
        weekly_avg_return_pct=perf.avg_return_pct,
    )


def _generate_market_oneliner(
    sp500_return: float | None,
    vix: float | None,
    regime: str,
    perf: WeeklyPerformanceReview,
) -> str:
    """시장 한줄 요약을 생성한다."""
    regime_kr = {"bull": "강세", "bear": "약세", "range": "횡보", "crisis": "위기"}.get(
        regime, "횡보"
    )
    parts = [f"시장 체제: {regime_kr}"]
    if sp500_return is not None:
        direction = "상승" if sp500_return > 0 else "하락"
        parts.append(f"S&P 500 주간 {direction} ({sp500_return:+.1f}%)")
    if vix is not None:
        if vix > 30:
            parts.append(f"VIX {vix:.1f} (위험)")
        elif vix > 25:
            parts.append(f"VIX {vix:.1f} (주의)")
        else:
            parts.append(f"VIX {vix:.1f} (안정)")
    if perf.win_rate_pct is not None:
        parts.append(f"추천 승률 {perf.win_rate_pct:.0f}%")
    return " | ".join(parts)


# ──────────────────────────────────────────
# Performance Review
# ──────────────────────────────────────────


def _build_performance_review(
    session: Session, date_ids: list[int],
) -> WeeklyPerformanceReview:
    """주간 추천 성과 리뷰."""
    if not date_ids:
        return WeeklyPerformanceReview(
            total_unique_picks=0, win_count=0, loss_count=0,
            win_rate_pct=None, avg_return_pct=None,
            best_pick=None, worst_pick=None,
            ai_approved_avg_return=None, ai_rejected_avg_return=None,
            all_picks=(),
        )

    settings = get_settings()
    tx_cost_pct = settings.transaction_cost_bps / 10000 * 100  # bps → %

    # 주간 추천 전체 조회
    recs = list(
        session.execute(
            select(FactDailyRecommendation)
            .where(FactDailyRecommendation.run_date_id.in_(date_ids))
            .order_by(FactDailyRecommendation.run_date_id, FactDailyRecommendation.rank)
        ).scalars().all()
    )

    if not recs:
        return WeeklyPerformanceReview(
            total_unique_picks=0, win_count=0, loss_count=0,
            win_rate_pct=None, avg_return_pct=None,
            best_pick=None, worst_pick=None,
            ai_approved_avg_return=None, ai_rejected_avg_return=None,
            all_picks=(),
        )

    # stock_id → stock 매핑 배치 로드
    stock_ids = {r.stock_id for r in recs}
    stocks = {
        s.stock_id: s
        for s in session.execute(
            select(DimStock).where(DimStock.stock_id.in_(stock_ids))
        ).scalars().all()
    }

    # 섹터 매핑
    sector_ids = {s.sector_id for s in stocks.values() if s.sector_id}
    sectors = {}
    if sector_ids:
        sectors = {
            s.sector_id: s.sector_name
            for s in session.execute(
                select(DimSector).where(DimSector.sector_id.in_(sector_ids))
            ).scalars().all()
        }

    # 주간 마지막 거래일 종가 배치 로드
    last_date_id = date_ids[-1]
    last_prices = {
        p.stock_id: float(p.close)
        for p in session.execute(
            select(FactDailyPrice)
            .where(
                FactDailyPrice.stock_id.in_(stock_ids),
                FactDailyPrice.date_id == last_date_id,
            )
        ).scalars().all()
    }

    # 종목별 집계
    stock_data: dict[int, dict] = defaultdict(lambda: {
        "ranks": [], "ai_approved": 0, "ai_rejected": 0,
        "first_price": None, "rec_dates": [],
    })
    for rec in recs:
        sid = rec.stock_id
        stock_data[sid]["ranks"].append(rec.rank)
        stock_data[sid]["rec_dates"].append(rec.run_date_id)
        if rec.ai_approved is True:
            stock_data[sid]["ai_approved"] += 1
        elif rec.ai_approved is False:
            stock_data[sid]["ai_rejected"] += 1
        if stock_data[sid]["first_price"] is None:
            stock_data[sid]["first_price"] = float(rec.price_at_recommendation)

    # 종목별 성과 계산
    picks: list[WeeklyPickPerformance] = []
    for sid, data in stock_data.items():
        stock = stocks.get(sid)
        if not stock:
            continue

        sector_name = sectors.get(stock.sector_id) if stock.sector_id else None
        first_price = data["first_price"]
        last_price = last_prices.get(sid)

        weekly_return = None
        if first_price and last_price and first_price > 0:
            weekly_return = round(
                (last_price / first_price - 1) * 100 - tx_cost_pct, 2,
            )

        picks.append(WeeklyPickPerformance(
            ticker=stock.ticker,
            name=stock.name,
            sector=sector_name,
            days_recommended=len(data["ranks"]),
            avg_rank=round(sum(data["ranks"]) / len(data["ranks"]), 1),
            weekly_return_pct=weekly_return,
            ai_approved_days=data["ai_approved"],
            ai_rejected_days=data["ai_rejected"],
        ))

    # 집계
    returns_valid = [p.weekly_return_pct for p in picks if p.weekly_return_pct is not None]
    win_count = sum(1 for r in returns_valid if r > 0)
    loss_count = sum(1 for r in returns_valid if r <= 0)
    win_rate = round(win_count / len(returns_valid) * 100, 1) if returns_valid else None
    avg_return = round(sum(returns_valid) / len(returns_valid), 2) if returns_valid else None

    # 베스트/워스트
    picks_with_return = [p for p in picks if p.weekly_return_pct is not None]
    best = max(picks_with_return, key=lambda p: p.weekly_return_pct) if picks_with_return else None
    worst = min(picks_with_return, key=lambda p: p.weekly_return_pct) if picks_with_return else None

    # AI 승인 vs 비승인 평균 수익률
    ai_approved_returns = [
        p.weekly_return_pct for p in picks
        if p.weekly_return_pct is not None and p.ai_approved_days > 0
    ]
    ai_rejected_returns = [
        p.weekly_return_pct for p in picks
        if p.weekly_return_pct is not None and p.ai_rejected_days > 0 and p.ai_approved_days == 0
    ]
    ai_approved_avg = (
        round(sum(ai_approved_returns) / len(ai_approved_returns), 2)
        if ai_approved_returns else None
    )
    ai_rejected_avg = (
        round(sum(ai_rejected_returns) / len(ai_rejected_returns), 2)
        if ai_rejected_returns else None
    )

    # 수익률 내림차순 정렬
    sorted_picks = tuple(sorted(picks, key=lambda p: p.weekly_return_pct or -999, reverse=True))

    return WeeklyPerformanceReview(
        total_unique_picks=len(picks),
        win_count=win_count,
        loss_count=loss_count,
        win_rate_pct=win_rate,
        avg_return_pct=avg_return,
        best_pick=best,
        worst_pick=worst,
        ai_approved_avg_return=ai_approved_avg,
        ai_rejected_avg_return=ai_rejected_avg,
        all_picks=sorted_picks,
    )


# ──────────────────────────────────────────
# Conviction Picks
# ──────────────────────────────────────────


def _build_conviction_picks(
    session: Session, date_ids: list[int], trading_days: int,
) -> tuple[ConvictionPick, ...]:
    """5거래일 중 3일 이상 추천된 확신 종목."""
    if not date_ids:
        return ()

    min_days = max(2, ceil(trading_days * 0.6))

    recs = list(
        session.execute(
            select(FactDailyRecommendation)
            .where(FactDailyRecommendation.run_date_id.in_(date_ids))
        ).scalars().all()
    )
    if not recs:
        return ()

    # stock_id별 집계
    stock_recs: dict[int, list[FactDailyRecommendation]] = defaultdict(list)
    for rec in recs:
        stock_recs[rec.stock_id].append(rec)

    # 배치 로드
    stock_ids = set(stock_recs.keys())
    stocks = {
        s.stock_id: s
        for s in session.execute(
            select(DimStock).where(DimStock.stock_id.in_(stock_ids))
        ).scalars().all()
    }
    sector_ids = {s.sector_id for s in stocks.values() if s.sector_id}
    sectors = {}
    if sector_ids:
        sectors = {
            s.sector_id: s.sector_name
            for s in session.execute(
                select(DimSector).where(DimSector.sector_id.in_(sector_ids))
            ).scalars().all()
        }

    # 마지막 거래일 종가
    last_date_id = date_ids[-1]
    last_prices = {
        p.stock_id: float(p.close)
        for p in session.execute(
            select(FactDailyPrice)
            .where(
                FactDailyPrice.stock_id.in_(stock_ids),
                FactDailyPrice.date_id == last_date_id,
            )
        ).scalars().all()
    }

    settings = get_settings()
    tx_cost_pct = settings.transaction_cost_bps / 10000 * 100

    conviction: list[ConvictionPick] = []
    for sid, rec_list in stock_recs.items():
        if len(rec_list) < min_days:
            continue

        stock = stocks.get(sid)
        if not stock:
            continue

        sector_name = sectors.get(stock.sector_id) if stock.sector_id else None
        ranks = [r.rank for r in rec_list]
        scores = [float(r.total_score) for r in rec_list]
        rec_date_ids = sorted(r.run_date_id for r in rec_list)

        # 연속 추천 일수 계산
        consecutive = _calc_consecutive_days(rec_date_ids, date_ids)

        # AI 컨센서스
        approved = sum(1 for r in rec_list if r.ai_approved is True)
        rejected = sum(1 for r in rec_list if r.ai_approved is False)
        if approved > rejected:
            ai_consensus = "추천"
        elif rejected > approved:
            ai_consensus = "제외"
        else:
            ai_consensus = "혼재"

        # 주간 수익률
        first_price = float(rec_list[0].price_at_recommendation) if rec_list else None
        last_price = last_prices.get(sid)
        weekly_return = None
        if first_price and last_price and first_price > 0:
            weekly_return = round(
                (last_price / first_price - 1) * 100 - tx_cost_pct, 2,
            )

        conviction.append(ConvictionPick(
            ticker=stock.ticker,
            name=stock.name,
            sector=sector_name,
            days_recommended=len(rec_list),
            consecutive_days=consecutive,
            avg_rank=round(sum(ranks) / len(ranks), 1),
            avg_total_score=round(sum(scores) / len(scores), 1),
            weekly_return_pct=weekly_return,
            ai_consensus=ai_consensus,
        ))

    return tuple(sorted(conviction, key=lambda c: c.days_recommended, reverse=True))


def _calc_consecutive_days(rec_date_ids: list[int], all_date_ids: list[int]) -> int:
    """date_ids 내에서 가장 긴 연속 추천 일수를 계산한다."""
    if not rec_date_ids or not all_date_ids:
        return 0
    rec_set = set(rec_date_ids)
    max_consec = 0
    current = 0
    for did in all_date_ids:
        if did in rec_set:
            current += 1
            max_consec = max(max_consec, current)
        else:
            current = 0
    return max_consec


# ──────────────────────────────────────────
# Sector Rotation
# ──────────────────────────────────────────


def _build_sector_rotation(
    session: Session, date_ids: list[int], prev_date_ids: list[int],
) -> tuple[SectorRotationEntry, ...]:
    """섹터 로테이션 분석."""
    if not date_ids:
        return ()

    # 추천 종목의 섹터별 카운트
    recs = list(
        session.execute(
            select(FactDailyRecommendation)
            .where(FactDailyRecommendation.run_date_id.in_(date_ids))
        ).scalars().all()
    )
    stock_ids = {r.stock_id for r in recs}
    stocks = {
        s.stock_id: s
        for s in session.execute(
            select(DimStock).where(DimStock.stock_id.in_(stock_ids))
        ).scalars().all()
    } if stock_ids else {}

    sector_ids = {s.sector_id for s in stocks.values() if s.sector_id}
    sectors = {}
    if sector_ids:
        sectors = {
            s.sector_id: s.sector_name
            for s in session.execute(
                select(DimSector).where(DimSector.sector_id.in_(sector_ids))
            ).scalars().all()
        }

    # 섹터별 추천 카운트
    sector_pick_count: Counter[str] = Counter()
    for rec in recs:
        stock = stocks.get(rec.stock_id)
        if stock and stock.sector_id:
            sector_name = sectors.get(stock.sector_id, "기타")
            sector_pick_count[sector_name] += 1

    # 섹터별 주간 수익률 (S&P 500 종목 기반)
    all_sp500 = list(
        session.execute(
            select(DimStock)
            .where(DimStock.is_sp500.is_(True), DimStock.is_active.is_(True))
        ).scalars().all()
    )
    all_stock_ids = {s.stock_id for s in all_sp500}

    # 첫/끝 거래일 가격
    first_id, last_id = date_ids[0], date_ids[-1]
    first_prices = {
        p.stock_id: float(p.close)
        for p in session.execute(
            select(FactDailyPrice)
            .where(FactDailyPrice.stock_id.in_(all_stock_ids), FactDailyPrice.date_id == first_id)
        ).scalars().all()
    }
    last_prices = {
        p.stock_id: float(p.close)
        for p in session.execute(
            select(FactDailyPrice)
            .where(FactDailyPrice.stock_id.in_(all_stock_ids), FactDailyPrice.date_id == last_id)
        ).scalars().all()
    }

    # 이번 주 거래량 합산
    this_week_volumes: dict[int, int] = defaultdict(int)
    for p in session.execute(
        select(FactDailyPrice)
        .where(FactDailyPrice.stock_id.in_(all_stock_ids), FactDailyPrice.date_id.in_(date_ids))
    ).scalars().all():
        this_week_volumes[p.stock_id] += int(p.volume)

    # 이전 주 거래량 합산
    prev_week_volumes: dict[int, int] = defaultdict(int)
    if prev_date_ids:
        for p in session.execute(
            select(FactDailyPrice)
            .where(FactDailyPrice.stock_id.in_(all_stock_ids), FactDailyPrice.date_id.in_(prev_date_ids))
        ).scalars().all():
            prev_week_volumes[p.stock_id] += int(p.volume)

    # 섹터별 수익률/거래량 집계
    all_sectors_map = {s.stock_id: sectors.get(s.sector_id, "기타") for s in all_sp500 if s.sector_id}
    sector_returns: dict[str, list[float]] = defaultdict(list)
    sector_vol_this: dict[str, int] = defaultdict(int)
    sector_vol_prev: dict[str, int] = defaultdict(int)

    for stock in all_sp500:
        sec = all_sectors_map.get(stock.stock_id)
        if not sec:
            continue
        fp = first_prices.get(stock.stock_id)
        lp = last_prices.get(stock.stock_id)
        if fp and lp and fp > 0:
            sector_returns[sec].append((lp / fp - 1) * 100)
        sector_vol_this[sec] += this_week_volumes.get(stock.stock_id, 0)
        sector_vol_prev[sec] += prev_week_volumes.get(stock.stock_id, 0)

    entries: list[SectorRotationEntry] = []
    for sec in sorted(set(list(sector_returns.keys()) + list(sector_pick_count.keys()))):
        rets = sector_returns.get(sec, [])
        avg_ret = round(sum(rets) / len(rets), 2) if rets else None

        vol_this = sector_vol_this.get(sec, 0)
        vol_prev = sector_vol_prev.get(sec, 0)
        vol_change = (
            round((vol_this / vol_prev - 1) * 100, 1)
            if vol_prev > 0 else None
        )

        if avg_ret is not None:
            momentum = "상승" if avg_ret > 0.5 else ("하락" if avg_ret < -0.5 else "유지")
        else:
            momentum = "유지"

        entries.append(SectorRotationEntry(
            sector=sec,
            weekly_return_pct=avg_ret,
            volume_change_pct=vol_change,
            momentum_delta=momentum,
            pick_count=sector_pick_count.get(sec, 0),
        ))

    return tuple(sorted(entries, key=lambda e: e.weekly_return_pct or -999, reverse=True))


# ──────────────────────────────────────────
# Macro Summary
# ──────────────────────────────────────────


def _build_macro_summary(
    session: Session, date_ids: list[int],
) -> WeeklyMacroSummary:
    """매크로 환경 주간 변화."""
    if not date_ids:
        return WeeklyMacroSummary(
            daily_scores=(), vix_series=(),
            us_10y_start=None, us_10y_end=None,
            us_13w_start=None, us_13w_end=None,
            spread_start=None, spread_end=None,
            dollar_start=None, dollar_end=None,
            gold_start=None, gold_end=None,
            oil_start=None, oil_end=None,
        )

    macros = list(
        session.execute(
            select(FactMacroIndicator)
            .where(FactMacroIndicator.date_id.in_(date_ids))
            .order_by(FactMacroIndicator.date_id)
        ).scalars().all()
    )

    if not macros:
        return WeeklyMacroSummary(
            daily_scores=(), vix_series=(),
            us_10y_start=None, us_10y_end=None,
            us_13w_start=None, us_13w_end=None,
            spread_start=None, spread_end=None,
            dollar_start=None, dollar_end=None,
            gold_start=None, gold_end=None,
            oil_start=None, oil_end=None,
        )

    daily_scores = tuple(
        (id_to_date(m.date_id).isoformat(), m.market_score) for m in macros
    )
    vix_series = tuple(
        (id_to_date(m.date_id).isoformat(), float(m.vix) if m.vix else None) for m in macros
    )

    first, last = macros[0], macros[-1]

    def _f(v: float | None) -> float | None:
        return round(float(v), 2) if v is not None else None

    return WeeklyMacroSummary(
        daily_scores=daily_scores,
        vix_series=vix_series,
        us_10y_start=_f(first.us_10y_yield),
        us_10y_end=_f(last.us_10y_yield),
        us_13w_start=_f(first.us_13w_yield),
        us_13w_end=_f(last.us_13w_yield),
        spread_start=_f(first.yield_spread),
        spread_end=_f(last.yield_spread),
        dollar_start=_f(first.dollar_index),
        dollar_end=_f(last.dollar_index),
        gold_start=_f(first.gold_price),
        gold_end=_f(last.gold_price),
        oil_start=_f(first.oil_price),
        oil_end=_f(last.oil_price),
    )


# ──────────────────────────────────────────
# Signal Trend
# ──────────────────────────────────────────


def _build_signal_trend(
    session: Session, date_ids: list[int], prev_date_ids: list[int],
) -> WeeklySignalTrend:
    """시그널 트렌드 분석."""
    if not date_ids:
        return WeeklySignalTrend(
            daily_buy_counts=(), daily_sell_counts=(),
            most_frequent_signal=None, avg_strength_change=None,
        )

    # signal_type 방향 매핑
    signal_types = {
        st.signal_type_id: st
        for st in session.execute(select(DimSignalType)).scalars().all()
    }

    signals = list(
        session.execute(
            select(FactSignal)
            .where(FactSignal.date_id.in_(date_ids))
        ).scalars().all()
    )

    # 일별 매수/매도 카운트
    buy_by_date: Counter[int] = Counter()
    sell_by_date: Counter[int] = Counter()
    signal_code_counts: Counter[str] = Counter()
    strengths_this: list[int] = []

    for sig in signals:
        st = signal_types.get(sig.signal_type_id)
        if not st:
            continue
        if st.direction == "BUY":
            buy_by_date[sig.date_id] += 1
        else:
            sell_by_date[sig.date_id] += 1
        signal_code_counts[st.code] += 1
        strengths_this.append(sig.strength)

    daily_buy = tuple(
        (id_to_date(did).isoformat(), buy_by_date.get(did, 0)) for did in date_ids
    )
    daily_sell = tuple(
        (id_to_date(did).isoformat(), sell_by_date.get(did, 0)) for did in date_ids
    )

    most_frequent = signal_code_counts.most_common(1)[0][0] if signal_code_counts else None

    # 이전 주 대비 평균 강도 변화
    avg_strength_change = None
    if prev_date_ids and strengths_this:
        prev_signals = list(
            session.execute(
                select(FactSignal)
                .where(FactSignal.date_id.in_(prev_date_ids))
            ).scalars().all()
        )
        strengths_prev = [s.strength for s in prev_signals]
        if strengths_prev:
            avg_this = sum(strengths_this) / len(strengths_this)
            avg_prev = sum(strengths_prev) / len(strengths_prev)
            avg_strength_change = round(avg_this - avg_prev, 1)

    return WeeklySignalTrend(
        daily_buy_counts=daily_buy,
        daily_sell_counts=daily_sell,
        most_frequent_signal=most_frequent,
        avg_strength_change=avg_strength_change,
    )


# ──────────────────────────────────────────
# AI Accuracy
# ──────────────────────────────────────────


def _build_ai_accuracy(
    session: Session, date_ids: list[int],
) -> WeeklyAIAccuracy:
    """AI 예측 정확도 주간 리뷰."""
    if not date_ids:
        return WeeklyAIAccuracy(
            approval_rate_pct=None, direction_accuracy_pct=None,
            confidence_vs_return_corr=None, total_reviewed=0,
        )

    recs = list(
        session.execute(
            select(FactDailyRecommendation)
            .where(FactDailyRecommendation.run_date_id.in_(date_ids))
        ).scalars().all()
    )

    reviewed = [r for r in recs if r.ai_approved is not None]
    total_reviewed = len(reviewed)

    if not reviewed:
        return WeeklyAIAccuracy(
            approval_rate_pct=None, direction_accuracy_pct=None,
            confidence_vs_return_corr=None, total_reviewed=0,
        )

    approved_count = sum(1 for r in reviewed if r.ai_approved is True)
    approval_rate = round(approved_count / total_reviewed * 100, 1)

    # 방향 정확도: return_20d가 있는 추천 (과거 추천의 성숙된 결과)
    with_returns = [
        r for r in recs if r.return_20d is not None and r.ai_approved is not None
    ]
    direction_acc = None
    if with_returns:
        correct = sum(
            1 for r in with_returns
            if (r.ai_approved and float(r.return_20d) > 0)
            or (not r.ai_approved and float(r.return_20d) <= 0)
        )
        direction_acc = round(correct / len(with_returns) * 100, 1)

    # 신뢰도 vs 수익률 상관관계 (간단 피어슨)
    conf_return_corr = None
    conf_return_pairs = [
        (r.ai_confidence, float(r.return_20d))
        for r in recs
        if r.ai_confidence is not None and r.return_20d is not None
    ]
    if len(conf_return_pairs) >= 5:
        confs = [c for c, _ in conf_return_pairs]
        rets = [r for _, r in conf_return_pairs]
        conf_return_corr = _pearson_corr(confs, rets)

    return WeeklyAIAccuracy(
        approval_rate_pct=approval_rate,
        direction_accuracy_pct=direction_acc,
        confidence_vs_return_corr=conf_return_corr,
        total_reviewed=total_reviewed,
    )


def _pearson_corr(x: list[float], y: list[float]) -> float | None:
    """간단 피어슨 상관계수."""
    n = len(x)
    if n < 2:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sx = sum((xi - mx) ** 2 for xi in x) ** 0.5
    sy = sum((yi - my) ** 2 for yi in y) ** 0.5
    if sx == 0 or sy == 0:
        return None
    return round(cov / (sx * sy), 2)


# ──────────────────────────────────────────
# Outlook
# ──────────────────────────────────────────


def _build_outlook(
    regime: MarketRegime,
    sector_rotation: tuple[SectorRotationEntry, ...],
) -> WeeklyOutlook:
    """다음 주 전망."""
    regime_strategies = {
        "bull": "강세장 지속 — 모멘텀 종목 중심 분할 매수, 수익 종목 보유 유지",
        "bear": "약세장 주의 — 현금 비중 확대, 방어적 섹터 중심, 손절 라인 엄수",
        "range": "횡보장 — 기술적 지지/저항 매매, 확신 종목 소액 분할 매수",
        "crisis": "위기 국면 — 매수 자제, 현금 확보 우선, 역발상 매수는 VIX 안정 후",
    }

    strategy = regime_strategies.get(regime.regime, regime_strategies["range"])

    # 관심/주의 섹터
    watchlist = tuple(
        e.sector for e in sector_rotation
        if e.weekly_return_pct is not None and e.weekly_return_pct > 0.5
    )[:5]
    avoid = tuple(
        e.sector for e in reversed(sector_rotation)
        if e.weekly_return_pct is not None and e.weekly_return_pct < -0.5
    )[:3]

    # 리밸런싱 제안
    if regime.regime == "bull":
        rebalancing = "상승 모멘텀 섹터 비중 확대, 부진 섹터 축소 검토"
    elif regime.regime == "bear":
        rebalancing = "방어 섹터(헬스케어, 유틸리티) 비중 확대, 성장주 비중 축소"
    elif regime.regime == "crisis":
        rebalancing = "포트폴리오 50% 이상 현금화 권고, 급락 시 우량주 저점 매수 준비"
    else:
        rebalancing = "현재 비중 유지, 확신 종목 소폭 추가 매수 검토"

    return WeeklyOutlook(
        regime_strategy=strategy,
        watchlist_sectors=watchlist,
        avoid_sectors=avoid,
        rebalancing_suggestion=rebalancing,
    )


# ──────────────────────────────────────────
# 고도화 섹션 빌더
# ──────────────────────────────────────────


def _build_best_worst_detail(
    session: Session,
    perf: WeeklyPerformanceReview,
    date_ids: list[int],
) -> tuple[WeeklyBestWorstDetail, ...]:
    """베스트/워스트 종목 상세 기술적 분석."""
    targets: list[WeeklyPickPerformance] = []
    if perf.best_pick:
        targets.append(perf.best_pick)
    if perf.worst_pick and (not perf.best_pick or perf.worst_pick.ticker != perf.best_pick.ticker):
        targets.append(perf.worst_pick)

    if not targets or not date_ids:
        return ()

    from src.db.models import DimIndicatorType
    from src.db.repository import StockRepository

    last_date_id = date_ids[-1]
    results: list[WeeklyBestWorstDetail] = []

    for pick in targets:
        stock = StockRepository.get_by_ticker(session, pick.ticker)
        if not stock:
            continue

        # 기술적 지표 조회
        indicators = dict(
            session.execute(
                select(DimIndicatorType.code, FactIndicatorValue.value)
                .join(DimIndicatorType, DimIndicatorType.indicator_type_id == FactIndicatorValue.indicator_type_id)
                .where(
                    FactIndicatorValue.stock_id == stock.stock_id,
                    FactIndicatorValue.date_id == last_date_id,
                )
            ).all()
        )

        rsi = indicators.get("RSI_14")
        macd_hist = indicators.get("MACD_HIST")
        sma_20 = indicators.get("SMA_20")
        sma_60 = indicators.get("SMA_60")
        vol_sma = indicators.get("VOLUME_SMA_20")

        # SMA 정렬 판단
        sma_5 = indicators.get("SMA_5")
        if sma_5 and sma_20 and sma_60:
            alignment = "정배열" if sma_5 > sma_20 > sma_60 else ("역배열" if sma_5 < sma_20 < sma_60 else "혼조")
        else:
            alignment = "혼조"

        # 거래량 비교
        last_price = session.execute(
            select(FactDailyPrice)
            .where(FactDailyPrice.stock_id == stock.stock_id, FactDailyPrice.date_id == last_date_id)
        ).scalar_one_or_none()
        vol_ratio = None
        if last_price and vol_sma and vol_sma > 0:
            vol_ratio = round(float(last_price.volume) / float(vol_sma) * 100, 1)

        # 원인 분석
        ret = pick.weekly_return_pct
        if ret is not None and ret > 0:
            catalyst = "강한 기술적 모멘텀" if alignment == "정배열" else "반등 시그널"
        elif ret is not None and ret < 0:
            catalyst = "기술적 약세 지속" if alignment == "역배열" else "차익실현 압력"
        else:
            catalyst = "횡보"

        results.append(WeeklyBestWorstDetail(
            ticker=pick.ticker,
            name=pick.name,
            weekly_return_pct=pick.weekly_return_pct,
            rsi_14=round(float(rsi), 1) if rsi else None,
            macd_histogram=round(float(macd_hist), 3) if macd_hist else None,
            sma_alignment=alignment,
            volume_vs_avg_pct=vol_ratio,
            sector=pick.sector,
            catalyst_note=catalyst,
        ))

    return tuple(results)


def _build_risk_dashboard(
    perf: WeeklyPerformanceReview,
    macro: WeeklyMacroSummary,
    sector_rotation: tuple[SectorRotationEntry, ...],
) -> RiskDashboard:
    """포트폴리오 수준 리스크 대시보드."""
    # 섹터 집중도
    total_picks = sum(s.pick_count for s in sector_rotation)
    max_sector_pct = None
    top_sector = None
    if total_picks > 0:
        top = max(sector_rotation, key=lambda s: s.pick_count)
        max_sector_pct = round(top.pick_count / total_picks * 100, 1)
        top_sector = top.sector

    # VIX 노출
    vix_values = [v for _, v in macro.vix_series if v is not None]
    last_vix = vix_values[-1] if vix_values else None
    if last_vix is not None:
        vix_exposure = "높음" if last_vix > 25 else ("보통" if last_vix > 18 else "낮음")
    else:
        vix_exposure = "보통"

    return RiskDashboard(
        portfolio_beta=None,  # 향후 개별 종목 beta 집계 시 계산
        max_sector_concentration_pct=max_sector_pct,
        top_sector=top_sector,
        vix_exposure=vix_exposure,
        avg_correlation=None,  # 향후 가격 상관관계 계산 시
        drawdown_from_peak_pct=None,
    )


def _build_win_rate_trend(
    session: Session, year: int, week_number: int,
) -> WinRateTrend:
    """최근 4주 승률 트렌드."""
    import json as _json
    from pathlib import Path

    rates: list[tuple[str, float | None]] = []
    for offset in range(4, 0, -1):
        w = week_number - offset
        y = year
        if w <= 0:
            y -= 1
            w += 52
        week_id = f"{y}-W{w:02d}"
        json_path = Path(f"reports/weekly/{week_id}.json")
        if json_path.exists():
            try:
                data = _json.loads(json_path.read_text(encoding="utf-8"))
                wr = data.get("performance_review", {}).get("win_rate_pct")
                rates.append((week_id, wr))
            except Exception:
                rates.append((week_id, None))
        else:
            rates.append((week_id, None))

    valid = [r for _, r in rates if r is not None]
    avg = round(sum(valid) / len(valid), 1) if valid else None

    if len(valid) >= 2:
        trend = "개선" if valid[-1] > valid[0] else ("악화" if valid[-1] < valid[0] else "유지")
    else:
        trend = "유지"

    return WinRateTrend(
        weekly_rates=tuple(rates),
        trend_direction=trend,
        four_week_avg_pct=avg,
    )


def _build_conviction_technicals(
    session: Session,
    conviction_picks: tuple[ConvictionPick, ...],
    date_ids: list[int],
) -> tuple[ConvictionTechnical, ...]:
    """확신 종목의 기술적 상황."""
    if not conviction_picks or not date_ids:
        return ()

    from src.db.models import DimIndicatorType
    from src.db.repository import StockRepository

    last_date_id = date_ids[-1]
    results: list[ConvictionTechnical] = []

    for pick in conviction_picks:
        stock = StockRepository.get_by_ticker(session, pick.ticker)
        if not stock:
            continue

        indicators = dict(
            session.execute(
                select(DimIndicatorType.code, FactIndicatorValue.value)
                .join(DimIndicatorType, DimIndicatorType.indicator_type_id == FactIndicatorValue.indicator_type_id)
                .where(
                    FactIndicatorValue.stock_id == stock.stock_id,
                    FactIndicatorValue.date_id == last_date_id,
                )
            ).all()
        )

        rsi = indicators.get("RSI_14")
        macd = indicators.get("MACD")
        macd_sig = indicators.get("MACD_SIGNAL")
        sma_5 = indicators.get("SMA_5")
        sma_20 = indicators.get("SMA_20")
        sma_60 = indicators.get("SMA_60")
        bb_upper = indicators.get("BB_UPPER")
        bb_lower = indicators.get("BB_LOWER")

        # MACD 시그널
        if macd is not None and macd_sig is not None:
            macd_str = "매수" if float(macd) > float(macd_sig) else "매도"
        else:
            macd_str = "중립"

        # SMA 정렬
        if sma_5 and sma_20 and sma_60:
            alignment = "정배열" if float(sma_5) > float(sma_20) > float(sma_60) else (
                "역배열" if float(sma_5) < float(sma_20) < float(sma_60) else "혼조"
            )
        else:
            alignment = "혼조"

        # BB 위치
        price_row = session.execute(
            select(FactDailyPrice)
            .where(FactDailyPrice.stock_id == stock.stock_id, FactDailyPrice.date_id == last_date_id)
        ).scalar_one_or_none()
        bb_pos = "중간"
        if price_row and bb_upper and bb_lower:
            price = float(price_row.close)
            if price > float(bb_upper) * 0.98:
                bb_pos = "상단"
            elif price < float(bb_lower) * 1.02:
                bb_pos = "하단"

        results.append(ConvictionTechnical(
            ticker=pick.ticker,
            name=pick.name,
            rsi_14=round(float(rsi), 1) if rsi else None,
            macd_signal=macd_str,
            sma_alignment=alignment,
            bb_position=bb_pos,
            support_price=round(float(bb_lower), 2) if bb_lower else None,
            resistance_price=round(float(bb_upper), 2) if bb_upper else None,
        ))

    return tuple(results)


def _build_week_over_week(
    current_perf: WeeklyPerformanceReview,
    prev_date_ids: list[int],
    session: Session,
) -> WeekOverWeekChange:
    """이전 주 대비 변화."""
    prev_perf = _build_performance_review(session, prev_date_ids) if prev_date_ids else None

    curr_wr = current_perf.win_rate_pct
    curr_ar = current_perf.avg_return_pct
    prev_wr = prev_perf.win_rate_pct if prev_perf else None
    prev_ar = prev_perf.avg_return_pct if prev_perf else None

    wr_delta = round(curr_wr - prev_wr, 1) if curr_wr is not None and prev_wr is not None else None
    ar_delta = round(curr_ar - prev_ar, 2) if curr_ar is not None and prev_ar is not None else None

    # 섹터 변화
    curr_sectors = {p.sector for p in current_perf.all_picks if p.sector}
    prev_sectors = {p.sector for p in prev_perf.all_picks if p.sector} if prev_perf else set()
    new_in = tuple(sorted(curr_sectors - prev_sectors))
    out = tuple(sorted(prev_sectors - curr_sectors))

    return WeekOverWeekChange(
        prev_win_rate_pct=prev_wr,
        curr_win_rate_pct=curr_wr,
        win_rate_delta=wr_delta,
        prev_avg_return_pct=prev_ar,
        curr_avg_return_pct=curr_ar,
        return_delta=ar_delta,
        regime_changed=False,
        new_sectors_in=new_in,
        sectors_out=out,
    )


def _build_action_items(
    regime: MarketRegime,
    conviction_picks: tuple[ConvictionPick, ...],
    sector_rotation: tuple[SectorRotationEntry, ...],
    perf: WeeklyPerformanceReview,
) -> tuple[WeeklyActionItem, ...]:
    """체제/확신종목/섹터/승률 기반 3개 액션 아이템을 생성한다."""
    items: list[WeeklyActionItem] = []

    # 1. 시장 체제 기반 전략
    regime_actions = {
        "bull": ("모멘텀 종목 중심 분할 매수 진행", "강세장에서는 추세 추종이 유리합니다"),
        "bear": ("현금 비중 50% 이상으로 확대", "약세장에서는 자본 보전이 최우선입니다"),
        "range": ("기술적 지지선 근처에서 소액 분할 매수", "횡보장에서는 저점 매수 전략이 효과적입니다"),
        "crisis": ("신규 매수 보류, 기존 포지션 손절 라인 점검", "위기 시 현금 확보가 최우선입니다"),
    }
    action, rationale = regime_actions.get(regime.regime, regime_actions["range"])
    items.append(WeeklyActionItem(priority=1, action=action, rationale=rationale))

    # 2. 확신 종목 기반
    if conviction_picks:
        top = conviction_picks[0]
        items.append(WeeklyActionItem(
            priority=2,
            action=f"{top.ticker} ({top.name}) 관심 종목으로 편입 검토",
            rationale=f"{top.days_recommended}일 연속 추천, 평균 점수 {top.avg_total_score:.1f}/10",
        ))
    else:
        items.append(WeeklyActionItem(
            priority=2,
            action="확신 종목 부재 — 관망 유지",
            rationale="이번 주 3일 이상 추천된 종목이 없어 신규 진입을 자제합니다",
        ))

    # 3. 섹터 기반
    hot = [s for s in sector_rotation if s.weekly_return_pct and s.weekly_return_pct > 1.0]
    cold = [s for s in sector_rotation if s.weekly_return_pct and s.weekly_return_pct < -1.0]
    if hot:
        items.append(WeeklyActionItem(
            priority=3,
            action=f"{hot[0].sector} 섹터 비중 확대 검토",
            rationale=f"주간 {hot[0].weekly_return_pct:+.1f}% 상승, 모멘텀 {hot[0].momentum_delta}",
        ))
    elif cold:
        items.append(WeeklyActionItem(
            priority=3,
            action=f"{cold[0].sector} 섹터 비중 축소 검토",
            rationale=f"주간 {cold[0].weekly_return_pct:+.1f}% 하락, 하락 모멘텀 지속",
        ))
    else:
        items.append(WeeklyActionItem(
            priority=3,
            action="섹터 비중 현행 유지",
            rationale="뚜렷한 섹터 로테이션 신호가 없습니다",
        ))

    return tuple(items)
