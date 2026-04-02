"""레짐 전환 감지 테스트 (Phase 5)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.analysis.regime import detect_regime_transition
from src.db.helpers import date_to_id
from src.db.models import (
    Base,
    DimDate,
    FactMacroIndicator,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()

    # 날짜 디멘션
    for d_offset in range(10):
        d = date(2026, 3, 1 + d_offset)
        did = date_to_id(d)
        session.add(DimDate(
            date_id=did, date=d, year=2026, quarter=1, month=3,
            week_of_year=9, day_of_week=d.weekday(), is_trading_day=True,
        ))
    session.flush()
    session.commit()
    return session


class TestDetectRegimeTransition:
    def test_no_data_returns_none(self):
        session = _make_session()
        result = detect_regime_transition(session)
        assert result is None
        session.close()

    def test_single_data_returns_none(self):
        session = _make_session()
        did = date_to_id(date(2026, 3, 1))
        session.add(FactMacroIndicator(
            date_id=did, vix=18.0, sp500_close=5000.0, sp500_sma20=4900.0,
        ))
        session.commit()
        result = detect_regime_transition(session)
        assert result is None
        session.close()

    def test_detects_transition_range_to_bull(self):
        """횡보 → 강세 전환을 감지한다."""
        session = _make_session()
        # 과거: range (VIX=22, SP close < SMA20)
        did1 = date_to_id(date(2026, 3, 1))
        session.add(FactMacroIndicator(
            date_id=did1, vix=22.0, sp500_close=4800.0, sp500_sma20=4900.0,
        ))
        # 현재: bull (VIX=15, SP close > SMA20)
        did2 = date_to_id(date(2026, 3, 5))
        session.add(FactMacroIndicator(
            date_id=did2, vix=15.0, sp500_close=5100.0, sp500_sma20=4900.0,
        ))
        session.commit()

        result = detect_regime_transition(session, lookback=5)
        assert result is not None
        assert "→" in result
        assert "bull" in result
        session.close()

    def test_detects_transition_to_crisis(self):
        """강세 → 위기 전환을 감지한다."""
        session = _make_session()
        did1 = date_to_id(date(2026, 3, 1))
        session.add(FactMacroIndicator(
            date_id=did1, vix=15.0, sp500_close=5100.0, sp500_sma20=4900.0,
        ))
        did2 = date_to_id(date(2026, 3, 5))
        session.add(FactMacroIndicator(
            date_id=did2, vix=35.0, sp500_close=4500.0, sp500_sma20=4900.0,
        ))
        session.commit()

        result = detect_regime_transition(session, lookback=5)
        assert result is not None
        assert "crisis" in result
        session.close()

    def test_no_transition_returns_none(self):
        """레짐이 같으면 None."""
        session = _make_session()
        did1 = date_to_id(date(2026, 3, 1))
        session.add(FactMacroIndicator(
            date_id=did1, vix=18.0, sp500_close=5100.0, sp500_sma20=4900.0,
        ))
        did2 = date_to_id(date(2026, 3, 5))
        session.add(FactMacroIndicator(
            date_id=did2, vix=17.0, sp500_close=5200.0, sp500_sma20=5000.0,
        ))
        session.commit()

        result = detect_regime_transition(session, lookback=5)
        # 둘 다 bull → 전환 없음
        assert result is None
        session.close()
