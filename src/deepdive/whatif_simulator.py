"""Phase 12c: What-if 포트폴리오 시뮬레이터 (순수 함수).

보유 변경(추가/매도/조정)을 가상으로 적용했을 때 리밸런싱 플랜, 섹터 분포,
경고 메시지가 어떻게 변하는지를 즉시 계산한다.

DB 접근 없음. rebalance_advisor.build_rebalance_plan을 재사용.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from src.deepdive.rebalance_advisor import (
    Holding,
    RebalancePlan,
    _GuideLike,
    build_rebalance_plan,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Modification:
    """단일 보유 변경 지시. shares 또는 shares_delta 중 정확히 하나."""

    ticker: str
    shares: int | None = None         # 절대값 (0 = 전량 매도)
    shares_delta: int | None = None   # 증감 (양수=매수, 음수=매도)

    def __post_init__(self) -> None:
        if (self.shares is None) == (self.shares_delta is None):
            raise ValueError(
                "Modification은 shares 또는 shares_delta 중 정확히 하나여야 합니다",
            )
        if self.shares is not None and self.shares < 0:
            raise ValueError("shares는 0 이상이어야 합니다")


@dataclass(frozen=True)
class StockInfo:
    """시뮬레이션에서 사용 가능한 종목 메타 (current_price + sector)."""

    current_price: float
    sector: str | None = None


@dataclass(frozen=True)
class SimulationResult:
    """시뮬레이션 결과."""

    before_plan: RebalancePlan
    after_plan: RebalancePlan
    before_sector_weights: tuple[tuple[str, float], ...]
    after_sector_weights: tuple[tuple[str, float], ...]
    before_total_value: float
    after_total_value: float
    modified_tickers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    violations: tuple[str, ...] = field(default_factory=tuple)


def simulate_holdings_change(
    current: Sequence[Holding],
    modifications: Sequence[Modification],
    guides: Mapping[str, _GuideLike],
    universe: Mapping[str, StockInfo] | None = None,
    *,
    max_sector_weight: float = 0.30,
    max_single_stock_pct: float = 0.10,
    max_daily_turnover_pct: float = 0.30,
    tx_cost_bps: float = 20.0,
) -> SimulationResult:
    """보유 변경을 가상으로 적용한 결과를 반환한다.

    Args:
        current: 현재 보유 종목 (shares > 0 인 것만).
        modifications: 가상 변경 지시 리스트.
        guides: 각 종목의 ExecutionGuide-like (rebalance_advisor 재사용).
        universe: modifications가 current에 없는 ticker를 참조할 때 필요한
                 가격/섹터 정보. 없으면 current에 없는 ticker는 거부된다.
        max_sector_weight / max_single_stock_pct / ...: rebalance_advisor 전달.

    Returns:
        SimulationResult — before/after 플랜 + 섹터 분포 + 경고.

    Raises:
        값 변경은 하지 않음 (immutable). 입력 시퀀스를 절대 수정하지 않는다.
    """
    # 1) 현재 → dict[ticker, Holding] (불변 복사)
    current_map: dict[str, Holding] = {h.ticker: h for h in current}
    universe = dict(universe) if universe else {}

    # current의 ticker들은 universe가 비어있어도 자기 자신 정보로 사용
    for h in current:
        universe.setdefault(h.ticker, StockInfo(
            current_price=h.current_price, sector=h.sector,
        ))

    # 2) 수정사항 적용 (순수 — 새 dict 구성)
    modified_map: dict[str, Holding] = dict(current_map)
    violations: list[str] = []
    modified_tickers: list[str] = []

    for mod in modifications:
        ticker = mod.ticker.upper()
        info = universe.get(ticker)
        if info is None:
            violations.append(f"{ticker}: 가격 정보 없음 (universe에 누락)")
            continue

        if ticker in modified_map:
            existing = modified_map[ticker]
            avg_cost = existing.avg_cost
            sector = existing.sector or info.sector
        else:
            avg_cost = info.current_price  # 신규 매수 가정: 평단 = 현재가
            sector = info.sector

        if mod.shares is not None:
            new_shares = mod.shares
        else:
            base = existing.shares if ticker in modified_map else 0.0
            new_shares = int(base) + int(mod.shares_delta or 0)

        if new_shares < 0:
            violations.append(
                f"{ticker}: 매도 수량이 보유량을 초과합니다 (결과 {new_shares}주)",
            )
            continue

        modified_tickers.append(ticker)

        if new_shares == 0:
            modified_map.pop(ticker, None)
            continue

        modified_map[ticker] = Holding(
            ticker=ticker,
            shares=float(new_shares),
            avg_cost=avg_cost,
            current_price=info.current_price,
            sector=sector,
        )

    after_holdings = tuple(modified_map.values())
    current_holdings_tuple = tuple(current)

    # 3) before/after 플랜 계산 (순수 함수)
    before_plan = build_rebalance_plan(
        current_holdings_tuple,
        guides,
        max_sector_weight=max_sector_weight,
        max_single_stock_pct=max_single_stock_pct,
        max_daily_turnover_pct=max_daily_turnover_pct,
        tx_cost_bps=tx_cost_bps,
    )
    after_plan = build_rebalance_plan(
        after_holdings,
        guides,
        max_sector_weight=max_sector_weight,
        max_single_stock_pct=max_single_stock_pct,
        max_daily_turnover_pct=max_daily_turnover_pct,
        tx_cost_bps=tx_cost_bps,
    )

    # 4) 섹터 분포
    before_sectors = _sector_distribution(current_holdings_tuple)
    after_sectors = _sector_distribution(after_holdings)

    # 5) 제약 위반 경고 (섹터 상한 초과)
    warnings = list(before_plan.warnings) + list(after_plan.warnings)
    for sector, weight in after_sectors:
        if weight > max_sector_weight + 1e-9:
            violations.append(
                f"{sector} 섹터 비중 {weight * 100:.1f}% > 상한 {max_sector_weight * 100:.0f}%",
            )
    for _, (ticker, holding) in enumerate(modified_map.items()):
        total_value = sum(h.shares * h.current_price for h in after_holdings)
        if total_value > 0:
            weight = (holding.shares * holding.current_price) / total_value
            if weight > max_single_stock_pct + 1e-9:
                violations.append(
                    f"{ticker} 단일 종목 {weight * 100:.1f}% > 상한 {max_single_stock_pct * 100:.0f}%",
                )
                break  # 같은 종목이 여러 modification에 걸려도 1회만 보고

    before_total = sum(h.shares * h.current_price for h in current_holdings_tuple)
    after_total = sum(h.shares * h.current_price for h in after_holdings)

    return SimulationResult(
        before_plan=before_plan,
        after_plan=after_plan,
        before_sector_weights=before_sectors,
        after_sector_weights=after_sectors,
        before_total_value=round(before_total, 2),
        after_total_value=round(after_total, 2),
        modified_tickers=tuple(dict.fromkeys(modified_tickers)),
        warnings=tuple(dict.fromkeys(warnings)),
        violations=tuple(dict.fromkeys(violations)),
    )


def _sector_distribution(
    holdings: Sequence[Holding],
) -> tuple[tuple[str, float], ...]:
    """섹터별 비중 (불변 튜플)."""
    total = sum(h.shares * h.current_price for h in holdings)
    if total <= 0:
        return ()
    acc: dict[str, float] = {}
    for h in holdings:
        key = h.sector or "Unknown"
        acc[key] = acc.get(key, 0.0) + (h.shares * h.current_price) / total
    return tuple(
        (sector, round(weight, 4))
        for sector, weight in sorted(acc.items(), key=lambda x: -x[1])
    )
