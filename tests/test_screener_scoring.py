"""스크리너 스코어링 함수 단위 테스트."""

import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

from src.analysis.screener import (
    _passes_filter,
    _passes_fundamental_filter,
    _score_technical,
    _score_fundamental,
    _score_smart_money,
    _score_momentum,
    _score_external,
    _generate_reason,
)


def _make_indicators_df(rsi=50.0, macd_hist=0.5, days=30):
    """테스트용 indicators DataFrame."""
    dates = pd.date_range("2026-01-01", periods=days, freq="B")
    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(days) * 0.5)
    return pd.DataFrame({
        "close": prices,
        "high": prices + 1,
        "low": prices - 1,
        "open": prices,
        "volume": [500000] * days,
        "rsi_14": [rsi] * days,
        "macd": [1.0] * days,
        "macd_signal": [0.5] * days,
        "macd_hist": [macd_hist] * days,
        "sma_5": prices,
        "sma_20": prices - 2,
        "sma_60": prices - 5,
        "sma_120": prices - 10,
        "volume_sma_20": [400000] * days,
        "bb_upper": prices + 5,
        "bb_middle": prices,
        "bb_lower": prices - 5,
        "stoch_k": [50.0] * days,
        "stoch_d": [50.0] * days,
    }, index=dates)


class TestWeightIntegrity:
    """스코어링 가중치 합계 검증."""

    def test_screener_weights_sum_to_1(self):
        from src.analysis.screener import (
            WEIGHT_TECHNICAL, WEIGHT_FUNDAMENTAL,
            WEIGHT_SMART_MONEY, WEIGHT_EXTERNAL, WEIGHT_MOMENTUM,
        )
        total = WEIGHT_TECHNICAL + WEIGHT_FUNDAMENTAL + WEIGHT_SMART_MONEY + WEIGHT_EXTERNAL + WEIGHT_MOMENTUM
        assert abs(total - 1.0) < 0.001

    def test_fundamental_weights_sum_to_1(self):
        from src.analysis.fundamental import _WEIGHTS
        total = sum(_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001


class TestSmartMoneyScoreCapping:
    """Smart Money 점수 상한 테스트."""

    def test_insider_ceo_capped_at_3(self):
        """내부자(CEO 포함) 기여분이 +3.0 이하."""
        session = MagicMock()
        latest = pd.Series({"close": 100.0})

        mock_trade = MagicMock()
        mock_trade.value = 1000000
        mock_trade.transaction_type = "Buy"
        mock_trade.insider_title = "CEO"
        mock_trade.date_id = 20260320

        with patch("src.db.repository.InsiderTradeRepository.get_by_stock", return_value=[mock_trade] * 10), \
             patch("src.db.repository.AnalystConsensusRepository.get_latest", return_value=None), \
             patch("src.db.repository.InstitutionalHoldingRepository.get_by_stock", return_value=[]):
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            session.execute.return_value = mock_result

            score = _score_smart_money(session, stock_id=1, latest=latest)
            # base 5.0 + insider(max 3.0) = 8.0 (not 10.0)
            assert score <= 8.5  # 내부자 + 약간의 다른 보너스 허용

    def test_score_never_exceeds_10(self):
        """모든 팩터 최대치에서도 10.0 초과 안 함."""
        session = MagicMock()
        latest = pd.Series({"close": 50.0})  # 목표가 대비 큰 upside

        mock_trade = MagicMock()
        mock_trade.value = 5000000
        mock_trade.transaction_type = "Buy"
        mock_trade.insider_title = "CEO"
        mock_trade.date_id = 20260320

        mock_consensus = MagicMock()
        mock_consensus.strong_buy = 10
        mock_consensus.buy = 5
        mock_consensus.hold = 0
        mock_consensus.sell = 0
        mock_consensus.strong_sell = 0
        mock_consensus.target_mean = 100.0  # 100% upside

        mock_val = MagicMock()
        mock_val.short_pct_of_float = 0.5  # 매우 낮음

        mock_holding = MagicMock()
        mock_holding.pct_of_shares = 15.0

        with patch("src.db.repository.InsiderTradeRepository.get_by_stock", return_value=[mock_trade] * 5), \
             patch("src.db.repository.AnalystConsensusRepository.get_latest", return_value=mock_consensus), \
             patch("src.db.repository.InstitutionalHoldingRepository.get_by_stock", return_value=[mock_holding] * 5):
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_val
            session.execute.return_value = mock_result

            score = _score_smart_money(session, stock_id=1, latest=latest)
            assert score <= 10.0


class TestMomentumLinear:
    """모멘텀 점수 선형 보간 테스트."""

    def test_linear_interpolation(self):
        """7.5% 수익률은 5%와 10% 사이 점수."""
        df = _make_indicators_df(days=30)
        # 20일 전 close = df["close"].iloc[-20]
        base_price = float(df["close"].iloc[-20])

        # 7.5% 수익
        target_price = base_price * 1.075
        latest = pd.Series({
            "close": target_price,
            "sma_5": target_price, "sma_20": target_price - 2,
            "sma_60": target_price - 5,
            "volume": 500000, "volume_sma_20": 400000,
        })
        score = _score_momentum(df, latest)
        assert 6.0 <= score <= 8.0  # 중간 수준

    def test_negative_return_linear(self):
        """-7.5% 수익률은 감점."""
        df = _make_indicators_df(days=30)
        base_price = float(df["close"].iloc[-20])
        target_price = base_price * 0.925
        latest = pd.Series({
            "close": target_price,
            "sma_5": target_price, "sma_20": target_price + 2,
            "sma_60": target_price + 5,
            "volume": 500000, "volume_sma_20": 400000,
        })
        score = _score_momentum(df, latest)
        assert score <= 5.0


class TestPassesFilterSMA120Relaxation:
    """SMA120 필터 완화 테스트."""

    def test_above_sma120_passes(self):
        """가격 > SMA120이면 통과."""
        latest = pd.Series({
            "volume": 200000, "rsi_14": 50.0,
            "sma_120": 100.0, "close": 105.0,
        })
        df = pd.DataFrame({"close": [100] * 70})
        assert _passes_filter(latest, df) is True

    def test_far_below_sma120_fails(self):
        """가격이 SMA120 대비 5% 이상 하회하면 제외."""
        latest = pd.Series({
            "volume": 200000, "rsi_14": 35.0,
            "sma_120": 100.0, "close": 93.0,  # -7%
        })
        df = pd.DataFrame({"close": [100] * 70})
        assert _passes_filter(latest, df) is False

    def test_slightly_below_with_oversold_passes(self):
        """SMA120 대비 -3% + RSI < 40이면 통과 (회복 초기)."""
        latest = pd.Series({
            "volume": 200000, "rsi_14": 35.0,
            "sma_120": 100.0, "close": 97.0,  # -3%
        })
        df = pd.DataFrame({"close": [100] * 70})
        assert _passes_filter(latest, df) is True

    def test_slightly_below_without_oversold_fails(self):
        """SMA120 대비 -3% + RSI > 40이면 제외."""
        latest = pd.Series({
            "volume": 200000, "rsi_14": 55.0,
            "sma_120": 100.0, "close": 97.0,  # -3%
        })
        df = pd.DataFrame({"close": [100] * 70})
        assert _passes_filter(latest, df) is False


class TestScoreMomentumDetailed:
    def test_strong_uptrend(self):
        df = _make_indicators_df(days=30)
        # 강한 상승: 20일 전 대비 +15%
        df["close"] = [100 + i * 0.5 for i in range(30)]
        latest = pd.Series({
            "close": 114.5,
            "sma_5": 113, "sma_20": 110, "sma_60": 105,
            "volume": 800000, "volume_sma_20": 400000,
        })
        score = _score_momentum(df, latest)
        assert score >= 7.0  # 강한 모멘텀

    def test_downtrend(self):
        df = _make_indicators_df(days=30)
        df["close"] = [130 - i * 0.5 for i in range(30)]
        latest = pd.Series({
            "close": 115.5,
            "sma_5": 116, "sma_20": 120, "sma_60": 125,
            "volume": 500000,
        })
        score = _score_momentum(df, latest)
        assert score <= 5.0  # 약한/하락 모멘텀

    def test_reverse_alignment_penalty(self):
        df = _make_indicators_df(days=30)
        latest = pd.Series({
            "close": 100,
            "sma_5": 98, "sma_20": 100, "sma_60": 102,  # 역배열
            "volume": 500000,
        })
        score = _score_momentum(df, latest)
        assert score <= 5.0


class TestScoreExternalDetailed:
    def test_high_market_score_plus_strong_sector(self):
        class MockSector:
            sector_name = "Energy"
        momentum = {"Energy": 9.0}
        score = _score_external(8, 0.0, momentum, MockSector())
        assert score >= 8.0

    def test_low_market_weak_sector(self):
        class MockSector:
            sector_name = "Consumer Staples"
        momentum = {"Consumer Staples": 2.0}
        score = _score_external(2, 0.0, momentum, MockSector())
        assert score <= 3.0


class TestGenerateReasonDetailed:
    def test_includes_specific_numbers(self):
        latest = pd.Series({
            "rsi_14": 32.0, "sma_5": 100, "sma_20": 95, "sma_60": 90,
            "macd_hist": 1.0, "close": 100,
        })
        reason = _generate_reason("AAPL", 8.0, 7.0, 5.0, 9.0, latest, 6.0)
        assert "AAPL" in reason
        assert "RSI" in reason or "과매도" in reason

    def test_macd_positive_mentioned(self):
        latest = pd.Series({
            "rsi_14": 55.0, "sma_5": 100, "sma_20": 100, "sma_60": 100,
            "macd_hist": 2.0, "close": 100,
        })
        reason = _generate_reason("TEST", 5.0, 5.0, 5.0, 5.0, latest)
        assert "MACD" in reason


class TestScoreTechnicalStochastic:
    """스토캐스틱 K/D가 기술적 점수에 반영되는지 테스트."""

    def test_stoch_oversold_boost(self):
        """K < 20이면 +0.5."""
        df = _make_indicators_df()
        df["stoch_k"] = 15.0
        df["stoch_d"] = 20.0
        session = MagicMock()
        score_low = _score_technical(df, session, stock_id=1)

        df2 = _make_indicators_df()
        df2["stoch_k"] = 50.0
        df2["stoch_d"] = 50.0
        score_mid = _score_technical(df2, session, stock_id=1)

        assert score_low > score_mid

    def test_stoch_overbought_penalty(self):
        """K > 80이면 -0.5."""
        df = _make_indicators_df()
        df["stoch_k"] = 85.0
        df["stoch_d"] = 80.0
        session = MagicMock()
        score_high = _score_technical(df, session, stock_id=1)

        df2 = _make_indicators_df()
        df2["stoch_k"] = 50.0
        df2["stoch_d"] = 50.0
        score_mid = _score_technical(df2, session, stock_id=1)

        assert score_high < score_mid

    def test_stoch_bullish_crossover(self):
        """K가 D를 상향 돌파 + K < 50 → +0.5."""
        df = _make_indicators_df()
        df["stoch_k"] = 35.0
        df["stoch_d"] = 30.0  # K > D, K < 50
        session = MagicMock()
        score = _score_technical(df, session, stock_id=1)

        df2 = _make_indicators_df()
        df2["stoch_k"] = 35.0
        df2["stoch_d"] = 40.0  # K < D
        score2 = _score_technical(df2, session, stock_id=1)

        assert score > score2


class TestScoreSmartMoneyShortInterest:
    """공매도 비율이 Smart Money 점수에 반영되는지 테스트."""

    def test_high_short_interest_penalty(self):
        """short_pct_of_float > 10% → -1.5."""
        session = MagicMock()
        latest = pd.Series({"close": 100.0})

        mock_val = MagicMock()
        mock_val.short_pct_of_float = 15.0

        with patch("src.db.repository.InsiderTradeRepository.get_by_stock", return_value=[]), \
             patch("src.db.repository.AnalystConsensusRepository.get_latest", return_value=None):

            # Mock the FactValuation query for short interest
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_val
            session.execute.return_value = mock_result

            score = _score_smart_money(session, stock_id=1, latest=latest)
            assert score < 5.0  # 기본 5.0에서 공매도 페널티

    def test_low_short_interest_bonus(self):
        """short_pct_of_float < 2% → +0.5."""
        session = MagicMock()
        latest = pd.Series({"close": 100.0})

        mock_val = MagicMock()
        mock_val.short_pct_of_float = 1.0

        with patch("src.db.repository.InsiderTradeRepository.get_by_stock", return_value=[]), \
             patch("src.db.repository.AnalystConsensusRepository.get_latest", return_value=None):

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = mock_val
            session.execute.return_value = mock_result

            score = _score_smart_money(session, stock_id=1, latest=latest)
            assert score >= 5.0  # 기본 5.0 + 보너스


class TestScoreFundamentalEarningsSurprise:
    """실적 서프라이즈가 Fundamental 점수에 반영되는지 테스트."""

    def test_earnings_beat_bonus(self):
        """3회 이상 beat → 점수 상승."""
        session = MagicMock()

        mock_fin = MagicMock()
        mock_fin.period = "2025Q4"
        mock_fin.revenue = 1000
        mock_fin.operating_income = 200
        mock_fin.net_income = 150
        mock_fin.total_assets = 5000
        mock_fin.total_liabilities = 2000
        mock_fin.total_equity = 3000
        mock_fin.operating_cashflow = 300

        # earnings surprise — 4분기 모두 beat
        mock_e = MagicMock()
        mock_e.surprise_pct = 5.0

        with patch("src.db.repository.FinancialRepository.get_by_stock", return_value=[mock_fin]), \
             patch("src.db.repository.EarningsSurpriseRepository.get_by_stock", return_value=[mock_e] * 4):

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            session.execute.return_value = mock_result

            score_with_beat = _score_fundamental(session, stock_id=1)

        # 비교: earnings 없을 때
        with patch("src.db.repository.FinancialRepository.get_by_stock", return_value=[mock_fin]), \
             patch("src.db.repository.EarningsSurpriseRepository.get_by_stock", return_value=[]):

            mock_result2 = MagicMock()
            mock_result2.scalar_one_or_none.return_value = None
            session.execute.return_value = mock_result2

            score_without = _score_fundamental(session, stock_id=1)

        assert score_with_beat > score_without

    def test_all_miss_penalty(self):
        """전부 miss → 점수 하락."""
        session = MagicMock()

        mock_fin = MagicMock()
        mock_fin.period = "2025Q4"
        mock_fin.revenue = 1000
        mock_fin.operating_income = 200
        mock_fin.net_income = 150
        mock_fin.total_assets = 5000
        mock_fin.total_liabilities = 2000
        mock_fin.total_equity = 3000
        mock_fin.operating_cashflow = 300

        mock_e = MagicMock()
        mock_e.surprise_pct = -3.0

        with patch("src.db.repository.FinancialRepository.get_by_stock", return_value=[mock_fin]), \
             patch("src.db.repository.EarningsSurpriseRepository.get_by_stock", return_value=[mock_e] * 4):

            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = None
            session.execute.return_value = mock_result

            score_with_miss = _score_fundamental(session, stock_id=1)

        with patch("src.db.repository.FinancialRepository.get_by_stock", return_value=[mock_fin]), \
             patch("src.db.repository.EarningsSurpriseRepository.get_by_stock", return_value=[]):

            mock_result2 = MagicMock()
            mock_result2.scalar_one_or_none.return_value = None
            session.execute.return_value = mock_result2

            score_without = _score_fundamental(session, stock_id=1)

        assert score_with_miss < score_without
