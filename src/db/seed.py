"""디멘션 초기 데이터 시딩."""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.db.engine import get_session
from src.db.helpers import _make_dim_date
from src.db.models import DimIndicatorType, DimMarket, DimSector, DimSignalType

logger = logging.getLogger(__name__)


def seed_dimensions(engine: Engine) -> None:
    """모든 디멘션 테이블을 시딩한다 (idempotent)."""
    with get_session(engine) as session:
        _seed_markets(session)
        _seed_indicator_types(session)
        _seed_signal_types(session)
    # dim_date는 대량 INSERT이므로 별도 세션
    with get_session(engine) as session:
        _seed_dates(session)


def _seed_markets(session: Session) -> None:
    """시장 디멘션 시딩."""
    markets = [
        {"code": "US", "name": "미국", "currency": "USD",
         "timezone": "America/New_York", "trading_hours": "09:30-16:00"},
    ]
    for m in markets:
        existing = session.execute(
            select(DimMarket).where(DimMarket.code == m["code"])
        ).scalar_one_or_none()
        if existing is None:
            session.add(DimMarket(**m))
    session.flush()


def _seed_indicator_types(session: Session) -> None:
    """기술적 지표 유형 시딩."""
    indicators = [
        ("SMA_5", "5일 단순이동평균", "trend", {"period": 5}),
        ("SMA_20", "20일 단순이동평균", "trend", {"period": 20}),
        ("SMA_60", "60일 단순이동평균", "trend", {"period": 60}),
        ("SMA_120", "120일 단순이동평균", "trend", {"period": 120}),
        ("EMA_12", "12일 지수이동평균", "trend", {"period": 12}),
        ("EMA_26", "26일 지수이동평균", "trend", {"period": 26}),
        ("RSI_14", "14일 RSI", "momentum", {"period": 14}),
        ("MACD", "MACD", "trend_momentum", {"fast": 12, "slow": 26, "signal": 9}),
        ("MACD_SIGNAL", "MACD 시그널선", "trend_momentum", None),
        ("MACD_HIST", "MACD 히스토그램", "trend_momentum", None),
        ("BB_UPPER", "볼린저밴드 상단", "volatility", {"period": 20, "std": 2}),
        ("BB_MIDDLE", "볼린저밴드 중단", "volatility", {"period": 20}),
        ("BB_LOWER", "볼린저밴드 하단", "volatility", {"period": 20, "std": 2}),
        ("STOCH_K", "스토캐스틱 %K", "momentum", {"period": 14, "smooth": 3}),
        ("STOCH_D", "스토캐스틱 %D", "momentum", {"period": 14, "smooth": 3}),
        ("VOLUME_SMA_20", "20일 거래량 이동평균", "volume", {"period": 20}),
    ]
    for code, name, category, params in indicators:
        existing = session.execute(
            select(DimIndicatorType).where(DimIndicatorType.code == code)
        ).scalar_one_or_none()
        if existing is None:
            session.add(DimIndicatorType(
                code=code, name=name, category=category, params=params,
            ))
    session.flush()


def _seed_signal_types(session: Session) -> None:
    """시그널 유형 시딩."""
    signals = [
        ("golden_cross", "골든크로스", "BUY", 0.8,
         "SMA 20이 SMA 60을 상향 돌파"),
        ("death_cross", "데드크로스", "SELL", 0.8,
         "SMA 20이 SMA 60을 하향 돌파"),
        ("rsi_oversold", "RSI 과매도", "BUY", 0.6,
         "RSI가 30 이하로 진입"),
        ("rsi_overbought", "RSI 과매수", "SELL", 0.6,
         "RSI가 70 이상으로 진입"),
        ("macd_bullish", "MACD 상향돌파", "BUY", 0.7,
         "MACD가 시그널선을 상향 돌파"),
        ("macd_bearish", "MACD 하향돌파", "SELL", 0.7,
         "MACD가 시그널선을 하향 돌파"),
        ("bb_lower_break", "볼린저 하단 이탈", "BUY", 0.5,
         "종가가 볼린저 밴드 하단을 이탈"),
        ("bb_upper_break", "볼린저 상단 이탈", "SELL", 0.5,
         "종가가 볼린저 밴드 상단을 이탈"),
        ("stoch_bullish", "스토캐스틱 매수 전환", "BUY", 0.5,
         "스토캐스틱 K가 D를 상향 돌파"),
        ("stoch_bearish", "스토캐스틱 매도 전환", "SELL", 0.5,
         "스토캐스틱 K가 D를 하향 돌파"),
    ]
    for code, name, direction, weight, desc in signals:
        existing = session.execute(
            select(DimSignalType).where(DimSignalType.code == code)
        ).scalar_one_or_none()
        if existing is None:
            session.add(DimSignalType(
                code=code, name=name, direction=direction,
                default_weight=weight, description=desc,
            ))
    session.flush()


def _seed_dates(session: Session, start_year: int = 2015, end_year: int = 2030) -> None:
    """날짜 디멘션 시딩 (start_year~end_year)."""
    from src.db.helpers import date_to_id

    # 이미 시딩된 범위 확인
    from src.db.models import DimDate

    count = session.execute(
        select(DimDate.date_id).limit(1)
    ).scalar_one_or_none()

    if count is not None:
        logger.info("dim_date 이미 시딩됨, 스킵")
        return

    start = date(start_year, 1, 1)
    end = date(end_year, 12, 31)
    current = start

    batch = []
    while current <= end:
        batch.append(_make_dim_date(current))
        current += timedelta(days=1)

        if len(batch) >= 1000:
            session.add_all(batch)
            session.flush()
            batch = []

    if batch:
        session.add_all(batch)
        session.flush()

    logger.info("dim_date 시딩 완료: %s ~ %s", start, end)
