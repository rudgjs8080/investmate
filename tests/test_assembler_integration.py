"""리포트 조립기 통합 테스트 -- in-memory SQLite."""

from datetime import date

import pytest

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import FactDailyRecommendation
from src.db.repository import (
    IndicatorValueRepository,
    MacroRepository,
    RecommendationRepository,
    SignalRepository,
)
from src.reports.assembler import (
    assemble_enriched_report,
    _build_macro,
    _build_technical,
    _get_signal_type_reverse_map,
)


class TestAssembleEnrichedReport:
    def test_empty_db(self, seeded_session):
        """추천 없는 경우에도 에러 없이 리포트 생성."""
        ensure_date_ids(seeded_session, [date(2026, 3, 19)])
        report = assemble_enriched_report(seeded_session, date(2026, 3, 19), 20260319)
        assert report.total_stocks_analyzed >= 0  # seeded_session may have 0 SP500 stocks
        assert len(report.recommendations) == 0

    def test_with_recommendation(self, seeded_session, sample_stock):
        """추천이 있는 경우 리포트에 포함."""
        ensure_date_ids(seeded_session, [date(2026, 3, 19)])

        # 매크로 데이터
        MacroRepository.upsert(seeded_session, 20260319, {
            "vix": 20.0, "sp500_close": 5500.0, "sp500_sma20": 5400.0,
            "us_10y_yield": 4.0, "market_score": 6,
        })

        # 추천 생성
        seeded_session.add(FactDailyRecommendation(
            run_date_id=20260319, stock_id=sample_stock["id"], rank=1,
            total_score=7.0, technical_score=6.0, fundamental_score=7.0,
            external_score=5.0, momentum_score=8.0, smart_money_score=5.0,
            recommendation_reason="test", price_at_recommendation=180.0,
        ))
        seeded_session.flush()

        report = assemble_enriched_report(seeded_session, date(2026, 3, 19), 20260319)
        assert len(report.recommendations) == 1
        assert report.recommendations[0].ticker == sample_stock["ticker"]
        assert report.macro.vix == 20.0

    def test_with_indicators(self, seeded_session, sample_stock):
        """지표가 있는 경우 기술적 분석 데이터 포함."""
        ensure_date_ids(seeded_session, [date(2026, 3, 18), date(2026, 3, 19)])
        type_map = IndicatorValueRepository.get_indicator_type_map(seeded_session)

        # 3/18에 지표 저장
        records = [
            {"date_id": 20260318, "indicator_type_id": type_map["RSI_14"], "value": 55.0},
            {"date_id": 20260318, "indicator_type_id": type_map["MACD"], "value": 2.5},
            {"date_id": 20260318, "indicator_type_id": type_map["SMA_20"], "value": 175.0},
        ]
        IndicatorValueRepository.upsert_values(seeded_session, sample_stock["id"], records)

        # 추천 (3/19)
        seeded_session.add(FactDailyRecommendation(
            run_date_id=20260319, stock_id=sample_stock["id"], rank=1,
            total_score=7.0, technical_score=6.0, fundamental_score=7.0,
            external_score=5.0, momentum_score=8.0, smart_money_score=5.0,
            recommendation_reason="test", price_at_recommendation=180.0,
        ))
        seeded_session.flush()

        report = assemble_enriched_report(seeded_session, date(2026, 3, 19), 20260319)
        rec = report.recommendations[0]
        # 3/18 지표가 범위 조회로 반환되어야 함
        assert rec.technical.rsi == 55.0
        assert rec.technical.macd == 2.5


class TestBuildTechnicalIntegration:
    def test_with_signals(self, seeded_session, sample_stock):
        """시그널 + 지표가 있는 경우 기술적 분석에 포함."""
        ensure_date_ids(seeded_session, [date(2026, 3, 18)])
        type_map = IndicatorValueRepository.get_indicator_type_map(seeded_session)
        sig_map = SignalRepository.get_signal_type_map(seeded_session)
        signal_type_map = _get_signal_type_reverse_map(seeded_session)

        # 지표 저장 (없으면 _build_technical이 early return)
        IndicatorValueRepository.upsert_values(seeded_session, sample_stock["id"], [
            {"date_id": 20260318, "indicator_type_id": type_map["RSI_14"], "value": 50.0},
        ])

        # 시그널 저장
        SignalRepository.create_signals_batch(
            seeded_session, sample_stock["id"], 20260318,
            [{"signal_type_id": sig_map["golden_cross"], "strength": 8, "description": "test"}],
        )
        seeded_session.flush()

        tech = _build_technical(seeded_session, sample_stock["id"], 20260319, signal_type_map)
        assert tech.rsi == 50.0
        assert len(tech.signals) >= 1
        assert tech.signals[0].signal_type == "golden_cross"
