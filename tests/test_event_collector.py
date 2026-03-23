"""이벤트 캘린더 테스트."""

from datetime import date

from src.data.event_collector import FOMC_DATES_2026, get_next_fomc_date


class TestFOMC:
    def test_next_fomc_from_jan(self):
        result = get_next_fomc_date(date(2026, 1, 1))
        assert result is not None
        fomc_date, days = result
        assert fomc_date == date(2026, 1, 28)
        assert days == 27

    def test_next_fomc_from_mid_year(self):
        result = get_next_fomc_date(date(2026, 6, 1))
        assert result is not None
        fomc_date, _ = result
        assert fomc_date == date(2026, 6, 17)

    def test_fomc_past_all(self):
        """모든 FOMC 이후 → None."""
        result = get_next_fomc_date(date(2027, 1, 1))
        assert result is None

    def test_fomc_on_date(self):
        """FOMC 당일 → 해당 FOMC."""
        result = get_next_fomc_date(date(2026, 3, 18))
        assert result is not None
        assert result[0] == date(2026, 3, 18)
        assert result[1] == 0
