"""시장 레짐 감지 모듈 테스트."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from src.analysis.regime import (
    REGIME_WEIGHTS,
    MarketRegime,
    _calculate_sma,
    _classify_regime,
    detect_regime,
)
from src.db.models import FactMacroIndicator


def _insert_macro_rows(
    session: Session,
    count: int,
    sp500_base: float = 5000.0,
    vix_base: float = 18.0,
    sp500_trend: float = 0.0,
) -> None:
    """매크로 데이터 행을 삽입한다.

    실제 유효 날짜를 사용하여 dim_date FK 제약을 충족한다.
    sp500_trend > 0이면 시간이 지남에 따라 가격 상승 (강세).
    """
    from datetime import date, timedelta

    base_date = date(2025, 1, 2)  # 목요일, 거래일
    for i in range(count):
        d = base_date + timedelta(days=i)
        date_id = d.year * 10000 + d.month * 100 + d.day
        sp500_price = sp500_base + (sp500_trend * i)
        row = FactMacroIndicator(
            date_id=date_id,
            vix=vix_base,
            sp500_close=sp500_price,
            us_10y_yield=4.0,
            us_13w_yield=3.5,
        )
        session.add(row)
    session.flush()


class TestClassifyRegime:
    """_classify_regime 단위 테스트 (DB 불필요)."""

    def test_bull_detection(self):
        """S&P 500 > SMA50 > SMA200, VIX < 20 -> bull."""
        result = _classify_regime(
            sp500=5200.0, vix=15.0, sma50=5100.0, sma200=5000.0,
        )
        assert result.regime == "bull"
        assert 0.0 <= result.confidence <= 1.0

    def test_bear_detection(self):
        """S&P 500 < SMA200, VIX > 25 -> bear."""
        result = _classify_regime(
            sp500=4800.0, vix=28.0, sma50=5000.0, sma200=5100.0,
        )
        assert result.regime == "bear"
        assert 0.0 <= result.confidence <= 1.0

    def test_crisis_detection(self):
        """VIX > 30 AND S&P 500 < SMA50 -> crisis."""
        result = _classify_regime(
            sp500=4700.0, vix=35.0, sma50=5000.0, sma200=5100.0,
        )
        assert result.regime == "crisis"
        assert 0.0 <= result.confidence <= 1.0

    def test_range_detection_mixed_signals(self):
        """혼합 신호 -> range."""
        # S&P 500 > SMA50 but VIX > 20 (bull 조건 불충족)
        result = _classify_regime(
            sp500=5100.0, vix=22.0, sma50=5000.0, sma200=4900.0,
        )
        assert result.regime == "range"
        assert 0.0 <= result.confidence <= 1.0

    def test_range_no_sma(self):
        """SMA 없으면 range fallback."""
        result = _classify_regime(
            sp500=5000.0, vix=18.0, sma50=None, sma200=None,
        )
        assert result.regime == "range"

    def test_crisis_takes_priority_over_bear(self):
        """VIX > 30이고 sp500 < sma50이면 bear보다 crisis 우선."""
        result = _classify_regime(
            sp500=4700.0, vix=32.0, sma50=5000.0, sma200=5100.0,
        )
        assert result.regime == "crisis"

    def test_bull_requires_all_conditions(self):
        """Bull은 sp500 > sma50 > sma200 AND VIX < 20 전부 필요."""
        # VIX가 21로 bull 조건 불충족
        result = _classify_regime(
            sp500=5200.0, vix=21.0, sma50=5100.0, sma200=5000.0,
        )
        assert result.regime != "bull"

    def test_confidence_bounded(self):
        """신뢰도는 0.0 ~ 1.0 범위."""
        for sp500, vix, sma50, sma200 in [
            (5200, 15, 5100, 5000),   # bull
            (4800, 28, 5000, 5100),   # bear
            (4700, 35, 5000, 5100),   # crisis
            (5100, 22, 5000, 4900),   # range
            (3000, 80, 5000, 5100),   # extreme crisis
            (6000, 10, 5500, 5000),   # extreme bull
        ]:
            result = _classify_regime(sp500, vix, sma50, sma200)
            assert 0.0 <= result.confidence <= 1.0, (
                f"regime={result.regime}, confidence={result.confidence}"
            )


class TestDetectRegimeWithDB:
    """detect_regime DB 통합 테스트."""

    def test_no_data_returns_range(self, session):
        """매크로 데이터 없으면 range fallback."""
        result = detect_regime(session)
        assert result.regime == "range"
        assert result.confidence == 0.3

    def test_insufficient_data_for_sma(self, seeded_session):
        """데이터가 50개 미만이면 SMA 계산 불가 -> range."""
        _insert_macro_rows(seeded_session, 30, sp500_base=5000.0, vix_base=15.0)
        result = detect_regime(seeded_session)
        # SMA50/200 계산 불가 -> range
        assert result.regime == "range"

    def test_bull_with_enough_data(self, seeded_session):
        """200일 이상 상승 추세 데이터 -> bull."""
        # 상승 추세: 가격이 꾸준히 오름 -> 현재 > SMA50 > SMA200
        _insert_macro_rows(
            seeded_session, 200,
            sp500_base=4000.0, vix_base=15.0, sp500_trend=5.0,
        )
        result = detect_regime(seeded_session)
        assert result.regime == "bull"

    def test_crisis_with_high_vix(self, seeded_session):
        """VIX 35 + 하락 추세 -> crisis."""
        # 하락 추세: 가격이 꾸준히 하락
        _insert_macro_rows(
            seeded_session, 60,
            sp500_base=5000.0, vix_base=35.0, sp500_trend=-10.0,
        )
        result = detect_regime(seeded_session)
        assert result.regime == "crisis"

    def test_no_sp500_returns_range(self, seeded_session):
        """S&P 500 없으면 range fallback."""
        row = FactMacroIndicator(
            date_id=20250101, vix=18.0, sp500_close=None,
        )
        seeded_session.add(row)
        seeded_session.flush()
        result = detect_regime(seeded_session)
        assert result.regime == "range"
        assert result.confidence == 0.3


class TestCalculateSMA:
    """_calculate_sma 단위 테스트."""

    def test_exact_period(self):
        prices = [100.0] * 50
        assert _calculate_sma(prices, 50) == 100.0

    def test_insufficient_data(self):
        prices = [100.0] * 10
        assert _calculate_sma(prices, 50) is None

    def test_uses_last_n(self):
        prices = [10.0] * 10 + [20.0] * 5
        result = _calculate_sma(prices, 5)
        assert result == 20.0


class TestRegimeWeights:
    """REGIME_WEIGHTS 검증."""

    @pytest.mark.parametrize("regime", ["bull", "bear", "range", "crisis"])
    def test_weights_sum_to_one(self, regime):
        """각 레짐의 가중치 합이 1.0."""
        total = sum(REGIME_WEIGHTS[regime].values())
        assert abs(total - 1.0) < 1e-10, f"{regime}: sum={total}"

    @pytest.mark.parametrize("regime", ["bull", "bear", "range", "crisis"])
    def test_weights_have_all_keys(self, regime):
        """모든 레짐에 5개 스코어 키가 존재."""
        expected_keys = {"technical", "fundamental", "smart_money", "external", "momentum"}
        assert set(REGIME_WEIGHTS[regime].keys()) == expected_keys

    @pytest.mark.parametrize("regime", ["bull", "bear", "range", "crisis"])
    def test_weights_positive(self, regime):
        """모든 가중치가 양수."""
        for key, val in REGIME_WEIGHTS[regime].items():
            assert val > 0, f"{regime}.{key}={val}"

    def test_bull_emphasizes_momentum(self):
        """강세장에서는 모멘텀 비중이 가장 높다."""
        w = REGIME_WEIGHTS["bull"]
        assert w["momentum"] == max(w.values())

    def test_bear_emphasizes_fundamental(self):
        """약세장에서는 펀더멘털 비중이 가장 높다."""
        w = REGIME_WEIGHTS["bear"]
        assert w["fundamental"] == max(w.values())

    def test_range_emphasizes_technical(self):
        """횡보장에서는 기술적 분석 비중이 가장 높다."""
        w = REGIME_WEIGHTS["range"]
        assert w["technical"] == max(w.values())

    def test_crisis_emphasizes_fundamental(self):
        """위기에서는 펀더멘털 비중이 가장 높다."""
        w = REGIME_WEIGHTS["crisis"]
        assert w["fundamental"] == max(w.values())


class TestMarketRegimeDataclass:
    """MarketRegime frozen dataclass 검증."""

    def test_immutable(self):
        regime = MarketRegime(regime="bull", confidence=0.8, description="테스트")
        with pytest.raises(AttributeError):
            regime.regime = "bear"

    def test_fields(self):
        regime = MarketRegime(regime="bear", confidence=0.6, description="약세")
        assert regime.regime == "bear"
        assert regime.confidence == 0.6
        assert regime.description == "약세"
