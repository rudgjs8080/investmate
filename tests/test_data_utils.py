"""데이터 유틸리티 테스트."""

from __future__ import annotations

import pandas as pd
import pytest

from src.data.utils import extract_ticker_data, flatten_multiindex, safe_float


class TestSafeFloat:
    def test_valid_float(self):
        assert safe_float(42.5) == 42.5

    def test_none(self):
        assert safe_float(None) is None

    def test_nan(self):
        assert safe_float(float("nan")) is None

    def test_string_number(self):
        assert safe_float("3.14") == 3.14

    def test_invalid_string(self):
        assert safe_float("abc") is None

    def test_zero(self):
        assert safe_float(0) == 0.0

    def test_negative(self):
        assert safe_float(-5.5) == -5.5


class TestFlattenMultiindex:
    def test_single_level_unchanged(self):
        df = pd.DataFrame({"A": [1, 2], "B": [3, 4]})
        result = flatten_multiindex(df)
        assert list(result.columns) == ["A", "B"]

    def test_multiindex_flattened(self):
        arrays = [["AAPL", "AAPL", "MSFT", "MSFT"], ["Close", "Volume", "Close", "Volume"]]
        tuples = list(zip(*arrays))
        index = pd.MultiIndex.from_tuples(tuples)
        df = pd.DataFrame([[1, 2, 3, 4]], columns=index)
        result = flatten_multiindex(df)
        assert not hasattr(result.columns, "levels") or len(result.columns.levels) <= 1

    def test_returns_copy(self):
        """원본 DataFrame을 변경하지 않는다."""
        arrays = [["A", "B"], ["X", "Y"]]
        tuples = list(zip(*arrays))
        index = pd.MultiIndex.from_tuples(tuples)
        df = pd.DataFrame([[1, 2]], columns=index)
        result = flatten_multiindex(df)
        assert result is not df  # copy


class TestExtractTickerData:
    def test_single_ticker(self):
        df = pd.DataFrame({"Close": [100.0], "Volume": [1000]})
        result = extract_ticker_data(df, "AAPL", ["AAPL"])
        assert result is not None
        assert "Close" in result.columns

    def test_ticker_not_found(self):
        arrays = [["AAPL", "AAPL"], ["Close", "Volume"]]
        tuples = list(zip(*arrays))
        index = pd.MultiIndex.from_tuples(tuples)
        df = pd.DataFrame([[100, 1000]], columns=index)
        result = extract_ticker_data(df, "MSFT", ["AAPL", "MSFT"])
        assert result is None

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        result = extract_ticker_data(df, "AAPL", ["AAPL"])
        # 단일 티커이지만 비어있으면 None
        assert result is None
