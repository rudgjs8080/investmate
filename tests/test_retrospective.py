"""예측 복기 엔진 (retrospective.py) 테스트."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.ai.retrospective import (
    RetrospectiveCandidate,
    _find_trading_date_ago,
    build_retrospective_prompt,
    compute_price_path,
    find_retrospective_candidates,
)
from src.db.helpers import date_to_id
from src.db.models import (
    DimDate,
    DimStock,
    FactDailyPrice,
    FactDailyRecommendation,
    FactMacroIndicator,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _seed_trading_dates(session):
    """30거래일분 날짜 시딩."""
    base = date(2026, 1, 1)
    for i in range(60):
        d = base + timedelta(days=i)
        session.add(DimDate(
            date_id=date_to_id(d),
            date=d,
            year=d.year,
            quarter=(d.month - 1) // 3 + 1,
            month=d.month,
            week_of_year=d.isocalendar()[1],
            day_of_week=d.weekday(),
            is_trading_day=d.weekday() < 5,
        ))
    session.commit()


@pytest.fixture
def _seed_stock(session):
    """테스트 종목."""
    from src.db.models import DimMarket

    session.add(DimMarket(market_id=1, code="US", name="US Market", currency="USD", timezone="US/Eastern"))
    session.add(DimStock(
        stock_id=1, ticker="AAPL", name="Apple Inc.",
        market_id=1, is_active=True, is_sp500=True,
    ))
    session.commit()


@pytest.fixture
def _seed_prices(session, _seed_trading_dates, _seed_stock):
    """테스트 가격 데이터."""
    base_price = 150.0
    base = date(2026, 1, 2)
    for i in range(40):
        d = base + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        price = base_price + i * 0.5  # 점진적 상승
        session.add(FactDailyPrice(
            stock_id=1,
            date_id=date_to_id(d),
            open=price - 0.5,
            high=price + 1.0,
            low=price - 1.0,
            close=price,
            adj_close=price,
            volume=1000000,
        ))
    session.commit()


@pytest.fixture
def _seed_macro(session, _seed_trading_dates):
    """테스트 매크로 데이터."""
    session.add(FactMacroIndicator(
        date_id=date_to_id(date(2026, 1, 2)),
        vix=22.5,
        sp500_close=4800.0,
        sp500_sma20=4750.0,
        market_score=6,
    ))
    session.commit()


# ---------------------------------------------------------------------------
# _find_trading_date_ago
# ---------------------------------------------------------------------------


class TestFindTradingDateAgo:
    def test_finds_20_trading_days_ago(self, session, _seed_trading_dates):
        """20거래일 전 날짜를 찾는다."""
        # 2026-02-15 기준
        run_date_id = date_to_id(date(2026, 2, 15))
        result = _find_trading_date_ago(session, run_date_id, 20)
        assert result is not None
        assert result < run_date_id

    def test_returns_none_if_insufficient_data(self, session, _seed_trading_dates):
        """데이터 부족 시 None."""
        # 아주 이른 날짜
        run_date_id = date_to_id(date(2026, 1, 5))
        result = _find_trading_date_ago(session, run_date_id, 20)
        assert result is None


# ---------------------------------------------------------------------------
# compute_price_path
# ---------------------------------------------------------------------------


class TestComputePricePath:
    def test_basic_path(self, session, _seed_prices):
        """가격 경로를 올바르게 계산한다."""
        rec_date_id = date_to_id(date(2026, 1, 2))
        path, max_gain, max_loss = compute_price_path(
            session, stock_id=1, rec_date_id=rec_date_id,
            base_price=150.0, days=5,
        )
        assert len(path) > 0
        assert max_gain >= max_loss

    def test_empty_path_no_prices(self, session, _seed_stock, _seed_trading_dates):
        """가격 데이터 없으면 빈 튜플 반환."""
        path, gain, loss = compute_price_path(
            session, stock_id=1, rec_date_id=date_to_id(date(2026, 3, 1)),
            base_price=100.0,
        )
        assert path == ()
        assert gain == 0.0
        assert loss == 0.0

    def test_zero_base_price(self, session, _seed_prices):
        """base_price=0이면 빈 결과."""
        path, gain, loss = compute_price_path(
            session, stock_id=1, rec_date_id=date_to_id(date(2026, 1, 2)),
            base_price=0.0,
        )
        assert path == ()


# ---------------------------------------------------------------------------
# build_retrospective_prompt
# ---------------------------------------------------------------------------


class TestBuildRetrospectivePrompt:
    def test_prompt_structure(self):
        """프롬프트가 올바른 구조를 갖는다."""
        candidates = [
            RetrospectiveCandidate(
                recommendation_id=1,
                ticker="AAPL",
                sector="Technology",
                ai_approved=True,
                ai_confidence=7,
                ai_reason="강한 매수 시그널",
                ai_target_price=185.0,
                ai_stop_loss=165.0,
                price_at_rec=175.0,
                return_20d=-8.2,
                max_gain_pct=3.1,
                max_loss_pct=-12.4,
                price_path=(0.2, 1.1, -2.0, -5.0, -8.2),
                regime_at_rec="bear",
                vix_at_rec=28.3,
            ),
        ]
        prompt = build_retrospective_prompt(candidates)

        assert "AAPL" in prompt
        assert "추천" in prompt
        assert "신뢰도 7" in prompt
        assert "Technology" in prompt
        assert "bear" in prompt
        assert "<retrospective_batch>" in prompt

    def test_excluded_stock(self):
        """제외 종목도 올바르게 표시된다."""
        candidates = [
            RetrospectiveCandidate(
                recommendation_id=2,
                ticker="MSFT",
                sector="Technology",
                ai_approved=False,
                ai_confidence=3,
                ai_reason="리스크 높음",
                ai_target_price=None,
                ai_stop_loss=None,
                price_at_rec=300.0,
                return_20d=5.0,
                max_gain_pct=8.0,
                max_loss_pct=-2.0,
                price_path=(1.0, 3.0, 5.0),
                regime_at_rec="range",
                vix_at_rec=18.0,
            ),
        ]
        prompt = build_retrospective_prompt(candidates)
        assert "제외" in prompt
        assert "MSFT" in prompt


# ---------------------------------------------------------------------------
# find_retrospective_candidates (통합)
# ---------------------------------------------------------------------------


class TestFindRetrospectiveCandidates:
    def test_no_candidates_without_recs(self, session, _seed_trading_dates, _seed_macro):
        """추천 없으면 빈 리스트."""
        run_date_id = date_to_id(date(2026, 2, 20))
        result = find_retrospective_candidates(session, run_date_id)
        assert result == []
