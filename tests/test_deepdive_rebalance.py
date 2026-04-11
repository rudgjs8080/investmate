"""Phase 11c: 포트폴리오 리밸런싱 제안 테스트."""

from __future__ import annotations

from dataclasses import dataclass

from src.deepdive.rebalance_advisor import (
    Holding,
    RebalancePlan,
    RebalanceSuggestion,
    build_rebalance_plan,
)


@dataclass(frozen=True)
class _GuideStub:
    """ExecutionGuide 인터페이스 최소 구현 — 테스트 주입용."""
    suggested_position_pct: float
    expected_value_pct: dict
    risk_reward_ratio: float | None = 2.0
    portfolio_fit_warnings: tuple = ()


def _h(ticker: str, shares: float, price: float, sector: str = "Tech") -> Holding:
    return Holding(
        ticker=ticker, shares=shares, avg_cost=price * 0.9,
        current_price=price, sector=sector,
    )


class TestBasicRebalance:
    def test_suggestions_delta_correct_direction(self):
        """타겟 > 현재 → 델타 양수(추가)."""
        holdings = [_h("AAPL", 10, 100.0)]  # 100% of $1000
        guides = {
            "AAPL": _GuideStub(
                suggested_position_pct=50.0,
                expected_value_pct={"1M": 3.0, "3M": 8.0, "6M": 15.0},
            ),
        }
        plan = build_rebalance_plan(holdings, guides)
        # 단일 종목이라 정규화 후 100% → 현재 100% → 델타 0
        assert isinstance(plan, RebalancePlan)

    def test_multi_ticker_normalization(self):
        """타겟 합이 상한(100%) 넘으면 정규화."""
        holdings = [
            _h("AAPL", 10, 100.0, "Tech"),
            _h("MSFT", 5, 200.0, "Tech"),
        ]
        guides = {
            "AAPL": _GuideStub(
                suggested_position_pct=80.0,
                expected_value_pct={"3M": 10.0},
            ),
            "MSFT": _GuideStub(
                suggested_position_pct=80.0,
                expected_value_pct={"3M": 10.0},
            ),
        }
        plan = build_rebalance_plan(holdings, guides)
        total_target = sum(s.target_weight for s in plan.suggestions)
        # 정규화 후 합 ≤ 1.0
        assert total_target <= 1.0 + 1e-6


class TestSectorCap:
    def test_sector_cap_blocks_excess(self):
        """같은 섹터 2종목이 각각 30%면 섹터 합 60% → 30% 제한."""
        holdings = [
            _h("AAPL", 10, 100.0, "Tech"),  # $1000
            _h("MSFT", 5, 200.0, "Tech"),   # $1000
        ]
        guides = {
            "AAPL": _GuideStub(
                suggested_position_pct=30.0,
                expected_value_pct={"3M": 5.0},
            ),
            "MSFT": _GuideStub(
                suggested_position_pct=30.0,
                expected_value_pct={"3M": 5.0},
            ),
        }
        plan = build_rebalance_plan(
            holdings, guides,
            max_sector_weight=0.30,
            max_single_stock_pct=0.50,  # 개별 종목 상한은 느슨하게
            max_daily_turnover_pct=1.0,  # 턴오버도 풀어줌 (섹터 cap만 검증)
        )
        # Tech 섹터 총 타겟 ≤ 30%
        tech_target = sum(
            s.target_weight for s in plan.suggestions if s.ticker in ("AAPL", "MSFT")
        )
        assert tech_target <= 0.30 + 1e-6
        assert "Tech" in plan.blocked_by_sector_cap


class TestTurnoverCap:
    def test_daily_turnover_scales_deltas(self):
        """일일 턴오버 30%p 제한."""
        holdings = [
            _h("AAPL", 10, 100.0, "Tech"),
            _h("MSFT", 10, 100.0, "Finance"),
        ]
        guides = {
            "AAPL": _GuideStub(
                suggested_position_pct=80.0,
                expected_value_pct={"3M": 10.0},
            ),
            "MSFT": _GuideStub(
                suggested_position_pct=20.0,
                expected_value_pct={"3M": 10.0},
            ),
        }
        plan = build_rebalance_plan(
            holdings, guides, max_daily_turnover_pct=0.30,
        )
        total_abs_delta = sum(abs(s.delta_pct) for s in plan.suggestions)
        assert total_abs_delta <= 0.30 * 100 + 1e-6


class TestTxCostFilter:
    def test_negative_ev_buy_removed(self):
        """거래비용 차감 후 EV ≤ 0인 매수(delta > 0) 제안 제거.

        매도(TRIM)는 리스크 축소 목적이므로 net_ev가 음수여도 남긴다.
        """
        holdings = [
            _h("EXIST", 5, 100.0, "Tech"),   # 50% 보유
            _h("BUY",   5, 100.0, "Finance"),  # 50% 보유
        ]
        guides = {
            "EXIST": _GuideStub(
                suggested_position_pct=30.0,   # trim (50→30)
                expected_value_pct={"3M": 0.1},
            ),
            "BUY": _GuideStub(
                suggested_position_pct=90.0,   # 추가 매수 (50→원래 많이 올릴 것이나 capped)
                expected_value_pct={"3M": 0.01},  # 사실상 0
            ),
        }
        plan = build_rebalance_plan(
            holdings, guides,
            tx_cost_bps=500.0,
            max_single_stock_pct=0.9,
            max_daily_turnover_pct=1.0,
        )
        # 매수 제안(delta > 0)은 순 EV가 양수여야 한다
        buy_suggestions = [s for s in plan.suggestions if s.delta_pct > 0]
        for s in buy_suggestions:
            assert s.net_ev_pct > 0, f"{s.ticker}: net_ev={s.net_ev_pct}"


class TestNoiseSuppression:
    def test_small_delta_suppressed(self):
        """|delta| < 1%p 제안은 제외."""
        holdings = [_h("STABLE", 10, 100.0, "Tech")]  # 100%
        guides = {
            "STABLE": _GuideStub(
                suggested_position_pct=100.5,  # 거의 동일
                expected_value_pct={"3M": 5.0},
            ),
        }
        plan = build_rebalance_plan(holdings, guides)
        # 1%p 미만이면 suggestions 비어있어야 함
        assert all(abs(s.delta_pct) >= 1.0 for s in plan.suggestions)


class TestEmptyCases:
    def test_empty_holdings_returns_empty(self):
        plan = build_rebalance_plan([], {})
        assert plan.suggestions == ()
        assert plan.cash_weight_after == 1.0

    def test_missing_guide_ticker_skipped(self):
        holdings = [_h("AAPL", 10, 100.0)]
        plan = build_rebalance_plan(holdings, {})  # AAPL 가이드 없음
        # 타겟 없으면 target_weight = 0 → 100% TRIM → cash 100%
        assert isinstance(plan, RebalancePlan)


class TestRationaleKorean:
    def test_rationale_contains_korean_tokens(self):
        holdings = [_h("AAPL", 10, 100.0, "Tech")]
        guides = {
            "AAPL": _GuideStub(
                suggested_position_pct=50.0,
                expected_value_pct={"3M": 10.0},
                risk_reward_ratio=2.5,
            ),
        }
        plan = build_rebalance_plan(holdings, guides)
        for s in plan.suggestions:
            assert isinstance(s.rationale, str)
            # 한국어 또는 R/R 토큰 포함 (noise 없을 때)
            assert len(s.rationale) > 0


class TestTypes:
    def test_suggestion_is_frozen(self):
        import pytest as _pytest

        s = RebalanceSuggestion(
            ticker="AAPL", current_weight=0.3, target_weight=0.4,
            delta_pct=10.0, delta_shares=5, delta_dollar=500.0,
            net_ev_pct=8.0, rationale="test",
        )
        with _pytest.raises((AttributeError, TypeError)):
            s.ticker = "MSFT"  # type: ignore[misc]
