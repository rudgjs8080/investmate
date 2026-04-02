"""AI 리밸런싱 트리거 테스트."""

from __future__ import annotations

import pytest

from src.ai.rebalance_trigger import (
    RebalanceSuggestion,
    _classify_regime,
    check_regime_change_trigger,
    check_stop_loss_triggers,
    generate_rebalance_alerts,
)
from src.db.models import (
    FactDailyPrice,
    FactDailyRecommendation,
    FactMacroIndicator,
)


# ──────────────────────────────────────────
# RebalanceSuggestion dataclass 테스트
# ──────────────────────────────────────────


class TestRebalanceSuggestion:
    """RebalanceSuggestion frozen dataclass 테스트."""

    def test_default_values(self):
        suggestion = RebalanceSuggestion()
        assert suggestion.tickers_to_review == ()
        assert suggestion.reasons == ()
        assert suggestion.urgency == "low"

    def test_frozen(self):
        suggestion = RebalanceSuggestion(urgency="high")
        with pytest.raises(AttributeError):
            suggestion.urgency = "low"  # type: ignore[misc]

    def test_custom_values(self):
        suggestion = RebalanceSuggestion(
            tickers_to_review=("AAPL", "MSFT"),
            reasons=("손절가 도달: AAPL, MSFT",),
            urgency="high",
        )
        assert len(suggestion.tickers_to_review) == 2
        assert suggestion.urgency == "high"


# ──────────────────────────────────────────
# _classify_regime 단위 테스트
# ──────────────────────────────────────────


class TestClassifyRegime:
    """시장 체제 분류 테스트."""

    def test_crisis(self):
        assert _classify_regime(35.0, 4000, 4200) == "crisis"

    def test_bear(self):
        assert _classify_regime(27.0, 4000, 4200) == "bear"

    def test_bull(self):
        assert _classify_regime(15.0, 4200, 4000) == "bull"

    def test_range_default(self):
        assert _classify_regime(22.0, 4100, 4000) == "range"

    def test_high_vix_overrides(self):
        """VIX > 30이면 S&P 위치와 무관하게 crisis."""
        assert _classify_regime(31.0, 5000, 4000) == "crisis"


# ──────────────────────────────────────────
# 헬퍼 함수
# ──────────────────────────────────────────


def _get_or_create_stock(session, ticker="AAPL", name="Apple Inc."):
    """seeded_session에서 종목을 조회하거나 생성한다."""
    from sqlalchemy import select

    from src.db.models import DimStock

    stock = session.scalar(
        select(DimStock).where(DimStock.ticker == ticker)
    )
    if stock:
        return stock

    # seeded_session에 AAPL이 없으면 직접 생성
    from src.db.models import DimMarket

    market = session.scalar(select(DimMarket).where(DimMarket.code == "US"))
    stock = DimStock(
        ticker=ticker, name=name, market_id=market.market_id,
        is_active=True, is_sp500=True,
    )
    session.add(stock)
    session.flush()
    return stock


# dim_date에 존재하는 날짜 사용 (seed: 2015-2030)
DATE_ID_PREV = 20250602  # 2025-06-02
DATE_ID_NOW = 20250603   # 2025-06-03


# ──────────────────────────────────────────
# check_stop_loss_triggers 테스트
# ──────────────────────────────────────────


class TestCheckStopLossTriggers:
    """손절가 트리거 감지 테스트."""

    def test_triggered_when_price_below_stop(self, seeded_session):
        """종가가 손절가 이하이면 트리거."""
        stock = _get_or_create_stock(seeded_session)

        rec = FactDailyRecommendation(
            run_date_id=DATE_ID_PREV,
            stock_id=stock.stock_id,
            rank=1,
            total_score=8.0,
            technical_score=7.0,
            fundamental_score=7.0,
            external_score=6.0,
            momentum_score=7.0,
            smart_money_score=5.0,
            recommendation_reason="test",
            price_at_recommendation=100.0,
            ai_stop_loss=95.0,
            ai_approved=True,
        )
        seeded_session.add(rec)

        price = FactDailyPrice(
            stock_id=stock.stock_id,
            date_id=DATE_ID_NOW,
            open=100.0,
            high=101.0,
            low=92.0,
            close=93.0,
            adj_close=93.0,
            volume=1_000_000,
        )
        seeded_session.add(price)
        seeded_session.flush()

        result = check_stop_loss_triggers(seeded_session, DATE_ID_NOW)
        assert stock.ticker in result

    def test_not_triggered_when_price_above_stop(self, seeded_session):
        """종가가 손절가 위이면 트리거되지 않음."""
        stock = _get_or_create_stock(seeded_session)

        rec = FactDailyRecommendation(
            run_date_id=DATE_ID_PREV,
            stock_id=stock.stock_id,
            rank=1,
            total_score=8.0,
            technical_score=7.0,
            fundamental_score=7.0,
            external_score=6.0,
            momentum_score=7.0,
            smart_money_score=5.0,
            recommendation_reason="test",
            price_at_recommendation=100.0,
            ai_stop_loss=95.0,
            ai_approved=True,
        )
        seeded_session.add(rec)

        price = FactDailyPrice(
            stock_id=stock.stock_id,
            date_id=DATE_ID_NOW,
            open=100.0,
            high=105.0,
            low=99.0,
            close=103.0,
            adj_close=103.0,
            volume=1_000_000,
        )
        seeded_session.add(price)
        seeded_session.flush()

        result = check_stop_loss_triggers(seeded_session, DATE_ID_NOW)
        assert stock.ticker not in result

    def test_no_recommendations(self, seeded_session):
        """추천이 없으면 빈 리스트."""
        result = check_stop_loss_triggers(seeded_session, DATE_ID_NOW)
        assert result == []


# ──────────────────────────────────────────
# check_regime_change_trigger 테스트
# ──────────────────────────────────────────


class TestCheckRegimeChangeTrigger:
    """레짐 변경 감지 테스트."""

    def test_regime_change_detected(self, seeded_session):
        """레짐 변경 시 문자열 반환."""
        macro_prev = FactMacroIndicator(
            date_id=DATE_ID_PREV,
            vix=15.0,
            sp500_close=4500.0,
            sp500_sma20=4400.0,
        )
        macro_now = FactMacroIndicator(
            date_id=DATE_ID_NOW,
            vix=35.0,
            sp500_close=4000.0,
            sp500_sma20=4400.0,
        )
        seeded_session.add_all([macro_prev, macro_now])
        seeded_session.flush()

        result = check_regime_change_trigger(seeded_session, DATE_ID_NOW)
        assert result is not None
        assert "bull" in result
        assert "crisis" in result

    def test_no_regime_change(self, seeded_session):
        """레짐 변경 없으면 None."""
        macro_prev = FactMacroIndicator(
            date_id=DATE_ID_PREV,
            vix=15.0,
            sp500_close=4500.0,
            sp500_sma20=4400.0,
        )
        macro_now = FactMacroIndicator(
            date_id=DATE_ID_NOW,
            vix=16.0,
            sp500_close=4520.0,
            sp500_sma20=4410.0,
        )
        seeded_session.add_all([macro_prev, macro_now])
        seeded_session.flush()

        result = check_regime_change_trigger(seeded_session, DATE_ID_NOW)
        assert result is None

    def test_insufficient_data(self, seeded_session):
        """데이터가 1건 이하이면 None."""
        macro = FactMacroIndicator(
            date_id=DATE_ID_NOW,
            vix=15.0,
            sp500_close=4500.0,
            sp500_sma20=4400.0,
        )
        seeded_session.add(macro)
        seeded_session.flush()

        result = check_regime_change_trigger(seeded_session, DATE_ID_NOW)
        assert result is None


# ──────────────────────────────────────────
# generate_rebalance_alerts 통합 테스트
# ──────────────────────────────────────────

# 두 번째 세트의 날짜 (같은 seeded_session에서 중복 방지)
DATE_ID_PREV2 = 20250604
DATE_ID_NOW2 = 20250605
DATE_ID_PREV3 = 20250606
DATE_ID_NOW3 = 20250609  # 월요일 (주말 건너뜀)
DATE_ID_PREV4 = 20250610
DATE_ID_NOW4 = 20250611


class TestGenerateRebalanceAlerts:
    """리밸런싱 알림 종합 테스트."""

    def test_no_triggers(self, seeded_session):
        """트리거 없으면 low urgency 빈 제안."""
        result = generate_rebalance_alerts(seeded_session, DATE_ID_NOW2)
        assert result.urgency == "low"
        assert result.tickers_to_review == ()
        assert result.reasons == ()

    def test_stop_loss_only(self, seeded_session):
        """손절만 트리거되면 high urgency."""
        stock = _get_or_create_stock(seeded_session, "MSFT", "Microsoft Corp")

        rec = FactDailyRecommendation(
            run_date_id=DATE_ID_PREV2,
            stock_id=stock.stock_id,
            rank=1,
            total_score=8.0,
            technical_score=7.0,
            fundamental_score=7.0,
            external_score=6.0,
            momentum_score=7.0,
            smart_money_score=5.0,
            recommendation_reason="test",
            price_at_recommendation=100.0,
            ai_stop_loss=95.0,
            ai_approved=True,
        )
        seeded_session.add(rec)

        price = FactDailyPrice(
            stock_id=stock.stock_id,
            date_id=DATE_ID_NOW2,
            open=100.0,
            high=100.0,
            low=90.0,
            close=90.0,
            adj_close=90.0,
            volume=1_000_000,
        )
        seeded_session.add(price)
        seeded_session.flush()

        result = generate_rebalance_alerts(seeded_session, DATE_ID_NOW2)
        assert result.urgency == "high"
        assert "MSFT" in result.tickers_to_review
        assert any("손절가" in r for r in result.reasons)

    def test_regime_change_only(self, seeded_session):
        """레짐 변경만 트리거되면 medium urgency."""
        macro_prev = FactMacroIndicator(
            date_id=DATE_ID_PREV3,
            vix=15.0,
            sp500_close=4500.0,
            sp500_sma20=4400.0,
        )
        macro_now = FactMacroIndicator(
            date_id=DATE_ID_NOW3,
            vix=35.0,
            sp500_close=4000.0,
            sp500_sma20=4400.0,
        )
        seeded_session.add_all([macro_prev, macro_now])
        seeded_session.flush()

        result = generate_rebalance_alerts(seeded_session, DATE_ID_NOW3)
        assert result.urgency == "medium"
        assert any("시장 체제" in r for r in result.reasons)

    def test_both_triggers_high_urgency(self, seeded_session):
        """손절 + 레짐 변경 동시 트리거 시 high urgency."""
        stock = _get_or_create_stock(seeded_session, "GOOGL", "Alphabet Inc")

        rec = FactDailyRecommendation(
            run_date_id=DATE_ID_PREV4,
            stock_id=stock.stock_id,
            rank=1,
            total_score=8.0,
            technical_score=7.0,
            fundamental_score=7.0,
            external_score=6.0,
            momentum_score=7.0,
            smart_money_score=5.0,
            recommendation_reason="test",
            price_at_recommendation=100.0,
            ai_stop_loss=95.0,
            ai_approved=True,
        )
        seeded_session.add(rec)

        price = FactDailyPrice(
            stock_id=stock.stock_id,
            date_id=DATE_ID_NOW4,
            open=100.0,
            high=100.0,
            low=90.0,
            close=90.0,
            adj_close=90.0,
            volume=1_000_000,
        )
        seeded_session.add(price)

        macro_prev = FactMacroIndicator(
            date_id=DATE_ID_PREV4,
            vix=15.0,
            sp500_close=4500.0,
            sp500_sma20=4400.0,
        )
        macro_now = FactMacroIndicator(
            date_id=DATE_ID_NOW4,
            vix=35.0,
            sp500_close=4000.0,
            sp500_sma20=4400.0,
        )
        seeded_session.add_all([macro_prev, macro_now])
        seeded_session.flush()

        result = generate_rebalance_alerts(seeded_session, DATE_ID_NOW4)
        assert result.urgency == "high"
        assert len(result.reasons) == 2
        assert "GOOGL" in result.tickers_to_review
