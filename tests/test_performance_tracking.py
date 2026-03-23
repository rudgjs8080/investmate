"""성과 추적 자동 업데이터 테스트 — update_recommendation_returns()."""

from __future__ import annotations

from datetime import date

import pytest

from src.analysis.performance import fill_execution_prices, update_recommendation_returns
from src.config import get_settings
from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import FactDailyPrice, FactDailyRecommendation

# 기본 거래 비용 (bps -> %)
_TX_COST_PCT = get_settings().transaction_cost_bps / 100


def _make_recommendation(
    session,
    stock_id: int,
    run_date: date,
    price: float,
    *,
    return_1d: float | None = None,
    return_5d: float | None = None,
    return_10d: float | None = None,
    return_20d: float | None = None,
) -> FactDailyRecommendation:
    """테스트용 추천 레코드 생성 헬퍼."""
    rec = FactDailyRecommendation(
        run_date_id=date_to_id(run_date),
        stock_id=stock_id,
        rank=1,
        total_score=7.5,
        technical_score=7.0,
        fundamental_score=6.5,
        external_score=6.0,
        momentum_score=7.0,
        smart_money_score=5.0,
        recommendation_reason="테스트 추천",
        price_at_recommendation=price,
        return_1d=return_1d,
        return_5d=return_5d,
        return_10d=return_10d,
        return_20d=return_20d,
    )
    session.add(rec)
    return rec


def _add_price(
    session, stock_id: int, d: date, close: float,
) -> FactDailyPrice:
    """테스트용 가격 데이터 추가 헬퍼."""
    price = FactDailyPrice(
        stock_id=stock_id,
        date_id=date_to_id(d),
        open=close,
        high=close,
        low=close,
        close=close,
        adj_close=close,
        volume=1_000_000,
    )
    session.add(price)
    return price


def _ensure_dates(session, dates: list[date]) -> None:
    """dim_date에 날짜가 존재하도록 보장한다."""
    ensure_date_ids(session, dates)


class TestUpdateRecommendationReturns:
    """update_recommendation_returns() 단위/통합 테스트."""

    def test_update_returns_basic(self, seeded_session, sample_stock):
        """추천가 100, 이후 가격 데이터로 1d/5d/10d/20d 수익률 계산."""
        session = seeded_session
        sid = sample_stock["id"]
        rec_date = date(2026, 2, 1)  # 일요일이지만 테스트용

        # 추천일 이후 25거래일치 가격 생성 (충분한 여유)
        future_dates = [date(2026, 2, d) for d in range(2, 28)]
        all_dates = [rec_date] + future_dates
        _ensure_dates(session, all_dates)

        # 추천 레코드: 가격 100
        _make_recommendation(session, sid, rec_date, 100.0)

        # 1일 후 102, 5일 후 105, 10일 후 110, 20일 후 120
        prices = {1: 102.0, 5: 105.0, 10: 110.0, 20: 120.0}
        for i, d in enumerate(future_dates, start=1):
            close = prices.get(i, 100.0 + i * 0.5)  # 기본 소폭 상승
            _add_price(session, sid, d, close)

        session.flush()

        count = update_recommendation_returns(session)
        assert count == 1

        rec = session.query(FactDailyRecommendation).one()
        # 거래 비용(0.2%) 차감 적용
        assert float(rec.return_1d) == pytest.approx(2.0 - _TX_COST_PCT)
        assert float(rec.return_5d) == pytest.approx(5.0 - _TX_COST_PCT)
        assert float(rec.return_10d) == pytest.approx(10.0 - _TX_COST_PCT)
        assert float(rec.return_20d) == pytest.approx(20.0 - _TX_COST_PCT)

    def test_update_returns_skips_recent(self, seeded_session, sample_stock):
        """오늘 생성된 추천은 아직 수익률 계산 불가 (미래 가격 없음)."""
        session = seeded_session
        sid = sample_stock["id"]
        today = date(2026, 3, 21)

        _ensure_dates(session, [today])
        _make_recommendation(session, sid, today, 150.0)
        session.flush()

        count = update_recommendation_returns(session)
        assert count == 0

        rec = session.query(FactDailyRecommendation).one()
        assert rec.return_1d is None
        assert rec.return_5d is None
        assert rec.return_10d is None
        assert rec.return_20d is None

    def test_update_returns_partial(self, seeded_session, sample_stock):
        """6거래일 경과 → return_1d, return_5d만 계산, 10d/20d는 NULL."""
        session = seeded_session
        sid = sample_stock["id"]
        rec_date = date(2026, 3, 10)

        # 추천일 + 이후 6일치
        future_dates = [date(2026, 3, d) for d in range(11, 17)]
        all_dates = [rec_date] + future_dates
        _ensure_dates(session, all_dates)

        _make_recommendation(session, sid, rec_date, 200.0)

        for i, d in enumerate(future_dates, start=1):
            close = 200.0 + i * 2.0  # 매일 2달러씩 상승
            _add_price(session, sid, d, close)

        session.flush()

        count = update_recommendation_returns(session)
        assert count == 1

        rec = session.query(FactDailyRecommendation).one()
        # 1일 후: 202, 수익률 = (202-200)/200*100 - tx_cost
        assert float(rec.return_1d) == pytest.approx(1.0 - _TX_COST_PCT)
        # 5일 후: 210, 수익률 = (210-200)/200*100 - tx_cost
        assert float(rec.return_5d) == pytest.approx(5.0 - _TX_COST_PCT)
        # 10일, 20일은 데이터 부족
        assert rec.return_10d is None
        assert rec.return_20d is None

    def test_update_returns_already_calculated(self, seeded_session, sample_stock):
        """이미 수익률이 있는 추천은 재계산하지 않는다."""
        session = seeded_session
        sid = sample_stock["id"]
        rec_date = date(2026, 1, 15)

        future_dates = [date(2026, 1, d) for d in range(16, 31)]
        # 2월도 추가
        future_dates += [date(2026, 2, d) for d in range(1, 15)]
        all_dates = [rec_date] + future_dates
        _ensure_dates(session, all_dates)

        # 모든 수익률이 이미 설정됨
        _make_recommendation(
            session, sid, rec_date, 100.0,
            return_1d=1.5, return_5d=3.0, return_10d=5.5, return_20d=8.0,
        )

        # 다른 가격 데이터 존재해도 무시해야 함
        for i, d in enumerate(future_dates, start=1):
            _add_price(session, sid, d, 999.0)  # 터무니없는 가격

        session.flush()

        count = update_recommendation_returns(session)
        assert count == 0

        rec = session.query(FactDailyRecommendation).one()
        # 기존 값 그대로 유지 (999.0 기반으로 재계산되지 않아야 함)
        assert rec.return_1d == pytest.approx(1.5)
        assert rec.return_5d == pytest.approx(3.0)
        assert rec.return_10d == pytest.approx(5.5)
        assert rec.return_20d == pytest.approx(8.0)

    def test_update_returns_no_price_data(self, seeded_session, sample_stock):
        """가격 데이터가 없으면 수익률은 NULL로 유지."""
        session = seeded_session
        sid = sample_stock["id"]
        rec_date = date(2026, 2, 20)

        _ensure_dates(session, [rec_date])
        _make_recommendation(session, sid, rec_date, 100.0)
        session.flush()

        # 가격 데이터 없음
        count = update_recommendation_returns(session)
        assert count == 0

        rec = session.query(FactDailyRecommendation).one()
        assert rec.return_1d is None
        assert rec.return_5d is None
        assert rec.return_10d is None
        assert rec.return_20d is None

    def test_update_returns_count(self, seeded_session, sample_stock):
        """여러 추천의 업데이트 건수 확인."""
        session = seeded_session
        sid = sample_stock["id"]

        # 두 번째 종목 추가
        from src.db.repository import StockRepository

        stock2 = StockRepository.add(
            session, "MSFT", "Microsoft Corp.", sample_stock["id"],
        )
        session.flush()
        sid2 = stock2.stock_id

        rec_date1 = date(2026, 1, 5)
        rec_date2 = date(2026, 1, 10)

        # 충분한 미래 날짜
        dates = [date(2026, 1, d) for d in range(5, 31)]
        dates += [date(2026, 2, d) for d in range(1, 10)]
        _ensure_dates(session, dates)

        _make_recommendation(session, sid, rec_date1, 100.0)
        _make_recommendation(session, sid2, rec_date2, 200.0)

        # 두 종목 모두 가격 데이터 추가
        for i, d in enumerate(dates, start=1):
            _add_price(session, sid, d, 100.0 + i)
            _add_price(session, sid2, d, 200.0 + i)

        session.flush()

        count = update_recommendation_returns(session)
        assert count == 2

    def test_update_returns_empty_db(self, seeded_session):
        """추천이 없으면 0 반환."""
        count = update_recommendation_returns(seeded_session)
        assert count == 0

    def test_update_returns_negative_return(self, seeded_session, sample_stock):
        """하락한 종목의 음수 수익률 계산 확인."""
        session = seeded_session
        sid = sample_stock["id"]
        rec_date = date(2026, 2, 5)

        future_dates = [date(2026, 2, d) for d in range(6, 10)]
        all_dates = [rec_date] + future_dates
        _ensure_dates(session, all_dates)

        _make_recommendation(session, sid, rec_date, 200.0)

        # 1일 후 190 (-5%), 나머지도 하락
        _add_price(session, sid, future_dates[0], 190.0)
        for d in future_dates[1:]:
            _add_price(session, sid, d, 185.0)

        session.flush()

        count = update_recommendation_returns(session)
        assert count == 1

        rec = session.query(FactDailyRecommendation).one()
        assert float(rec.return_1d) == pytest.approx(-5.0 - _TX_COST_PCT)  # (190-200)/200*100 - tx_cost

    def test_update_returns_partial_periods_filled(self, seeded_session, sample_stock):
        """일부 기간만 NULL인 추천 — NULL인 필드만 계산."""
        session = seeded_session
        sid = sample_stock["id"]
        rec_date = date(2026, 1, 20)

        future_dates = [date(2026, 1, d) for d in range(21, 31)]
        future_dates += [date(2026, 2, d) for d in range(1, 15)]
        all_dates = [rec_date] + future_dates
        _ensure_dates(session, all_dates)

        # return_1d와 return_5d는 이미 있고, 10d/20d만 NULL
        _make_recommendation(
            session, sid, rec_date, 100.0,
            return_1d=2.0, return_5d=4.0,
        )

        for i, d in enumerate(future_dates, start=1):
            _add_price(session, sid, d, 100.0 + i * 0.5)

        session.flush()

        count = update_recommendation_returns(session)
        assert count == 1

        rec = session.query(FactDailyRecommendation).one()
        # 기존 값 유지
        assert rec.return_1d == pytest.approx(2.0)
        assert rec.return_5d == pytest.approx(4.0)
        # 새로 계산됨
        assert rec.return_10d is not None
        assert rec.return_20d is not None
