"""Phase 11b: 촉매 캘린더 알림 테스트."""

from __future__ import annotations

from datetime import date

from src.deepdive.alert_engine import (
    evaluate_catalyst_alerts,
    format_catalyst_block,
)
from src.deepdive.schemas import UpcomingCatalyst


def _cat(kind: str, days_until: int, event_date: date | None = None) -> UpcomingCatalyst:
    return UpcomingCatalyst(
        kind=kind,
        event_date=event_date or date(2026, 4, 15),
        days_until=days_until,
        label=f"{kind} {days_until}일 후",
    )


class TestEarningsImminent:
    def test_d1_fires(self):
        triggers = evaluate_catalyst_alerts(
            "AAPL", 180.0, [_cat("earnings", 1)],
        )
        assert len(triggers) == 1
        assert triggers[0].trigger_type == "earnings_imminent"
        assert "D-1" in triggers[0].message

    def test_d3_fires(self):
        triggers = evaluate_catalyst_alerts(
            "AAPL", 180.0, [_cat("earnings", 3)],
        )
        assert len(triggers) == 1
        assert "D-3" in triggers[0].message

    def test_d5_no_fire(self):
        triggers = evaluate_catalyst_alerts(
            "AAPL", 180.0, [_cat("earnings", 5)],
        )
        assert triggers == []

    def test_d0_no_fire(self):
        triggers = evaluate_catalyst_alerts(
            "AAPL", 180.0, [_cat("earnings", 0)],
        )
        assert triggers == []


class TestExDividendImminent:
    def test_d1_fires(self):
        triggers = evaluate_catalyst_alerts(
            "MSFT", 400.0, [_cat("ex_dividend", 1)],
        )
        assert len(triggers) == 1
        assert triggers[0].trigger_type == "ex_dividend_imminent"

    def test_d2_no_fire(self):
        triggers = evaluate_catalyst_alerts(
            "MSFT", 400.0, [_cat("ex_dividend", 2)],
        )
        assert triggers == []


class TestFomcImminent:
    def test_d3_fires(self):
        triggers = evaluate_catalyst_alerts(
            "SPY", 500.0, [_cat("fomc", 3)],
        )
        assert len(triggers) == 1
        assert triggers[0].trigger_type == "fomc_imminent"


class TestCatalystAlertGuards:
    def test_empty_catalysts_returns_empty(self):
        assert evaluate_catalyst_alerts("AAPL", 180.0, []) == []

    def test_zero_price_returns_empty(self):
        assert evaluate_catalyst_alerts(
            "AAPL", 0.0, [_cat("earnings", 1)]
        ) == []

    def test_multiple_mixed_kinds(self):
        triggers = evaluate_catalyst_alerts(
            "TSLA",
            250.0,
            [
                _cat("earnings", 1, date(2026, 4, 12)),
                _cat("fomc", 3, date(2026, 4, 14)),
                _cat("earnings", 7),  # no fire
            ],
        )
        types = {t.trigger_type for t in triggers}
        assert "earnings_imminent" in types
        assert "fomc_imminent" in types
        assert len(triggers) == 2


class TestFormatCatalystBlock:
    def test_empty(self):
        assert format_catalyst_block([]) == ""

    def test_korean_block(self):
        items = [
            ("AAPL", [_cat("earnings", 1, date(2026, 4, 12))]),
            ("MSFT", [_cat("ex_dividend", 1, date(2026, 4, 12))]),
        ]
        block = format_catalyst_block(items)
        assert "📅 임박 촉매" in block
        assert "AAPL" in block
        assert "MSFT" in block
        assert "2026-04-12" in block

    def test_sorted_by_days_until(self):
        items = [
            ("NVDA", [_cat("earnings", 5, date(2026, 4, 16))]),
            ("AAPL", [_cat("earnings", 1, date(2026, 4, 12))]),
            ("TSLA", [_cat("fomc", 3, date(2026, 4, 14))]),
        ]
        block = format_catalyst_block(items)
        aapl_idx = block.find("AAPL")
        tsla_idx = block.find("TSLA")
        nvda_idx = block.find("NVDA")
        assert aapl_idx < tsla_idx < nvda_idx

    def test_outside_week_excluded(self):
        items = [
            ("AAPL", [_cat("earnings", 15, date(2026, 4, 27))]),
        ]
        assert format_catalyst_block(items) == ""

    def test_truncates_beyond_max(self):
        items = [
            (f"T{i}", [_cat("earnings", 1, date(2026, 4, 12))])
            for i in range(25)
        ]
        block = format_catalyst_block(items, max_items=20)
        assert "외 5건" in block
