"""Deep Dive 분석 레이어 테스트."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import FactDailyPrice, FactFinancial, FactMacroIndicator, FactValuation
from src.db.repository import StockRepository


def _seed_stock(session, us_market):
    """테스트 종목 생성."""
    return StockRepository.add(session, "TEST", "Test Corp", us_market, is_sp500=True)


def _seed_prices(session, stock_id, n_days=100):
    """n일치 가격 데이터 시드."""
    base = date.today() - timedelta(days=n_days)
    dates = [base + timedelta(days=i) for i in range(n_days)]
    ensure_date_ids(session, dates)

    for i, d in enumerate(dates):
        price = 100 + i * 0.5
        session.add(FactDailyPrice(
            stock_id=stock_id,
            date_id=date_to_id(d),
            open=price - 0.5,
            high=price + 1.0,
            low=price - 1.0,
            close=price,
            adj_close=price,
            volume=1000000,
        ))
    session.flush()


def _seed_financials(session, stock_id, n_quarters=4):
    """n분기치 재무 데이터 시드."""
    for i in range(n_quarters):
        q = n_quarters - i
        session.add(FactFinancial(
            stock_id=stock_id,
            period=f"2025Q{q}" if q <= 4 else f"2024Q{q - 4}",
            revenue=1000000 * (1 + i * 0.05),
            operating_income=200000 * (1 + i * 0.03),
            net_income=150000 * (1 + i * 0.04),
            total_assets=5000000,
            total_liabilities=2000000,
            total_equity=3000000,
            operating_cashflow=180000,
        ))
    session.flush()


class TestLayer1Fundamental:
    """Layer 1: 펀더멘털 헬스체크."""

    def test_with_financials(self, seeded_session, us_market):
        from src.deepdive.layers import compute_layer1_fundamental

        stock = _seed_stock(seeded_session, us_market)
        _seed_financials(seeded_session, stock.stock_id)

        result = compute_layer1_fundamental(seeded_session, stock.stock_id)
        assert result is not None
        assert result.health_grade in ("A", "B", "C", "D", "F")
        assert 0 <= result.f_score <= 9
        assert result.margin_trend in ("improving", "declining", "stable")

    def test_no_financials(self, seeded_session, us_market):
        from src.deepdive.layers import compute_layer1_fundamental

        stock = _seed_stock(seeded_session, us_market)
        result = compute_layer1_fundamental(seeded_session, stock.stock_id)
        assert result is None


class TestLayer3Technical:
    """Layer 3: 멀티TF 기술적."""

    def test_with_prices(self, seeded_session, us_market):
        from src.deepdive.layers import compute_layer3_technical

        stock = _seed_stock(seeded_session, us_market)
        _seed_prices(seeded_session, stock.stock_id, 100)
        today_id = date_to_id(date.today())

        result = compute_layer3_technical(seeded_session, stock.stock_id, today_id)
        assert result is not None
        assert result.technical_grade in ("Bullish", "Neutral", "Bearish")
        assert 0 <= result.position_52w_pct <= 100

    def test_insufficient_prices(self, seeded_session, us_market):
        from src.deepdive.layers import compute_layer3_technical

        stock = _seed_stock(seeded_session, us_market)
        _seed_prices(seeded_session, stock.stock_id, 10)
        today_id = date_to_id(date.today())

        result = compute_layer3_technical(seeded_session, stock.stock_id, today_id)
        assert result is None

    def test_52w_position(self, seeded_session, us_market):
        from src.deepdive.layers import compute_layer3_technical

        stock = _seed_stock(seeded_session, us_market)
        _seed_prices(seeded_session, stock.stock_id, 100)
        today_id = date_to_id(date.today())

        result = compute_layer3_technical(seeded_session, stock.stock_id, today_id)
        assert result is not None
        # 가격이 꾸준히 상승하므로 52w 위치는 높아야 함
        assert result.position_52w_pct > 50


class TestLayer4Flow:
    """Layer 4: 수급/포지셔닝."""

    def test_no_data(self, seeded_session, us_market):
        from src.deepdive.layers import compute_layer4_flow

        stock = _seed_stock(seeded_session, us_market)
        result = compute_layer4_flow(seeded_session, stock.stock_id)
        # 데이터 없어도 기본 FlowProfile 반환 (insider_net=0)
        assert result is not None
        assert result.flow_grade in ("Accumulation", "Neutral", "Distribution")


class TestLayer2Valuation:
    """Layer 2: 밸류에이션 컨텍스트."""

    def test_with_valuation_data(self, seeded_session, us_market):
        from src.deepdive.layers_valuation import compute_layer2_valuation

        stock = _seed_stock(seeded_session, us_market)
        _seed_financials(seeded_session, stock.stock_id, 8)
        # 밸류에이션 히스토리 시드
        dates = [date.today() - timedelta(days=i) for i in range(50)]
        ensure_date_ids(seeded_session, dates)
        for i, d in enumerate(dates):
            seeded_session.add(FactValuation(
                stock_id=stock.stock_id, date_id=date_to_id(d),
                market_cap=1000000000, per=20 + i * 0.2, pbr=3.0 + i * 0.05,
                roe=15.0, debt_ratio=40.0,
            ))
        seeded_session.flush()

        result = compute_layer2_valuation(seeded_session, stock.stock_id, stock.sector_id)
        assert result is not None
        assert result.valuation_grade in ("Cheap", "Fair", "Rich", "Extreme")

    def test_no_valuation(self, seeded_session, us_market):
        from src.deepdive.layers_valuation import compute_layer2_valuation

        stock = _seed_stock(seeded_session, us_market)
        result = compute_layer2_valuation(seeded_session, stock.stock_id, None)
        assert result is None


class TestLayer5Narrative:
    """Layer 5: 내러티브 + 촉매."""

    def test_no_news(self, seeded_session, us_market):
        from src.deepdive.layers_narrative import compute_layer5_narrative

        stock = _seed_stock(seeded_session, us_market)
        result = compute_layer5_narrative(seeded_session, stock.stock_id, "TEST", date.today())
        # 뉴스 0건 시 Neutral grade 반환
        if result is not None:
            assert result.narrative_grade == "Neutral"
            assert isinstance(result.upcoming_catalysts, list)
        # None도 허용 (촉매 조회 실패 시)


class TestLayer6Macro:
    """Layer 6: 거시 민감도."""

    def test_with_macro_data(self, seeded_session, us_market):
        from src.deepdive.layers_macro import compute_layer6_macro

        stock = _seed_stock(seeded_session, us_market)
        _seed_prices(seeded_session, stock.stock_id, 100)

        # 매크로 데이터 시드
        base = date.today() - timedelta(days=100)
        dates = [base + timedelta(days=i) for i in range(100)]
        ensure_date_ids(seeded_session, dates)
        for d in dates:
            seeded_session.add(FactMacroIndicator(
                date_id=date_to_id(d), vix=20.0, us_10y_yield=4.2,
            ))
        seeded_session.flush()

        result = compute_layer6_macro(seeded_session, stock.stock_id, None, date_to_id(date.today()))
        assert result is not None
        assert result.macro_grade in ("Favorable", "Neutral", "Headwind")

    def test_insufficient_data(self, seeded_session, us_market):
        from src.deepdive.layers_macro import compute_layer6_macro

        stock = _seed_stock(seeded_session, us_market)
        result = compute_layer6_macro(seeded_session, stock.stock_id, None, date_to_id(date.today()))
        assert result is None


class TestComputeAll:
    """compute_all_layers 통합."""

    def test_all_layers(self, seeded_session, us_market):
        from src.deepdive.layers import compute_all_layers

        stock = _seed_stock(seeded_session, us_market)
        _seed_prices(seeded_session, stock.stock_id, 100)
        _seed_financials(seeded_session, stock.stock_id)
        today_id = date_to_id(date.today())

        result = compute_all_layers(seeded_session, stock.stock_id, today_id)
        assert "layer1" in result
        assert "layer3" in result
        assert "layer4" in result
        # Phase 2 레이어도 key 존재 (None 허용)
        assert "layer2" in result
        assert "layer5" in result
        assert "layer6" in result
