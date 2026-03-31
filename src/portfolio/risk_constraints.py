"""리스크 제약 엔진 — 하드/소프트 제약 + 자동 조정."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import numpy as np

logger = logging.getLogger(__name__)

# 95% 신뢰구간 Z-score
Z_95 = 1.645


class ConstraintSeverity(str, Enum):
    """제약 위반 심각도."""

    HARD = "hard"
    SOFT = "soft"


@dataclass(frozen=True)
class ConstraintViolation:
    """제약 위반 상세."""

    constraint_name: str
    severity: ConstraintSeverity
    description: str
    ticker: str | None = None
    current_value: float = 0.0
    limit_value: float = 0.0


@dataclass(frozen=True)
class RiskConstraints:
    """리스크 제약 설정."""

    max_single_stock_pct: float = 0.10
    max_sector_pct: float = 0.30
    max_leverage: float = 1.0
    daily_var_limit: float = 0.02  # 포트폴리오 가치의 2%
    avg_correlation_warn: float = 0.50
    factor_tilt_sigma: float = 2.0


@dataclass(frozen=True)
class ConstraintCheckResult:
    """제약 검사 결과."""

    adjusted_weights: dict[str, float]
    violations: tuple[ConstraintViolation, ...]
    warnings: tuple[ConstraintViolation, ...]
    cash_weight: float
    portfolio_var_95: float | None = None


def check_and_adjust(
    weights: dict[str, float],
    sector_map: dict[str, str | None],
    cov_matrix: np.ndarray | None = None,
    tickers_order: list[str] | None = None,
    constraints: RiskConstraints | None = None,
) -> ConstraintCheckResult:
    """비중에 리스크 제약을 적용하고 위반 시 자동 조정한다.

    조정 순서: 단일종목 캡 → 섹터 캡 → VaR 캡 → 레버리지 캡.
    초과 비중은 현금으로 환원한다.

    Args:
        weights: ticker -> weight 딕셔너리
        sector_map: ticker -> sector 매핑
        cov_matrix: 일간 수익률 공분산 행렬
        tickers_order: 공분산 행렬의 티커 순서
        constraints: 리스크 제약 설정

    Returns:
        ConstraintCheckResult
    """
    if constraints is None:
        constraints = RiskConstraints()

    adjusted = dict(weights)
    violations: list[ConstraintViolation] = []
    warnings_list: list[ConstraintViolation] = []

    # 1) 단일 종목 캡
    adjusted, stock_violations = _enforce_single_stock_limit(
        adjusted, constraints.max_single_stock_pct,
    )
    violations.extend(stock_violations)

    # 2) 섹터 캡
    adjusted, sector_violations = _enforce_sector_limit(
        adjusted, sector_map, constraints.max_sector_pct,
    )
    violations.extend(sector_violations)

    # 3) VaR 캡
    portfolio_var: float | None = None
    if cov_matrix is not None and tickers_order is not None:
        adjusted, var_violations, portfolio_var = _enforce_var_limit(
            adjusted, cov_matrix, tickers_order, constraints.daily_var_limit,
        )
        violations.extend(var_violations)

    # 4) 레버리지 캡
    adjusted, leverage_violations = _enforce_leverage_limit(
        adjusted, constraints.max_leverage,
    )
    violations.extend(leverage_violations)

    # 소프트 리밋: 상관관계 경고
    if cov_matrix is not None and tickers_order is not None:
        corr_warnings = _check_correlation_warning(
            adjusted, cov_matrix, tickers_order, constraints.avg_correlation_warn,
        )
        warnings_list.extend(corr_warnings)

    total_exposure = sum(adjusted.values())
    cash = max(0.0, 1.0 - total_exposure)

    return ConstraintCheckResult(
        adjusted_weights=adjusted,
        violations=tuple(violations),
        warnings=tuple(warnings_list),
        cash_weight=round(cash, 6),
        portfolio_var_95=round(portfolio_var, 6) if portfolio_var is not None else None,
    )


def _enforce_single_stock_limit(
    weights: dict[str, float],
    max_pct: float,
) -> tuple[dict[str, float], list[ConstraintViolation]]:
    """단일 종목 최대 비중 제약."""
    adjusted = {}
    violations: list[ConstraintViolation] = []

    for ticker, w in weights.items():
        if w > max_pct:
            violations.append(ConstraintViolation(
                constraint_name="single_stock_limit",
                severity=ConstraintSeverity.HARD,
                description=f"{ticker} 비중 {w:.1%} → {max_pct:.1%} 캡 적용",
                ticker=ticker,
                current_value=w,
                limit_value=max_pct,
            ))
            adjusted[ticker] = max_pct
        else:
            adjusted[ticker] = w

    return adjusted, violations


def _enforce_sector_limit(
    weights: dict[str, float],
    sector_map: dict[str, str | None],
    max_pct: float,
) -> tuple[dict[str, float], list[ConstraintViolation]]:
    """단일 섹터 최대 비중 제약."""
    # 섹터별 비중 합산
    sector_weights: dict[str, float] = {}
    sector_tickers: dict[str, list[str]] = {}
    for ticker, w in weights.items():
        sector = sector_map.get(ticker)
        if sector is None:
            continue
        sector_weights[sector] = sector_weights.get(sector, 0.0) + w
        if sector not in sector_tickers:
            sector_tickers[sector] = []
        sector_tickers[sector].append(ticker)

    adjusted = dict(weights)
    violations: list[ConstraintViolation] = []

    for sector, total_w in sector_weights.items():
        if total_w <= max_pct:
            continue

        violations.append(ConstraintViolation(
            constraint_name="sector_limit",
            severity=ConstraintSeverity.HARD,
            description=f"{sector} 섹터 비중 {total_w:.1%} → {max_pct:.1%} 축소",
            current_value=total_w,
            limit_value=max_pct,
        ))

        # 비례 축소
        scale = max_pct / total_w
        for ticker in sector_tickers[sector]:
            adjusted[ticker] = round(adjusted[ticker] * scale, 6)

    return adjusted, violations


def _enforce_var_limit(
    weights: dict[str, float],
    cov_matrix: np.ndarray,
    tickers_order: list[str],
    daily_var_limit: float,
) -> tuple[dict[str, float], list[ConstraintViolation], float]:
    """일일 VaR 한도 제약 (95% 신뢰구간, 파라메트릭).

    VaR = Z_0.95 * sqrt(w^T * Sigma_daily * w)
    """
    n = len(tickers_order)
    if cov_matrix.shape != (n, n):
        return dict(weights), [], 0.0

    w = np.array([weights.get(t, 0.0) for t in tickers_order])
    port_daily_vol = np.sqrt(w @ cov_matrix @ w)
    var_95 = Z_95 * port_daily_vol

    violations: list[ConstraintViolation] = []

    if var_95 > daily_var_limit and var_95 > 0:
        scale = daily_var_limit / var_95
        violations.append(ConstraintViolation(
            constraint_name="daily_var_limit",
            severity=ConstraintSeverity.HARD,
            description=f"일일 VaR {var_95:.2%} → {daily_var_limit:.2%} 축소 (스케일 {scale:.2f})",
            current_value=var_95,
            limit_value=daily_var_limit,
        ))
        adjusted = {
            t: round(weights.get(t, 0.0) * scale, 6) for t in tickers_order
        }
        # 공분산 행렬에 없는 티커도 동일 스케일 적용
        for t in weights:
            if t not in adjusted:
                logger.warning("VaR 조정 시 %s 공분산 없음 — 동일 스케일 적용", t)
                adjusted[t] = round(weights[t] * scale, 6)
        # 축소 후 VaR 재계산
        w_adj = np.array([adjusted.get(t, 0.0) for t in tickers_order])
        var_95 = Z_95 * np.sqrt(w_adj @ cov_matrix @ w_adj)
        return adjusted, violations, float(var_95)

    return dict(weights), violations, float(var_95)


def _enforce_leverage_limit(
    weights: dict[str, float],
    max_leverage: float,
) -> tuple[dict[str, float], list[ConstraintViolation]]:
    """총 노출도(레버리지) 제약."""
    total = sum(weights.values())
    violations: list[ConstraintViolation] = []

    if total <= max_leverage:
        return dict(weights), violations

    scale = max_leverage / total
    violations.append(ConstraintViolation(
        constraint_name="leverage_limit",
        severity=ConstraintSeverity.HARD,
        description=f"총 노출도 {total:.2f}x → {max_leverage:.1f}x 축소",
        current_value=total,
        limit_value=max_leverage,
    ))

    return {t: round(w * scale, 6) for t, w in weights.items()}, violations


def _check_correlation_warning(
    weights: dict[str, float],
    cov_matrix: np.ndarray,
    tickers_order: list[str],
    threshold: float,
) -> list[ConstraintViolation]:
    """종목 간 평균 상관관계 경고 (소프트 리밋)."""
    n = len(tickers_order)
    if n < 2 or cov_matrix.shape != (n, n):
        return []

    # 공분산 → 상관관계 변환
    diag = np.sqrt(np.diag(cov_matrix))
    diag = np.where(diag < 1e-12, 1e-12, diag)
    corr_matrix = cov_matrix / np.outer(diag, diag)

    # 투자 비중이 있는 종목 간 가중 평균 상관관계
    active_indices = [
        i for i, t in enumerate(tickers_order)
        if weights.get(t, 0.0) > 0.001
    ]

    if len(active_indices) < 2:
        return []

    total_corr = 0.0
    count = 0
    for i_idx, i in enumerate(active_indices):
        for j in active_indices[i_idx + 1:]:
            total_corr += corr_matrix[i, j]
            count += 1

    avg_corr = total_corr / count if count > 0 else 0.0

    if avg_corr > threshold:
        return [ConstraintViolation(
            constraint_name="correlation_concentration",
            severity=ConstraintSeverity.SOFT,
            description=f"종목 간 평균 상관관계 {avg_corr:.2f} > {threshold:.2f} — 분산 부족 경고",
            current_value=avg_corr,
            limit_value=threshold,
        )]

    return []
