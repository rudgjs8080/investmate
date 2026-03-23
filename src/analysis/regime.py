"""시장 레짐 감지 모듈.

S&P 500 가격과 VIX 수준을 기반으로 현재 시장을 4가지 레짐으로 분류한다:
- Bull (강세): S&P 500 > SMA50 > SMA200, VIX < 20
- Bear (약세): S&P 500 < SMA200, VIX > 25
- Range (횡보): 혼합 신호, VIX 15-25
- Crisis (위기): VIX > 30 AND S&P 500 < SMA50
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import FactMacroIndicator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketRegime:
    """시장 레짐 분류 결과."""

    regime: str  # "bull", "bear", "range", "crisis"
    confidence: float  # 0.0 ~ 1.0
    description: str  # 한글 설명


# 레짐별 스코어링 가중치 (합계 = 1.0)
REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "bull": {
        "technical": 0.20,
        "fundamental": 0.20,
        "smart_money": 0.15,
        "external": 0.15,
        "momentum": 0.30,
    },
    "bear": {
        "technical": 0.15,
        "fundamental": 0.35,
        "smart_money": 0.20,
        "external": 0.20,
        "momentum": 0.10,
    },
    "range": {
        "technical": 0.30,
        "fundamental": 0.25,
        "smart_money": 0.15,
        "external": 0.15,
        "momentum": 0.15,
    },
    "crisis": {
        "technical": 0.10,
        "fundamental": 0.30,
        "smart_money": 0.25,
        "external": 0.25,
        "momentum": 0.10,
    },
}

# 레짐 한글 설명
_REGIME_DESCRIPTIONS: dict[str, str] = {
    "bull": "강세장: S&P 500이 주요 이동평균선 위, 변동성 낮음",
    "bear": "약세장: S&P 500이 장기 이동평균선 아래, 변동성 높음",
    "range": "횡보장: 혼합 신호, 뚜렷한 방향성 없음",
    "crisis": "위기: 극심한 변동성과 급격한 하락세",
}

# VIX 임계값
_VIX_CRISIS = 30.0
_VIX_HIGH = 25.0
_VIX_LOW = 20.0

# 최소 데이터 요구량
_MIN_DATA_FOR_SMA50 = 50
_MIN_DATA_FOR_SMA200 = 200


def detect_regime(session: Session) -> MarketRegime:
    """현재 시장 레짐을 감지한다.

    Uses:
    - S&P 500 vs SMA50, SMA200
    - VIX level

    Falls back to "range" if data insufficient.
    """
    # 최근 200일치 매크로 데이터 조회
    rows = (
        session.execute(
            select(FactMacroIndicator)
            .order_by(FactMacroIndicator.date_id.desc())
            .limit(200)
        )
        .scalars()
        .all()
    )

    if not rows:
        logger.warning("레짐 감지: 매크로 데이터 없음 → 횡보장(range) fallback")
        return MarketRegime(
            regime="range",
            confidence=0.3,
            description=_REGIME_DESCRIPTIONS["range"] + " (데이터 부족)",
        )

    # 최신 VIX와 S&P 500
    latest = rows[0]
    vix = float(latest.vix) if latest.vix is not None else None
    sp500 = float(latest.sp500_close) if latest.sp500_close is not None else None

    if sp500 is None:
        logger.warning("레짐 감지: S&P 500 데이터 없음 → 횡보장(range) fallback")
        return MarketRegime(
            regime="range",
            confidence=0.3,
            description=_REGIME_DESCRIPTIONS["range"] + " (데이터 부족)",
        )

    # S&P 500 종가 시계열 추출 (오래된 순서로)
    sp500_prices = [
        float(r.sp500_close)
        for r in reversed(rows)
        if r.sp500_close is not None
    ]

    # SMA 계산
    sma50 = _calculate_sma(sp500_prices, 50)
    sma200 = _calculate_sma(sp500_prices, 200)

    return _classify_regime(sp500, vix, sma50, sma200)


def _calculate_sma(prices: list[float], period: int) -> float | None:
    """단순 이동평균을 계산한다. 데이터 부족 시 None."""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def _classify_regime(
    sp500: float,
    vix: float | None,
    sma50: float | None,
    sma200: float | None,
) -> MarketRegime:
    """규칙 기반으로 시장 레짐을 분류한다."""
    # VIX가 없으면 중립 가정
    effective_vix = vix if vix is not None else 20.0

    # Crisis: VIX > 30 AND sp500 < sma50
    if effective_vix > _VIX_CRISIS and sma50 is not None and sp500 < sma50:
        confidence = _crisis_confidence(sp500, effective_vix, sma50)
        logger.info(
            "레짐 감지: 위기 (VIX=%.1f, S&P500=%.1f, SMA50=%.1f, 신뢰도=%.2f)",
            effective_vix, sp500, sma50, confidence,
        )
        return MarketRegime(
            regime="crisis",
            confidence=confidence,
            description=_REGIME_DESCRIPTIONS["crisis"],
        )

    # Bull: sp500 > sma50 > sma200 AND VIX < 20
    if (
        sma50 is not None
        and sma200 is not None
        and sp500 > sma50 > sma200
        and effective_vix < _VIX_LOW
    ):
        confidence = _bull_confidence(sp500, effective_vix, sma50, sma200)
        logger.info(
            "레짐 감지: 강세 (VIX=%.1f, S&P500=%.1f, SMA50=%.1f, SMA200=%.1f, 신뢰도=%.2f)",
            effective_vix, sp500, sma50, sma200, confidence,
        )
        return MarketRegime(
            regime="bull",
            confidence=confidence,
            description=_REGIME_DESCRIPTIONS["bull"],
        )

    # Bear: sp500 < sma200 AND VIX > 25
    if sma200 is not None and sp500 < sma200 and effective_vix > _VIX_HIGH:
        confidence = _bear_confidence(sp500, effective_vix, sma200)
        logger.info(
            "레짐 감지: 약세 (VIX=%.1f, S&P500=%.1f, SMA200=%.1f, 신뢰도=%.2f)",
            effective_vix, sp500, sma200, confidence,
        )
        return MarketRegime(
            regime="bear",
            confidence=confidence,
            description=_REGIME_DESCRIPTIONS["bear"],
        )

    # Range: 나머지 (혼합 신호)
    confidence = _range_confidence(sp500, effective_vix, sma50, sma200)
    logger.info(
        "레짐 감지: 횡보 (VIX=%.1f, S&P500=%.1f, 신뢰도=%.2f)",
        effective_vix, sp500, confidence,
    )
    return MarketRegime(
        regime="range",
        confidence=confidence,
        description=_REGIME_DESCRIPTIONS["range"],
    )


def _crisis_confidence(sp500: float, vix: float, sma50: float) -> float:
    """위기 레짐 신뢰도 계산."""
    confidence = 0.5
    # VIX가 높을수록 신뢰도 증가 (30~50 범위에서 0~0.25 추가)
    confidence += min(0.25, (vix - _VIX_CRISIS) / 80.0)
    # S&P 500이 SMA50 아래로 멀수록 신뢰도 증가
    if sma50 > 0:
        gap_pct = (sma50 - sp500) / sma50
        confidence += min(0.25, gap_pct * 2.5)
    return min(1.0, confidence)


def _bull_confidence(
    sp500: float, vix: float, sma50: float, sma200: float,
) -> float:
    """강세 레짐 신뢰도 계산."""
    confidence = 0.5
    # VIX가 낮을수록 신뢰도 증가 (20~10 범위에서 0~0.2 추가)
    confidence += min(0.2, (_VIX_LOW - vix) / 50.0)
    # S&P 500이 SMA50 위로 멀수록 신뢰도 증가
    if sma50 > 0:
        gap_pct = (sp500 - sma50) / sma50
        confidence += min(0.15, gap_pct * 1.5)
    # SMA50 > SMA200 차이가 클수록 신뢰도 증가
    if sma200 > 0:
        sma_gap = (sma50 - sma200) / sma200
        confidence += min(0.15, sma_gap * 1.5)
    return min(1.0, confidence)


def _bear_confidence(sp500: float, vix: float, sma200: float) -> float:
    """약세 레짐 신뢰도 계산."""
    confidence = 0.5
    # VIX가 높을수록 신뢰도 증가
    confidence += min(0.2, (vix - _VIX_HIGH) / 50.0)
    # S&P 500이 SMA200 아래로 멀수록 신뢰도 증가
    if sma200 > 0:
        gap_pct = (sma200 - sp500) / sma200
        confidence += min(0.3, gap_pct * 3.0)
    return min(1.0, confidence)


def _range_confidence(
    sp500: float,
    vix: float,
    sma50: float | None,
    sma200: float | None,
) -> float:
    """횡보 레짐 신뢰도 계산."""
    confidence = 0.5
    # VIX가 15-25 범위 중앙에 가까울수록 신뢰도 증가
    vix_mid = (_VIX_LOW + _VIX_HIGH) / 2.0  # 22.5
    vix_dist = abs(vix - vix_mid) / 10.0
    confidence += max(0.0, 0.2 - vix_dist * 0.2)
    # SMA50과 SMA200 사이에 있으면 신뢰도 증가
    if sma50 is not None and sma200 is not None:
        lower = min(sma50, sma200)
        upper = max(sma50, sma200)
        if lower <= sp500 <= upper:
            confidence += 0.15
    return min(1.0, confidence)
