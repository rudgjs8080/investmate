"""리포트 히스토리 비교 모듈.

어제와 오늘의 추천 결과를 비교하여 변동 사항을 요약한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RankChange:
    """종목 순위 변동."""

    ticker: str
    name: str
    prev_rank: int
    curr_rank: int

    @property
    def delta(self) -> int:
        """순위 변동 (양수 = 상승, 음수 = 하락)."""
        return self.prev_rank - self.curr_rank


@dataclass(frozen=True)
class ReportDiff:
    """두 리포트 간 차이."""

    new_entries: list[str]  # 신규 추천 종목 티커
    dropped: list[str]  # 탈락 종목 티커
    retained: list[RankChange]  # 유지 종목 순위 변동
    market_score_delta: int  # 시장 점수 변화
    today_count: int  # 오늘 추천 수
    yesterday_count: int  # 어제 추천 수

    @property
    def has_changes(self) -> bool:
        return bool(
            self.new_entries
            or self.dropped
            or self.market_score_delta != 0
            or any(r.delta != 0 for r in self.retained)
        )


def compare_recommendations(
    today: list[dict],
    yesterday: list[dict],
    today_market_score: int = 5,
    yesterday_market_score: int = 5,
) -> ReportDiff:
    """오늘과 어제의 추천 목록을 비교한다.

    Args:
        today: [{"ticker": "AAPL", "name": "Apple Inc.", "rank": 1}, ...]
        yesterday: 동일 구조.
        today_market_score: 오늘 시장 점수.
        yesterday_market_score: 어제 시장 점수.

    Returns:
        ReportDiff 비교 결과.
    """
    today_map = {r["ticker"]: r for r in today}
    yesterday_map = {r["ticker"]: r for r in yesterday}

    today_tickers = set(today_map.keys())
    yesterday_tickers = set(yesterday_map.keys())

    new_entries = sorted(today_tickers - yesterday_tickers)
    dropped = sorted(yesterday_tickers - today_tickers)

    retained = []
    for ticker in sorted(today_tickers & yesterday_tickers):
        retained.append(RankChange(
            ticker=ticker,
            name=today_map[ticker].get("name", ticker),
            prev_rank=yesterday_map[ticker]["rank"],
            curr_rank=today_map[ticker]["rank"],
        ))

    return ReportDiff(
        new_entries=new_entries,
        dropped=dropped,
        retained=retained,
        market_score_delta=today_market_score - yesterday_market_score,
        today_count=len(today),
        yesterday_count=len(yesterday),
    )


def format_diff_summary(diff: ReportDiff) -> str:
    """비교 결과를 한국어 요약 문자열로 반환한다."""
    if not diff.has_changes:
        return "어제와 동일한 추천입니다."

    parts = []

    if diff.new_entries:
        parts.append(f"신규: {', '.join(diff.new_entries)}")

    if diff.dropped:
        parts.append(f"탈락: {', '.join(diff.dropped)}")

    rank_ups = [r for r in diff.retained if r.delta > 0]
    rank_downs = [r for r in diff.retained if r.delta < 0]

    if rank_ups:
        up_str = ", ".join(f"{r.ticker}(+{r.delta})" for r in rank_ups)
        parts.append(f"순위 상승: {up_str}")

    if rank_downs:
        down_str = ", ".join(f"{r.ticker}({r.delta})" for r in rank_downs)
        parts.append(f"순위 하락: {down_str}")

    if diff.market_score_delta != 0:
        direction = "개선" if diff.market_score_delta > 0 else "악화"
        parts.append(f"시장 점수 {direction} ({diff.market_score_delta:+d})")

    return " | ".join(parts)
