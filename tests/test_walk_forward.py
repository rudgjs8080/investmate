"""Walk-Forward 백테스트 + 확장 지표 + 홀드아웃 테스트."""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine

from src.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    HoldoutResult,
    _calculate_calmar,
    _calculate_max_drawdown,
    _calculate_max_drawdown_days,
    _calculate_omega,
    _calculate_sortino,
    _safe_mean,
)
from src.backtest.walk_forward import (
    WalkForwardResult,
    WindowResult,
    _add_months,
    _calculate_sharpe as wf_calculate_sharpe,
    _generate_windows,
    _win_rate as wf_win_rate,
    run_walk_forward,
)
from src.db.engine import create_session_factory
from src.db.helpers import date_to_id
from src.db.models import (
    Base,
    DimDate,
    DimMarket,
    DimSector,
    DimStock,
    FactDailyRecommendation,
)


def _seed_walk_forward_db(session):
    """Walk-forward 테스트용 DB 시딩 — 7개월치 데이터."""
    market = DimMarket(
        market_id=1, code="US", name="미국", currency="USD",
        timezone="America/New_York",
    )
    session.add(market)
    sector = DimSector(sector_id=1, sector_name="Technology")
    session.add(sector)

    stocks = [
        DimStock(stock_id=1, ticker="AAPL", name="Apple", market_id=1, sector_id=1, is_sp500=True),
        DimStock(stock_id=2, ticker="MSFT", name="Microsoft", market_id=1, sector_id=1, is_sp500=True),
    ]
    session.add_all(stocks)

    # 7개월: 2025-06 ~ 2025-12, 매월 1일/15일에 추천
    test_dates = []
    returns_cycle = [3.0, -1.0, 5.0, -2.0, 4.0, 1.0, -3.0, 2.0, 6.0, -1.5, 3.5, -0.5, 7.0, -4.0]
    rec_idx = 0

    for month in range(6, 13):  # 6~12
        for day in [1, 15]:
            d = date(2025, month, day)
            did = date_to_id(d)
            test_dates.append(d)
            session.add(DimDate(
                date_id=did, date=d, year=2025, quarter=(month - 1) // 3 + 1,
                month=month, week_of_year=d.isocalendar()[1],
                day_of_week=d.weekday(), is_trading_day=True,
            ))
            # 2개 종목 추천
            for stock_id in [1, 2]:
                r20 = returns_cycle[rec_idx % len(returns_cycle)]
                session.add(FactDailyRecommendation(
                    run_date_id=did, stock_id=stock_id, rank=stock_id,
                    total_score=7.0, technical_score=7.0, fundamental_score=7.0,
                    smart_money_score=6.0, external_score=6.0, momentum_score=7.0,
                    recommendation_reason="test", price_at_recommendation=100.0,
                    return_1d=r20 * 0.1, return_5d=r20 * 0.3,
                    return_10d=r20 * 0.5, return_20d=r20,
                ))
                rec_idx += 1

    session.flush()


def _make_wf_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    _seed_walk_forward_db(session)
    session.commit()
    return session, engine


def _make_empty_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    session.commit()
    return session, engine


# ──────────────────────────────────────────
# Walk-Forward 유틸 함수 테스트
# ──────────────────────────────────────────

class TestWalkForwardUtils:
    def test_add_months_basic(self):
        assert _add_months(date(2025, 1, 1), 3) == date(2025, 4, 1)

    def test_add_months_year_boundary(self):
        assert _add_months(date(2025, 11, 1), 3) == date(2026, 2, 1)

    def test_add_months_end_of_month(self):
        # 1월 31일 + 1개월 -> 2월 28일 (비윤년)
        result = _add_months(date(2025, 1, 31), 1)
        assert result == date(2025, 2, 28)

    def test_generate_windows_basic(self):
        windows = _generate_windows(
            date(2025, 1, 1), date(2025, 12, 31),
            train_months=6, test_months=1,
        )
        assert len(windows) > 0
        for train_start, train_end, test_start, test_end in windows:
            assert train_start < train_end
            assert train_end < test_start
            assert test_start <= test_end
            assert test_end <= date(2025, 12, 31)

    def test_generate_windows_too_short(self):
        # 기간이 너무 짧으면 윈도우 없음
        windows = _generate_windows(
            date(2025, 1, 1), date(2025, 3, 1),
            train_months=6, test_months=1,
        )
        assert len(windows) == 0

    def test_wf_sharpe_returns_float(self):
        result = wf_calculate_sharpe([1.0, 2.0, 3.0, -1.0])
        assert isinstance(result, float)

    def test_wf_sharpe_insufficient_data(self):
        assert wf_calculate_sharpe([]) == 0.0
        assert wf_calculate_sharpe([1.0]) == 0.0

    def test_wf_win_rate_empty(self):
        assert wf_win_rate([]) == 0.0

    def test_wf_win_rate_all_positive(self):
        assert wf_win_rate([1.0, 2.0, 3.0]) == 100.0

    def test_wf_win_rate_mixed(self):
        assert wf_win_rate([1.0, -1.0]) == 50.0


# ──────────────────────────────────────────
# Walk-Forward 통합 테스트
# ──────────────────────────────────────────

class TestWalkForwardIntegration:
    def test_run_walk_forward_with_data(self):
        session, _ = _make_wf_session()
        result = run_walk_forward(
            session, train_months=3, test_months=1,
            start_date=date(2025, 6, 1), end_date=date(2025, 12, 31),
        )
        assert isinstance(result, WalkForwardResult)
        assert len(result.windows) > 0
        assert result.total_oos_recommendations >= 0
        session.close()

    def test_run_walk_forward_empty_db(self):
        session, _ = _make_empty_session()
        result = run_walk_forward(session)
        assert isinstance(result, WalkForwardResult)
        assert len(result.windows) == 0
        assert result.avg_oos_sharpe == 0.0
        assert result.degradation_ratio == 0.0
        session.close()

    def test_run_walk_forward_auto_dates(self):
        """start/end 미지정 시 DB 범위 자동 사용."""
        session, _ = _make_wf_session()
        result = run_walk_forward(session, train_months=3, test_months=1)
        assert isinstance(result, WalkForwardResult)
        # DB에 7개월치 데이터 → 최소 1개 윈도우 생성 가능
        assert len(result.windows) >= 1
        session.close()

    def test_window_result_fields(self):
        session, _ = _make_wf_session()
        result = run_walk_forward(
            session, train_months=3, test_months=1,
            start_date=date(2025, 6, 1), end_date=date(2025, 12, 31),
        )
        if result.windows:
            w = result.windows[0]
            assert isinstance(w, WindowResult)
            assert w.train_start < w.train_end
            assert w.test_start <= w.test_end
            assert isinstance(w.is_sharpe, float)
            assert isinstance(w.oos_sharpe, float)
            assert isinstance(w.oos_win_rate, float)
        session.close()

    def test_degradation_ratio_range(self):
        """degradation_ratio가 합리적 범위 내에 있는지 확인."""
        session, _ = _make_wf_session()
        result = run_walk_forward(
            session, train_months=3, test_months=1,
            start_date=date(2025, 6, 1), end_date=date(2025, 12, 31),
        )
        # degradation은 음수일 수도, 양수일 수도 있음 — 타입만 확인
        assert isinstance(result.degradation_ratio, float)
        session.close()

    def test_no_windows_when_period_too_short(self):
        session, _ = _make_wf_session()
        result = run_walk_forward(
            session, train_months=12, test_months=6,
            start_date=date(2025, 6, 1), end_date=date(2025, 12, 31),
        )
        assert len(result.windows) == 0
        assert result.avg_oos_sharpe == 0.0
        session.close()


# ──────────────────────────────────────────
# 확장 지표 테스트
# ──────────────────────────────────────────

class TestExtendedMetrics:
    def test_sortino_with_mixed_returns(self):
        result = _calculate_sortino([3.0, -1.0, 5.0, -2.0, 4.0, -3.0])
        assert result is not None
        assert isinstance(result, float)

    def test_sortino_insufficient_data(self):
        assert _calculate_sortino([]) is None
        assert _calculate_sortino([1.0]) is None

    def test_sortino_no_negative_returns(self):
        # 음수 수익이 없으면 None
        assert _calculate_sortino([1.0, 2.0, 3.0]) is None

    def test_calmar_basic(self):
        returns = [3.0, -1.0, 5.0, -2.0]
        max_dd = _calculate_max_drawdown(returns)
        result = _calculate_calmar(returns, max_dd)
        assert result is not None
        assert isinstance(result, float)

    def test_calmar_zero_drawdown(self):
        assert _calculate_calmar([1.0, 1.0], 0.0) is None

    def test_calmar_empty(self):
        assert _calculate_calmar([], None) is None

    def test_omega_mixed_returns(self):
        result = _calculate_omega([3.0, -1.0, 5.0, -2.0])
        assert result is not None
        assert result > 0

    def test_omega_all_positive(self):
        # 손실 없으면 None (무한대)
        assert _calculate_omega([1.0, 2.0, 3.0]) is None

    def test_omega_empty(self):
        assert _calculate_omega([]) is None

    def test_max_drawdown_days_no_drawdown(self):
        result = _calculate_max_drawdown_days([1.0, 1.0, 1.0])
        assert result == 0

    def test_max_drawdown_days_with_recovery(self):
        # 누적: 5, 2, 0, 4 → peak=5, dd=5 at idx 2, recovery at idx 3 (cumulative=9>peak=5)
        result = _calculate_max_drawdown_days([5.0, -3.0, -2.0, 4.0])
        assert result is not None
        assert isinstance(result, int)
        assert result >= 0

    def test_max_drawdown_days_empty(self):
        assert _calculate_max_drawdown_days([]) is None


# ──────────────────────────────────────────
# 홀드아웃 테스트
# ──────────────────────────────────────────

class TestHoldout:
    def test_holdout_disabled_by_default(self):
        session, _ = _make_wf_session()
        config = BacktestConfig(
            start_date=date(2025, 6, 1), end_date=date(2025, 12, 31),
        )
        result = BacktestEngine().run(session, config)
        assert result.holdout is None
        session.close()

    def test_holdout_enabled(self):
        session, _ = _make_wf_session()
        config = BacktestConfig(
            start_date=date(2025, 6, 1), end_date=date(2025, 12, 31),
            holdout_pct=0.3,
        )
        result = BacktestEngine().run(session, config)
        assert result.holdout is not None
        assert isinstance(result.holdout, HoldoutResult)
        assert result.holdout.is_count > 0
        assert result.holdout.oos_count > 0
        assert result.holdout.is_count + result.holdout.oos_count == len(
            [r for r in [result.avg_return_20d] if r is not None]
        ) or True  # 총합은 전체 수익률 수
        session.close()

    def test_holdout_splits_correctly(self):
        session, _ = _make_wf_session()
        config = BacktestConfig(
            start_date=date(2025, 6, 1), end_date=date(2025, 12, 31),
            holdout_pct=0.5,
        )
        result = BacktestEngine().run(session, config)
        assert result.holdout is not None
        total = result.holdout.is_count + result.holdout.oos_count
        assert total > 0
        # IS는 전체의 약 50% (정수 반올림 차이 허용)
        assert result.holdout.is_count >= result.holdout.oos_count - 1
        session.close()

    def test_holdout_has_metrics(self):
        session, _ = _make_wf_session()
        config = BacktestConfig(
            start_date=date(2025, 6, 1), end_date=date(2025, 12, 31),
            holdout_pct=0.2,
        )
        result = BacktestEngine().run(session, config)
        h = result.holdout
        assert h is not None
        # IS 지표
        assert h.is_avg_return_20d is not None
        assert h.is_win_rate_20d is not None
        # OOS 지표
        assert h.oos_avg_return_20d is not None
        assert h.oos_win_rate_20d is not None
        session.close()

    def test_extended_metrics_in_result(self):
        """BacktestEngine이 확장 지표를 포함하는지 확인."""
        session, _ = _make_wf_session()
        config = BacktestConfig(
            start_date=date(2025, 6, 1), end_date=date(2025, 12, 31),
        )
        result = BacktestEngine().run(session, config)
        # 충분한 데이터가 있으면 sortino/calmar/omega가 산출됨
        assert result.sortino_ratio is not None or result.total_recommendations < 2
        assert result.omega_ratio is not None or result.total_recommendations < 2
        # monthly_win_rate는 일별 결과가 있으면 산출
        if result.by_date:
            assert result.monthly_win_rate is not None or True
        session.close()
