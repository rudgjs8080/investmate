"""공통 테스트 fixture — Star Schema."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base


@pytest.fixture
def engine():
    """In-memory SQLite 엔진."""
    eng = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(eng, "connect")
    def _set_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine) -> Session:
    """테스트용 세션."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    sess = factory()
    yield sess
    sess.close()


@pytest.fixture
def seeded_session(engine) -> Session:
    """Dimension 시딩된 세션."""
    from src.db.seed import seed_dimensions

    seed_dimensions(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    sess = factory()
    yield sess
    sess.close()


@pytest.fixture
def us_market(seeded_session) -> int:
    """US 시장 ID."""
    from src.db.repository import StockRepository

    market_id = StockRepository.resolve_market_id(seeded_session, "US")
    assert market_id is not None
    return market_id


@pytest.fixture
def sample_stock(seeded_session, us_market) -> dict:
    """샘플 종목."""
    from src.db.repository import StockRepository

    stock = StockRepository.add(
        seeded_session, "AAPL", "Apple Inc.", us_market,
        is_sp500=True,
    )
    seeded_session.commit()
    return {"id": stock.stock_id, "ticker": stock.ticker, "name": stock.name}
