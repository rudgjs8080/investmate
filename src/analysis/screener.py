"""종목 스크리닝 + 랭킹 엔진."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd
from sqlalchemy.orm import Session

from src.analysis.fundamental import analyze_fundamentals, build_sector_medians
from src.analysis.signals import _SIGNAL_WEIGHTS, detect_signals
from src.data.schemas import FinancialRecord, RecommendationData, ValuationRecord
from src.db.helpers import date_to_id
from src.db.repository import (
    DailyPriceRepository,
    FinancialRepository,
    IndicatorValueRepository,
    SignalRepository,
    StockRepository,
    ValuationRepository,
)

logger = logging.getLogger(__name__)

# 스코어링 가중치 (CLAUDE.md 스펙 기반)
WEIGHT_TECHNICAL = 0.25
WEIGHT_FUNDAMENTAL = 0.25
WEIGHT_SMART_MONEY = 0.15
WEIGHT_EXTERNAL = 0.15
WEIGHT_MOMENTUM = 0.20

# 필터 임계값 (기본값, config로 오버라이드 가능)
def _get_filter_thresholds() -> tuple[int, int]:
    """설정에서 스크리너 필터 임계값을 로드한다."""
    try:
        from src.config import get_settings
        s = get_settings()
        return s.screener_min_data_days, s.screener_min_volume
    except Exception:
        return 60, 100_000

MIN_DATA_DAYS, MIN_VOLUME = _get_filter_thresholds()


def _get_max_sector_pct() -> float:
    """설정에서 섹터 집중도 상한을 로드한다."""
    try:
        from src.config import get_settings
        return get_settings().max_sector_pct
    except Exception:
        return 0.4


def _apply_sector_cap(
    candidates: list[dict], max_per_sector: int,
) -> list[dict]:
    """섹터 집중도 제한을 적용한다.

    단일 섹터의 종목 수가 max_per_sector를 초과하면
    해당 섹터에서 점수가 낮은 종목을 후순위로 밀어낸다.
    """
    sector_counts: dict[str, int] = {}
    result: list[dict] = []
    overflow: list[dict] = []

    for cand in candidates:
        sector = cand.get("sector_name", "기타")
        count = sector_counts.get(sector, 0)
        if count < max_per_sector:
            result.append(cand)
            sector_counts[sector] = count + 1
        else:
            overflow.append(cand)

    # 초과 종목을 뒤에 붙임 (다른 섹터 종목이 먼저 선발된 후 필요 시 사용)
    return result + overflow


def _apply_correlation_filter(
    candidates: list[dict],
    session: Session,
    top_n: int,
) -> list[dict]:
    """고상관(>0.7) 종목을 제거하고 차순위로 교체한다.

    상위 top_n 후보의 20일 수익률 상관 행렬을 검사하여
    평균 상관계수가 0.7을 초과하는 종목 중 점수가 가장 낮은 것을 제거한다.
    """
    import numpy as np

    if len(candidates) <= 2:
        return candidates

    check_size = min(len(candidates), top_n + 5)  # 여유분 포함
    top_candidates = candidates[:check_size]

    # 20일 수익률 데이터 수집
    from sqlalchemy import select as sa_select
    stock_returns: dict[int, list[float]] = {}
    for cand in top_candidates:
        sid = cand["stock_id"]
        from src.db.models import FactDailyPrice
        rows = session.execute(
            sa_select(FactDailyPrice.adj_close)
            .where(FactDailyPrice.stock_id == sid)
            .order_by(FactDailyPrice.date_id.desc())
            .limit(61)
        ).scalars().all()

        if len(rows) >= 2:
            prices = [float(p) for p in reversed(rows)]
            returns = [(prices[i] / prices[i - 1] - 1) for i in range(1, len(prices))]
            stock_returns[sid] = returns

    # 상관 행렬 계산 (top_n만)
    top_sids = [c["stock_id"] for c in top_candidates[:top_n] if c["stock_id"] in stock_returns]
    if len(top_sids) < 3:
        return candidates

    # 모든 종목의 수익률 길이를 맞춤
    min_len = min(len(stock_returns[sid]) for sid in top_sids)
    if min_len < 5:
        return candidates

    return_matrix = np.array([stock_returns[sid][:min_len] for sid in top_sids])
    corr_matrix = np.corrcoef(return_matrix)

    # 각 종목의 평균 상관계수 (자기 자신 제외)
    n = len(top_sids)
    removed_sids: set[int] = set()

    for _ in range(1):  # 단일 패스
        avg_corrs = {}
        active = [i for i in range(n) if top_sids[i] not in removed_sids]
        if len(active) < 3:
            break

        for i in active:
            others = [corr_matrix[i][j] for j in active if j != i]
            avg_corrs[i] = sum(others) / len(others) if others else 0

        # 평균 상관 > 0.75인 종목 중 점수가 가장 낮은 것 제거
        high_corr = [(idx, avg_corrs[idx]) for idx in active if avg_corrs[idx] > 0.75]
        if not high_corr:
            break

        # 점수 기준 최하위 제거
        high_corr.sort(key=lambda x: next(
            c["total_score"] for c in top_candidates if c["stock_id"] == top_sids[x[0]]
        ))
        worst_idx = high_corr[0][0]
        removed_sids.add(top_sids[worst_idx])
        logger.info(
            "상관관계 필터: %s 제거 (평균 상관 %.2f)",
            next(c["ticker"] for c in top_candidates if c["stock_id"] == top_sids[worst_idx]),
            high_corr[0][1],
        )

    if not removed_sids:
        return candidates

    return [c for c in candidates if c["stock_id"] not in removed_sids]


def screen_and_rank(
    session: Session,
    run_date: date,
    top_n: int = 10,
    market_score: int = 5,
    news_sentiment: float = 0.0,
    sector_momentum: dict[str, float] | None = None,
) -> list[RecommendationData]:
    """S&P 500 전 종목을 스크리닝하고 랭킹한다.

    Returns:
        상위 N개 추천 종목 리스트.
    """
    from src.analysis.technical import calculate_indicators, load_date_map, prices_to_dataframe

    stocks = StockRepository.get_sp500_active(session)
    if not stocks:
        logger.warning("스크리닝 대상 종목 없음")
        return []

    # 시장 레짐 감지 → 적응형 가중치 적용
    try:
        from src.analysis.regime import REGIME_WEIGHTS, detect_regime
        regime = detect_regime(session)
        weights = REGIME_WEIGHTS[regime.regime]
        logger.info(
            "적응형 가중치 적용: 레짐=%s (신뢰도=%.2f), 가중치=%s",
            regime.regime, regime.confidence, weights,
        )
    except Exception as e:
        logger.warning("레짐 감지 실패, 기본 가중치 사용: %s", e)
        weights = {
            "technical": WEIGHT_TECHNICAL,
            "fundamental": WEIGHT_FUNDAMENTAL,
            "smart_money": WEIGHT_SMART_MONEY,
            "external": WEIGHT_EXTERNAL,
            "momentum": WEIGHT_MOMENTUM,
        }

    # 현재 VIX 조회 (적응형 모멘텀용)
    current_vix: float | None = None
    try:
        from src.db.repository import MacroRepository
        macro = MacroRepository.get_latest(session)
        if macro and macro.vix is not None:
            current_vix = float(macro.vix)
    except Exception:
        pass

    # 배치 캐시 로드 (500종목 × 개별 쿼리 → 각 1회)
    date_map = load_date_map(session)
    val_map = ValuationRepository.get_latest_all(session)
    run_date_id = date_to_id(run_date)

    # 섹터별 밸류에이션 중앙값 (상대 비교용)
    try:
        all_sector_medians = build_sector_medians(session)
    except Exception:
        all_sector_medians = {}

    # 실적발표 임박 종목 사전 필터
    earnings_blacklist: set[int] = set()
    try:
        from src.data.event_collector import collect_earnings_calendar
        tickers_for_earnings = [s.ticker for s in stocks]
        earnings_ctx = collect_earnings_calendar(tickers_for_earnings, run_date)
        for ticker, ctx in earnings_ctx.items():
            if ctx.is_pre_earnings:
                stock_obj = next((s for s in stocks if s.ticker == ticker), None)
                if stock_obj:
                    earnings_blacklist.add(stock_obj.stock_id)
                    logger.info("실적발표 임박 제외: %s", ticker)
    except Exception as e:
        logger.debug("실적 캘린더 조회 실패: %s", e)

    candidates: list[dict] = []

    for stock in stocks:
        try:
            df = prices_to_dataframe(session, stock.stock_id, date_map=date_map)
            if df.empty or len(df) < MIN_DATA_DAYS:
                continue

            indicators_df = calculate_indicators(df)
            latest = indicators_df.iloc[-1]

            # ── 1단계: 필터링 ──
            if not _passes_filter(latest, df):
                continue

            # 기본적 필터 (캐시된 밸류에이션 사용)
            if not _passes_fundamental_filter(session, stock.stock_id, val_map=val_map):
                continue

            # 밸류에이션 데이터 신선도 경고 (필터 아님, 로깅만)
            _val = val_map.get(stock.stock_id)
            if _val and hasattr(_val, 'date_id'):
                val_age = run_date_id - _val.date_id if _val.date_id else 0
                if val_age > 9000:  # ~90 days in YYYYMMDD format (rough check)
                    logger.debug("밸류에이션 데이터 오래됨: %s (%d일 전)", stock.ticker, val_age // 100)

            # 실적발표 임박 종목 제외
            if stock.stock_id in earnings_blacklist:
                continue

            # ── 2단계: 스코어링 ──
            tech_score = _score_technical(indicators_df, session, stock.stock_id)
            sector_name = stock.sector.sector_name if stock.sector else None
            sector_meds = all_sector_medians.get(sector_name) if sector_name else None
            fund_score = _score_fundamental(session, stock.stock_id, sector_medians=sector_meds)
            smart_score = _score_smart_money(session, stock.stock_id, latest)
            ext_score = _score_external(
                market_score, news_sentiment,
                sector_momentum, stock.sector,
            )
            mom_score = _score_momentum(df, latest, vix=current_vix)

            total = (
                tech_score * weights["technical"]
                + fund_score * weights["fundamental"]
                + smart_score * weights["smart_money"]
                + ext_score * weights["external"]
                + mom_score * weights["momentum"]
            )

            reason = _generate_reason(
                stock.ticker, tech_score, fund_score, ext_score, mom_score, latest,
                smart_score,
            )

            # 팩터 어트리뷰션: 각 팩터가 총점에 기여한 비율(%)
            factor_attribution = _compute_factor_attribution(
                tech_score, fund_score, smart_score, ext_score, mom_score,
                weights, total,
            )
            attribution_suffix = (
                f" [팩터: 기술{factor_attribution['technical']}%"
                f" 펀더{factor_attribution['fundamental']}%"
                f" 수급{factor_attribution['smart_money']}%"
                f" 외부{factor_attribution['external']}%"
                f" 모멘텀{factor_attribution['momentum']}%]"
            )

            candidates.append({
                "stock_id": stock.stock_id,
                "ticker": stock.ticker,
                "name": stock.name,
                "sector_name": sector_name or "기타",
                "total_score": round(total, 4),
                "technical_score": round(tech_score, 2),
                "fundamental_score": round(fund_score, 2),
                "smart_money_score": round(smart_score, 2),
                "external_score": round(ext_score, 2),
                "momentum_score": round(mom_score, 2),
                "recommendation_reason": reason + attribution_suffix,
                "price_at_recommendation": float(latest["close"]),
                "factor_attribution": factor_attribution,
            })

        except Exception as e:
            logger.warning("스크리닝 실패 [%s]: %s", stock.ticker, e)
            continue

    # ── 3단계: 랭킹 ──
    candidates.sort(key=lambda x: x["total_score"], reverse=True)

    # 같은 회사 중복 제거 (GOOGL/GOOG, BRK-A/BRK-B 등)
    seen_names: set[str] = set()
    deduped: list[dict] = []
    for cand in candidates:
        # 종목명 기반 중복 제거 (동일 회사 dual-class 방지)
        base_name = cand["name"].split("(")[0].strip()  # "Alphabet Inc. (Class A)" -> "Alphabet Inc."
        if base_name in seen_names:
            continue
        seen_names.add(base_name)
        deduped.append(cand)

    # 섹터 집중도 제한 (단일 섹터 최대 max_sector_pct)
    max_sector_pct = _get_max_sector_pct()
    max_per_sector = max(1, int(top_n * max_sector_pct))
    deduped = _apply_sector_cap(deduped, max_per_sector)

    # 상관관계 필터 (고상관 종목 제거)
    deduped = _apply_correlation_filter(deduped, session, top_n)

    top = deduped[:top_n]

    # 팩터 집중 경고: 단일 팩터가 50% 초과 시 로깅
    if top:
        _warn_factor_concentration(top)

    # sector_name, factor_attribution은 내부용 — RecommendationData에는 불필요
    results = []
    for rank, cand in enumerate(top, start=1):
        cand_clean = {
            k: v for k, v in cand.items()
            if k not in ("sector_name", "factor_attribution")
        }
        results.append(RecommendationData(rank=rank, **cand_clean))

    logger.info(
        "스크리닝 완료: %d/%d 통과, 상위 %d 선정",
        len(candidates), len(stocks), len(results),
    )
    return results


def _compute_factor_attribution(
    tech_score: float,
    fund_score: float,
    smart_score: float,
    ext_score: float,
    mom_score: float,
    weights: dict[str, float],
    total: float,
) -> dict[str, float]:
    """각 팩터의 총점 기여 비율(%)을 계산한다."""
    if total <= 0:
        return {
            "technical": 0.0,
            "fundamental": 0.0,
            "smart_money": 0.0,
            "external": 0.0,
            "momentum": 0.0,
        }
    return {
        "technical": round(tech_score * weights["technical"] / total * 100, 1),
        "fundamental": round(fund_score * weights["fundamental"] / total * 100, 1),
        "smart_money": round(smart_score * weights["smart_money"] / total * 100, 1),
        "external": round(ext_score * weights["external"] / total * 100, 1),
        "momentum": round(mom_score * weights["momentum"] / total * 100, 1),
    }


def _warn_factor_concentration(top_candidates: list[dict]) -> None:
    """단일 팩터가 평균 50% 이상이면 경고를 로깅한다."""
    factors = ["technical", "fundamental", "smart_money", "external", "momentum"]
    avg_attrs: dict[str, float] = {}
    for factor in factors:
        values = [
            c.get("factor_attribution", {}).get(factor, 20.0)
            for c in top_candidates
        ]
        avg_attrs[factor] = sum(values) / len(values) if values else 20.0

    dominant = max(avg_attrs, key=avg_attrs.get)  # type: ignore[arg-type]
    if avg_attrs[dominant] > 50:
        logger.warning(
            "팩터 집중 경고: %s %.1f%% — 포트폴리오 과노출",
            dominant,
            avg_attrs[dominant],
        )


def calculate_portfolio_beta(
    session: Session,
    stock_ids: list[int],
    lookback_days: int = 60,
) -> float | None:
    """추천 포트폴리오의 Beta를 계산한다 (S&P 500 대비).

    각 종목의 일일 수익률과 S&P 500 일일 수익률의 공분산/분산 비율로
    개별 베타를 구하고, 동일 가중 평균으로 포트폴리오 베타를 산출한다.
    """
    import numpy as np
    from sqlalchemy import select

    from src.db.models import FactDailyPrice, FactMacroIndicator

    if not stock_ids:
        return None

    # S&P 500 일일 수익률 (최근 lookback_days+1 레코드)
    macro_rows = (
        session.execute(
            select(FactMacroIndicator.sp500_close)
            .where(FactMacroIndicator.sp500_close.isnot(None))
            .order_by(FactMacroIndicator.date_id.desc())
            .limit(lookback_days + 1)
        )
        .scalars()
        .all()
    )
    if len(macro_rows) < 10:
        return None

    spy_prices = [float(p) for p in reversed(macro_rows)]
    spy_returns = np.array([
        spy_prices[i] / spy_prices[i - 1] - 1
        for i in range(1, len(spy_prices))
    ])

    # 각 종목의 베타 계산
    betas: list[float] = []
    spy_var = float(np.var(spy_returns, ddof=0))
    if spy_var == 0:
        return None

    for sid in stock_ids:
        rows = (
            session.execute(
                select(FactDailyPrice.adj_close)
                .where(FactDailyPrice.stock_id == sid)
                .order_by(FactDailyPrice.date_id.desc())
                .limit(lookback_days + 1)
            )
            .scalars()
            .all()
        )
        if len(rows) < 10:
            continue

        prices = [float(p) for p in reversed(rows)]
        stock_returns = np.array([
            prices[i] / prices[i - 1] - 1
            for i in range(1, len(prices))
        ])

        # 길이 맞춤 (짧은 쪽 기준)
        min_len = min(len(stock_returns), len(spy_returns))
        sr = stock_returns[:min_len]
        spr = spy_returns[:min_len]

        cov = float(np.cov(sr, spr, ddof=0)[0][1])
        var = float(np.var(spr, ddof=0))
        if var > 0:
            betas.append(cov / var)

    if not betas:
        return None

    return round(sum(betas) / len(betas), 4)


def _passes_fundamental_filter(
    session: Session, stock_id: int,
    val_map: dict | None = None,
) -> bool:
    """기본적 필터를 통과하는지 확인한다 (재무 데이터 기반)."""
    # 캐시된 밸류에이션 사용 (없으면 개별 쿼리 fallback)
    if val_map is not None:
        val = val_map.get(stock_id)
    else:
        from src.db.models import FactValuation
        from sqlalchemy import select
        val = session.execute(
            select(FactValuation)
            .where(FactValuation.stock_id == stock_id)
            .order_by(FactValuation.date_id.desc())
            .limit(1)
        ).scalar_one_or_none()

    if val is None:
        # 재무 데이터 없으면 필터 통과 (데이터 부족은 제외하지 않음)
        return True

    # PER <= 0이면 적자 기업 제외 (PER이 None이면 통과)
    if val.per is not None and float(val.per) <= 0:
        return False

    # 부채비율 > 0.8이면 과도한 부채 제외
    if val.debt_ratio is not None and float(val.debt_ratio) > 0.8:
        return False

    # Altman Z-Score 부도 위험 필터
    try:
        from src.analysis.quality import calculate_altman_z
        from src.db.repository import FinancialRepository as _FinRepo

        fins = _FinRepo.get_by_stock(session, stock_id)
        if fins:
            market_cap = float(val.market_cap) if val.market_cap else None
            altman = calculate_altman_z(fins[0], market_cap)
            if altman.zone == "distress":
                return False
    except Exception:
        pass

    return True


def _passes_filter(latest: pd.Series, df: pd.DataFrame) -> bool:
    """기본 필터를 통과하는지 확인한다."""
    # 최소 거래량
    if latest.get("volume", 0) < MIN_VOLUME:
        return False

    # 달러 거래량 필터 ($500K 미만 제외)
    close = latest.get("close")
    volume = latest.get("volume")
    if close is not None and volume is not None and not pd.isna(close) and not pd.isna(volume):
        dollar_volume = float(close) * float(volume)
        if dollar_volume < 500_000:
            return False

    # RSI 과매수 제외
    rsi = latest.get("rsi_14")
    if rsi is not None and not pd.isna(rsi) and rsi > 70:
        return False

    # 장기 상승 추세 (가격 > SMA120, 회복 초기 종목은 완화)
    sma120 = latest.get("sma_120")
    close = latest.get("close")
    if sma120 is not None and not pd.isna(sma120) and close is not None and sma120 > 0:
        gap_pct = (close - sma120) / sma120
        if gap_pct < -0.05:
            # SMA120 대비 5% 이상 하회 → 제외
            return False
        elif gap_pct < 0:
            # 5% 이내 하회: RSI 과매도(< 40)일 때만 통과 (회복 초기)
            rsi = latest.get("rsi_14")
            if rsi is None or pd.isna(rsi) or rsi > 40:
                return False

    return True


def _score_technical(
    indicators_df: pd.DataFrame, session: Session, stock_id: int,
) -> float:
    """기술적 분석 점수 (1-10)."""
    detected = detect_signals(indicators_df, stock_id)
    buy_signals = [s for s in detected if s.direction == "BUY"]
    sell_signals = [s for s in detected if s.direction == "SELL"]

    # Maximum possible weighted score for normalization
    # (all 5 BUY signals with max strength 10)
    max_weighted_score = sum(
        10 * _SIGNAL_WEIGHTS.get(code, 1)
        for code in ["golden_cross", "rsi_oversold", "macd_bullish", "bb_lower_break", "stoch_bullish"]
    )  # = 10*3 + 10*2 + 10*2 + 10*1 + 10*1 = 90

    # Weighted buy/sell scores using signal strength and weight
    buy_weighted = sum(s.strength * _SIGNAL_WEIGHTS.get(s.signal_type, 1) for s in buy_signals)
    sell_weighted = sum(s.strength * _SIGNAL_WEIGHTS.get(s.signal_type, 1) for s in sell_signals)

    # Normalize to 0-5 range (so total contribution is 0 to ±5 on the 1-10 scale)
    net_signal_score = (buy_weighted - sell_weighted) / max_weighted_score * 5.0

    score = 5.0 + net_signal_score

    # RSI 위치
    latest = indicators_df.iloc[-1]
    rsi = latest.get("rsi_14")
    if rsi is not None and not pd.isna(rsi):
        if 30 < rsi < 50:
            score += 1.0  # 과매도에서 회복 중
        elif rsi > 60:
            score -= 0.5

    # MACD 히스토그램 양수
    macd_hist = latest.get("macd_hist")
    if macd_hist is not None and not pd.isna(macd_hist) and macd_hist > 0:
        score += 0.5

    # 스토캐스틱 K/D
    stoch_k = latest.get("stoch_k")
    stoch_d = latest.get("stoch_d")
    if stoch_k is not None and not pd.isna(stoch_k):
        if stoch_k < 20:
            score += 0.5  # 과매도
        elif stoch_k > 80:
            score -= 0.5  # 과매수
        # K가 D를 상향 돌파 + K < 50 (매수 전환)
        if stoch_d is not None and not pd.isna(stoch_d):
            if stoch_k > stoch_d and stoch_k < 50:
                score += 0.5

    return max(1.0, min(10.0, score))


def _score_fundamental(
    session: Session, stock_id: int,
    sector_medians: dict[str, float] | None = None,
) -> float:
    """기본적 분석 점수 (1-10). sector_medians 제공 시 섹터 상대 비교."""
    from src.db.models import FactValuation

    fins = FinancialRepository.get_by_stock(session, stock_id)
    if not fins:
        return 5.0

    fin_records = [
        FinancialRecord(
            period=f.period, revenue=f.revenue,
            operating_income=f.operating_income, net_income=f.net_income,
            total_assets=f.total_assets, total_liabilities=f.total_liabilities,
            total_equity=f.total_equity, operating_cashflow=f.operating_cashflow,
        )
        for f in fins
    ]

    # 최신 밸류에이션
    from sqlalchemy import select
    val_row = session.execute(
        select(FactValuation)
        .where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc())
        .limit(1)
    ).scalar_one_or_none()

    val_record = None
    if val_row:
        val_record = ValuationRecord(
            date=date.today(),
            per=float(val_row.per) if val_row.per else None,
            pbr=float(val_row.pbr) if val_row.pbr else None,
            roe=float(val_row.roe) if val_row.roe else None,
            debt_ratio=float(val_row.debt_ratio) if val_row.debt_ratio else None,
            dividend_yield=float(val_row.dividend_yield) if val_row.dividend_yield else None,
        )

    result = analyze_fundamentals(fin_records, val_record, sector_medians=sector_medians)
    adjusted = result.composite_score

    # 실적 서프라이즈 보정
    try:
        from src.db.repository import EarningsSurpriseRepository
        earnings = EarningsSurpriseRepository.get_by_stock(session, stock_id, limit=4)
        if earnings:
            beat_count = sum(
                1 for e in earnings
                if e.surprise_pct is not None and float(e.surprise_pct) > 0
            )
            if beat_count >= 3:
                adjusted = min(10.0, adjusted + 1.0)
            elif beat_count == 0:
                adjusted = max(1.0, adjusted - 1.0)
    except Exception:
        pass

    # 재무 품질 보정 (Piotroski F-Score + Earnings Quality)
    try:
        from src.analysis.quality import assess_quality
        piotroski, _altman, eq = assess_quality(session, stock_id)
        if piotroski.score < 3:
            adjusted = max(1.0, adjusted - 2.0)
        elif piotroski.score >= 7:
            adjusted = min(10.0, adjusted + 1.5)
        if eq.quality == "low":
            adjusted = max(1.0, adjusted - 1.5)
    except Exception:
        pass

    return adjusted


def _score_smart_money(
    session: Session, stock_id: int, latest: pd.Series,
) -> float:
    """수급/스마트머니 점수 (1-10)."""
    score = 5.0

    # 내부자 거래 (시간 감쇠 적용)
    try:
        import math
        from src.db.repository import InsiderTradeRepository
        from src.db.helpers import id_to_date
        trades = InsiderTradeRepository.get_by_stock(session, stock_id, limit=50)
        if trades:
            net_value = 0.0
            has_ceo_buy = False
            today = date.today()
            for t in trades:
                raw_val = float(t.value or 0)
                # 시간 감쇠: 반감기 ~21일 (exp(-age/30))
                try:
                    trade_date = id_to_date(t.date_id)
                    age_days = (today - trade_date).days
                    decay = math.exp(-age_days / 90)
                except Exception:
                    decay = 0.5  # fallback
                weighted_val = raw_val * decay
                if t.transaction_type in ("Buy", "Purchase"):
                    net_value += weighted_val
                    if (t.insider_title
                            and any(title in t.insider_title.upper()
                                    for title in ["CEO", "CFO", "CHIEF"])):
                        has_ceo_buy = True
                else:
                    net_value -= weighted_val
            if net_value > 0:
                # 시가총액 대비 정규화
                from src.db.models import FactValuation
                from sqlalchemy import select as _sel_val
                latest_val = session.execute(
                    _sel_val(FactValuation.market_cap)
                    .where(FactValuation.stock_id == stock_id)
                    .where(FactValuation.market_cap.isnot(None))
                    .order_by(FactValuation.date_id.desc())
                    .limit(1)
                ).scalar_one_or_none()
                market_cap = float(latest_val) if latest_val else None

                if market_cap and market_cap > 0:
                    intensity = net_value / market_cap
                    if intensity > 0.001:  # 0.1% of market cap = very significant
                        insider_bonus = min(3.0, intensity * 3000)
                    elif intensity > 0.0001:  # 0.01%
                        insider_bonus = min(1.5, intensity * 15000)
                    else:
                        insider_bonus = 0.5
                else:
                    insider_bonus = 1.0  # Fallback if no market cap
                if has_ceo_buy:
                    insider_bonus = min(3.0, insider_bonus + 1.0)
                score += insider_bonus
            elif net_value < 0:
                score -= 1.0
    except Exception:
        pass

    # 애널리스트 컨센서스
    try:
        from src.db.repository import AnalystConsensusRepository
        consensus = AnalystConsensusRepository.get_latest(session, stock_id)
        if consensus:
            total_recs = (
                consensus.strong_buy + consensus.buy + consensus.hold
                + consensus.sell + consensus.strong_sell
            )
            if total_recs > 0:
                buy_ratio = (consensus.strong_buy + consensus.buy) / total_recs
                if buy_ratio >= 0.7:
                    score += 2.0
                elif buy_ratio < 0.3:
                    score -= 2.0

                # 목표가 괴리율
                close = latest.get("close")
                if consensus.target_mean and close and close > 0:
                    upside = (float(consensus.target_mean) - close) / close
                    if upside >= 0.2:
                        score += 1.5  # 균형 보정: 상향 +1.5
                    elif upside <= -0.15:
                        score -= 1.5  # 균형 보정: 하향 -1.5
    except Exception:
        pass

    # 공매도 비율
    try:
        from src.db.models import FactValuation
        from sqlalchemy import select as sel
        val = session.execute(
            sel(FactValuation)
            .where(FactValuation.stock_id == stock_id)
            .order_by(FactValuation.date_id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if val and val.short_pct_of_float is not None:
            short_pct = float(val.short_pct_of_float)
            if short_pct > 10:
                score -= 1.5  # 높은 공매도 = 약세 신호
            elif short_pct > 5:
                score -= 0.5
            elif short_pct < 2:
                score += 0.5  # 낮은 공매도 = 약한 강세
    except Exception:
        pass

    # 기관 보유 현황
    try:
        from src.db.repository import InstitutionalHoldingRepository
        holdings = InstitutionalHoldingRepository.get_by_stock(session, stock_id, limit=10)
        if holdings:
            total_pct = sum(
                float(h.pct_of_shares) for h in holdings
                if h.pct_of_shares is not None
            )
            if total_pct > 30:
                score += 1.0  # 강한 기관 보유 = 안정적 수급
            elif total_pct > 15:
                score += 0.5
    except Exception:
        pass

    return max(1.0, min(10.0, score))


def _score_external(
    market_score: int,
    news_sentiment: float,
    sector_momentum: dict[str, float] | None,
    sector: object | None,
) -> float:
    """외부 요인 점수 (1-10)."""
    score = float(market_score)

    # 뉴스 감성 반영
    score += news_sentiment * 2.0

    # 섹터 모멘텀
    if sector_momentum and sector is not None:
        sector_name = getattr(sector, "sector_name", None)
        if sector_name and sector_name in sector_momentum:
            score += (sector_momentum[sector_name] - 5.0) * 0.3

    return max(1.0, min(10.0, score))


def _score_momentum(
    df: pd.DataFrame, latest: pd.Series, *, vix: float | None = None,
) -> float:
    """가격 모멘텀 점수 (1-10).

    VIX에 따른 적응형 모멘텀:
    - VIX > 30 (위기): 평균 회귀 모드 (양수 수익률 감점, 음수 보상)
    - VIX < 12 (과열): 모멘텀 증폭 (더 가파른 보간)
    - 12 <= VIX <= 30: 기존 행동 유지
    """
    score = 5.0

    if len(df) < 20:
        return score

    # 최근 20일 수익률
    close_20d_ago = df["close"].iloc[-20]
    current = latest["close"]
    if close_20d_ago > 0:
        ret_20d = (current - close_20d_ago) / close_20d_ago * 100
        if vix is not None and vix > 30:
            # 위기 모드: 급락폭에 따른 조건부 반전
            if ret_20d < -15:
                # 급락 후 과매도 → 평균회귀 기대 (반전)
                score += max(-2.0, min(2.0, -ret_20d / 5.0))
            elif ret_20d > 5:
                # 위기 중 반등 → 약화된 모멘텀 (데드캣 바운스 주의)
                score += max(-1.0, min(1.0, ret_20d / 10.0))
            # else: 횡보 → 모멘텀 기여 0
        elif vix is not None and vix < 12:
            # 과열 모드: 모멘텀 증폭 (더 가파른 보간)
            score += max(-3.0, min(3.0, ret_20d / 3.0))
        else:
            # 기본: 선형 보간 5%당 +1.0, 최대 ±2.0
            score += max(-2.0, min(2.0, ret_20d / 5.0))

    # 이동평균 배열 (SMA5 > SMA20 > SMA60 = 정배열)
    sma5 = latest.get("sma_5")
    sma20 = latest.get("sma_20")
    sma60 = latest.get("sma_60")
    if all(v is not None and not pd.isna(v) for v in [sma5, sma20, sma60]):
        if sma5 > sma20 > sma60:
            score += 1.5  # 정배열
        elif sma5 < sma20 < sma60:
            score -= 1.0  # 역배열

    # 거래량 추세
    vol_sma = latest.get("volume_sma_20")
    vol = latest.get("volume")
    if vol_sma is not None and not pd.isna(vol_sma) and vol_sma > 0:
        if vol > vol_sma * 1.5:
            score += 0.5  # 거래량 급증

    return max(1.0, min(10.0, score))


def _generate_reason(
    ticker: str,
    tech: float, fund: float, ext: float, mom: float,
    latest: pd.Series,
    smart: float = 5.0,
) -> str:
    """추천 근거를 자동 생성한다 (구체적 숫자 포함)."""
    parts = []

    # 기술적
    rsi = latest.get("rsi_14")
    rsi_val = None
    if rsi is not None and not pd.isna(rsi):
        rsi_val = float(rsi)

    if tech >= 7:
        if rsi_val and rsi_val < 30:
            parts.append(f"RSI {rsi_val:.0f} 과매도 반등 기대")
        else:
            parts.append("강한 기술적 매수 시그널")
    elif tech >= 5:
        sma5 = latest.get("sma_5")
        sma20 = latest.get("sma_20")
        sma60 = latest.get("sma_60")
        if all(v is not None and not pd.isna(v) for v in [sma5, sma20, sma60]) and sma5 > sma20 > sma60:
            parts.append("이동평균 정배열 (상승추세)")
        else:
            parts.append("긍정적 기술적 지표")

    # 기본적
    if fund >= 7:
        parts.append("우수한 펀더멘털")
    elif fund >= 5:
        parts.append("적정 밸류에이션")

    # 수급
    if smart >= 7:
        parts.append("스마트머니 유입")

    # 모멘텀
    if mom >= 8:
        close = latest.get("close")
        close_20d = None
        if len(latest) > 0:
            close_20d = latest.get("sma_20")
        if close and close_20d and close_20d > 0:
            ret = (close - close_20d) / close_20d * 100
            parts.append(f"20일 대비 +{ret:.1f}% 상승")
        else:
            parts.append("강한 상승 모멘텀")
    elif mom >= 6:
        parts.append("양호한 모멘텀")

    # RSI 수치
    if rsi_val is not None:
        parts.append(f"RSI {rsi_val:.0f}")

    # MACD
    macd_hist = latest.get("macd_hist")
    if macd_hist is not None and not pd.isna(macd_hist) and macd_hist > 0:
        parts.append("MACD 상승")

    # 스토캐스틱
    stoch_k = latest.get("stoch_k")
    stoch_d = latest.get("stoch_d")
    if stoch_k is not None and not pd.isna(stoch_k):
        if stoch_d is not None and not pd.isna(stoch_d) and stoch_k > stoch_d and stoch_k < 50:
            parts.append("스토캐스틱 매수 전환")
        elif stoch_k < 20:
            parts.append("스토캐스틱 과매도")

    if not parts:
        parts.append("종합 점수 양호")

    return f"{ticker}: " + ", ".join(parts)


def update_recommendation_returns(
    session: Session, prices_by_stock: dict[int, pd.DataFrame],
) -> int:
    """과거 추천의 사후 수익률을 업데이트한다.

    Returns:
        업데이트된 추천 수.
    """
    from src.db.models import FactDailyRecommendation
    from sqlalchemy import select

    stmt = select(FactDailyRecommendation).where(
        FactDailyRecommendation.return_1d.is_(None)
        | FactDailyRecommendation.return_5d.is_(None)
        | FactDailyRecommendation.return_10d.is_(None)
        | FactDailyRecommendation.return_20d.is_(None)
    )
    recs = session.execute(stmt).scalars().all()

    updated = 0
    for rec in recs:
        df = prices_by_stock.get(rec.stock_id)
        if df is None or df.empty:
            continue

        rec_price = float(rec.price_at_recommendation)
        if rec_price <= 0:
            continue

        from src.db.helpers import id_to_date
        rec_date = id_to_date(rec.run_date_id)

        # 날짜 기반 수익률 계산
        for days, attr in [(1, "return_1d"), (5, "return_5d"), (10, "return_10d"), (20, "return_20d")]:
            if getattr(rec, attr) is not None:
                continue
            from datetime import timedelta
            target_date = rec_date + timedelta(days=days)
            future = df[df.index >= target_date]
            if not future.empty:
                future_price = float(future.iloc[0]["close"])
                ret = (future_price - rec_price) / rec_price * 100
                setattr(rec, attr, round(ret, 2))
                updated += 1

    if updated:
        session.flush()

    return updated
