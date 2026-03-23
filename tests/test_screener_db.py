"""스크리너 DB 연동 함수 테스트."""

from datetime import date

import pytest

from src.analysis.screener import _passes_fundamental_filter, _score_fundamental, _score_smart_money, _score_technical
from src.db.helpers import ensure_date_ids
from src.db.repository import FinancialRepository, ValuationRepository
import pandas as pd


class TestPassesFundamentalFilter:
    def test_passes_without_data(self, seeded_session, sample_stock):
        """재무 데이터 없으면 통과."""
        assert _passes_fundamental_filter(seeded_session, sample_stock["id"]) is True

    def test_rejects_negative_per(self, seeded_session, sample_stock):
        """적자(PER <= 0) 기업 제외."""
        ensure_date_ids(seeded_session, [date(2026, 3, 15)])
        ValuationRepository.upsert(seeded_session, sample_stock["id"], [
            {"date_id": 20260315, "per": -5.0, "debt_ratio": 0.3},
        ])
        seeded_session.flush()
        assert _passes_fundamental_filter(seeded_session, sample_stock["id"]) is False

    def test_rejects_high_debt(self, seeded_session, sample_stock):
        """부채비율 80% 초과 제외."""
        ensure_date_ids(seeded_session, [date(2026, 3, 15)])
        ValuationRepository.upsert(seeded_session, sample_stock["id"], [
            {"date_id": 20260315, "per": 15.0, "debt_ratio": 0.85},
        ])
        seeded_session.flush()
        assert _passes_fundamental_filter(seeded_session, sample_stock["id"]) is False

    def test_passes_good_values(self, seeded_session, sample_stock):
        """정상 재무 데이터 통과."""
        ensure_date_ids(seeded_session, [date(2026, 3, 15)])
        ValuationRepository.upsert(seeded_session, sample_stock["id"], [
            {"date_id": 20260315, "per": 15.0, "debt_ratio": 0.4},
        ])
        seeded_session.flush()
        assert _passes_fundamental_filter(seeded_session, sample_stock["id"]) is True


class TestScoreFundamental:
    def test_default_score_no_data(self, seeded_session, sample_stock):
        """재무 데이터 없으면 기본 5.0."""
        score = _score_fundamental(seeded_session, sample_stock["id"])
        assert score == 5.0

    def test_with_financials(self, seeded_session, sample_stock):
        """재무 데이터가 있으면 점수 산출."""
        FinancialRepository.upsert(seeded_session, sample_stock["id"], [
            {"period": "2025Q4", "revenue": 100000, "net_income": 20000,
             "total_assets": 500000, "total_liabilities": 150000, "total_equity": 350000},
        ])
        ensure_date_ids(seeded_session, [date(2026, 3, 15)])
        ValuationRepository.upsert(seeded_session, sample_stock["id"], [
            {"date_id": 20260315, "per": 15.0, "pbr": 2.0, "roe": 0.2, "debt_ratio": 0.3},
        ])
        seeded_session.flush()
        score = _score_fundamental(seeded_session, sample_stock["id"])
        assert 1.0 <= score <= 10.0
        assert score != 5.0  # Should differ from default


class TestScoreSmartMoney:
    def test_default_score(self, seeded_session, sample_stock):
        """데이터 없으면 기본 5.0."""
        latest = pd.Series({"close": 100.0})
        score = _score_smart_money(seeded_session, sample_stock["id"], latest)
        assert score == 5.0


class TestScoreTechnical:
    def test_with_indicators(self, seeded_session, sample_stock):
        """지표 DataFrame으로 기술적 점수 산출."""
        import numpy as np
        dates = pd.date_range("2026-01-01", periods=50, freq="B")
        df = pd.DataFrame({
            "close": np.linspace(100, 120, 50),
            "high": np.linspace(101, 121, 50),
            "low": np.linspace(99, 119, 50),
            "open": np.linspace(100, 120, 50),
            "volume": [500000] * 50,
            "rsi_14": np.linspace(40, 55, 50),
            "macd": np.linspace(0.5, 2.0, 50),
            "macd_signal": np.linspace(0.3, 1.8, 50),
            "macd_hist": [0.2] * 50,
            "sma_5": np.linspace(100, 120, 50),
            "sma_20": np.linspace(98, 118, 50),
            "sma_60": np.linspace(95, 115, 50),
            "sma_120": np.linspace(90, 110, 50),
            "bb_upper": np.linspace(110, 130, 50),
            "bb_middle": np.linspace(100, 120, 50),
            "bb_lower": np.linspace(90, 110, 50),
            "stoch_k": [50.0] * 50,
            "stoch_d": [50.0] * 50,
            "volume_sma_20": [400000] * 50,
        }, index=dates)

        score = _score_technical(df, seeded_session, sample_stock["id"])
        assert 1.0 <= score <= 10.0
