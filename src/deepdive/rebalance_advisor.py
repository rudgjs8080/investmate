"""Phase 11c: 포트폴리오 리밸런싱 제안 (순수 함수 모듈).

execution_guide.suggested_position_pct는 종목 독립 계산이다. 이 모듈은
포트폴리오 전체 관점에서 타겟 비중을 정규화하고 현재 비중과의 델타를
산출해 리밸런싱 제안을 만든다.

DB 접근 없음 — 모든 입력은 호출자가 주입.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# DTO
# ──────────────────────────────────────────


@dataclass(frozen=True)
class Holding:
    """현재 보유 포지션."""

    ticker: str
    shares: float
    avg_cost: float
    current_price: float
    sector: str | None = None


@dataclass(frozen=True)
class RebalanceSuggestion:
    """단일 종목 리밸런싱 제안."""

    ticker: str
    current_weight: float   # 0.0~1.0
    target_weight: float    # 0.0~1.0
    delta_pct: float        # (target - current) * 100, %p 단위
    delta_shares: int       # 현재가 기준 근사치(정수)
    delta_dollar: float     # 정수 근사치의 실제 달러 변동
    net_ev_pct: float       # 거래비용 차감 후 3M EV (%)
    rationale: str          # 한국어 한 줄 요약


@dataclass(frozen=True)
class RebalancePlan:
    """배치 결과 + 메타."""

    suggestions: tuple[RebalanceSuggestion, ...] = ()
    total_turnover_pct: float = 0.0      # 제안된 |delta|의 합 (%p)
    cash_weight_after: float = 1.0
    blocked_by_sector_cap: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


class _GuideLike(Protocol):
    """ExecutionGuide 또는 테스트 스텁이 만족할 최소 인터페이스."""

    suggested_position_pct: float
    expected_value_pct: Mapping[str, float]
    risk_reward_ratio: float | None
    portfolio_fit_warnings: tuple


# ──────────────────────────────────────────
# 메인 함수
# ──────────────────────────────────────────


_NOISE_THRESHOLD_PCT = 1.0  # |delta| < 1%p는 노이즈로 제거
_DEFAULT_EV_HORIZON = "3M"


def build_rebalance_plan(
    holdings: Sequence[Holding],
    guides: Mapping[str, _GuideLike],
    *,
    max_sector_weight: float = 0.30,
    max_single_stock_pct: float = 0.10,
    max_daily_turnover_pct: float = 0.30,
    tx_cost_bps: float = 20.0,
) -> RebalancePlan:
    """리밸런싱 플랜 계산 진입점.

    로직:
        1. 현재 비중 계산(shares × price 합 기반)
        2. 타겟 비중: guide.suggested_position_pct / 100 → 합계 정규화
        3. 단일 종목 상한 적용
        4. 섹터 상한 적용 (초과분은 비례 축소)
        5. 턴오버 제약(|delta| 합 ≤ max_daily_turnover_pct)
        6. 거래비용 차감 후 EV ≤ 0 필터
        7. |delta| < NOISE_THRESHOLD_PCT 제거
    """
    if not holdings:
        return RebalancePlan()

    total_value = sum(h.shares * h.current_price for h in holdings)
    if total_value <= 0:
        return RebalancePlan()

    # 1) 현재 비중
    current_weights = {
        h.ticker: (h.shares * h.current_price) / total_value for h in holdings
    }
    sectors = {h.ticker: (h.sector or "") for h in holdings}
    prices = {h.ticker: h.current_price for h in holdings}

    # 2) 타겟 비중(원본) — 가이드 없으면 0 (→ TRIM 대상)
    raw_target: dict[str, float] = {}
    for h in holdings:
        g = guides.get(h.ticker)
        if g is None:
            raw_target[h.ticker] = 0.0
        else:
            raw_target[h.ticker] = max(0.0, float(g.suggested_position_pct) / 100.0)

    # 3) 단일 종목 상한
    for t in list(raw_target):
        if raw_target[t] > max_single_stock_pct:
            raw_target[t] = max_single_stock_pct

    # 4) 섹터 상한
    blocked_sectors: set[str] = set()
    sector_totals: dict[str, float] = {}
    for t, w in raw_target.items():
        s = sectors.get(t) or ""
        sector_totals[s] = sector_totals.get(s, 0.0) + w
    for s, total in sector_totals.items():
        if s and total > max_sector_weight + 1e-9:
            # 비례 축소
            scale = max_sector_weight / total if total > 0 else 0.0
            for t in raw_target:
                if (sectors.get(t) or "") == s:
                    raw_target[t] *= scale
            blocked_sectors.add(s)

    # 타겟 합이 100%를 초과하면 전체 비례 축소
    target_sum = sum(raw_target.values())
    if target_sum > 1.0:
        scale = 1.0 / target_sum
        raw_target = {t: w * scale for t, w in raw_target.items()}
        target_sum = 1.0

    # 5) 턴오버 제약 — |delta| 합 ≥ 상한이면 델타 비례 축소
    deltas = {t: raw_target[t] - current_weights.get(t, 0.0) for t in raw_target}
    abs_sum = sum(abs(d) for d in deltas.values())
    if abs_sum > max_daily_turnover_pct:
        scale = max_daily_turnover_pct / abs_sum if abs_sum > 0 else 0.0
        deltas = {t: d * scale for t, d in deltas.items()}
        # target을 재산출 (current + scaled delta)
        raw_target = {t: current_weights.get(t, 0.0) + deltas[t] for t in deltas}

    # 6) 제안 생성 + 거래비용 EV 필터 + 노이즈 제거
    round_trip_bps = tx_cost_bps  # bps 단위, 왕복 포함이라 가정
    suggestions: list[RebalanceSuggestion] = []

    for t in sorted(raw_target):
        cur = current_weights.get(t, 0.0)
        tgt = raw_target[t]
        delta = tgt - cur
        delta_pct = delta * 100.0

        if abs(delta_pct) < _NOISE_THRESHOLD_PCT:
            continue

        guide = guides.get(t)
        base_ev = 0.0
        rr = None
        if guide is not None:
            ev_map = getattr(guide, "expected_value_pct", {}) or {}
            base_ev = float(ev_map.get(_DEFAULT_EV_HORIZON, 0.0))
            rr = getattr(guide, "risk_reward_ratio", None)

        # 거래비용 반영: |delta| 비중 × bps → %p
        cost_pct = abs(delta) * (round_trip_bps / 100.0)  # bps→%, weight×%
        net_ev = base_ev - cost_pct
        if net_ev <= 0 and delta > 0:
            # 추가 매수인데 순 EV가 음수면 제외
            continue

        # 달러/주수 환산
        delta_dollar = delta * _portfolio_total(holdings)
        px = prices.get(t, 0.0)
        delta_shares = int(delta_dollar / px) if px > 0 else 0
        actual_dollar = delta_shares * px

        rationale = _build_rationale(
            guide=guide,
            sector=sectors.get(t) or "",
            blocked_sectors=blocked_sectors,
            delta_pct=delta_pct,
            rr=rr,
        )

        suggestions.append(
            RebalanceSuggestion(
                ticker=t,
                current_weight=round(cur, 4),
                target_weight=round(tgt, 4),
                delta_pct=round(delta_pct, 2),
                delta_shares=delta_shares,
                delta_dollar=round(actual_dollar, 2),
                net_ev_pct=round(net_ev, 2),
                rationale=rationale,
            )
        )

    total_turnover = sum(abs(s.delta_pct) for s in suggestions)
    cash_after = max(0.0, 1.0 - sum(raw_target.values()))

    warnings: list[str] = []
    if blocked_sectors:
        warnings.append(
            f"섹터 상한 적용: {', '.join(sorted(blocked_sectors))}"
        )
    if total_turnover >= max_daily_turnover_pct * 100.0 - 0.5:
        warnings.append("일일 턴오버 상한 근접")

    return RebalancePlan(
        suggestions=tuple(suggestions),
        total_turnover_pct=round(total_turnover, 2),
        cash_weight_after=round(cash_after, 4),
        blocked_by_sector_cap=tuple(sorted(blocked_sectors)),
        warnings=tuple(warnings),
    )


def _portfolio_total(holdings: Sequence[Holding]) -> float:
    return sum(h.shares * h.current_price for h in holdings)


def _build_rationale(
    *,
    guide: _GuideLike | None,
    sector: str,
    blocked_sectors: set[str],
    delta_pct: float,
    rr: float | None,
) -> str:
    """한국어 한 줄 근거."""
    parts: list[str] = []
    direction = "추가" if delta_pct > 0 else "축소"
    parts.append(f"델타 {delta_pct:+.1f}%p {direction}")
    if rr is not None:
        parts.append(f"R/R {rr:.1f}")
    if sector:
        if sector in blocked_sectors:
            parts.append(f"{sector} 섹터 상한")
        else:
            parts.append(f"{sector} 여유")
    if guide is not None:
        warnings = getattr(guide, "portfolio_fit_warnings", ()) or ()
        if warnings:
            parts.append("포트폴리오 경고")
    return " · ".join(parts)
