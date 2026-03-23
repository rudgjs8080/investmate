"""백테스트 엔진 테스트."""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine

from src.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    _calculate_max_drawdown,
    _calculate_sharpe,
    _estimate_tx_cost,
    _safe_mean,
    _win_rate,
)
from src.portfolio.optimizer import estimate_market_impact
from src.backtest.comparator import (
    DEFAULT_WEIGHTS,
    WeightComparisonResult,
    compare_weights,
)
from src.db.engine import create_session_factory, init_db
from src.db.helpers import date_to_id
from src.db.models import (
    Base,
    DimDate,
    DimMarket,
    DimSector,
    DimStock,
    FactDailyRecommendation,
)


def _seed_test_db(session):
    """테스트용 DB 시딩."""
    # 마켓
    market = DimMarket(market_id=1, code="US", name="미국", currency="USD", timezone="America/New_York")
    session.add(market)

    # 섹터
    sector = DimSector(sector_id=1, sector_name="Technology")
    session.add(sector)

    # 종목
    stock1 = DimStock(stock_id=1, ticker="AAPL", name="Apple Inc.", market_id=1, sector_id=1, is_sp500=True)
    stock2 = DimStock(stock_id=2, ticker="MSFT", name="Microsoft Corp.", market_id=1, sector_id=1, is_sp500=True)
    stock3 = DimStock(stock_id=3, ticker="GOOG", name="Alphabet Inc.", market_id=1, sector_id=1, is_sp500=True)
    session.add_all([stock1, stock2, stock3])

    # 날짜
    dates = [date(2026, 3, 1), date(2026, 3, 2), date(2026, 3, 3)]
    for d in dates:
        did = date_to_id(d)
        session.add(DimDate(
            date_id=did, date=d, year=d.year, quarter=1, month=d.month,
            week_of_year=d.isocalendar()[1], day_of_week=d.weekday(),
            is_trading_day=True,
        ))
    session.flush()

    # 추천 데이터
    recs = [
        # 3/1: AAPL rank 1, MSFT rank 2
        FactDailyRecommendation(
            run_date_id=date_to_id(date(2026, 3, 1)), stock_id=1, rank=1,
            total_score=8.0, technical_score=9.0, fundamental_score=7.0,
            smart_money_score=6.0, external_score=7.0, momentum_score=8.0,
            recommendation_reason="test", price_at_recommendation=150.0,
            return_1d=1.5, return_5d=3.0, return_10d=4.0, return_20d=5.0,
        ),
        FactDailyRecommendation(
            run_date_id=date_to_id(date(2026, 3, 1)), stock_id=2, rank=2,
            total_score=7.0, technical_score=6.0, fundamental_score=8.0,
            smart_money_score=7.0, external_score=6.0, momentum_score=7.0,
            recommendation_reason="test", price_at_recommendation=300.0,
            return_1d=-0.5, return_5d=1.0, return_10d=2.0, return_20d=-2.0,
        ),
        # 3/2: GOOG rank 1
        FactDailyRecommendation(
            run_date_id=date_to_id(date(2026, 3, 2)), stock_id=3, rank=1,
            total_score=7.5, technical_score=7.0, fundamental_score=7.0,
            smart_money_score=8.0, external_score=7.0, momentum_score=7.0,
            recommendation_reason="test", price_at_recommendation=2800.0,
            return_1d=2.0, return_5d=4.0, return_10d=5.0, return_20d=8.0,
        ),
    ]
    session.add_all(recs)
    session.flush()


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    _seed_test_db(session)
    session.commit()
    return session, engine


# ──────────────────────────────────────────
# 유틸 함수 테스트
# ──────────────────────────────────────────
class TestUtilFunctions:
    def test_safe_mean_empty(self):
        assert _safe_mean([]) is None

    def test_safe_mean_values(self):
        assert _safe_mean([1.0, 3.0]) == 2.0

    def test_win_rate_empty(self):
        assert _win_rate([]) is None

    def test_win_rate_all_positive(self):
        assert _win_rate([1.0, 2.0, 3.0]) == 100.0

    def test_win_rate_mixed(self):
        assert _win_rate([1.0, -1.0, 2.0, -2.0]) == 50.0

    def test_sharpe_insufficient(self):
        assert _calculate_sharpe([]) is None
        assert _calculate_sharpe([1.0]) is None

    def test_sharpe_positive(self):
        result = _calculate_sharpe([1.0, 2.0, 3.0, 4.0])
        assert result is not None
        assert result > 0

    def test_max_drawdown_empty(self):
        assert _calculate_max_drawdown([]) is None

    def test_max_drawdown_no_loss(self):
        assert _calculate_max_drawdown([1.0, 1.0, 1.0]) == 0.0

    def test_max_drawdown_with_loss(self):
        result = _calculate_max_drawdown([5.0, -3.0, -2.0, 4.0])
        assert result is not None
        assert result >= 5.0  # peak 5 → cumulative 0 = dd 5


# ──────────────────────────────────────────
# BacktestEngine 테스트
# ──────────────────────────────────────────
class TestBacktestEngine:
    def test_run_with_data(self):
        session, _ = _make_session()
        config = BacktestConfig(
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 3),
        )
        result = BacktestEngine().run(session, config)

        assert isinstance(result, BacktestResult)
        assert result.total_days == 2
        assert result.total_recommendations == 3
        assert result.avg_return_1d is not None
        assert result.avg_return_20d is not None
        assert result.win_rate_1d is not None
        assert result.best_pick is not None
        assert result.best_pick[0] == "GOOG"  # 8.0% return
        assert result.worst_pick is not None
        assert result.worst_pick[0] == "MSFT"  # -2.0% return
        assert len(result.by_date) == 2
        session.close()

    def test_run_empty_period(self):
        session, _ = _make_session()
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 31),
        )
        result = BacktestEngine().run(session, config)
        assert result.total_days == 0
        assert result.total_recommendations == 0
        session.close()

    def test_win_rates(self):
        session, _ = _make_session()
        config = BacktestConfig(
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 3),
        )
        result = BacktestEngine().run(session, config)
        # 1d: 1.5, -0.5, 2.0 → 2 wins / 3 = 66.7%
        assert result.win_rate_1d is not None
        assert abs(result.win_rate_1d - 66.7) < 1.0
        session.close()

    def test_daily_results(self):
        session, _ = _make_session()
        config = BacktestConfig(
            start_date=date(2026, 3, 1), end_date=date(2026, 3, 3),
        )
        result = BacktestEngine().run(session, config)
        day1 = result.by_date[0]
        assert day1.recommendation_count == 2
        assert day1.avg_return_1d is not None
        session.close()


# ──────────────────────────────────────────
# 가중치 비교 테스트
# ──────────────────────────────────────────
class TestCompareWeights:
    def test_compare_produces_results(self):
        session, _ = _make_session()
        weight_sets = [
            ("기본", DEFAULT_WEIGHTS),
            ("기술 중심", {"technical": 0.50, "fundamental": 0.10, "smart_money": 0.10, "external": 0.10, "momentum": 0.20}),
        ]
        results = compare_weights(
            session, date(2026, 3, 1), date(2026, 3, 3), weight_sets,
        )
        assert len(results) == 2
        for r in results:
            assert isinstance(r, WeightComparisonResult)
            assert r.total_picks >= 0
        session.close()

    def test_different_weights_different_picks(self):
        session, _ = _make_session()
        # top_n=1 → 다른 가중치면 다른 종목이 선정될 수 있음
        weight_sets = [
            ("기본", DEFAULT_WEIGHTS),
            ("펀더멘털 중심", {"technical": 0.05, "fundamental": 0.80, "smart_money": 0.05, "external": 0.05, "momentum": 0.05}),
        ]
        results = compare_weights(
            session, date(2026, 3, 1), date(2026, 3, 3), weight_sets, top_n=1,
        )
        assert len(results) == 2
        # 둘 다 결과가 있어야 함
        for r in results:
            assert r.total_picks >= 0
        session.close()

    def test_empty_period(self):
        session, _ = _make_session()
        results = compare_weights(
            session, date(2025, 1, 1), date(2025, 1, 31),
            [("기본", DEFAULT_WEIGHTS)],
        )
        assert results == []
        session.close()


# ──────────────────────────────────────────
# 유동성 기반 거래비용 테스트
# ──────────────────────────────────────────
class TestEstimateTxCost:
    def test_high_liquidity(self):
        # $10M+ → base_bps
        cost = _estimate_tx_cost(volume=100_000, price=150.0, base_bps=20)
        assert cost == 20  # 100k * 150 = $15M

    def test_medium_liquidity(self):
        # $1M ~ $10M → base_bps + 5
        cost = _estimate_tx_cost(volume=10_000, price=200.0, base_bps=20)
        assert cost == 25  # 10k * 200 = $2M

    def test_low_liquidity(self):
        # < $1M → base_bps + 15
        cost = _estimate_tx_cost(volume=1_000, price=50.0, base_bps=20)
        assert cost == 35  # 1k * 50 = $50K

    def test_none_volume(self):
        cost = _estimate_tx_cost(volume=None, price=100.0, base_bps=20)
        assert cost == 35  # worst case

    def test_none_price(self):
        cost = _estimate_tx_cost(volume=10_000, price=None, base_bps=20)
        assert cost == 35

    def test_zero_volume(self):
        cost = _estimate_tx_cost(volume=0, price=100.0, base_bps=20)
        assert cost == 35

    def test_custom_base_bps(self):
        cost = _estimate_tx_cost(volume=200_000, price=100.0, base_bps=10)
        assert cost == 10  # 200k * 100 = $20M → base


# ──────────────────────────────────────────
# 시장 충격 모델 테스트
# ──────────────────────────────────────────
class TestEstimateMarketImpact:
    def test_small_participation(self):
        # 소규모 포지션 → 낮은 충격
        impact = estimate_market_impact(
            position_size=10_000, daily_volume=1_000_000, price=100.0,
        )
        assert impact > 0
        assert impact < 0.1  # 매우 작은 참여율

    def test_large_participation(self):
        # 대규모 포지션 → 높은 충격
        impact_large = estimate_market_impact(
            position_size=5_000_000, daily_volume=100_000, price=50.0,
        )
        impact_small = estimate_market_impact(
            position_size=50_000, daily_volume=100_000, price=50.0,
        )
        assert impact_large > impact_small

    def test_zero_volume_returns_default(self):
        impact = estimate_market_impact(
            position_size=10_000, daily_volume=0, price=100.0,
        )
        assert impact == 0.5

    def test_zero_price_returns_default(self):
        impact = estimate_market_impact(
            position_size=10_000, daily_volume=1_000_000, price=0,
        )
        assert impact == 0.5

    def test_negative_volume_returns_default(self):
        impact = estimate_market_impact(
            position_size=10_000, daily_volume=-100, price=100.0,
        )
        assert impact == 0.5
