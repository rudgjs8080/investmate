"""데이터 접근 레이어 — Star Schema Repository 패턴."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import (
    BridgeNewsStock,
    DimIndicatorType,
    DimMarket,
    DimSector,
    DimSignalType,
    DimStock,
    FactAnalystConsensus,
    FactCollectionLog,
    FactDailyPrice,
    FactDailyRecommendation,
    FactEarningsSurprise,
    FactFinancial,
    FactIndicatorValue,
    FactInsiderTrade,
    FactInstitutionalHolding,
    FactMacroIndicator,
    FactFactorReturn,
    FactNews,
    FactSignal,
    FactValuation,
)


class StockRepository:
    """종목 마스터 CRUD."""

    @staticmethod
    def add(session: Session, ticker: str, name: str, market_id: int,
            sector_id: int | None = None, is_sp500: bool = False) -> DimStock:
        stock = DimStock(
            ticker=ticker.upper(), name=name, market_id=market_id,
            sector_id=sector_id, is_sp500=is_sp500,
        )
        session.add(stock)
        session.flush()
        return stock

    @staticmethod
    def get_by_ticker(session: Session, ticker: str) -> DimStock | None:
        stmt = select(DimStock).where(DimStock.ticker == ticker.upper())
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def get_sp500_active(session: Session) -> list[DimStock]:
        stmt = (
            select(DimStock)
            .where(DimStock.is_sp500.is_(True), DimStock.is_active.is_(True))
            .order_by(DimStock.ticker)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_all(session: Session) -> list[DimStock]:
        stmt = select(DimStock).order_by(DimStock.ticker)
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def resolve_market_id(session: Session, code: str) -> int | None:
        stmt = select(DimMarket.market_id).where(DimMarket.code == code)
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def resolve_sector_id(
        session: Session, sector_name: str,
        industry_group: str | None = None,
        industry: str | None = None,
    ) -> int:
        """섹터 ID를 조회하거나 없으면 생성한다."""
        stmt = select(DimSector).where(DimSector.sector_name == sector_name)
        if industry:
            stmt = stmt.where(DimSector.industry == industry)
        sector = session.execute(stmt).scalar_one_or_none()
        if sector is not None:
            return sector.sector_id

        new_sector = DimSector(
            sector_name=sector_name,
            industry_group=industry_group,
            industry=industry,
        )
        session.add(new_sector)
        session.flush()
        return new_sector.sector_id


class DailyPriceRepository:
    """일봉 데이터 CRUD + UPSERT."""

    @staticmethod
    def upsert_prices_batch(
        session: Session, stock_id: int, prices: list[dict]
    ) -> int:
        """일봉 데이터를 배치 UPSERT한다. date_id는 자동 처리."""
        if not prices:
            return 0

        # date → date_id 매핑
        dates = [p["date"] for p in prices if isinstance(p.get("date"), date)]
        date_map = ensure_date_ids(session, dates)

        now = datetime.now()
        rows = []
        for price in prices:
            d = price.pop("date", None)
            if d is None:
                continue
            did = date_map.get(d) or date_to_id(d)
            rows.append({"stock_id": stock_id, "date_id": did, **price})

        if not rows:
            return 0

        # chunk 단위 배치 INSERT
        for i in range(0, len(rows), 500):
            chunk = rows[i : i + 500]
            stmt = sqlite_insert(FactDailyPrice).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "date_id"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "adj_close": stmt.excluded.adj_close,
                    "volume": stmt.excluded.volume,
                    "updated_at": now,
                },
            )
            session.execute(stmt)

        session.flush()
        return len(rows)

    @staticmethod
    def get_last_date(session: Session, stock_id: int) -> date | None:
        """해당 종목의 마지막 수집 날짜를 반환한다."""
        from src.db.models import DimDate

        stmt = (
            select(DimDate.date)
            .join(FactDailyPrice, FactDailyPrice.date_id == DimDate.date_id)
            .where(FactDailyPrice.stock_id == stock_id)
            .order_by(DimDate.date.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def get_prices(
        session: Session, stock_id: int,
        start_date: date | None = None, end_date: date | None = None,
    ) -> list[FactDailyPrice]:
        from src.db.models import DimDate

        stmt = (
            select(FactDailyPrice)
            .join(DimDate, FactDailyPrice.date_id == DimDate.date_id)
            .where(FactDailyPrice.stock_id == stock_id)
            .order_by(DimDate.date)
        )
        if start_date is not None:
            stmt = stmt.where(DimDate.date >= start_date)
        if end_date is not None:
            stmt = stmt.where(DimDate.date <= end_date)

        return list(session.execute(stmt).scalars().all())


class IndicatorValueRepository:
    """기술적 지표 EAV CRUD."""

    _BATCH_CHUNK_SIZE = 500

    @staticmethod
    def upsert_values(
        session: Session, stock_id: int, records: list[dict],
        *, auto_flush: bool = True,
    ) -> int:
        """EAV 지표 값을 배치 UPSERT한다. 각 dict는 {date_id, indicator_type_id, value}."""
        if not records:
            return 0

        now = datetime.now()
        rows = [{"stock_id": stock_id, **rec} for rec in records]

        # chunk 단위로 배치 INSERT
        chunk_size = IndicatorValueRepository._BATCH_CHUNK_SIZE
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i : i + chunk_size]
            stmt = sqlite_insert(FactIndicatorValue).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "date_id", "indicator_type_id"],
                set_={
                    "value": stmt.excluded.value,
                    "updated_at": now,
                },
            )
            session.execute(stmt)

        if auto_flush:
            session.flush()
        return len(rows)

    @staticmethod
    def get_indicator_type_map(session: Session) -> dict[str, int]:
        """code → indicator_type_id 매핑을 반환한다."""
        stmt = select(DimIndicatorType.code, DimIndicatorType.indicator_type_id)
        rows = session.execute(stmt).all()
        return {code: tid for code, tid in rows}

    @staticmethod
    def get_latest_for_stock(
        session: Session, stock_id: int, date_id: int,
    ) -> dict[str, float]:
        """해당 날짜 이하에서 가장 최근 지표 값을 {code: value} 형태로 반환한다.

        파이프라인 실행일(run_date_id)과 실제 거래일이 다를 수 있으므로
        date_id 이하에서 가장 최근 거래일의 지표를 조회한다.
        """
        from sqlalchemy import func

        # 해당 종목의 date_id 이하 최신 거래일 찾기
        latest_date_stmt = (
            select(func.max(FactIndicatorValue.date_id))
            .where(
                FactIndicatorValue.stock_id == stock_id,
                FactIndicatorValue.date_id <= date_id,
            )
        )
        latest_date = session.execute(latest_date_stmt).scalar_one_or_none()
        if latest_date is None:
            return {}

        stmt = (
            select(DimIndicatorType.code, FactIndicatorValue.value)
            .join(
                DimIndicatorType,
                FactIndicatorValue.indicator_type_id == DimIndicatorType.indicator_type_id,
            )
            .where(
                FactIndicatorValue.stock_id == stock_id,
                FactIndicatorValue.date_id == latest_date,
            )
        )
        rows = session.execute(stmt).all()
        return {code: float(val) for code, val in rows}


class FinancialRepository:
    """원본 재무제표 CRUD."""

    @staticmethod
    def upsert(session: Session, stock_id: int, records: list[dict]) -> int:
        if not records:
            return 0

        count = 0
        for rec in records:
            stmt = sqlite_insert(FactFinancial).values(stock_id=stock_id, **rec)
            update_fields = {
                k: getattr(stmt.excluded, k) for k in rec if k != "period"
            }
            update_fields["updated_at"] = datetime.now()
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "period"],
                set_=update_fields,
            )
            session.execute(stmt)
            count += 1

        session.flush()
        return count

    @staticmethod
    def get_by_stock(session: Session, stock_id: int) -> list[FactFinancial]:
        stmt = (
            select(FactFinancial)
            .where(FactFinancial.stock_id == stock_id)
            .order_by(FactFinancial.period.desc())
        )
        return list(session.execute(stmt).scalars().all())


class ValuationRepository:
    """파생 밸류에이션 CRUD."""

    @staticmethod
    def upsert(session: Session, stock_id: int, records: list[dict]) -> int:
        if not records:
            return 0

        count = 0
        for rec in records:
            stmt = sqlite_insert(FactValuation).values(stock_id=stock_id, **rec)
            update_fields = {
                k: getattr(stmt.excluded, k) for k in rec if k != "date_id"
            }
            update_fields["updated_at"] = datetime.now()
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "date_id"],
                set_=update_fields,
            )
            session.execute(stmt)
            count += 1

        session.flush()
        return count


    @staticmethod
    def get_latest_all(session: Session) -> dict[int, "FactValuation"]:
        """전 종목의 최신 밸류에이션을 배치 로드한다.

        Returns:
            {stock_id: FactValuation} 딕셔너리.
        """
        from sqlalchemy import func
        subq = (
            select(
                FactValuation.stock_id,
                func.max(FactValuation.date_id).label("max_did"),
            )
            .group_by(FactValuation.stock_id)
            .subquery()
        )
        stmt = (
            select(FactValuation)
            .join(subq, (FactValuation.stock_id == subq.c.stock_id)
                  & (FactValuation.date_id == subq.c.max_did))
        )
        rows = session.execute(stmt).scalars().all()
        return {v.stock_id: v for v in rows}


class SignalRepository:
    """시그널 CRUD."""

    @staticmethod
    def get_signal_type_map(session: Session) -> dict[str, int]:
        """code → signal_type_id 매핑."""
        stmt = select(DimSignalType.code, DimSignalType.signal_type_id)
        rows = session.execute(stmt).all()
        return {code: tid for code, tid in rows}

    @staticmethod
    def create_signals_batch(
        session: Session, stock_id: int, date_id: int, signals: list[dict]
    ) -> int:
        if not signals:
            return 0

        for sig in signals:
            session.add(FactSignal(
                stock_id=stock_id, date_id=date_id, **sig
            ))

        session.flush()
        return len(signals)

    @staticmethod
    def get_by_stock(
        session: Session, stock_id: int, limit: int = 50,
    ) -> list[FactSignal]:
        stmt = (
            select(FactSignal)
            .where(FactSignal.stock_id == stock_id)
            .order_by(FactSignal.date_id.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_by_date(session: Session, date_id: int) -> list[FactSignal]:
        """해당 날짜 이하에서 가장 최근 거래일의 전체 시그널을 반환한다."""
        from sqlalchemy import func

        latest_date_stmt = (
            select(func.max(FactSignal.date_id))
            .where(FactSignal.date_id <= date_id)
        )
        latest_date = session.execute(latest_date_stmt).scalar_one_or_none()
        if latest_date is None:
            return []

        stmt = (
            select(FactSignal)
            .where(FactSignal.date_id == latest_date)
            .order_by(FactSignal.stock_id)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_by_stock_and_date(
        session: Session, stock_id: int, date_id: int,
    ) -> list[FactSignal]:
        """특정 종목의 해당 날짜 이하 최근 시그널 목록."""
        from sqlalchemy import func

        latest_date_stmt = (
            select(func.max(FactSignal.date_id))
            .where(
                FactSignal.stock_id == stock_id,
                FactSignal.date_id <= date_id,
            )
        )
        latest_date = session.execute(latest_date_stmt).scalar_one_or_none()
        if latest_date is None:
            return []

        stmt = (
            select(FactSignal)
            .where(FactSignal.stock_id == stock_id, FactSignal.date_id == latest_date)
        )
        return list(session.execute(stmt).scalars().all())


class MacroRepository:
    """매크로 지표 CRUD."""

    @staticmethod
    def upsert(session: Session, date_id: int, data: dict) -> None:
        stmt = sqlite_insert(FactMacroIndicator).values(date_id=date_id, **data)
        update_fields = {k: getattr(stmt.excluded, k) for k in data}
        update_fields["updated_at"] = datetime.now()
        stmt = stmt.on_conflict_do_update(
            index_elements=["date_id"],
            set_=update_fields,
        )
        session.execute(stmt)
        session.flush()

    @staticmethod
    def get_latest(session: Session) -> FactMacroIndicator | None:
        stmt = (
            select(FactMacroIndicator)
            .order_by(FactMacroIndicator.date_id.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def get_previous(session: Session, date_id: int) -> FactMacroIndicator | None:
        """특정 date_id 이전의 가장 최근 매크로 데이터를 반환한다."""
        stmt = (
            select(FactMacroIndicator)
            .where(FactMacroIndicator.date_id < date_id)
            .order_by(FactMacroIndicator.date_id.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()


class RecommendationRepository:
    """데일리 추천 CRUD."""

    @staticmethod
    def create_batch(
        session: Session, run_date_id: int, recommendations: list[dict]
    ) -> int:
        if not recommendations:
            return 0

        for rec in recommendations:
            session.add(FactDailyRecommendation(
                run_date_id=run_date_id, **rec
            ))

        session.flush()
        return len(recommendations)

    @staticmethod
    def get_by_date(
        session: Session, run_date_id: int,
    ) -> list[FactDailyRecommendation]:
        stmt = (
            select(FactDailyRecommendation)
            .where(FactDailyRecommendation.run_date_id == run_date_id)
            .order_by(FactDailyRecommendation.rank)
        )
        return list(session.execute(stmt).scalars().all())


class NewsRepository:
    """뉴스 CRUD + Bridge."""

    @staticmethod
    def upsert_by_url(session: Session, articles: list[dict]) -> int:
        if not articles:
            return 0

        count = 0
        for article in articles:
            stmt = sqlite_insert(FactNews).values(**article)
            stmt = stmt.on_conflict_do_update(
                index_elements=["url"],
                set_={
                    "title": stmt.excluded.title,
                    "summary": stmt.excluded.summary,
                    "source": stmt.excluded.source,
                    "sentiment_score": stmt.excluded.sentiment_score,
                    "updated_at": datetime.now(),
                },
            )
            session.execute(stmt)
            count += 1

        session.flush()
        return count

    @staticmethod
    def link_to_stocks(
        session: Session, news_id: int, stock_ids: list[int],
        relevance: float | None = None,
    ) -> None:
        for sid in stock_ids:
            stmt = sqlite_insert(BridgeNewsStock).values(
                news_id=news_id, stock_id=sid, relevance=relevance
            )
            stmt = stmt.on_conflict_do_nothing()
            session.execute(stmt)
        session.flush()

    @staticmethod
    def get_by_stock(
        session: Session, stock_id: int, limit: int = 10,
    ) -> list[FactNews]:
        stmt = (
            select(FactNews)
            .join(BridgeNewsStock, BridgeNewsStock.news_id == FactNews.news_id)
            .where(BridgeNewsStock.stock_id == stock_id)
            .order_by(FactNews.published_at.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())


class CollectionLogRepository:
    """파이프라인 실행 이력 CRUD."""

    @staticmethod
    def log_step(
        session: Session, run_date_id: int, step: str,
        status: str, started_at: datetime,
        finished_at: datetime | None = None,
        records_count: int = 0, message: str | None = None,
    ) -> FactCollectionLog:
        log = FactCollectionLog(
            run_date_id=run_date_id, step=step, status=status,
            started_at=started_at, finished_at=finished_at,
            records_count=records_count, message=message,
        )
        session.add(log)
        session.flush()
        return log

    @staticmethod
    def get_by_run_date(
        session: Session, run_date_id: int,
    ) -> list[FactCollectionLog]:
        stmt = (
            select(FactCollectionLog)
            .where(FactCollectionLog.run_date_id == run_date_id)
            .order_by(FactCollectionLog.started_at)
        )
        return list(session.execute(stmt).scalars().all())


# ──────────────────────────────────────────
# 강화 데이터 Repository
# ──────────────────────────────────────────


class InsiderTradeRepository:
    """내부자 거래 CRUD."""

    @staticmethod
    def upsert_batch(
        session: Session, stock_id: int, trades: list[dict]
    ) -> int:
        if not trades:
            return 0

        count = 0
        for trade in trades:
            stmt = sqlite_insert(FactInsiderTrade).values(
                stock_id=stock_id, **trade
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[
                    "stock_id", "date_id", "insider_name", "transaction_type"
                ],
                set_={
                    "shares": stmt.excluded.shares,
                    "value": stmt.excluded.value,
                    "shares_owned_after": stmt.excluded.shares_owned_after,
                    "updated_at": datetime.now(),
                },
            )
            session.execute(stmt)
            count += 1

        session.flush()
        return count

    @staticmethod
    def get_by_stock(
        session: Session, stock_id: int, limit: int = 50,
    ) -> list[FactInsiderTrade]:
        stmt = (
            select(FactInsiderTrade)
            .where(FactInsiderTrade.stock_id == stock_id)
            .order_by(FactInsiderTrade.date_id.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())


class InstitutionalHoldingRepository:
    """기관 보유 CRUD."""

    @staticmethod
    def upsert_batch(
        session: Session, stock_id: int, holdings: list[dict]
    ) -> int:
        if not holdings:
            return 0

        for holding in holdings:
            session.add(FactInstitutionalHolding(
                stock_id=stock_id, **holding
            ))

        session.flush()
        return len(holdings)

    @staticmethod
    def get_by_stock(
        session: Session, stock_id: int, limit: int = 20,
    ) -> list[FactInstitutionalHolding]:
        stmt = (
            select(FactInstitutionalHolding)
            .where(FactInstitutionalHolding.stock_id == stock_id)
            .order_by(FactInstitutionalHolding.date_id.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())


class AnalystConsensusRepository:
    """애널리스트 컨센서스 CRUD."""

    @staticmethod
    def upsert(session: Session, stock_id: int, date_id: int, data: dict) -> None:
        stmt = sqlite_insert(FactAnalystConsensus).values(
            stock_id=stock_id, date_id=date_id, **data
        )
        update_fields = {k: getattr(stmt.excluded, k) for k in data}
        update_fields["updated_at"] = datetime.now()
        stmt = stmt.on_conflict_do_update(
            index_elements=["stock_id", "date_id"],
            set_=update_fields,
        )
        session.execute(stmt)
        session.flush()

    @staticmethod
    def get_latest(
        session: Session, stock_id: int,
    ) -> FactAnalystConsensus | None:
        stmt = (
            select(FactAnalystConsensus)
            .where(FactAnalystConsensus.stock_id == stock_id)
            .order_by(FactAnalystConsensus.date_id.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()


class EarningsSurpriseRepository:
    """실적 서프라이즈 CRUD."""

    @staticmethod
    def upsert(session: Session, stock_id: int, records: list[dict]) -> int:
        if not records:
            return 0

        count = 0
        for rec in records:
            stmt = sqlite_insert(FactEarningsSurprise).values(
                stock_id=stock_id, **rec
            )
            update_fields = {
                k: getattr(stmt.excluded, k) for k in rec if k != "period"
            }
            update_fields["updated_at"] = datetime.now()
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_id", "period"],
                set_=update_fields,
            )
            session.execute(stmt)
            count += 1

        session.flush()
        return count

    @staticmethod
    def get_by_stock(
        session: Session, stock_id: int, limit: int = 10,
    ) -> list[FactEarningsSurprise]:
        stmt = (
            select(FactEarningsSurprise)
            .where(FactEarningsSurprise.stock_id == stock_id)
            .order_by(FactEarningsSurprise.date_id.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())


class FactorReturnRepository:
    """팩터 수익률 CRUD."""

    @staticmethod
    def upsert_batch(session: Session, records: list[dict]) -> int:
        """팩터 수익률을 배치 UPSERT한다."""
        if not records:
            return 0

        now = datetime.now()
        for rec in records:
            stmt = sqlite_insert(FactFactorReturn).values(**rec)
            stmt = stmt.on_conflict_do_update(
                index_elements=["date_id", "factor_name"],
                set_={
                    "long_return": stmt.excluded.long_return,
                    "short_return": stmt.excluded.short_return,
                    "spread": stmt.excluded.spread,
                    "updated_at": now,
                },
            )
            session.execute(stmt)

        session.flush()
        return len(records)

    @staticmethod
    def get_by_factor(
        session: Session,
        factor_name: str,
        start_date_id: int | None = None,
        end_date_id: int | None = None,
    ) -> list[FactFactorReturn]:
        """특정 팩터의 수익률 시계열을 조회한다."""
        stmt = (
            select(FactFactorReturn)
            .where(FactFactorReturn.factor_name == factor_name)
        )
        if start_date_id is not None:
            stmt = stmt.where(FactFactorReturn.date_id >= start_date_id)
        if end_date_id is not None:
            stmt = stmt.where(FactFactorReturn.date_id <= end_date_id)
        stmt = stmt.order_by(FactFactorReturn.date_id)
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_all_factors(
        session: Session,
        start_date_id: int | None = None,
    ) -> list[FactFactorReturn]:
        """전체 팩터 수익률을 조회한다."""
        stmt = select(FactFactorReturn)
        if start_date_id is not None:
            stmt = stmt.where(FactFactorReturn.date_id >= start_date_id)
        stmt = stmt.order_by(FactFactorReturn.date_id, FactFactorReturn.factor_name)
        return list(session.execute(stmt).scalars().all())
