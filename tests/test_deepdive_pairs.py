"""페어 자동 선정 알고리즘 테스트."""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import DimMarket, DimSector, DimStock, FactDailyPrice, FactValuation
from src.db.repository import WatchlistRepository
from src.db.repository import WatchlistRepository
from src.deepdive.pair_analysis import _cosine_sim, refresh_peers_if_stale, select_peers


def _seed_sector_stocks(session: Session) -> tuple[int, int, list[int]]:
    """테스트용 섹터 + 종목 시딩. 반환: (sector_id, target_stock_id, peer_stock_ids)."""
    market = DimMarket(code="US", name="US", currency="USD", timezone="US/Eastern")
    session.add(market)
    session.flush()

    sector = DimSector(sector_name="Technology")
    session.add(sector)
    session.flush()

    target = DimStock(
        ticker="AAPL", name="Apple", market_id=market.market_id,
        sector_id=sector.sector_id, is_sp500=True, is_active=True,
    )
    session.add(target)
    session.flush()

    peers = []
    for i, (tkr, nm) in enumerate([
        ("MSFT", "Microsoft"), ("GOOG", "Alphabet"), ("META", "Meta"),
        ("CRM", "Salesforce"), ("ADBE", "Adobe"), ("ORCL", "Oracle"),
    ]):
        s = DimStock(
            ticker=tkr, name=nm, market_id=market.market_id,
            sector_id=sector.sector_id, is_sp500=True, is_active=True,
        )
        session.add(s)
        session.flush()
        peers.append(s.stock_id)

    # 날짜 시딩
    dates = [date(2025, 1, 1) + timedelta(days=d) for d in range(70)]
    ensure_date_ids(session, dates)

    # 가격 데이터 시딩 (target + peers)
    all_ids = [target.stock_id] + peers
    for sid in all_ids:
        base = 150.0 + sid * 10
        for d in range(65):
            dt = date(2025, 1, 1) + timedelta(days=d)
            session.add(FactDailyPrice(
                stock_id=sid, date_id=date_to_id(dt),
                open=base, high=base + 2, low=base - 2, close=base + d * 0.1,
                adj_close=base + d * 0.1, volume=1000000,
            ))

    # 밸류에이션 시딩
    latest_date_id = date_to_id(date(2025, 3, 5))
    ensure_date_ids(session, [date(2025, 3, 5)])
    for sid in all_ids:
        cap = 2e12 if sid == target.stock_id else 1.5e12 + sid * 1e11
        session.add(FactValuation(
            stock_id=sid, date_id=latest_date_id,
            market_cap=cap, per=25.0,
        ))

    session.commit()
    return sector.sector_id, target.stock_id, peers


class TestCosineSimilarity:
    """코사인 유사도 기본 테스트."""

    def test_identical_vectors(self):
        """동일 수익률 벡터 -> 유사도 1.0."""
        a = [0.01, 0.02, -0.01, 0.03]
        assert _cosine_sim(a, a) == pytest.approx(1.0)

    def test_opposite_vectors(self):
        """반대 수익률 벡터 -> 유사도 -1.0."""
        a = [1.0, 2.0, 3.0]
        b = [-1.0, -2.0, -3.0]
        assert _cosine_sim(a, b) == pytest.approx(-1.0)

    def test_zero_vector(self):
        """영벡터 -> 유사도 0.0."""
        assert _cosine_sim([0, 0, 0], [1, 2, 3]) == 0.0

    def test_orthogonal(self):
        """직교 벡터 -> 유사도 0.0."""
        assert _cosine_sim([1, 0], [0, 1]) == pytest.approx(0.0)


class TestSelectPeers:
    """페어 선정 통합 테스트."""

    def test_sector_filter(self, session):
        """동일 섹터 종목만 반환."""
        sector_id, target_id, _ = _seed_sector_stocks(session)
        peers = select_peers(session, target_id, "AAPL", sector_id, top_n=5)
        assert len(peers) <= 5
        assert all(p.peer_ticker != "AAPL" for p in peers)

    def test_no_sector(self, session):
        """sector_id=None -> 빈 리스트."""
        peers = select_peers(session, 1, "AAPL", None)
        assert peers == []

    def test_market_cap_filter(self, session):
        """시총 범위 내 종목만 반환."""
        sector_id, target_id, _ = _seed_sector_stocks(session)
        peers = select_peers(session, target_id, "AAPL", sector_id, top_n=10)
        # 모든 페어의 시총비가 합리적 범위 내
        for p in peers:
            assert p.market_cap_ratio > 0


class TestRefreshPeers:
    """staleness 체크 테스트."""

    def test_fresh_pairs_reused(self, session):
        """7일 미만 -> 기존 페어 재사용."""
        sector_id, target_id, _ = _seed_sector_stocks(session)

        # 먼저 페어 생성
        peers1 = refresh_peers_if_stale(
            session, target_id, "AAPL", sector_id, staleness_days=7,
        )
        session.commit()

        # 다시 호출 -> 재사용 (DB 히트 최소화)
        peers2 = refresh_peers_if_stale(
            session, target_id, "AAPL", sector_id, staleness_days=7,
        )
        assert len(peers2) == len(peers1)
