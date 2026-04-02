"""반사실 분석 테스트 — Phase 6."""

from datetime import date

from sqlalchemy import create_engine

from src.ai.counterfactual import (
    CounterfactualResult,
    compute_counterfactuals,
    format_counterfactuals_for_prompt,
)
from src.db.engine import create_session_factory
from src.db.helpers import date_to_id
from src.db.models import (
    Base,
    DimDate,
    DimMarket,
    DimSector,
    DimStock,
    FactCounterfactual,
    FactDailyRecommendation,
)


def _make_session():
    engine = create_engine("sqlite:///:memory:")

    from sqlalchemy import event as sa_event

    @sa_event.listens_for(engine, "connect")
    def _set_fk(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    session.add(
        DimMarket(
            market_id=1,
            code="US",
            name="미국",
            currency="USD",
            timezone="America/New_York",
        )
    )
    session.add(DimSector(sector_id=1, sector_name="Technology"))
    session.add(
        DimStock(
            stock_id=1,
            ticker="AAPL",
            name="Apple",
            market_id=1,
            sector_id=1,
            is_sp500=True,
        )
    )
    session.add(
        DimStock(
            stock_id=2,
            ticker="MSFT",
            name="Microsoft",
            market_id=1,
            sector_id=1,
            is_sp500=True,
        )
    )
    session.add(
        DimStock(
            stock_id=3,
            ticker="GOOG",
            name="Alphabet",
            market_id=1,
            sector_id=1,
            is_sp500=True,
        )
    )
    d = date(2026, 3, 1)
    did = date_to_id(d)
    session.add(
        DimDate(
            date_id=did,
            date=d,
            year=2026,
            quarter=1,
            month=3,
            week_of_year=9,
            day_of_week=0,
            is_trading_day=True,
        )
    )
    session.flush()
    session.commit()
    return session


class TestCounterfactualResult:
    def test_frozen_dataclass(self):
        r = CounterfactualResult(
            ticker="AAPL",
            original_decision="excluded",
            original_return=5.0,
            counterfactual_return=5.0,
            delta=5.0,
            lesson="test lesson",
        )
        assert r.ticker == "AAPL"
        assert r.original_decision == "excluded"
        assert r.original_return == 5.0
        assert r.delta == 5.0
        assert r.lesson == "test lesson"

    def test_frozen_immutable(self):
        r = CounterfactualResult(
            ticker="AAPL",
            original_decision="excluded",
            original_return=5.0,
            counterfactual_return=5.0,
            delta=5.0,
            lesson="test",
        )
        try:
            r.ticker = "MSFT"  # type: ignore[misc]
            assert False, "Should raise FrozenInstanceError"
        except AttributeError:
            pass


class TestComputeCounterfactuals:
    def test_high_score_excluded_stock_that_rose(self):
        """Case 1: AI가 고득점 종목을 거부했으나 실제로 상승 (missed opportunity)."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(
            FactDailyRecommendation(
                run_date_id=did,
                stock_id=1,
                rank=1,
                total_score=8.0,
                technical_score=8.0,
                fundamental_score=7.0,
                smart_money_score=7.0,
                external_score=7.0,
                momentum_score=8.0,
                recommendation_reason="high score test",
                price_at_recommendation=150.0,
                ai_approved=False,
                return_20d=10.0,
            )
        )
        session.commit()

        results = compute_counterfactuals(session, did)
        assert len(results) == 1
        r = results[0]
        assert r.ticker == "AAPL"
        assert r.original_decision == "excluded"
        assert r.delta == 10.0
        assert "거부했으나" in r.lesson
        assert "+10.0%" in r.lesson
        session.close()

    def test_high_score_excluded_stock_that_fell(self):
        """Case 1: AI가 고득점 종목을 거부했고 실제로 하락 (correct decision)."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(
            FactDailyRecommendation(
                run_date_id=did,
                stock_id=1,
                rank=1,
                total_score=7.5,
                technical_score=8.0,
                fundamental_score=7.0,
                smart_money_score=6.0,
                external_score=7.0,
                momentum_score=7.0,
                recommendation_reason="correct exclusion",
                price_at_recommendation=150.0,
                ai_approved=False,
                return_20d=-5.0,
            )
        )
        session.commit()

        results = compute_counterfactuals(session, did)
        assert len(results) == 1
        r = results[0]
        assert r.original_decision == "excluded"
        assert r.delta == -5.0
        assert "올바른 판단" in r.lesson
        session.close()

    def test_low_score_approved_stock_that_fell(self):
        """Case 2: AI가 저득점 종목을 승인했으나 실제로 하락 (bad approval)."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(
            FactDailyRecommendation(
                run_date_id=did,
                stock_id=2,
                rank=2,
                total_score=5.0,
                technical_score=5.0,
                fundamental_score=5.0,
                smart_money_score=5.0,
                external_score=5.0,
                momentum_score=5.0,
                recommendation_reason="low score test",
                price_at_recommendation=300.0,
                ai_approved=True,
                return_20d=-8.0,
            )
        )
        session.commit()

        results = compute_counterfactuals(session, did)
        assert len(results) == 1
        r = results[0]
        assert r.ticker == "MSFT"
        assert r.original_decision == "approved"
        assert r.delta == 8.0  # avoided loss is positive
        assert "승인했으나" in r.lesson
        assert "-8.0%" in r.lesson
        session.close()

    def test_low_score_approved_stock_that_rose(self):
        """Case 2: AI가 저득점 종목을 승인했고 실제로 상승 (correct approval)."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(
            FactDailyRecommendation(
                run_date_id=did,
                stock_id=2,
                rank=2,
                total_score=5.5,
                technical_score=5.0,
                fundamental_score=5.0,
                smart_money_score=5.0,
                external_score=5.0,
                momentum_score=5.0,
                recommendation_reason="correct approval",
                price_at_recommendation=300.0,
                ai_approved=True,
                return_20d=3.0,
            )
        )
        session.commit()

        results = compute_counterfactuals(session, did)
        assert len(results) == 1
        r = results[0]
        assert r.original_decision == "approved"
        assert r.delta == 0.0  # no avoided loss
        assert "올바른 판단" in r.lesson
        session.close()

    def test_empty_when_no_data(self):
        """데이터가 없을 때 빈 리스트를 반환."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        results = compute_counterfactuals(session, did)
        assert results == []
        session.close()

    def test_skips_mid_score_stocks(self):
        """중간 점수(6.0 <= score < 7.0) 종목은 반사실 분석 대상이 아님."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(
            FactDailyRecommendation(
                run_date_id=did,
                stock_id=1,
                rank=1,
                total_score=6.5,
                technical_score=6.5,
                fundamental_score=6.5,
                smart_money_score=6.5,
                external_score=6.5,
                momentum_score=6.5,
                recommendation_reason="mid score",
                price_at_recommendation=150.0,
                ai_approved=False,
                return_20d=5.0,
            )
        )
        session.commit()

        results = compute_counterfactuals(session, did)
        assert results == []
        session.close()

    def test_limits_to_top_10(self):
        """결과가 10개를 초과하면 상위 10개만 반환."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        # Create 15 stocks
        for i in range(4, 19):
            session.add(
                DimStock(
                    stock_id=i,
                    ticker=f"T{i:03d}",
                    name=f"Stock {i}",
                    market_id=1,
                    sector_id=1,
                    is_sp500=True,
                )
            )
        session.flush()

        # Create 15 high-score excluded recs with positive returns
        for i in range(1, 16):
            stock_id = i if i <= 3 else i + 1
            session.add(
                FactDailyRecommendation(
                    run_date_id=did,
                    stock_id=stock_id,
                    rank=i,
                    total_score=8.0,
                    technical_score=8.0,
                    fundamental_score=7.0,
                    smart_money_score=7.0,
                    external_score=7.0,
                    momentum_score=8.0,
                    recommendation_reason=f"test {i}",
                    price_at_recommendation=100.0,
                    ai_approved=False,
                    return_20d=float(i),
                )
            )
        session.commit()

        results = compute_counterfactuals(session, did)
        assert len(results) == 10
        # Sorted by absolute delta descending
        assert abs(results[0].delta or 0) >= abs(results[-1].delta or 0)
        session.close()

    def test_skips_records_without_return_20d(self):
        """return_20d가 None인 레코드는 건너뜀."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        session.add(
            FactDailyRecommendation(
                run_date_id=did,
                stock_id=1,
                rank=1,
                total_score=8.0,
                technical_score=8.0,
                fundamental_score=7.0,
                smart_money_score=7.0,
                external_score=7.0,
                momentum_score=8.0,
                recommendation_reason="no return",
                price_at_recommendation=150.0,
                ai_approved=False,
                return_20d=None,
            )
        )
        session.commit()

        results = compute_counterfactuals(session, did)
        assert results == []
        session.close()


class TestFormatCounterfactualsForPrompt:
    def test_renders_text(self):
        results = [
            CounterfactualResult(
                ticker="AAPL",
                original_decision="excluded",
                original_return=10.0,
                counterfactual_return=10.0,
                delta=10.0,
                lesson="AAPL: 고득점(8.0) 종목을 거부했으나 +10.0% 상승.",
            ),
            CounterfactualResult(
                ticker="MSFT",
                original_decision="approved",
                original_return=-5.0,
                counterfactual_return=0.0,
                delta=5.0,
                lesson="MSFT: 저득점(5.0) 종목을 승인했으나 -5.0% 하락.",
            ),
        ]
        text = format_counterfactuals_for_prompt(results)
        assert text is not None
        assert "반사실" in text
        assert "AAPL" in text
        assert "MSFT" in text

    def test_returns_none_for_empty(self):
        assert format_counterfactuals_for_prompt([]) is None

    def test_limits_to_top_3(self):
        results = [
            CounterfactualResult(
                ticker=f"T{i}",
                original_decision="excluded",
                original_return=float(i),
                counterfactual_return=float(i),
                delta=float(i),
                lesson=f"Lesson {i}",
            )
            for i in range(5)
        ]
        text = format_counterfactuals_for_prompt(results)
        assert text is not None
        # Should contain exactly 3 numbered items
        assert "1." in text
        assert "2." in text
        assert "3." in text
        # T3 and T4 should not appear (only top 3)
        assert "Lesson 3" not in text
        assert "Lesson 4" not in text


class TestFactCounterfactualDB:
    def test_create_and_read(self):
        """FactCounterfactual DB 생성 및 조회."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        # First create a recommendation to reference
        session.add(
            FactDailyRecommendation(
                recommendation_id=1,
                run_date_id=did,
                stock_id=1,
                rank=1,
                total_score=8.0,
                technical_score=8.0,
                fundamental_score=7.0,
                smart_money_score=7.0,
                external_score=7.0,
                momentum_score=8.0,
                recommendation_reason="test",
                price_at_recommendation=150.0,
                ai_approved=False,
                return_20d=10.0,
            )
        )
        session.flush()

        cf = FactCounterfactual(
            run_date_id=did,
            recommendation_id=1,
            ticker="AAPL",
            original_decision="excluded",
            original_return=10.0,
            counterfactual_return=10.0,
            delta=10.0,
            lesson_text="고득점 종목 거부 — missed opportunity",
        )
        session.add(cf)
        session.commit()

        saved = session.query(FactCounterfactual).first()
        assert saved is not None
        assert saved.ticker == "AAPL"
        assert saved.original_decision == "excluded"
        assert float(saved.original_return) == 10.0
        assert float(saved.delta) == 10.0
        assert saved.lesson_text == "고득점 종목 거부 — missed opportunity"
        assert saved.run_date_id == did
        session.close()

    def test_cascade_delete(self):
        """추천 삭제 시 반사실 레코드도 CASCADE 삭제."""
        session = _make_session()
        d = date(2026, 3, 1)
        did = date_to_id(d)

        rec = FactDailyRecommendation(
            recommendation_id=99,
            run_date_id=did,
            stock_id=1,
            rank=1,
            total_score=8.0,
            technical_score=8.0,
            fundamental_score=7.0,
            smart_money_score=7.0,
            external_score=7.0,
            momentum_score=8.0,
            recommendation_reason="test",
            price_at_recommendation=150.0,
        )
        session.add(rec)
        session.flush()

        cf = FactCounterfactual(
            run_date_id=did,
            recommendation_id=99,
            ticker="AAPL",
            original_decision="excluded",
        )
        session.add(cf)
        session.commit()

        # Delete the recommendation
        session.delete(rec)
        session.commit()

        # Counterfactual should also be deleted
        remaining = session.query(FactCounterfactual).all()
        assert len(remaining) == 0
        session.close()
