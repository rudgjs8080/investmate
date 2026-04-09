"""페어 자동 선정 알고리즘 — 동일 섹터 + 시총 + 코사인 유사도 기반."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import DimStock, FactDailyPrice, FactValuation
from src.db.repository import WatchlistRepository
from src.deepdive.schemas import PeerComparison

logger = logging.getLogger(__name__)


def refresh_peers_if_stale(
    session: Session,
    stock_id: int,
    ticker: str,
    sector_id: int | None,
    staleness_days: int = 7,
) -> list[PeerComparison]:
    """staleness 체크 후 필요 시 갱신. 반환: 현재 페어 목록."""
    updated_at = WatchlistRepository.get_pairs_updated_at(session, ticker)
    if updated_at is not None:
        age = datetime.now() - updated_at
        if age < timedelta(days=staleness_days):
            existing = WatchlistRepository.get_pairs(session, ticker)
            if existing:
                return _pairs_to_comparisons(session, ticker, stock_id, existing)

    peers = select_peers(session, stock_id, ticker, sector_id)
    if peers:
        WatchlistRepository.upsert_pairs(
            session, ticker,
            [{"peer_ticker": p.peer_ticker, "similarity_score": p.similarity_score}
             for p in peers],
        )
    return peers


def select_peers(
    session: Session,
    stock_id: int,
    ticker: str,
    sector_id: int | None,
    top_n: int = 5,
) -> list[PeerComparison]:
    """GICS 섹터 + 시총 + 코사인 유사도 top N 페어 선정."""
    if sector_id is None:
        return []

    candidates = _get_sector_candidates(session, sector_id, stock_id)
    if not candidates:
        return []

    target_cap = _get_latest_market_cap(session, stock_id)
    if target_cap is not None and target_cap > 0:
        filtered = _filter_by_market_cap(session, candidates, target_cap)
        if len(filtered) >= 3:
            candidates = filtered

    candidate_ids = [c.stock_id for c in candidates]
    similarities = _compute_cosine_similarities(session, stock_id, candidate_ids)
    if not similarities:
        return []

    sorted_candidates = sorted(
        similarities.items(), key=lambda x: x[1], reverse=True,
    )[:top_n]

    candidate_map = {c.stock_id: c for c in candidates}
    target_return = _get_60d_return(session, stock_id)
    target_per = _get_latest_per(session, stock_id)

    results = []
    for cand_id, sim_score in sorted_candidates:
        cand = candidate_map.get(cand_id)
        if cand is None:
            continue
        peer_cap = _get_latest_market_cap(session, cand_id)
        cap_ratio = (peer_cap / target_cap) if (target_cap and peer_cap) else 0.0
        results.append(PeerComparison(
            peer_ticker=cand.ticker,
            peer_name=cand.name,
            similarity_score=sim_score,
            market_cap_ratio=cap_ratio,
            return_60d_peer=_get_60d_return(session, cand_id),
            return_60d_target=target_return,
            per_peer=_get_latest_per(session, cand_id),
            per_target=target_per,
        ))
    return results


def _get_sector_candidates(
    session: Session, sector_id: int, exclude_stock_id: int,
) -> list[DimStock]:
    """동일 섹터 S&P 500 종목 조회."""
    stmt = (
        select(DimStock)
        .where(
            DimStock.sector_id == sector_id,
            DimStock.is_sp500.is_(True),
            DimStock.is_active.is_(True),
            DimStock.stock_id != exclude_stock_id,
        )
    )
    return list(session.execute(stmt).scalars().all())


def _filter_by_market_cap(
    session: Session,
    candidates: list[DimStock],
    target_market_cap: float,
    low_ratio: float = 0.3,
    high_ratio: float = 3.0,
) -> list[DimStock]:
    """시총 0.3x~3x 필터."""
    low = target_market_cap * low_ratio
    high = target_market_cap * high_ratio
    filtered = []
    for c in candidates:
        cap = _get_latest_market_cap(session, c.stock_id)
        if cap is not None and low <= cap <= high:
            filtered.append(c)
    return filtered


def _compute_cosine_similarities(
    session: Session,
    target_stock_id: int,
    candidate_ids: list[int],
    lookback_days: int = 60,
) -> dict[int, float]:
    """60일 수익률 코사인 유사도 계산. 반환: {stock_id: similarity}."""
    target_returns = _get_daily_returns(session, target_stock_id, lookback_days)
    if len(target_returns) < 20:
        return {}

    result = {}
    for cand_id in candidate_ids:
        cand_returns = _get_daily_returns(session, cand_id, lookback_days)
        min_len = min(len(target_returns), len(cand_returns))
        if min_len < 20:
            continue
        sim = _cosine_sim(target_returns[:min_len], cand_returns[:min_len])
        result[cand_id] = sim
    return result


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """numpy 코사인 유사도."""
    a_arr = np.array(a)
    b_arr = np.array(b)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))


# --- 헬퍼 ---

def _get_daily_returns(
    session: Session, stock_id: int, lookback_days: int,
) -> list[float]:
    """최근 N거래일 일간 수익률 리스트."""
    stmt = (
        select(FactDailyPrice.close)
        .where(FactDailyPrice.stock_id == stock_id)
        .order_by(FactDailyPrice.date_id.desc())
        .limit(lookback_days + 1)
    )
    prices = list(session.execute(stmt).scalars().all())
    if len(prices) < 2:
        return []
    prices.reverse()
    return [
        (prices[i] - prices[i - 1]) / prices[i - 1]
        for i in range(1, len(prices))
        if prices[i - 1] != 0
    ]


def _get_latest_market_cap(session: Session, stock_id: int) -> float | None:
    """종목의 최신 시가총액."""
    stmt = (
        select(FactValuation.market_cap)
        .where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc())
        .limit(1)
    )
    val = session.execute(stmt).scalar_one_or_none()
    return float(val) if val is not None else None


def _get_latest_per(session: Session, stock_id: int) -> float | None:
    """종목의 최신 PER."""
    stmt = (
        select(FactValuation.per)
        .where(FactValuation.stock_id == stock_id)
        .order_by(FactValuation.date_id.desc())
        .limit(1)
    )
    val = session.execute(stmt).scalar_one_or_none()
    return float(val) if val is not None else None


def _get_60d_return(session: Session, stock_id: int) -> float:
    """60일 수익률 %."""
    stmt = (
        select(FactDailyPrice.close)
        .where(FactDailyPrice.stock_id == stock_id)
        .order_by(FactDailyPrice.date_id.desc())
        .limit(61)
    )
    prices = list(session.execute(stmt).scalars().all())
    if len(prices) < 2:
        return 0.0
    latest = float(prices[0])
    oldest = float(prices[-1])
    if oldest == 0:
        return 0.0
    return (latest - oldest) / oldest * 100


def _pairs_to_comparisons(
    session: Session, ticker: str, stock_id: int,
    pairs: list,
) -> list[PeerComparison]:
    """DB 페어 레코드 → PeerComparison DTO 변환."""
    target_return = _get_60d_return(session, stock_id)
    target_per = _get_latest_per(session, stock_id)
    target_cap = _get_latest_market_cap(session, stock_id)

    results = []
    for p in pairs:
        peer_stock = session.execute(
            select(DimStock).where(DimStock.ticker == p.peer_ticker)
        ).scalar_one_or_none()
        if peer_stock is None:
            continue
        peer_cap = _get_latest_market_cap(session, peer_stock.stock_id)
        cap_ratio = (peer_cap / target_cap) if (target_cap and peer_cap) else 0.0
        results.append(PeerComparison(
            peer_ticker=p.peer_ticker,
            peer_name=peer_stock.name,
            similarity_score=float(p.similarity_score or 0),
            market_cap_ratio=cap_ratio,
            return_60d_peer=_get_60d_return(session, peer_stock.stock_id),
            return_60d_target=target_return,
            per_peer=_get_latest_per(session, peer_stock.stock_id),
            per_target=target_per,
        ))
    return results
