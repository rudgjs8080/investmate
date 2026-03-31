"""팩터 수익률 추적 테스트."""

from __future__ import annotations

from datetime import date

import pytest

from src.analysis.factor_returns import FactorMomentum, FactorSpread


class TestFactorSpread:
    """FactorSpread frozen 검증."""

    def test_frozen(self):
        fs = FactorSpread(
            date=date(2026, 3, 15), factor_name="value",
            long_return=1.5, short_return=-0.5, spread=2.0,
        )
        with pytest.raises(AttributeError):
            fs.spread = 0.0  # type: ignore[misc]

    def test_fields(self):
        fs = FactorSpread(
            date=date(2026, 3, 15), factor_name="momentum",
            long_return=2.0, short_return=0.5, spread=1.5,
        )
        assert fs.factor_name == "momentum"
        assert fs.spread == 1.5


class TestFactorMomentum:
    """FactorMomentum frozen 검증."""

    def test_frozen(self):
        fm = FactorMomentum(
            factor_name="quality",
            momentum_1m=1.0, momentum_3m=3.0, momentum_6m=5.0,
        )
        with pytest.raises(AttributeError):
            fm.momentum_1m = 0.0  # type: ignore[misc]


class TestStoreAndRetrieve:
    """팩터 수익률 DB 저장/조회 테스트."""

    def test_store_and_get(self, seeded_session):
        from src.analysis.factor_returns import store_factor_returns
        from src.db.helpers import date_to_id, ensure_date_ids
        from src.db.repository import FactorReturnRepository

        session = seeded_session
        d = date(2026, 3, 15)
        ensure_date_ids(session, [d])

        spreads = [
            FactorSpread(date=d, factor_name="value",
                         long_return=1.5, short_return=-0.5, spread=2.0),
            FactorSpread(date=d, factor_name="momentum",
                         long_return=0.8, short_return=0.2, spread=0.6),
        ]

        count = store_factor_returns(session, spreads)
        assert count == 2

        # 조회
        date_id = date_to_id(d)
        results = FactorReturnRepository.get_by_factor(session, "value")
        assert len(results) >= 1
        assert results[0].spread == pytest.approx(2.0, abs=0.01)

    def test_upsert_updates_existing(self, seeded_session):
        from src.analysis.factor_returns import store_factor_returns
        from src.db.helpers import ensure_date_ids
        from src.db.repository import FactorReturnRepository

        session = seeded_session
        d = date(2026, 3, 16)
        ensure_date_ids(session, [d])

        # 첫 저장
        store_factor_returns(session, [
            FactorSpread(date=d, factor_name="quality",
                         long_return=1.0, short_return=0.0, spread=1.0),
        ])

        # 업데이트
        store_factor_returns(session, [
            FactorSpread(date=d, factor_name="quality",
                         long_return=2.0, short_return=-1.0, spread=3.0),
        ])

        results = FactorReturnRepository.get_by_factor(session, "quality")
        assert len(results) == 1
        assert results[0].spread == pytest.approx(3.0, abs=0.01)
