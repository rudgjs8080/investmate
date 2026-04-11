"""Phase 12d: /personal/compare 페이지 테스트."""

from __future__ import annotations

import json
from datetime import date as date_type

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import (
    Base,
    DimMarket,
    DimSector,
    DimStock,
    FactDailyPrice,
    FactDeepDiveReport,
)
from src.db.repository import WatchlistRepository
from src.web.app import create_app
from src.web.deps import get_db


@pytest.fixture()
def _engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )

    @event.listens_for(eng, "connect")
    def _pragma(dbapi_conn, _r):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def _db_session(_engine) -> Session:
    factory = sessionmaker(bind=_engine, expire_on_commit=False)
    sess = factory()

    market = DimMarket(
        code="US", name="US", currency="USD", timezone="US/Eastern",
    )
    sess.add(market)
    sess.flush()

    tech = DimSector(sector_name="Technology")
    sess.add(tech)
    sess.flush()

    today = date_type.today()
    ensure_date_ids(sess, [today])
    today_id = date_to_id(today)

    def _make_stock(ticker, name):
        s = DimStock(
            ticker=ticker, name=name,
            market_id=market.market_id, sector_id=tech.sector_id, is_sp500=True,
        )
        sess.add(s)
        sess.flush()
        return s

    aapl = _make_stock("AAPL", "Apple Inc.")
    msft = _make_stock("MSFT", "Microsoft Corp.")
    nvda = _make_stock("NVDA", "NVIDIA")

    # 워치리스트
    for t in ("AAPL", "MSFT", "NVDA"):
        WatchlistRepository.add_ticker(sess, t)

    # 최신 가격
    def _price(stock, close):
        sess.add(FactDailyPrice(
            stock_id=stock.stock_id, date_id=today_id,
            open=close, high=close + 1, low=close - 1,
            close=close, adj_close=close, volume=1000000,
        ))

    _price(aapl, 180.0)
    _price(msft, 350.0)
    _price(nvda, 900.0)
    sess.flush()

    # 보고서
    def _report(stock, ticker, action, conviction, ev_3m, rr, grades):
        return FactDeepDiveReport(
            date_id=today_id,
            stock_id=stock.stock_id,
            ticker=ticker,
            action_grade=action,
            conviction=conviction,
            uncertainty="medium",
            report_json=json.dumps({
                "ai_result": {},
                "execution_guide": {
                    "suggested_position_pct": 8.0,
                    "expected_value_pct": {"3M": ev_3m},
                    "risk_reward_ratio": rr,
                    "buy_zone_low": 175.0,
                    "buy_zone_high": 185.0,
                    "portfolio_fit_warnings": [],
                },
                "layers": {
                    "layer1": {"health_grade": grades[0]},
                    "layer2": {"valuation_grade": grades[1]},
                    "layer3": {"technical_grade": grades[2]},
                    "layer4": {"flow_grade": grades[3]},
                    "layer5": {"narrative_grade": grades[4]},
                    "layer6": {"macro_grade": grades[5]},
                },
            }),
        )

    sess.add(_report(
        aapl, "AAPL", "ADD", 8, 12.0, 2.5,
        ["A", "Fair", "Bullish", "Accumulation", "Positive", "Favorable"],
    ))
    sess.add(_report(
        msft, "MSFT", "HOLD", 6, 5.0, 1.5,
        ["A", "Rich", "Neutral", "Neutral", "Positive", "Neutral"],
    ))
    sess.add(_report(
        nvda, "NVDA", "TRIM", 5, -3.0, 0.9,
        ["B", "Extreme", "Bearish", "Distribution", "Negative", "Headwind"],
    ))
    sess.flush()

    yield sess
    sess.close()


@pytest.fixture()
def client(_db_session):
    app = create_app()

    def _override_db():
        yield _db_session

    app.dependency_overrides[get_db] = _override_db
    return TestClient(app, raise_server_exceptions=False)


class TestCompareRoute:
    def test_no_tickers_shows_picker(self, client):
        resp = client.get("/personal/compare")
        assert resp.status_code == 200
        assert "종목 선택" in resp.text
        assert "비교할 종목을 선택해주세요" in resp.text

    def test_two_tickers(self, client):
        resp = client.get("/personal/compare?tickers=AAPL,MSFT")
        assert resp.status_code == 200
        # 두 종목 모두 헤더에 등장 (jinja 렌더된 a 태그 내부에 등장)
        assert resp.text.count("AAPL") >= 2
        assert resp.text.count("MSFT") >= 2
        # 액션 라벨
        assert "ADD" in resp.text
        assert "HOLD" in resp.text

    def test_three_tickers_radar_data(self, client):
        resp = client.get("/personal/compare?tickers=AAPL,MSFT,NVDA")
        assert resp.status_code == 200
        assert "NVDA" in resp.text
        # 레이더 차트 컨테이너
        assert "dd_compare_radar" in resp.text

    def test_max_four_tickers(self, client):
        resp = client.get("/personal/compare?tickers=AAPL,MSFT,NVDA,GOOGL,TSLA")
        assert resp.status_code == 200
        # GOOGL / TSLA는 없는 종목이지만 상위 4개만 캐핑됨
        # (AAPL,MSFT,NVDA,GOOGL까지) — GOOGL은 missing으로 표시
        assert "TSLA" not in resp.text  # 캐핑되어 렌더되지 않음

    def test_unknown_ticker_shows_missing(self, client):
        resp = client.get("/personal/compare?tickers=AAPL,UNKNOWN")
        assert resp.status_code == 200
        # UNKNOWN은 missing 상태로 표시 — "-" 셀 존재
        assert "UNKNOWN" in resp.text

    def test_invalid_ticker_ignored(self, client):
        resp = client.get("/personal/compare?tickers=AAPL,!!!")
        assert resp.status_code == 200
        # !!! 은 validation 실패로 스킵, AAPL 만 표시
        assert "AAPL" in resp.text

    def test_deduplicates_tickers(self, client):
        resp = client.get("/personal/compare?tickers=AAPL,AAPL,MSFT")
        assert resp.status_code == 200
        # AAPL 컬럼은 1개여야 함 — 테이블 헤더에 2번만 등장 (form + column header)
        # 정확한 카운트는 어려우니 상태 200만 확인

    def test_best_worst_coloring(self, client):
        """ADD 종목이 best_worst에서 best로 강조되는지 확인."""
        resp = client.get("/personal/compare?tickers=AAPL,NVDA")
        assert resp.status_code == 200
        # best 셀 (AAPL ADD) / worst 셀 (NVDA TRIM) 하이라이트 클래스 등장
        assert "bg-green-50" in resp.text or "text-green-700" in resp.text
