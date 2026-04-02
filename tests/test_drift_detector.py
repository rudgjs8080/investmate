"""ML 드리프트 감지 시스템 테스트."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import (
    DimStock,
    FactDailyRecommendation,
    FactMLDriftCheck,
    FactMLModelLog,
)
from src.db.repository import StockRepository
from src.ml.drift_detector import DriftReport, detect_drift


# ─── DriftReport frozen dataclass ───────────────────────


class TestDriftReport:
    """DriftReport frozen dataclass 테스트."""

    def test_frozen(self):
        """DriftReport는 불변이다."""
        report = DriftReport(
            is_drifted=False,
            accuracy_current=60.0,
            accuracy_baseline=65.0,
            accuracy_delta=-5.0,
            sample_count_current=10,
            sample_count_baseline=20,
            recommended_action="none",
        )
        with pytest.raises(AttributeError):
            report.is_drifted = True  # type: ignore[misc]

    def test_fields(self):
        """모든 필드가 올바르게 설정된다."""
        report = DriftReport(
            is_drifted=True,
            accuracy_current=40.0,
            accuracy_baseline=65.0,
            accuracy_delta=-25.0,
            sample_count_current=15,
            sample_count_baseline=30,
            recommended_action="retrain",
        )
        assert report.is_drifted is True
        assert report.accuracy_current == 40.0
        assert report.accuracy_baseline == 65.0
        assert report.accuracy_delta == -25.0
        assert report.sample_count_current == 15
        assert report.sample_count_baseline == 30
        assert report.recommended_action == "retrain"


# ─── detect_drift 함수 ─────────────────────────────────


def _seed_stock(session: Session, market_id: int) -> int:
    """테스트용 종목을 생성하고 stock_id를 반환한다."""
    stock = StockRepository.add(session, "TEST", "Test Inc", market_id, is_sp500=True)
    session.commit()
    return stock.stock_id


def _add_recommendation(
    session: Session,
    stock_id: int,
    run_date: date,
    return_20d: float | None,
) -> None:
    """테스트용 추천 레코드를 추가한다."""
    ensure_date_ids(session, [run_date])
    rec = FactDailyRecommendation(
        run_date_id=date_to_id(run_date),
        stock_id=stock_id,
        rank=1,
        total_score=7.0,
        technical_score=7.0,
        fundamental_score=7.0,
        external_score=7.0,
        momentum_score=7.0,
        smart_money_score=5.0,
        recommendation_reason="test",
        price_at_recommendation=100.0,
        return_20d=return_20d,
    )
    session.add(rec)
    session.flush()


class TestDetectDriftNoData:
    """데이터 없을 때 detect_drift 동작 테스트."""

    def test_no_data_returns_none_action(self, seeded_session):
        """데이터가 없으면 action='none'을 반환한다."""
        report = detect_drift(seeded_session)
        assert report.recommended_action == "none"
        assert report.is_drifted is False
        assert report.sample_count_current == 0
        assert report.sample_count_baseline == 0

    def test_no_data_accuracy_zero(self, seeded_session):
        """데이터가 없으면 정확도는 0.0%이다."""
        report = detect_drift(seeded_session)
        assert report.accuracy_current == 0.0
        assert report.accuracy_baseline == 0.0
        assert report.accuracy_delta == 0.0


class TestDetectDriftRetrain:
    """심각한 정확도 하락 시 retrain 테스트."""

    def test_severe_drop_triggers_retrain(self, seeded_session, us_market):
        """최근 윈도우 정확도가 기준보다 threshold 이상 하락하면 retrain을 권고한다."""
        stock_id = _seed_stock(seeded_session, us_market)
        ref = date(2026, 3, 15)

        # Baseline period (older): 80% win rate (8/10 positive)
        for i in range(10):
            d = ref - timedelta(days=60 + i)
            ret = 5.0 if i < 8 else -3.0
            _add_recommendation(seeded_session, stock_id, d, ret)

        # Recent window: 20% win rate (1/5 positive) — severe drop
        for i in range(5):
            d = ref - timedelta(days=i + 1)
            ret = 5.0 if i == 0 else -3.0
            _add_recommendation(seeded_session, stock_id, d, ret)

        seeded_session.commit()

        report = detect_drift(
            seeded_session, window_days=20, baseline_days=60,
            threshold=0.10, reference_date=ref,
        )
        assert report.is_drifted is True
        assert report.recommended_action == "retrain"
        assert report.accuracy_delta < -10.0

    def test_retrain_requires_min_samples(self, seeded_session, us_market):
        """최소 샘플 수 미달 시 드리프트를 감지하지 않는다."""
        stock_id = _seed_stock(seeded_session, us_market)
        ref = date(2026, 3, 15)

        # Baseline: 100% win rate
        for i in range(10):
            d = ref - timedelta(days=60 + i)
            _add_recommendation(seeded_session, stock_id, d, 5.0)

        # Recent: only 2 samples (below min_samples=5), 0% win rate
        for i in range(2):
            d = ref - timedelta(days=i + 1)
            _add_recommendation(seeded_session, stock_id, d, -3.0)

        seeded_session.commit()

        report = detect_drift(
            seeded_session, window_days=20, baseline_days=60,
            threshold=0.10, reference_date=ref,
        )
        assert report.is_drifted is False
        assert report.recommended_action == "none"


class TestDetectDriftMonitor:
    """중간 수준 하락 시 monitor 테스트."""

    def test_moderate_drop_triggers_monitor(self, seeded_session, us_market):
        """정확도 하락이 threshold/2 ~ threshold 사이면 monitor를 권고한다."""
        stock_id = _seed_stock(seeded_session, us_market)
        ref = date(2026, 3, 15)

        # Baseline: 70% win rate (7/10)
        for i in range(10):
            d = ref - timedelta(days=60 + i)
            ret = 5.0 if i < 7 else -3.0
            _add_recommendation(seeded_session, stock_id, d, ret)

        # Recent: ~57% win rate (4/7) — moderate drop of ~13%p
        # With threshold=0.20, threshold/2=0.10
        for i in range(7):
            d = ref - timedelta(days=i + 1)
            ret = 5.0 if i < 4 else -3.0
            _add_recommendation(seeded_session, stock_id, d, ret)

        seeded_session.commit()

        report = detect_drift(
            seeded_session, window_days=20, baseline_days=60,
            threshold=0.20, reference_date=ref,
        )
        assert report.recommended_action == "monitor"
        assert report.is_drifted is False


class TestDetectDriftNone:
    """정확도가 안정적일 때 none 테스트."""

    def test_stable_accuracy_returns_none(self, seeded_session, us_market):
        """정확도 변화가 임계값 이내이면 none을 반환한다."""
        stock_id = _seed_stock(seeded_session, us_market)
        ref = date(2026, 3, 15)

        # Both periods: ~60% win rate
        for i in range(10):
            d = ref - timedelta(days=60 + i)
            ret = 5.0 if i < 6 else -3.0
            _add_recommendation(seeded_session, stock_id, d, ret)

        for i in range(6):
            d = ref - timedelta(days=i + 1)
            ret = 5.0 if i < 4 else -3.0
            _add_recommendation(seeded_session, stock_id, d, ret)

        seeded_session.commit()

        report = detect_drift(
            seeded_session, window_days=20, baseline_days=60,
            threshold=0.10, reference_date=ref,
        )
        assert report.recommended_action == "none"
        assert report.is_drifted is False

    def test_improved_accuracy_returns_none(self, seeded_session, us_market):
        """정확도가 향상되면 none을 반환한다 (양수 delta)."""
        stock_id = _seed_stock(seeded_session, us_market)
        ref = date(2026, 3, 15)

        # Baseline: 40% win rate
        for i in range(10):
            d = ref - timedelta(days=60 + i)
            ret = 5.0 if i < 4 else -3.0
            _add_recommendation(seeded_session, stock_id, d, ret)

        # Recent: 80% win rate — improvement
        for i in range(5):
            d = ref - timedelta(days=i + 1)
            ret = 5.0 if i < 4 else -3.0
            _add_recommendation(seeded_session, stock_id, d, ret)

        seeded_session.commit()

        report = detect_drift(
            seeded_session, window_days=20, baseline_days=60,
            threshold=0.10, reference_date=ref,
        )
        assert report.recommended_action == "none"
        assert report.is_drifted is False
        assert report.accuracy_delta > 0


class TestDetectDriftInsufficientData:
    """데이터 부족 상황 테스트."""

    def test_only_recent_no_baseline(self, seeded_session, us_market):
        """기준 기간 데이터 없으면 baseline=0%이지만 recent가 양수이면 none."""
        stock_id = _seed_stock(seeded_session, us_market)
        ref = date(2026, 3, 15)

        # Only recent data, no baseline
        for i in range(5):
            d = ref - timedelta(days=i + 1)
            _add_recommendation(seeded_session, stock_id, d, 5.0)

        seeded_session.commit()

        report = detect_drift(
            seeded_session, window_days=20, baseline_days=60,
            threshold=0.10, reference_date=ref,
        )
        # Recent 100%, baseline 0% → delta = +100% → none
        assert report.recommended_action == "none"
        assert report.sample_count_baseline == 0

    def test_null_returns_excluded(self, seeded_session, us_market):
        """return_20d가 NULL인 레코드는 제외된다."""
        stock_id = _seed_stock(seeded_session, us_market)
        ref = date(2026, 3, 15)

        # Add records with None return_20d
        for i in range(5):
            d = ref - timedelta(days=i + 1)
            _add_recommendation(seeded_session, stock_id, d, None)

        seeded_session.commit()

        report = detect_drift(
            seeded_session, window_days=20, baseline_days=60,
            threshold=0.10, reference_date=ref,
        )
        assert report.sample_count_current == 0
        assert report.recommended_action == "none"


# ─── DB 모델 테스트 ────────────────────────────────────


class TestFactMLModelLogDB:
    """FactMLModelLog DB create/read 테스트."""

    def test_create_and_read(self, seeded_session):
        """모델 학습 이력을 생성하고 조회할 수 있다."""
        ensure_date_ids(seeded_session, [date(2026, 3, 15)])
        log = FactMLModelLog(
            trained_date_id=date_to_id(date(2026, 3, 15)),
            model_type="binary",
            train_auc=0.85,
            train_rmse=None,
            feature_count=28,
            sample_count=500,
            file_path="data/models/lgbm_20260315.pkl",
            is_active=True,
        )
        seeded_session.add(log)
        seeded_session.commit()

        loaded = seeded_session.query(FactMLModelLog).first()
        assert loaded is not None
        assert loaded.model_type == "binary"
        assert float(loaded.train_auc) == 0.85
        assert loaded.feature_count == 28
        assert loaded.sample_count == 500
        assert loaded.is_active is True

    def test_defaults(self, seeded_session):
        """기본값이 올바르게 설정된다."""
        ensure_date_ids(seeded_session, [date(2026, 3, 15)])
        log = FactMLModelLog(
            trained_date_id=date_to_id(date(2026, 3, 15)),
            model_type="regression",
        )
        seeded_session.add(log)
        seeded_session.commit()

        loaded = seeded_session.query(FactMLModelLog).first()
        assert loaded is not None
        assert loaded.is_active is True
        assert loaded.train_auc is None
        assert loaded.file_path is None


class TestFactMLDriftCheckDB:
    """FactMLDriftCheck DB create/read 테스트."""

    def test_create_and_read(self, seeded_session):
        """드리프트 검사 이력을 생성하고 조회할 수 있다."""
        ensure_date_ids(seeded_session, [date(2026, 3, 15)])
        check = FactMLDriftCheck(
            date_id=date_to_id(date(2026, 3, 15)),
            accuracy_current=45.0,
            accuracy_baseline=65.0,
            is_drifted=True,
            action_taken="retrain",
        )
        seeded_session.add(check)
        seeded_session.commit()

        loaded = seeded_session.query(FactMLDriftCheck).first()
        assert loaded is not None
        assert float(loaded.accuracy_current) == 45.0
        assert float(loaded.accuracy_baseline) == 65.0
        assert loaded.is_drifted is True
        assert loaded.action_taken == "retrain"

    def test_defaults(self, seeded_session):
        """기본값이 올바르게 설정된다."""
        ensure_date_ids(seeded_session, [date(2026, 3, 15)])
        check = FactMLDriftCheck(
            date_id=date_to_id(date(2026, 3, 15)),
        )
        seeded_session.add(check)
        seeded_session.commit()

        loaded = seeded_session.query(FactMLDriftCheck).first()
        assert loaded is not None
        assert loaded.is_drifted is False
        assert loaded.action_taken is None
        assert loaded.accuracy_current is None


# ─── Config 설정 테스트 ─────────────────────────────────


class TestConfigDriftSettings:
    """ML 드리프트 관련 설정 테스트."""

    def test_default_values(self):
        """기본값이 올바르게 설정된다."""
        from src.config import Settings

        settings = Settings()
        assert settings.ml_drift_threshold == 0.10
        assert settings.ml_auto_retrain is True

    def test_env_override(self, monkeypatch):
        """환경변수로 설정을 오버라이드할 수 있다."""
        from src.config import Settings

        monkeypatch.setenv("INVESTMATE_ML_DRIFT_THRESHOLD", "0.15")
        monkeypatch.setenv("INVESTMATE_ML_AUTO_RETRAIN", "false")
        settings = Settings()
        assert settings.ml_drift_threshold == 0.15
        assert settings.ml_auto_retrain is False
