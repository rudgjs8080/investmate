"""AI 보강 데이터 수집기 테스트."""

from src.ai.data_enricher import EnrichedStockData, compute_sector_per_averages


class TestComputeSectorPerAverages:
    def test_basic_calculation(self):
        data = [
            ("AAPL", "Technology", 25.0),
            ("MSFT", "Technology", 35.0),
            ("XOM", "Energy", 15.0),
        ]
        result = compute_sector_per_averages(data)
        assert "Technology" in result
        assert result["Technology"] == 30.0  # (25+35)/2
        assert result["Energy"] == 15.0

    def test_filters_extreme_per(self):
        data = [
            ("AAPL", "Technology", 25.0),
            ("TEST", "Technology", 500.0),  # > 200, filtered
        ]
        result = compute_sector_per_averages(data)
        assert result["Technology"] == 25.0  # 500 filtered

    def test_filters_negative_per(self):
        data = [
            ("AAPL", "Technology", 25.0),
            ("TEST", "Technology", -5.0),  # negative, filtered
        ]
        result = compute_sector_per_averages(data)
        assert result["Technology"] == 25.0

    def test_none_per_skipped(self):
        data = [
            ("AAPL", "Technology", None),
            ("MSFT", "Technology", 30.0),
        ]
        result = compute_sector_per_averages(data)
        assert result["Technology"] == 30.0

    def test_empty_input(self):
        assert compute_sector_per_averages([]) == {}


class TestEnrichedStockData:
    def test_frozen_dataclass(self):
        ed = EnrichedStockData(ticker="AAPL", high_52w=200.0, low_52w=120.0, beta=1.1)
        assert ed.ticker == "AAPL"
        assert ed.beta == 1.1
        assert ed.forward_per is None  # default

    def test_pct_calculations(self):
        ed = EnrichedStockData(
            ticker="TEST", pct_from_52w_high=-10.0, pct_from_52w_low=50.0,
        )
        assert ed.pct_from_52w_high == -10.0
        assert ed.pct_from_52w_low == 50.0
