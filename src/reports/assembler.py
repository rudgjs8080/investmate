"""리포트 데이터 조립기 --DB에서 풍부한 상세 데이터를 수집하여 EnrichedDailyReport를 조립한다."""

from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.analysis.fundamental import FundamentalScore, analyze_fundamentals
from src.data.schemas import FinancialRecord, ValuationRecord
from src.db.helpers import date_to_id
from src.db.models import (
    DimSignalType,
    DimStock,
    FactDailyPrice,
    FactValuation,
)
from src.db.repository import (
    AnalystConsensusRepository,
    CollectionLogRepository,
    DailyPriceRepository,
    EarningsSurpriseRepository,
    FinancialRepository,
    IndicatorValueRepository,
    InsiderTradeRepository,
    InstitutionalHoldingRepository,
    MacroRepository,
    NewsRepository,
    RecommendationRepository,
    SignalRepository,
    StockRepository,
)
from src.reports.report_models import (
    EarningsDetail,
    EnrichedDailyReport,
    FundamentalDetail,
    MacroEnvironment,
    NewsItem,
    SignalDetail,
    SignalSummaryItem,
    SmartMoneyDetail,
    StockRecommendationDetail,
    TechnicalDetail,
)

logger = logging.getLogger(__name__)


def assemble_enriched_report(
    session: Session, run_date: date, run_date_id: int,
) -> EnrichedDailyReport:
    """DB에서 모든 데이터를 수집하여 풍부한 리포트 모델을 조립한다."""

    # 추천 결과
    recs = RecommendationRepository.get_by_date(session, run_date_id)

    # 매크로
    macro_row = MacroRepository.get_latest(session)
    macro = _build_macro(macro_row)

    # 전체 시그널
    all_signals_raw = SignalRepository.get_by_date(session, run_date_id)
    signal_type_map = _get_signal_type_reverse_map(session)

    # 파이프라인 소요 시간
    duration = _calc_pipeline_duration(session, run_date_id)

    # 전체 종목 수
    total_stocks = len(StockRepository.get_sp500_active(session))

    # 추천 종목 상세
    recommendations = tuple(
        _assemble_stock_detail(session, rec, run_date_id, signal_type_map)
        for rec in recs
    )

    # 전체 시그널 요약
    all_signals = _build_signal_summary(session, all_signals_raw, signal_type_map)
    buy_count = sum(1 for s in all_signals if s.direction == "BUY")
    sell_count = sum(1 for s in all_signals if s.direction == "SELL")

    return EnrichedDailyReport(
        run_date=run_date,
        total_stocks_analyzed=total_stocks,
        stocks_passed_filter=len(recs),
        pipeline_duration_sec=duration,
        macro=macro,
        recommendations=recommendations,
        all_signals=all_signals,
        buy_signal_count=buy_count,
        sell_signal_count=sell_count,
    )


# ──────────────────────────────────────────
# 내부 헬퍼
# ──────────────────────────────────────────


def _build_macro(macro_row) -> MacroEnvironment:
    """매크로 DB 행 → MacroEnvironment 변환."""
    if macro_row is None:
        return MacroEnvironment()

    vix = float(macro_row.vix) if macro_row.vix else None
    sp500 = float(macro_row.sp500_close) if macro_row.sp500_close else None
    sma20 = float(macro_row.sp500_sma20) if macro_row.sp500_sma20 else None
    us_10y = float(macro_row.us_10y_yield) if macro_row.us_10y_yield else None
    us_13w = float(macro_row.us_13w_yield) if macro_row.us_13w_yield else None
    dollar = float(macro_row.dollar_index) if macro_row.dollar_index else None
    score = macro_row.market_score

    vix_status = "미정"
    if vix is not None:
        vix_status = "안정" if vix < 20 else ("주의" if vix < 30 else "위험")

    sp500_trend = "미정"
    if sp500 is not None and sma20 is not None:
        sp500_trend = "상승" if sp500 > sma20 else "하락"

    mood = "미정"
    if score is not None:
        mood = "강세" if score >= 7 else ("중립" if score >= 4 else "약세")

    yield_spread = None
    if us_10y is not None and us_13w is not None:
        yield_spread = round(us_10y - us_13w, 2)

    return MacroEnvironment(
        market_score=score,
        mood=mood,
        vix=vix,
        vix_status=vix_status,
        sp500_close=sp500,
        sp500_sma20=sma20,
        sp500_trend=sp500_trend,
        us_10y_yield=us_10y,
        us_13w_yield=us_13w,
        dollar_index=dollar,
        yield_spread=yield_spread,
    )


def _assemble_stock_detail(
    session: Session, rec, run_date_id: int, signal_type_map: dict,
) -> StockRecommendationDetail:
    """추천 종목 1개에 대한 상세 데이터를 조립한다."""
    stock = session.execute(
        select(DimStock).where(DimStock.stock_id == rec.stock_id)
    ).scalar_one_or_none()

    ticker = stock.ticker if stock else f"#{rec.stock_id}"
    name = stock.name if stock else "Unknown"
    sector = stock.sector.sector_name if stock and stock.sector else None

    # 기술적 분석
    technical = _build_technical(session, rec.stock_id, run_date_id, signal_type_map)

    # 기본적 분석
    fundamental = _build_fundamental(session, rec.stock_id, run_date_id)

    # 수급/스마트머니
    smart_money = _build_smart_money(session, rec.stock_id, float(rec.price_at_recommendation))

    # 실적 서프라이즈
    earnings = _build_earnings(session, rec.stock_id)

    # 뉴스
    news = _build_news(session, rec.stock_id)

    # 전일 대비 변동률
    price_change_pct = _calc_price_change(session, rec.stock_id, run_date_id)

    # 리스크 요인
    risk_factors = _derive_risk_factors(technical, fundamental, smart_money, earnings)

    return StockRecommendationDetail(
        rank=rec.rank,
        ticker=ticker,
        name=name,
        sector=sector,
        price=float(rec.price_at_recommendation),
        price_change_pct=price_change_pct,
        total_score=float(rec.total_score),
        technical_score=float(rec.technical_score),
        fundamental_score=float(rec.fundamental_score),
        smart_money_score=float(rec.smart_money_score) if hasattr(rec, "smart_money_score") and rec.smart_money_score else 5.0,
        external_score=float(rec.external_score),
        momentum_score=float(rec.momentum_score),
        recommendation_reason=rec.recommendation_reason,
        technical=technical,
        fundamental=fundamental,
        smart_money=smart_money,
        earnings=earnings,
        news=news,
        risk_factors=risk_factors,
        ai_approved=rec.ai_approved if hasattr(rec, "ai_approved") else None,
        ai_reason=rec.ai_reason if hasattr(rec, "ai_reason") else None,
        ai_target_price=float(rec.ai_target_price) if hasattr(rec, "ai_target_price") and rec.ai_target_price else None,
        ai_stop_loss=float(rec.ai_stop_loss) if hasattr(rec, "ai_stop_loss") and rec.ai_stop_loss else None,
        ai_confidence=int(rec.ai_confidence) if hasattr(rec, "ai_confidence") and rec.ai_confidence is not None else None,
        ai_risk_level=str(rec.ai_risk_level) if hasattr(rec, "ai_risk_level") and rec.ai_risk_level else None,
        ai_entry_strategy=str(rec.ai_entry_strategy) if hasattr(rec, "ai_entry_strategy") and rec.ai_entry_strategy else None,
        ai_exit_strategy=str(rec.ai_exit_strategy) if hasattr(rec, "ai_exit_strategy") and rec.ai_exit_strategy else None,
        position_weight=float(rec.position_weight) if hasattr(rec, "position_weight") and rec.position_weight is not None else None,
        trailing_stop=float(rec.trailing_stop) if hasattr(rec, "trailing_stop") and rec.trailing_stop is not None else None,
        atr_stop=float(rec.atr_stop) if hasattr(rec, "atr_stop") and rec.atr_stop is not None else None,
        sizing_strategy=str(rec.sizing_strategy) if hasattr(rec, "sizing_strategy") and rec.sizing_strategy else None,
        spread_cost_bps=float(rec.spread_cost_bps) if hasattr(rec, "spread_cost_bps") and rec.spread_cost_bps is not None else None,
        impact_cost_bps=float(rec.impact_cost_bps) if hasattr(rec, "impact_cost_bps") and rec.impact_cost_bps is not None else None,
        total_cost_bps=float(rec.total_cost_bps) if hasattr(rec, "total_cost_bps") and rec.total_cost_bps is not None else None,
    )


def _build_technical(
    session: Session, stock_id: int, date_id: int, signal_type_map: dict,
) -> TechnicalDetail:
    """기술적 지표 + 시그널 조립."""
    indicators = IndicatorValueRepository.get_latest_for_stock(session, stock_id, date_id)
    if not indicators:
        return TechnicalDetail()

    rsi = indicators.get("RSI_14")
    macd = indicators.get("MACD")
    macd_sig = indicators.get("MACD_SIGNAL")
    macd_hist = indicators.get("MACD_HIST")
    sma_5 = indicators.get("SMA_5")
    sma_20 = indicators.get("SMA_20")
    sma_60 = indicators.get("SMA_60")
    sma_120 = indicators.get("SMA_120")
    bb_upper = indicators.get("BB_UPPER")
    bb_middle = indicators.get("BB_MIDDLE")
    bb_lower = indicators.get("BB_LOWER")
    stoch_k = indicators.get("STOCH_K")
    stoch_d = indicators.get("STOCH_D")
    vol_sma = indicators.get("VOLUME_SMA_20")

    # RSI 상태
    rsi_status = "중립"
    if rsi is not None:
        if rsi > 70:
            rsi_status = "과매수"
        elif rsi > 65:
            rsi_status = "과매수 근접"
        elif rsi < 30:
            rsi_status = "과매도"
        elif rsi < 35:
            rsi_status = "과매도 근접"

    # MACD 상태
    macd_status = "중립"
    if macd_hist is not None:
        macd_status = "상승" if macd_hist > 0 else "하락"

    # 이동평균 배열
    sma_alignment = _calc_sma_alignment(sma_5, sma_20, sma_60)

    # 볼린저밴드 위치
    bb_position = "중단"
    if bb_upper is not None and bb_lower is not None and bb_middle is not None:
        close_proxy = sma_5 or bb_middle  # SMA5를 현재가 근사치로 사용
        bb_range = bb_upper - bb_lower
        if bb_range > 0 and close_proxy:
            position_ratio = (close_proxy - bb_lower) / bb_range
            if position_ratio > 0.8:
                bb_position = "상단근접"
            elif position_ratio < 0.2:
                bb_position = "하단근접"

    # 거래량 비율 (최근 가격 데이터에서 계산)
    volume_ratio = None
    if vol_sma and vol_sma > 0:
        latest_volume = session.execute(
            select(FactDailyPrice.volume)
            .where(FactDailyPrice.stock_id == stock_id, FactDailyPrice.date_id <= date_id)
            .order_by(FactDailyPrice.date_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest_volume:
            volume_ratio = round(float(latest_volume) / vol_sma, 2)

    # 시그널 (중복 제거: 같은 signal_type은 강도 최대 1개만)
    signals_raw = SignalRepository.get_by_stock_and_date(session, stock_id, date_id)
    best_by_type: dict[int, object] = {}
    for s in signals_raw:
        existing = best_by_type.get(s.signal_type_id)
        if existing is None or s.strength > existing.strength:
            best_by_type[s.signal_type_id] = s
    deduped_signals = [
        SignalDetail(
            signal_type=signal_type_map.get(s.signal_type_id, f"#{s.signal_type_id}"),
            direction=_get_signal_direction(session, s.signal_type_id),
            strength=s.strength,
            description=s.description or "",
        )
        for s in best_by_type.values()
    ]
    signals = tuple(deduped_signals)

    return TechnicalDetail(
        rsi=rsi, rsi_status=rsi_status,
        macd=macd, macd_signal=macd_sig, macd_hist=macd_hist, macd_status=macd_status,
        sma_5=sma_5, sma_20=sma_20, sma_60=sma_60, sma_120=sma_120,
        sma_alignment=sma_alignment,
        bb_upper=bb_upper, bb_middle=bb_middle, bb_lower=bb_lower, bb_position=bb_position,
        stoch_k=stoch_k, stoch_d=stoch_d,
        volume_ratio=volume_ratio,
        signals=signals,
    )


def _build_fundamental(
    session: Session, stock_id: int, date_id: int,
) -> FundamentalDetail:
    """기본적 분석 상세 조립."""
    # 재무제표
    financials_raw = FinancialRepository.get_by_stock(session, stock_id)
    financials = [
        FinancialRecord(
            period=f.period,
            revenue=float(f.revenue) if f.revenue else None,
            operating_income=float(f.operating_income) if f.operating_income else None,
            net_income=float(f.net_income) if f.net_income else None,
            total_assets=float(f.total_assets) if f.total_assets else None,
            total_liabilities=float(f.total_liabilities) if f.total_liabilities else None,
            total_equity=float(f.total_equity) if f.total_equity else None,
            operating_cashflow=float(f.operating_cashflow) if f.operating_cashflow else None,
        )
        for f in financials_raw[:4]
    ]

    # 밸류에이션
    val_row = session.execute(
        select(FactValuation)
        .where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    val_record = None
    if val_row:
        val_record = ValuationRecord(
            date=date(2000, 1, 1),  # placeholder
            market_cap=float(val_row.market_cap) if val_row.market_cap else None,
            per=float(val_row.per) if val_row.per else None,
            pbr=float(val_row.pbr) if val_row.pbr else None,
            roe=float(val_row.roe) if val_row.roe else None,
            debt_ratio=float(val_row.debt_ratio) if val_row.debt_ratio else None,
            dividend_yield=float(val_row.dividend_yield) if val_row.dividend_yield else None,
            ev_ebitda=float(val_row.ev_ebitda) if val_row.ev_ebitda else None,
        )

    score: FundamentalScore = analyze_fundamentals(financials, val_record)

    return FundamentalDetail(
        per=val_record.per if val_record else None,
        per_score=score.per_score,
        pbr=val_record.pbr if val_record else None,
        pbr_score=score.pbr_score,
        roe=val_record.roe if val_record else None,
        roe_score=score.roe_score,
        debt_ratio=val_record.debt_ratio if val_record else None,
        debt_score=score.debt_score,
        growth_score=score.growth_score,
        composite_score=score.composite_score,
        summary=score.summary,
        market_cap=val_record.market_cap if val_record else None,
        dividend_yield=val_record.dividend_yield if val_record else None,
        ev_ebitda=val_record.ev_ebitda if val_record else None,
    )


def _build_smart_money(
    session: Session, stock_id: int, current_price: float,
) -> SmartMoneyDetail:
    """수급/스마트머니 상세 조립."""
    # 애널리스트
    consensus = AnalystConsensusRepository.get_latest(session, stock_id)
    sb = buy = hold = sell = ss = 0
    target_mean = target_high = target_low = upside_pct = None

    if consensus:
        sb, buy, hold, sell, ss = (
            consensus.strong_buy, consensus.buy, consensus.hold,
            consensus.sell, consensus.strong_sell,
        )
        target_mean = float(consensus.target_mean) if consensus.target_mean else None
        target_high = float(consensus.target_high) if consensus.target_high else None
        target_low = float(consensus.target_low) if consensus.target_low else None
        if target_mean and current_price > 0:
            upside_pct = round((target_mean - current_price) / current_price * 100, 1)

    # 내부자 거래
    insider_trades = InsiderTradeRepository.get_by_stock(session, stock_id, limit=20)
    insider_net = 0.0
    for t in insider_trades:
        val = float(t.value) if t.value else 0
        if t.transaction_type in ("Purchase", "Buy"):
            insider_net += val
        elif t.transaction_type in ("Sale", "Sell"):
            insider_net -= val

    insider_summary = "데이터 없음"
    if insider_trades:
        if insider_net > 0:
            insider_summary = f"순매수 ${_fmt_large_num(insider_net)}"
        elif insider_net < 0:
            insider_summary = f"순매도 ${_fmt_large_num(abs(insider_net))}"
        else:
            insider_summary = "매수/매도 균형"

    # 기관 보유
    holdings = InstitutionalHoldingRepository.get_by_stock(session, stock_id, limit=5)
    top_institutions = tuple(
        (h.institution_name, float(h.value) if h.value else 0)
        for h in holdings[:3]
    )

    # 공매도 (밸류에이션 테이블에서)
    val_row = session.execute(
        select(FactValuation)
        .where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    short_ratio = float(val_row.short_ratio) if val_row and val_row.short_ratio else None
    short_pct = float(val_row.short_pct_of_float) if val_row and val_row.short_pct_of_float else None

    return SmartMoneyDetail(
        analyst_strong_buy=sb, analyst_buy=buy, analyst_hold=hold,
        analyst_sell=sell, analyst_strong_sell=ss,
        target_mean=target_mean, target_high=target_high, target_low=target_low,
        upside_pct=upside_pct,
        insider_net_value=insider_net if insider_trades else None,
        insider_summary=insider_summary,
        top_institutions=top_institutions,
        short_ratio=short_ratio, short_pct=short_pct,
    )


def _build_earnings(session: Session, stock_id: int) -> EarningsDetail:
    """실적 서프라이즈 상세 조립."""
    earnings_list = EarningsSurpriseRepository.get_by_stock(session, stock_id, limit=8)
    if not earnings_list:
        return EarningsDetail()

    latest = earnings_list[0]

    # 연속 상회 계산
    beat_streak = 0
    for e in earnings_list:
        if e.surprise_pct is not None and float(e.surprise_pct) > 0:
            beat_streak += 1
        else:
            break

    return EarningsDetail(
        latest_period=latest.period,
        eps_surprise_pct=float(latest.surprise_pct) if latest.surprise_pct else None,
        revenue_surprise_pct=float(latest.revenue_surprise_pct) if latest.revenue_surprise_pct else None,
        beat_streak=beat_streak,
    )


def _build_news(session: Session, stock_id: int) -> tuple[NewsItem, ...]:
    """종목 관련 뉴스 조립."""
    articles = NewsRepository.get_by_stock(session, stock_id, limit=5)
    return tuple(
        NewsItem(
            title=a.title,
            source=a.source,
            published_at=a.published_at.strftime("%Y-%m-%d") if a.published_at else None,
            sentiment_score=float(a.sentiment_score) if a.sentiment_score else None,
        )
        for a in articles
    )


def _calc_price_change(
    session: Session, stock_id: int, date_id: int,
) -> float | None:
    """전일 대비 변동률 계산."""
    prices = session.execute(
        select(FactDailyPrice.close)
        .where(FactDailyPrice.stock_id == stock_id)
        .order_by(FactDailyPrice.date_id.desc())
        .limit(2)
    ).scalars().all()

    if len(prices) >= 2:
        today = float(prices[0])
        yesterday = float(prices[1])
        if yesterday != 0:
            return round((today - yesterday) / yesterday * 100, 2)
    return None


def _calc_pipeline_duration(session: Session, run_date_id: int) -> float | None:
    """파이프라인 총 소요 시간 계산."""
    logs = CollectionLogRepository.get_by_run_date(session, run_date_id)
    if not logs:
        return None

    total = 0.0
    for log in logs:
        if log.started_at and log.finished_at:
            total += (log.finished_at - log.started_at).total_seconds()
    return round(total, 1) if total > 0 else None


def _build_signal_summary(
    session: Session, signals_raw: list, signal_type_map: dict,
) -> tuple[SignalSummaryItem, ...]:
    """전체 시그널 → 요약 목록 (종목+시그널유형 기준 중복 제거)."""
    items = []
    stock_cache: dict[int, DimStock | None] = {}
    seen: set[tuple[int, int]] = set()  # (stock_id, signal_type_id)

    for s in signals_raw:
        key = (s.stock_id, s.signal_type_id)
        if key in seen:
            continue
        seen.add(key)

        if s.stock_id not in stock_cache:
            stock_cache[s.stock_id] = session.execute(
                select(DimStock).where(DimStock.stock_id == s.stock_id)
            ).scalar_one_or_none()

        stock = stock_cache[s.stock_id]
        items.append(SignalSummaryItem(
            ticker=stock.ticker if stock else f"#{s.stock_id}",
            name=stock.name if stock else "Unknown",
            signal_type=signal_type_map.get(s.signal_type_id, f"#{s.signal_type_id}"),
            direction=_get_signal_direction(session, s.signal_type_id),
            strength=s.strength,
            description=s.description or "",
        ))

    return tuple(items)


def _get_signal_type_reverse_map(session: Session) -> dict[int, str]:
    """signal_type_id → code 매핑."""
    stmt = select(DimSignalType.signal_type_id, DimSignalType.code)
    rows = session.execute(stmt).all()
    return {tid: code for tid, code in rows}


_direction_cache: dict[int, str] = {}


def _get_signal_direction(session: Session, signal_type_id: int) -> str:
    """시그널 방향(BUY/SELL) 조회."""
    if signal_type_id in _direction_cache:
        return _direction_cache[signal_type_id]

    row = session.execute(
        select(DimSignalType.direction)
        .where(DimSignalType.signal_type_id == signal_type_id)
    ).scalar_one_or_none()

    direction = row or "HOLD"
    _direction_cache[signal_type_id] = direction
    return direction


def _calc_sma_alignment(
    sma_5: float | None, sma_20: float | None, sma_60: float | None,
) -> str:
    """이동평균 배열 판단."""
    if sma_5 is None or sma_20 is None or sma_60 is None:
        return "혼조"
    if sma_5 > sma_20 > sma_60:
        return "정배열"
    if sma_5 < sma_20 < sma_60:
        return "역배열"
    return "혼조"


def _derive_risk_factors(
    tech: TechnicalDetail, fund: FundamentalDetail,
    smart: SmartMoneyDetail, earnings: EarningsDetail,
) -> tuple[str, ...]:
    """데이터 기반 리스크 요인 자동 생성."""
    risks: list[str] = []

    if tech.rsi is not None and tech.rsi > 65:
        risks.append(f"RSI {tech.rsi:.0f} --과매수 접근 중")
    if tech.sma_alignment == "역배열":
        risks.append("이동평균 역배열 --하락 추세")

    if fund.per is not None and fund.per > 35:
        risks.append(f"PER {fund.per:.1f} --고평가 구간")
    if fund.debt_ratio is not None and fund.debt_ratio > 0.6:
        risks.append(f"부채비율 {fund.debt_ratio:.1%} --높은 수준")

    if smart.short_pct is not None and smart.short_pct > 5:
        risks.append(f"공매도 {smart.short_pct:.1f}% --주의 필요")
    if smart.insider_net_value is not None and smart.insider_net_value < -100_000:
        risks.append("내부자 순매도 진행 중")
    if smart.upside_pct is not None and smart.upside_pct < 0:
        risks.append(f"애널리스트 목표가 하회 ({smart.upside_pct:+.1f}%)")

    if earnings.beat_streak == 0 and earnings.latest_period:
        risks.append("최근 실적 미달")

    if not risks:
        risks.append("특별한 리스크 요인 없음")

    return tuple(risks)


def _fmt_large_num(n: float) -> str:
    """큰 숫자를 K/M/B 형식으로 포맷."""
    if abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.0f}K"
    return f"{n:,.0f}"
