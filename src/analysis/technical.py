"""기술적 분석 모듈 — 기술적 지표 계산."""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)
from sqlalchemy.orm import Session
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import EMAIndicator, MACD, SMAIndicator
from ta.volatility import BollingerBands

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.repository import DailyPriceRepository, IndicatorValueRepository


def calculate_indicators(prices_df: pd.DataFrame) -> pd.DataFrame:
    """일봉 DataFrame에서 기술적 지표를 계산한다.

    Args:
        prices_df: 'close', 'high', 'low', 'volume' 컬럼을 포함하는 DataFrame.

    Returns:
        지표 컬럼이 추가된 새 DataFrame (입력은 변경하지 않음).
    """
    df = prices_df.copy()

    close = df["close"]
    high = df["high"]
    low = df["low"]

    # SMA
    df["sma_5"] = SMAIndicator(close, window=5).sma_indicator()
    df["sma_20"] = SMAIndicator(close, window=20).sma_indicator()
    df["sma_60"] = SMAIndicator(close, window=60).sma_indicator()
    df["sma_120"] = SMAIndicator(close, window=120).sma_indicator()

    # EMA
    df["ema_12"] = EMAIndicator(close, window=12).ema_indicator()
    df["ema_26"] = EMAIndicator(close, window=26).ema_indicator()

    # RSI
    df["rsi_14"] = RSIIndicator(close, window=14).rsi()

    # MACD
    macd = MACD(close, window_slow=26, window_fast=12, window_sign=9)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    # Bollinger Bands
    bb = BollingerBands(close, window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()

    # Stochastic
    stoch = StochasticOscillator(high, low, close, window=14, smooth_window=3)
    df["stoch_k"] = stoch.stoch()
    df["stoch_d"] = stoch.stoch_signal()

    # Volume SMA
    if "volume" in df.columns:
        df["volume_sma_20"] = SMAIndicator(
            df["volume"].astype(float), window=20
        ).sma_indicator()

    return df


# DataFrame 컬럼명 → dim_indicator_types.code 매핑
INDICATOR_COLUMN_TO_CODE = {
    "sma_5": "SMA_5", "sma_20": "SMA_20", "sma_60": "SMA_60",
    "sma_120": "SMA_120", "ema_12": "EMA_12", "ema_26": "EMA_26",
    "rsi_14": "RSI_14", "macd": "MACD", "macd_signal": "MACD_SIGNAL",
    "macd_hist": "MACD_HIST", "bb_upper": "BB_UPPER",
    "bb_middle": "BB_MIDDLE", "bb_lower": "BB_LOWER",
    "stoch_k": "STOCH_K", "stoch_d": "STOCH_D",
    "volume_sma_20": "VOLUME_SMA_20",
}

INDICATOR_COLUMNS = list(INDICATOR_COLUMN_TO_CODE.keys())


def load_date_map(session: Session) -> dict[int, object]:
    """전체 dim_date → date 매핑을 1회 로드한다 (캐시용)."""
    from src.db.models import DimDate
    from sqlalchemy import select
    stmt = select(DimDate.date_id, DimDate.date)
    return dict(session.execute(stmt).all())


def prices_to_dataframe(
    session: Session, stock_id: int,
    date_map: dict[int, object] | None = None,
) -> pd.DataFrame:
    """DB에서 일봉 데이터를 DataFrame으로 로드한다.

    Args:
        date_map: 사전 로드된 date_id→date 매핑. None이면 개별 쿼리.
    """
    prices = DailyPriceRepository.get_prices(session, stock_id)
    if not prices:
        return pd.DataFrame()

    # date_id → date 역매핑 (캐시 있으면 재사용)
    if date_map is None:
        from src.db.models import DimDate
        from sqlalchemy import select
        date_ids = [p.date_id for p in prices]
        stmt = select(DimDate.date_id, DimDate.date).where(DimDate.date_id.in_(date_ids))
        date_map = dict(session.execute(stmt).all())

    data = [
        {
            "date": date_map.get(p.date_id),
            "open": float(p.open),
            "high": float(p.high),
            "low": float(p.low),
            "close": float(p.close),
            "volume": int(p.volume),
            "adj_close": float(p.adj_close),
        }
        for p in prices
        if date_map.get(p.date_id) is not None
    ]
    df = pd.DataFrame(data)
    if df.empty:
        return df
    df.set_index("date", inplace=True)
    df.sort_index(inplace=True)
    return df


def store_indicators(
    session: Session, stock_id: int, indicators_df: pd.DataFrame,
    *, last_stored_date_id: int | None = None, auto_flush: bool = True,
) -> int:
    """계산된 지표를 EAV 패턴으로 DB에 저장한다.

    Args:
        last_stored_date_id: 이 date_id 이후 데이터만 저장 (증분 모드).
        auto_flush: False이면 flush를 호출자에게 위임.
    """
    # indicator_type_id 캐시
    type_map = IndicatorValueRepository.get_indicator_type_map(session)

    # 날짜 → date_id
    dates = list(indicators_df.index)
    date_id_map = ensure_date_ids(session, dates)

    records = []
    unmapped_codes: set[str] = set()
    for idx, row in indicators_df.iterrows():
        did = date_id_map.get(idx)
        if did is None:
            continue

        # 증분 모드: 이미 저장된 날짜 스킵
        if last_stored_date_id is not None and did <= last_stored_date_id:
            continue

        for col, code in INDICATOR_COLUMN_TO_CODE.items():
            val = row.get(col)
            if val is not None and not pd.isna(val):
                tid = type_map.get(code)
                if tid is not None:
                    records.append({
                        "date_id": did,
                        "indicator_type_id": tid,
                        "value": float(val),
                    })
                else:
                    unmapped_codes.add(code)

    if unmapped_codes:
        logger.warning("지표 유형 미등록 (dim_indicator_types에 없음): %s", ", ".join(sorted(unmapped_codes)))

    return IndicatorValueRepository.upsert_values(
        session, stock_id, records, auto_flush=auto_flush,
    )


def analyze_and_store(session: Session, stock_id: int) -> int:
    """DB에서 가격 로드 → 지표 계산 → DB 저장. 저장된 레코드 수 반환."""
    df = prices_to_dataframe(session, stock_id)
    if df.empty:
        return 0

    indicators_df = calculate_indicators(df)
    return store_indicators(session, stock_id, indicators_df)
