"""ML 피처 엔지니어링 테스트."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from src.db.helpers import date_to_id
from src.db.models import (
    DimStock,
    FactDailyPrice,
    FactDailyRecommendation,
    FactValuation,
)
from src.ml.features import FEATURE_NAMES, build_features_for_stock, build_training_data


def test_feature_names_count():
    """FEATURE_NAMES는 28개 항목이어야 한다."""
    assert len(FEATURE_NAMES) == 28


def test_feature_names_unique():
    """FEATURE_NAMES에 중복이 없어야 한다."""
    assert len(FEATURE_NAMES) == len(set(FEATURE_NAMES))


def test_build_features_empty_db(seeded_session, sample_stock):
    """빈 DB에서 모든 피처가 None이어야 한다."""
    did = date_to_id(date(2025, 3, 20))
    features = build_features_for_stock(seeded_session, sample_stock["id"], did)

    assert isinstance(features, dict)
    assert len(features) == len(FEATURE_NAMES)
    for name in FEATURE_NAMES:
        assert name in features
        assert features[name] is None


def test_build_features_with_prices(seeded_session, sample_stock):
    """가격 데이터가 있으면 모멘텀 피처가 계산된다."""
    stock_id = sample_stock["id"]
    # 25일치 가격 데이터 생성
    base = date(2025, 3, 1)
    for i in range(25):
        d = date(2025, 3, 1 + i) if (1 + i) <= 28 else date(2025, 4, i - 27)
        did = date_to_id(d)
        price = 150.0 + i * 0.5  # 상승 추세
        seeded_session.add(FactDailyPrice(
            stock_id=stock_id,
            date_id=did,
            open=Decimal(str(price - 0.5)),
            high=Decimal(str(price + 1)),
            low=Decimal(str(price - 1)),
            close=Decimal(str(price)),
            adj_close=Decimal(str(price)),
            volume=1_000_000,
        ))
    seeded_session.commit()

    target_did = date_to_id(date(2025, 3, 25))
    features = build_features_for_stock(seeded_session, stock_id, target_did)

    # 20일 이상 데이터 → 모멘텀 피처 있어야 함
    assert features["momentum_5d"] is not None
    assert features["momentum_20d"] is not None
    assert features["sma20_dist"] is not None
    assert features["volume_ratio"] is not None
    assert features["bb_position"] is not None


def test_build_training_data_insufficient(seeded_session):
    """60건 미만이면 빈 DataFrame을 반환한다."""
    df = build_training_data(seeded_session, min_days=60)
    assert isinstance(df, pd.DataFrame)
    assert df.empty


def test_build_training_data_structure(seeded_session, sample_stock):
    """충분한 데이터가 있으면 피처 + return_20d 컬럼이 포함된다."""
    stock_id = sample_stock["id"]

    # 65건의 추천 데이터 생성 (return_20d 포함)
    # dim_date에 존재하는 유효한 날짜 사용
    from datetime import timedelta
    base_date = date(2025, 1, 2)  # 목요일
    for i in range(65):
        d = base_date + timedelta(days=i)
        did = date_to_id(d)
        seeded_session.add(FactDailyRecommendation(
            run_date_id=did,
            stock_id=stock_id,
            rank=1,
            total_score=Decimal("7.5"),
            technical_score=Decimal("7.0"),
            fundamental_score=Decimal("8.0"),
            smart_money_score=Decimal("6.0"),
            external_score=Decimal("7.0"),
            momentum_score=Decimal("7.5"),
            recommendation_reason="test",
            price_at_recommendation=Decimal("150.0"),
            return_20d=Decimal(str(0.05 * (1 if i % 2 == 0 else -1))),
        ))
    seeded_session.commit()

    df = build_training_data(seeded_session, min_days=60)
    assert not df.empty
    assert "return_20d" in df.columns
    assert "stock_id" in df.columns
    assert "date_id" in df.columns
    # 피처 이름 확인
    for name in FEATURE_NAMES:
        assert name in df.columns
