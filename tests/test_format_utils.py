"""숫자 포맷 유틸리티 테스트."""

from src.reports.format_utils import fmt_large_number


class TestFmtLargeNumber:
    def test_none(self):
        assert fmt_large_number(None) == "-"

    def test_trillion(self):
        assert fmt_large_number(1_500_000_000_000) == "1.5T"

    def test_billion(self):
        assert fmt_large_number(2_300_000_000) == "2.3B"

    def test_million(self):
        assert fmt_large_number(45_000_000) == "45.0M"

    def test_thousand(self):
        assert fmt_large_number(500_000) == "500.0K"

    def test_small_k(self):
        assert fmt_large_number(1234) == "1.2K"

    def test_small_number(self):
        assert fmt_large_number(999) == "999"

    def test_negative(self):
        result = fmt_large_number(-2_500_000_000)
        assert result == "-2.5B"

    def test_zero(self):
        assert fmt_large_number(0) == "0"
