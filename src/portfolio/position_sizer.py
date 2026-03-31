"""포지션 사이징 엔진 — 3가지 전략 (ERC / Vol Target / Half-Kelly)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionSizingInput:
    """포지션 사이징 입력."""

    ticker: str
    stock_id: int
    volatility: float  # 연환산 변동성
    ai_confidence: int | None  # 1-10 (step4.5)
    sector: str | None
    price: float
    daily_volume: float | None


@dataclass(frozen=True)
class SizingResult:
    """포지션 사이징 결과."""

    strategy: str
    weights: dict[str, float]  # ticker -> weight (0~1)
    cash_weight: float
    total_exposure: float  # sum of weights (<=1.0)
    raw_weights: dict[str, float]  # 신뢰도 틸트 전 원시 비중


def size_positions(
    inputs: list[PositionSizingInput],
    cov_matrix: np.ndarray | None,
    strategy: str = "vol_target",
    target_vol: float = 0.15,
    risk_free_rate: float = 0.04,
    expected_returns: dict[str, float] | None = None,
) -> SizingResult:
    """추천 종목의 포지션 비중을 산출한다.

    Args:
        inputs: 종목별 사이징 입력 리스트
        cov_matrix: 일간 수익률 공분산 행렬 (inputs 순서와 동일)
        strategy: "erc" | "vol_target" | "half_kelly"
        target_vol: 목표 연환산 변동성 (vol_target 전략용)
        risk_free_rate: 무위험 수익률 (half_kelly 전략용)
        expected_returns: ticker -> 기대 수익률 (half_kelly 전략용)

    Returns:
        SizingResult
    """
    if not inputs:
        return SizingResult(
            strategy=strategy,
            weights={},
            cash_weight=1.0,
            total_exposure=0.0,
            raw_weights={},
        )

    n = len(inputs)

    if n == 1:
        ticker = inputs[0].ticker
        raw = {ticker: 1.0}
        tilted = _apply_confidence_tilt(raw, inputs)
        exposure = sum(tilted.values())
        return SizingResult(
            strategy=strategy,
            weights=tilted,
            cash_weight=round(1.0 - exposure, 6),
            total_exposure=round(exposure, 6),
            raw_weights=raw,
        )

    # 전략별 비중 산출
    if strategy == "erc":
        raw = _equal_risk_contribution(inputs, cov_matrix)
    elif strategy == "half_kelly":
        raw = _half_kelly(inputs, expected_returns, cov_matrix, risk_free_rate)
    else:
        raw = _volatility_targeting(inputs, cov_matrix, target_vol)

    # AI 신뢰도 기반 틸트
    tilted = _apply_confidence_tilt(raw, inputs)

    exposure = sum(tilted.values())
    cash = max(0.0, 1.0 - exposure)

    return SizingResult(
        strategy=strategy,
        weights=tilted,
        cash_weight=round(cash, 6),
        total_exposure=round(exposure, 6),
        raw_weights=raw,
    )


def _equal_risk_contribution(
    inputs: list[PositionSizingInput],
    cov_matrix: np.ndarray | None,
) -> dict[str, float]:
    """Equal Risk Contribution — 한계 리스크 기여도 균등화.

    scipy SLSQP로 최적화하되, 실패 시 역변동성 가중 fallback.
    """
    n = len(inputs)
    tickers = [inp.ticker for inp in inputs]

    if cov_matrix is None or cov_matrix.shape != (n, n):
        return _inverse_volatility_fallback(inputs)

    # 목적 함수: 한계 리스크 기여도의 분산 최소화
    def objective(weights: np.ndarray) -> float:
        port_vol = np.sqrt(weights @ cov_matrix @ weights)
        if port_vol < 1e-12:
            return 0.0
        marginal = cov_matrix @ weights
        risk_contrib = weights * marginal / port_vol
        target = port_vol / n
        return float(np.sum((risk_contrib - target) ** 2))

    bounds = tuple((0.001, 1.0) for _ in range(n))
    constraints = {"type": "eq", "fun": lambda w: np.sum(w) - 1.0}
    init = np.array([1.0 / n] * n)

    try:
        result = minimize(
            objective, init, method="SLSQP",
            bounds=bounds, constraints=constraints,
        )
        if result.success:
            weights = result.x
            weights = np.maximum(weights, 0.0)
            weights = weights / weights.sum()
            return {
                tickers[i]: round(float(weights[i]), 6)
                for i in range(n)
            }
    except Exception:
        logger.warning("ERC 최적화 실패, 역변동성 가중 fallback")

    return _inverse_volatility_fallback(inputs)


def _inverse_volatility_fallback(
    inputs: list[PositionSizingInput],
) -> dict[str, float]:
    """역변동성 가중 — ERC fallback."""
    vols = np.array([max(inp.volatility, 0.01) for inp in inputs])
    inv_vol = 1.0 / vols
    weights = inv_vol / inv_vol.sum()
    return {
        inputs[i].ticker: round(float(weights[i]), 6)
        for i in range(len(inputs))
    }


def _volatility_targeting(
    inputs: list[PositionSizingInput],
    cov_matrix: np.ndarray | None,
    target_vol: float,
) -> dict[str, float]:
    """Volatility Targeting — 목표 변동성에 맞춰 노출도 조절.

    1) ERC 비중 산출
    2) 포트폴리오 변동성 계산
    3) target_vol에 맞춰 스케일링 (레버리지 금지)
    """
    base_weights = _equal_risk_contribution(inputs, cov_matrix)
    n = len(inputs)
    tickers = [inp.ticker for inp in inputs]

    if cov_matrix is None or cov_matrix.shape != (n, n):
        return base_weights

    # 포트폴리오 연환산 변동성 계산
    w = np.array([base_weights.get(t, 0.0) for t in tickers])
    daily_vol = np.sqrt(w @ cov_matrix @ w)
    annual_vol = daily_vol * np.sqrt(252)

    if annual_vol < 1e-10:
        return base_weights

    # 스케일링 (레버리지 금지: cap at 1.0)
    scale = min(target_vol / annual_vol, 1.0)

    return {
        ticker: round(float(base_weights[ticker] * scale), 6)
        for ticker in tickers
    }


def _half_kelly(
    inputs: list[PositionSizingInput],
    expected_returns: dict[str, float] | None,
    cov_matrix: np.ndarray | None,
    risk_free_rate: float,
) -> dict[str, float]:
    """Half-Kelly — 최적 베팅 비율의 절반.

    f* = Sigma^{-1} * (mu - rf), 각 비중을 절반으로 축소.
    """
    n = len(inputs)
    tickers = [inp.ticker for inp in inputs]

    if expected_returns is None or cov_matrix is None or cov_matrix.shape != (n, n):
        return _inverse_volatility_fallback(inputs)

    # 기대 수익률 벡터 (일간 → 연간 변환 불필요, 이미 연환산 가정)
    mu = np.array([expected_returns.get(t, 0.0) for t in tickers])
    excess = mu - risk_free_rate

    try:
        inv_cov = np.linalg.inv(cov_matrix * 252)  # 연환산 공분산
        kelly_full = inv_cov @ excess
    except np.linalg.LinAlgError:
        logger.warning("공분산 행렬 역행렬 실패, 역변동성 fallback")
        return _inverse_volatility_fallback(inputs)

    # Half-Kelly, 음수 비중은 0으로
    kelly_half = np.maximum(kelly_full * 0.5, 0.0)

    total = kelly_half.sum()
    if total < 1e-10:
        return _inverse_volatility_fallback(inputs)

    # 합계가 1을 초과하면 정규화
    if total > 1.0:
        kelly_half = kelly_half / total

    return {
        tickers[i]: round(float(kelly_half[i]), 6)
        for i in range(n)
    }


def _apply_confidence_tilt(
    weights: dict[str, float],
    inputs: list[PositionSizingInput],
) -> dict[str, float]:
    """AI 신뢰도 기반 비중 틸트.

    confidence=5이면 중립, >5이면 비중 상향, <5이면 하향.
    신뢰도가 None이면 중립(5)으로 처리.
    """
    confidence_map = {
        inp.ticker: max(1, min(10, inp.ai_confidence if inp.ai_confidence is not None else 5))
        for inp in inputs
    }

    tilted = {}
    for ticker, w in weights.items():
        conf = confidence_map.get(ticker, 5)
        tilt_factor = conf / 5.0  # 5=중립(1.0), 10=2.0배, 1=0.2배
        tilted[ticker] = w * tilt_factor

    # 합계 정규화 (원래 총 노출도 유지)
    original_sum = sum(weights.values())
    tilted_sum = sum(tilted.values())

    if tilted_sum > 1e-10 and original_sum > 1e-10:
        scale = original_sum / tilted_sum
        return {t: round(w * scale, 6) for t, w in tilted.items()}

    return dict(weights)
