"""Phase 12b: AlertRepository 영구 저장/조회/확인 테스트."""

from __future__ import annotations

from datetime import date as date_type, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import Base, DimMarket, DimStock, FactDeepDiveAlert
from src.db.repository import AlertRepository
from src.deepdive.alert_engine import AlertTrigger


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine) -> Session:
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    s = factory()
    # 기본 종목/마켓
    market = DimMarket(
        code="US", name="US", currency="USD", timezone="US/Eastern",
    )
    s.add(market)
    s.flush()
    s.add(DimStock(
        ticker="AAPL", name="Apple",
        market_id=market.market_id, is_sp500=True,
    ))
    s.add(DimStock(
        ticker="MSFT", name="Microsoft",
        market_id=market.market_id, is_sp500=True,
    ))
    s.flush()
    # 날짜 축 준비
    ensure_date_ids(s, [date_type.today() - timedelta(days=i) for i in range(10)])
    s.flush()
    yield s
    s.close()


def _stock_lookup(session: Session) -> dict[str, int]:
    from sqlalchemy import select

    rows = session.execute(select(DimStock)).scalars().all()
    return {r.ticker: r.stock_id for r in rows}


def _trigger(ticker: str, ttype: str = "buy_zone_entered", severity: str = "info") -> AlertTrigger:
    return AlertTrigger(
        ticker=ticker,
        trigger_type=ttype,
        severity=severity,
        message=f"{ticker} {ttype} test",
        current_price=100.0,
        reference_price=99.0,
    )


class TestPersistBatch:
    def test_inserts_new_alerts(self, session):
        today_id = date_to_id(date_type.today())
        triggers = [_trigger("AAPL"), _trigger("MSFT", "stop_proximity", "warning")]
        count = AlertRepository.persist_batch(
            session,
            date_id=today_id,
            stock_id_lookup=_stock_lookup(session),
            alerts=triggers,
        )
        assert count == 2

    def test_dedup_same_day_same_trigger(self, session):
        today_id = date_to_id(date_type.today())
        t = _trigger("AAPL")
        AlertRepository.persist_batch(session, today_id, _stock_lookup(session), [t])
        second = AlertRepository.persist_batch(session, today_id, _stock_lookup(session), [t])
        assert second == 0

    def test_allows_next_day_same_trigger(self, session):
        today_id = date_to_id(date_type.today())
        yesterday_id = date_to_id(date_type.today() - timedelta(days=1))
        t = _trigger("AAPL")
        AlertRepository.persist_batch(session, yesterday_id, _stock_lookup(session), [t])
        count = AlertRepository.persist_batch(session, today_id, _stock_lookup(session), [t])
        assert count == 1

    def test_allows_different_trigger_same_day(self, session):
        today_id = date_to_id(date_type.today())
        t1 = _trigger("AAPL", "buy_zone_entered")
        t2 = _trigger("AAPL", "target_1m_hit")
        AlertRepository.persist_batch(session, today_id, _stock_lookup(session), [t1])
        count = AlertRepository.persist_batch(session, today_id, _stock_lookup(session), [t2])
        assert count == 1

    def test_skips_unknown_ticker(self, session):
        today_id = date_to_id(date_type.today())
        t = _trigger("UNKNOWN")
        count = AlertRepository.persist_batch(session, today_id, _stock_lookup(session), [t])
        assert count == 0

    def test_empty_list_noop(self, session):
        today_id = date_to_id(date_type.today())
        count = AlertRepository.persist_batch(session, today_id, _stock_lookup(session), [])
        assert count == 0


class TestGetRecent:
    def _seed(self, session):
        today_id = date_to_id(date_type.today())
        yesterday_id = date_to_id(date_type.today() - timedelta(days=1))
        lookup = _stock_lookup(session)
        AlertRepository.persist_batch(
            session, today_id, lookup,
            [
                _trigger("AAPL", "buy_zone_entered", "info"),
                _trigger("AAPL", "stop_proximity", "critical"),
                _trigger("MSFT", "target_1m_hit", "info"),
            ],
        )
        AlertRepository.persist_batch(
            session, yesterday_id, lookup,
            [_trigger("AAPL", "review_trigger_hit", "warning")],
        )

    def test_gets_all_recent(self, session):
        self._seed(session)
        alerts = AlertRepository.get_recent(session, days=30)
        assert len(alerts) == 4

    def test_filter_severity_critical_only(self, session):
        self._seed(session)
        alerts = AlertRepository.get_recent(session, days=30, severity_min="critical")
        assert len(alerts) == 1
        assert alerts[0].severity == "critical"

    def test_filter_severity_warning_or_higher(self, session):
        self._seed(session)
        alerts = AlertRepository.get_recent(session, days=30, severity_min="warning")
        # critical (1) + warning (1) = 2
        assert len(alerts) == 2

    def test_filter_by_ticker(self, session):
        self._seed(session)
        alerts = AlertRepository.get_recent(session, days=30, ticker="MSFT")
        assert len(alerts) == 1
        assert alerts[0].ticker == "MSFT"

    def test_filter_unread(self, session):
        self._seed(session)
        all_alerts = AlertRepository.get_recent(session, days=30)
        AlertRepository.acknowledge(session, all_alerts[0].alert_id)
        unread = AlertRepository.get_recent(session, days=30, ack_filter="unread")
        assert len(unread) == 3


class TestAcknowledge:
    def test_marks_single(self, session):
        today_id = date_to_id(date_type.today())
        AlertRepository.persist_batch(
            session, today_id, _stock_lookup(session), [_trigger("AAPL")],
        )
        alerts = AlertRepository.get_recent(session, days=30)
        aid = alerts[0].alert_id
        ok = AlertRepository.acknowledge(session, aid)
        assert ok is True
        # 재조회 시 acknowledged=True
        updated = session.get(FactDeepDiveAlert, aid)
        assert updated.acknowledged is True
        assert updated.acknowledged_at is not None

    def test_nonexistent_returns_false(self, session):
        ok = AlertRepository.acknowledge(session, 99999)
        assert ok is False

    def test_idempotent(self, session):
        today_id = date_to_id(date_type.today())
        AlertRepository.persist_batch(
            session, today_id, _stock_lookup(session), [_trigger("AAPL")],
        )
        alerts = AlertRepository.get_recent(session, days=30)
        aid = alerts[0].alert_id
        AlertRepository.acknowledge(session, aid)
        # 두 번째 호출도 True
        assert AlertRepository.acknowledge(session, aid) is True


class TestAcknowledgeAll:
    def test_marks_all_unread(self, session):
        today_id = date_to_id(date_type.today())
        AlertRepository.persist_batch(
            session, today_id, _stock_lookup(session),
            [_trigger("AAPL"), _trigger("MSFT", "target_1m_hit")],
        )
        count = AlertRepository.acknowledge_all(session)
        assert count == 2
        assert AlertRepository.count_unread(session) == 0

    def test_date_scoped(self, session):
        today_id = date_to_id(date_type.today())
        yesterday_id = date_to_id(date_type.today() - timedelta(days=1))
        AlertRepository.persist_batch(
            session, today_id, _stock_lookup(session), [_trigger("AAPL")],
        )
        AlertRepository.persist_batch(
            session, yesterday_id, _stock_lookup(session),
            [_trigger("MSFT", "buy_zone_entered")],
        )
        count = AlertRepository.acknowledge_all(session, date_id=today_id)
        assert count == 1
        # 어제 것은 아직 미확인
        assert AlertRepository.count_unread(session) == 1


class TestCountUnread:
    def test_zero_when_empty(self, session):
        assert AlertRepository.count_unread(session) == 0

    def test_excludes_acknowledged(self, session):
        today_id = date_to_id(date_type.today())
        AlertRepository.persist_batch(
            session, today_id, _stock_lookup(session),
            [_trigger("AAPL"), _trigger("MSFT", "stop_proximity")],
        )
        alerts = AlertRepository.get_recent(session, days=30)
        AlertRepository.acknowledge(session, alerts[0].alert_id)
        assert AlertRepository.count_unread(session) == 1
