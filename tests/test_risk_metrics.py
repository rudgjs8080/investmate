"""리스크 제어 시스템 테스트 — 섹터 캡 + 상관관계 필터 + 팩터 어트리뷰션."""

from __future__ import annotations

import logging

import pytest

from src.analysis.screener import (
    _apply_sector_cap,
    _compute_factor_attribution,
    _warn_factor_concentration,
    calculate_portfolio_beta,
)


class TestSectorCap:
    """섹터 집중도 제한 테스트."""

    def _make_cand(self, ticker: str, sector: str, score: float) -> dict:
        return {
            "stock_id": hash(ticker) % 10000,
            "ticker": ticker,
            "name": ticker,
            "sector_name": sector,
            "total_score": score,
        }

    def test_no_cap_needed(self):
        """모든 섹터 다양하면 변화 없음."""
        candidates = [
            self._make_cand("AAPL", "IT", 9.0),
            self._make_cand("XOM", "Energy", 8.5),
            self._make_cand("JNJ", "Health", 8.0),
            self._make_cand("JPM", "Financials", 7.5),
        ]
        result = _apply_sector_cap(candidates, max_per_sector=4)
        assert [c["ticker"] for c in result] == ["AAPL", "XOM", "JNJ", "JPM"]

    def test_cap_limits_sector(self):
        """단일 섹터 초과 시 후순위로 밀림."""
        candidates = [
            self._make_cand("AAPL", "IT", 9.0),
            self._make_cand("MSFT", "IT", 8.8),
            self._make_cand("GOOGL", "IT", 8.5),
            self._make_cand("XOM", "Energy", 8.3),
            self._make_cand("NVDA", "IT", 8.0),
            self._make_cand("CVX", "Energy", 7.5),
        ]
        result = _apply_sector_cap(candidates, max_per_sector=2)
        # IT 2개만 허용, GOOGL과 NVDA는 후순위
        first_four = [c["ticker"] for c in result[:4]]
        assert first_four == ["AAPL", "MSFT", "XOM", "CVX"]

    def test_cap_preserves_order(self):
        """점수 순서대로 섹터 캡이 적용됨."""
        candidates = [
            self._make_cand("AAPL", "IT", 9.5),
            self._make_cand("MSFT", "IT", 9.0),
            self._make_cand("GOOGL", "IT", 8.5),
            self._make_cand("META", "IT", 8.0),
            self._make_cand("XOM", "Energy", 7.5),
        ]
        result = _apply_sector_cap(candidates, max_per_sector=2)
        # AAPL, MSFT 선발 → XOM → GOOGL, META 후순위
        assert result[0]["ticker"] == "AAPL"
        assert result[1]["ticker"] == "MSFT"
        assert result[2]["ticker"] == "XOM"

    def test_cap_with_single_allowed(self):
        """섹터당 1개만 허용."""
        candidates = [
            self._make_cand("AAPL", "IT", 9.0),
            self._make_cand("MSFT", "IT", 8.5),
            self._make_cand("XOM", "Energy", 8.0),
            self._make_cand("CVX", "Energy", 7.5),
        ]
        result = _apply_sector_cap(candidates, max_per_sector=1)
        first_two = [c["ticker"] for c in result[:2]]
        assert first_two == ["AAPL", "XOM"]

    def test_empty_candidates(self):
        """빈 후보 리스트."""
        assert _apply_sector_cap([], max_per_sector=4) == []

    def test_overflow_appended(self):
        """초과 종목이 리스트 끝에 붙음."""
        candidates = [
            self._make_cand("AAPL", "IT", 9.0),
            self._make_cand("MSFT", "IT", 8.5),
            self._make_cand("GOOGL", "IT", 8.0),
        ]
        result = _apply_sector_cap(candidates, max_per_sector=2)
        assert len(result) == 3
        assert result[-1]["ticker"] == "GOOGL"


# ──────────────────────────────────────────
# Task 6-1: 팩터 어트리뷰션 테스트
# ──────────────────────────────────────────


class TestFactorAttribution:
    """팩터 어트리뷰션 비율 계산 테스트."""

    def test_attribution_sums_to_100(self):
        """각 팩터 기여율의 합이 ~100%이어야 한다."""
        weights = {
            "technical": 0.25,
            "fundamental": 0.25,
            "smart_money": 0.15,
            "external": 0.15,
            "momentum": 0.20,
        }
        tech, fund, smart, ext, mom = 7.0, 6.0, 5.0, 4.0, 8.0
        total = (
            tech * weights["technical"]
            + fund * weights["fundamental"]
            + smart * weights["smart_money"]
            + ext * weights["external"]
            + mom * weights["momentum"]
        )

        attr = _compute_factor_attribution(tech, fund, smart, ext, mom, weights, total)

        total_pct = sum(attr.values())
        assert 99.5 <= total_pct <= 100.5, f"합계 {total_pct}%가 ~100%가 아님"

    def test_attribution_equal_scores(self):
        """모든 점수가 동일하면 가중치 비율대로 분배."""
        weights = {
            "technical": 0.25,
            "fundamental": 0.25,
            "smart_money": 0.15,
            "external": 0.15,
            "momentum": 0.20,
        }
        score = 5.0
        total = score * sum(weights.values())

        attr = _compute_factor_attribution(
            score, score, score, score, score, weights, total,
        )

        assert attr["technical"] == 25.0
        assert attr["fundamental"] == 25.0
        assert attr["smart_money"] == 15.0
        assert attr["external"] == 15.0
        assert attr["momentum"] == 20.0

    def test_attribution_zero_total(self):
        """총점이 0이면 모든 기여율이 0."""
        weights = {
            "technical": 0.25,
            "fundamental": 0.25,
            "smart_money": 0.15,
            "external": 0.15,
            "momentum": 0.20,
        }
        attr = _compute_factor_attribution(0, 0, 0, 0, 0, weights, 0)

        assert all(v == 0.0 for v in attr.values())

    def test_attribution_dominant_factor(self):
        """한 팩터가 높은 점수면 해당 팩터의 기여율이 가장 높다."""
        weights = {
            "technical": 0.25,
            "fundamental": 0.25,
            "smart_money": 0.15,
            "external": 0.15,
            "momentum": 0.20,
        }
        # 기술적 10, 나머지 1
        tech, fund, smart, ext, mom = 10.0, 1.0, 1.0, 1.0, 1.0
        total = (
            tech * weights["technical"]
            + fund * weights["fundamental"]
            + smart * weights["smart_money"]
            + ext * weights["external"]
            + mom * weights["momentum"]
        )

        attr = _compute_factor_attribution(tech, fund, smart, ext, mom, weights, total)

        assert attr["technical"] > 50, "기술적 팩터가 지배적이어야 함"
        assert attr["technical"] > attr["fundamental"]

    def test_attribution_all_factors_present(self):
        """반환 딕셔너리에 5개 팩터 키가 모두 존재."""
        weights = {
            "technical": 0.25,
            "fundamental": 0.25,
            "smart_money": 0.15,
            "external": 0.15,
            "momentum": 0.20,
        }
        attr = _compute_factor_attribution(5, 5, 5, 5, 5, weights, 5.0)

        expected_keys = {"technical", "fundamental", "smart_money", "external", "momentum"}
        assert set(attr.keys()) == expected_keys


# ──────────────────────────────────────────
# Task 6-2: 포트폴리오 베타 테스트
# ──────────────────────────────────────────


class TestPortfolioBeta:
    """포트폴리오 베타 계산 테스트."""

    @staticmethod
    def _seed_market_and_stocks(session, stock_ids: list[int]) -> None:
        """베타 테스트용 최소 디멘션 데이터를 생성한다."""
        from src.db.models import DimMarket, DimStock

        market = DimMarket(
            market_id=1, code="US", name="미국", currency="USD", timezone="UTC",
        )
        session.merge(market)
        for sid in stock_ids:
            stock = DimStock(
                stock_id=sid, ticker=f"TEST{sid}", name=f"Test Stock {sid}",
                market_id=1, is_active=True, is_sp500=True,
            )
            session.merge(stock)
        session.flush()

    def test_beta_with_known_data(self, session):
        """알려진 데이터로 베타를 계산한다."""
        from datetime import date, timedelta

        from src.db.models import DimDate, FactDailyPrice, FactMacroIndicator

        self._seed_market_and_stocks(session, [1])

        base_date = date(2025, 1, 1)
        sp500_prices = [100.0 + i * 0.5 for i in range(62)]
        stock_prices = [50.0 + i * 0.3 for i in range(62)]

        for i in range(62):
            d = base_date + timedelta(days=i)
            did = int(d.strftime("%Y%m%d"))
            session.add(DimDate(
                date_id=did,
                date=d,
                year=d.year,
                quarter=(d.month - 1) // 3 + 1,
                month=d.month,
                week_of_year=1,
                day_of_week=d.weekday(),
                is_trading_day=True,
            ))
            session.add(FactMacroIndicator(
                date_id=did,
                sp500_close=sp500_prices[i],
            ))
            session.add(FactDailyPrice(
                stock_id=1,
                date_id=did,
                open=stock_prices[i],
                high=stock_prices[i] + 1,
                low=stock_prices[i] - 1,
                close=stock_prices[i],
                adj_close=stock_prices[i],
                volume=1000000,
            ))
        session.commit()

        beta = calculate_portfolio_beta(session, [1], lookback_days=60)

        assert beta is not None
        # 두 가격이 모두 선형 증가 → 베타는 양수이고 약 0.6 근처
        assert 0.0 < beta < 2.0

    def test_beta_empty_stock_ids(self, session):
        """빈 종목 리스트면 None 반환."""
        result = calculate_portfolio_beta(session, [], lookback_days=60)
        assert result is None

    def test_beta_insufficient_macro_data(self, session):
        """매크로 데이터 부족 시 None 반환."""
        result = calculate_portfolio_beta(session, [1], lookback_days=60)
        assert result is None

    def test_beta_multiple_stocks(self, session):
        """여러 종목의 평균 베타를 계산한다."""
        from datetime import date, timedelta

        from src.db.models import DimDate, FactDailyPrice, FactMacroIndicator

        self._seed_market_and_stocks(session, [10, 20])

        base_date = date(2025, 4, 1)
        sp500_prices = [200.0 + i * 1.0 for i in range(62)]
        stock1_prices = [100.0 + i * 2.0 for i in range(62)]  # 고베타
        stock2_prices = [100.0 + i * 0.2 for i in range(62)]  # 저베타

        for i in range(62):
            d = base_date + timedelta(days=i)
            did = int(d.strftime("%Y%m%d"))
            session.add(DimDate(
                date_id=did,
                date=d,
                year=d.year,
                quarter=2,
                month=d.month,
                week_of_year=14,
                day_of_week=d.weekday(),
                is_trading_day=True,
            ))
            session.add(FactMacroIndicator(date_id=did, sp500_close=sp500_prices[i]))
            for sid, prices in [(10, stock1_prices), (20, stock2_prices)]:
                session.add(FactDailyPrice(
                    stock_id=sid,
                    date_id=did,
                    open=prices[i],
                    high=prices[i] + 1,
                    low=prices[i] - 1,
                    close=prices[i],
                    adj_close=prices[i],
                    volume=1000000,
                ))
        session.commit()

        beta = calculate_portfolio_beta(session, [10, 20], lookback_days=60)

        assert beta is not None
        # 평균 베타 — 하나는 고베타, 하나는 저베타
        assert beta > 0


# ──────────────────────────────────────────
# Task 6-3: 팩터 집중 경고 테스트
# ──────────────────────────────────────────


class TestFactorConcentrationWarning:
    """팩터 집중 경고 로깅 테스트."""

    def test_warning_when_dominant_factor(self, caplog):
        """단일 팩터 > 50% 시 경고 로그가 발생한다."""
        candidates = [
            {
                "ticker": "AAPL",
                "factor_attribution": {
                    "technical": 60.0,
                    "fundamental": 10.0,
                    "smart_money": 10.0,
                    "external": 10.0,
                    "momentum": 10.0,
                },
            },
            {
                "ticker": "MSFT",
                "factor_attribution": {
                    "technical": 55.0,
                    "fundamental": 15.0,
                    "smart_money": 10.0,
                    "external": 10.0,
                    "momentum": 10.0,
                },
            },
        ]
        with caplog.at_level(logging.WARNING, logger="src.analysis.screener"):
            _warn_factor_concentration(candidates)

        assert any("팩터 집중 경고" in r.message for r in caplog.records)
        assert any("technical" in r.message for r in caplog.records)

    def test_no_warning_when_balanced(self, caplog):
        """모든 팩터가 균등하면 경고가 발생하지 않는다."""
        candidates = [
            {
                "ticker": "AAPL",
                "factor_attribution": {
                    "technical": 25.0,
                    "fundamental": 25.0,
                    "smart_money": 15.0,
                    "external": 15.0,
                    "momentum": 20.0,
                },
            },
            {
                "ticker": "MSFT",
                "factor_attribution": {
                    "technical": 25.0,
                    "fundamental": 25.0,
                    "smart_money": 15.0,
                    "external": 15.0,
                    "momentum": 20.0,
                },
            },
        ]
        with caplog.at_level(logging.WARNING, logger="src.analysis.screener"):
            _warn_factor_concentration(candidates)

        assert not any("팩터 집중 경고" in r.message for r in caplog.records)

    def test_warning_with_missing_attribution(self, caplog):
        """factor_attribution 없는 후보는 기본값 20% 적용."""
        candidates = [
            {"ticker": "AAPL"},
            {"ticker": "MSFT"},
        ]
        with caplog.at_level(logging.WARNING, logger="src.analysis.screener"):
            _warn_factor_concentration(candidates)

        # 모든 팩터가 20% → 경고 없음
        assert not any("팩터 집중 경고" in r.message for r in caplog.records)
