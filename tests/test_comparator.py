"""리포트 히스토리 비교 테스트."""

from __future__ import annotations

from src.reports.comparator import (
    RankChange,
    ReportDiff,
    compare_recommendations,
    format_diff_summary,
)


def _rec(ticker: str, rank: int, name: str = "") -> dict:
    return {"ticker": ticker, "name": name or ticker, "rank": rank}


class TestCompareRecommendations:
    def test_identical_lists(self):
        today = [_rec("AAPL", 1), _rec("MSFT", 2)]
        yesterday = [_rec("AAPL", 1), _rec("MSFT", 2)]
        diff = compare_recommendations(today, yesterday)
        assert diff.new_entries == []
        assert diff.dropped == []
        assert len(diff.retained) == 2
        assert not diff.has_changes

    def test_new_entry(self):
        today = [_rec("AAPL", 1), _rec("NVDA", 2)]
        yesterday = [_rec("AAPL", 1)]
        diff = compare_recommendations(today, yesterday)
        assert "NVDA" in diff.new_entries
        assert diff.dropped == []

    def test_dropped_entry(self):
        today = [_rec("AAPL", 1)]
        yesterday = [_rec("AAPL", 1), _rec("MSFT", 2)]
        diff = compare_recommendations(today, yesterday)
        assert "MSFT" in diff.dropped
        assert diff.new_entries == []

    def test_rank_change(self):
        today = [_rec("AAPL", 1), _rec("MSFT", 2)]
        yesterday = [_rec("MSFT", 1), _rec("AAPL", 2)]
        diff = compare_recommendations(today, yesterday)
        assert len(diff.retained) == 2
        aapl = next(r for r in diff.retained if r.ticker == "AAPL")
        assert aapl.delta == 1  # 2 -> 1 = 상승

    def test_market_score_delta(self):
        diff = compare_recommendations([], [], today_market_score=8, yesterday_market_score=5)
        assert diff.market_score_delta == 3

    def test_complete_turnover(self):
        today = [_rec("NVDA", 1), _rec("TSLA", 2)]
        yesterday = [_rec("AAPL", 1), _rec("MSFT", 2)]
        diff = compare_recommendations(today, yesterday)
        assert set(diff.new_entries) == {"NVDA", "TSLA"}
        assert set(diff.dropped) == {"AAPL", "MSFT"}
        assert diff.retained == []

    def test_empty_both(self):
        diff = compare_recommendations([], [])
        assert not diff.has_changes

    def test_counts(self):
        today = [_rec("AAPL", 1), _rec("MSFT", 2), _rec("NVDA", 3)]
        yesterday = [_rec("AAPL", 1)]
        diff = compare_recommendations(today, yesterday)
        assert diff.today_count == 3
        assert diff.yesterday_count == 1


class TestFormatDiffSummary:
    def test_no_changes(self):
        diff = ReportDiff([], [], [], 0, 10, 10)
        assert "동일" in format_diff_summary(diff)

    def test_new_and_dropped(self):
        diff = ReportDiff(
            new_entries=["NVDA"],
            dropped=["MSFT"],
            retained=[],
            market_score_delta=0,
            today_count=10,
            yesterday_count=10,
        )
        summary = format_diff_summary(diff)
        assert "신규: NVDA" in summary
        assert "탈락: MSFT" in summary

    def test_rank_changes(self):
        diff = ReportDiff(
            new_entries=[],
            dropped=[],
            retained=[
                RankChange("AAPL", "Apple", 3, 1),  # +2
                RankChange("MSFT", "Microsoft", 1, 3),  # -2
            ],
            market_score_delta=0,
            today_count=10,
            yesterday_count=10,
        )
        summary = format_diff_summary(diff)
        assert "상승" in summary
        assert "하락" in summary

    def test_market_score_improvement(self):
        diff = ReportDiff([], [], [], 2, 10, 10)
        summary = format_diff_summary(diff)
        assert "개선" in summary
        assert "+2" in summary

    def test_market_score_deterioration(self):
        diff = ReportDiff([], [], [], -3, 10, 10)
        summary = format_diff_summary(diff)
        assert "악화" in summary
