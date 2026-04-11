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
    DimWatchlist,
    DimWatchlistHolding,
    DimWatchlistPair,
    FactAnalystConsensus,
    FactCollectionLog,
    FactDailyPrice,
    FactDailyRecommendation,
    FactDeepDiveAction,
    FactDeepDiveAlert,
    FactDeepDiveChange,
    FactDeepDiveForecast,
    FactDeepDiveReport,
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


# ──────────────────────────────────────────
# Deep Dive — 워치리스트 + 개인 분석
# ──────────────────────────────────────────


class WatchlistRepository:
    """워치리스트 CRUD."""

    @staticmethod
    def add_ticker(
        session: Session, ticker: str, note: str | None = None,
    ) -> DimWatchlist:
        """워치리스트에 종목 추가. 이미 존재하면 재활성화."""
        ticker = ticker.upper()
        existing = session.execute(
            select(DimWatchlist).where(DimWatchlist.ticker == ticker)
        ).scalar_one_or_none()
        if existing is not None:
            existing.active = True
            if note is not None:
                existing.note = note
            session.flush()
            return existing
        item = DimWatchlist(ticker=ticker, active=True, note=note)
        session.add(item)
        session.flush()
        return item

    @staticmethod
    def remove_ticker(session: Session, ticker: str) -> bool:
        """soft delete (active=False). 존재하지 않으면 False."""
        ticker = ticker.upper()
        item = session.execute(
            select(DimWatchlist).where(DimWatchlist.ticker == ticker)
        ).scalar_one_or_none()
        if item is None:
            return False
        item.active = False
        session.flush()
        return True

    @staticmethod
    def get_active(session: Session) -> list[DimWatchlist]:
        """active=True인 종목 리스트 (ticker 정렬)."""
        stmt = (
            select(DimWatchlist)
            .where(DimWatchlist.active.is_(True))
            .order_by(DimWatchlist.ticker)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def set_holding(
        session: Session,
        ticker: str,
        shares: int,
        avg_cost: float,
        opened_at: date | None = None,
    ) -> DimWatchlistHolding:
        """보유정보 UPSERT."""
        ticker = ticker.upper()
        existing = session.execute(
            select(DimWatchlistHolding).where(DimWatchlistHolding.ticker == ticker)
        ).scalar_one_or_none()
        if existing is not None:
            existing.shares = shares
            existing.avg_cost = avg_cost
            if opened_at is not None:
                existing.opened_at = opened_at
            session.flush()
            return existing
        holding = DimWatchlistHolding(
            ticker=ticker, shares=shares, avg_cost=avg_cost, opened_at=opened_at,
        )
        session.add(holding)
        session.flush()
        return holding

    @staticmethod
    def get_holding(session: Session, ticker: str) -> DimWatchlistHolding | None:
        """종목별 보유정보 조회."""
        stmt = select(DimWatchlistHolding).where(
            DimWatchlistHolding.ticker == ticker.upper()
        )
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def delete_holding(session: Session, ticker: str) -> bool:
        """보유정보 삭제. 존재하지 않으면 False."""
        from sqlalchemy import delete

        ticker = ticker.upper()
        result = session.execute(
            delete(DimWatchlistHolding).where(
                DimWatchlistHolding.ticker == ticker
            )
        )
        session.flush()
        return (result.rowcount or 0) > 0

    @staticmethod
    def get_all_holdings(session: Session) -> dict[str, DimWatchlistHolding]:
        """{ticker: holding} 매핑."""
        stmt = select(DimWatchlistHolding)
        holdings = session.execute(stmt).scalars().all()
        return {h.ticker: h for h in holdings}

    @staticmethod
    def upsert_pairs(
        session: Session, ticker: str, pairs: list[dict],
    ) -> int:
        """페어 종목 UPSERT. 기존 pairs 삭제 후 재삽입. 반환: INSERT 건수."""
        from sqlalchemy import delete

        ticker = ticker.upper()
        session.execute(
            delete(DimWatchlistPair).where(DimWatchlistPair.ticker == ticker)
        )
        for p in pairs:
            session.add(DimWatchlistPair(
                ticker=ticker,
                peer_ticker=p["peer_ticker"].upper(),
                similarity_score=p.get("similarity_score"),
            ))
        session.flush()
        return len(pairs)

    @staticmethod
    def get_pairs(session: Session, ticker: str) -> list[DimWatchlistPair]:
        """종목의 페어 목록. ORDER BY similarity_score DESC."""
        stmt = (
            select(DimWatchlistPair)
            .where(DimWatchlistPair.ticker == ticker.upper())
            .order_by(DimWatchlistPair.similarity_score.desc())
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_pairs_updated_at(session: Session, ticker: str) -> datetime | None:
        """종목 페어의 최신 updated_at. 없으면 None."""
        from sqlalchemy import func as sa_func

        stmt = (
            select(sa_func.max(DimWatchlistPair.updated_at))
            .where(DimWatchlistPair.ticker == ticker.upper())
        )
        return session.execute(stmt).scalar_one_or_none()


class DeepDiveRepository:
    """Deep Dive 보고서 저장/조회."""

    @staticmethod
    def insert_report(session: Session, **kwargs) -> FactDeepDiveReport:
        """보고서 INSERT (절대 UPDATE 아님)."""
        report = FactDeepDiveReport(**kwargs)
        session.add(report)
        session.flush()
        return report

    @staticmethod
    def insert_action(session: Session, **kwargs) -> FactDeepDiveAction:
        """액션 이력 INSERT."""
        action = FactDeepDiveAction(**kwargs)
        session.add(action)
        session.flush()
        return action

    @staticmethod
    def get_latest_report(
        session: Session, stock_id: int,
    ) -> FactDeepDiveReport | None:
        """종목의 최신 보고서."""
        stmt = (
            select(FactDeepDiveReport)
            .where(FactDeepDiveReport.stock_id == stock_id)
            .order_by(FactDeepDiveReport.date_id.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def get_latest_reports_all(session: Session) -> list[FactDeepDiveReport]:
        """전 종목 최신 보고서 (카드 그리드용)."""
        from sqlalchemy import func as sa_func

        # 종목별 max(date_id) subquery
        sub = (
            select(
                FactDeepDiveReport.stock_id,
                sa_func.max(FactDeepDiveReport.date_id).label("max_date_id"),
            )
            .group_by(FactDeepDiveReport.stock_id)
            .subquery()
        )
        stmt = (
            select(FactDeepDiveReport)
            .join(
                sub,
                (FactDeepDiveReport.stock_id == sub.c.stock_id)
                & (FactDeepDiveReport.date_id == sub.c.max_date_id),
            )
            .order_by(FactDeepDiveReport.ticker)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_reports_by_ticker(
        session: Session, ticker: str, limit: int = 30,
    ) -> list[FactDeepDiveReport]:
        """종목별 보고서 이력."""
        stmt = (
            select(FactDeepDiveReport)
            .where(FactDeepDiveReport.ticker == ticker.upper())
            .order_by(FactDeepDiveReport.date_id.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def delete_reports_for_date(
        session: Session, date_id: int, stock_id: int | None = None,
    ) -> int:
        """force 재실행 시 기존 보고서/액션 삭제. 반환: 삭제 건수."""
        from sqlalchemy import delete

        report_stmt = delete(FactDeepDiveReport).where(
            FactDeepDiveReport.date_id == date_id
        )
        action_stmt = delete(FactDeepDiveAction).where(
            FactDeepDiveAction.date_id == date_id
        )
        if stock_id is not None:
            report_stmt = report_stmt.where(FactDeepDiveReport.stock_id == stock_id)
            action_stmt = action_stmt.where(FactDeepDiveAction.stock_id == stock_id)

        r1 = session.execute(report_stmt)
        r2 = session.execute(action_stmt)
        session.flush()
        return (r1.rowcount or 0) + (r2.rowcount or 0)

    @staticmethod
    def insert_forecasts_batch(
        session: Session,
        report_id: int,
        date_id: int,
        stock_id: int,
        ticker: str,
        forecasts: list,
    ) -> int:
        """시나리오 예측 일괄 INSERT. 반환: INSERT 건수."""
        count = 0
        for f in forecasts:
            session.add(FactDeepDiveForecast(
                report_id=report_id,
                date_id=date_id,
                stock_id=stock_id,
                ticker=ticker,
                horizon=f.horizon,
                scenario=f.scenario,
                probability=float(f.probability),
                price_low=float(f.price_low),
                price_high=float(f.price_high),
                trigger_condition=f.trigger_condition,
            ))
            count += 1
        session.flush()
        return count

    @staticmethod
    def get_forecasts_by_report(
        session: Session, report_id: int,
    ) -> list[FactDeepDiveForecast]:
        """보고서별 시나리오 예측 조회."""
        stmt = (
            select(FactDeepDiveForecast)
            .where(FactDeepDiveForecast.report_id == report_id)
            .order_by(FactDeepDiveForecast.horizon, FactDeepDiveForecast.scenario)
        )
        return list(session.execute(stmt).scalars().all())

    # --- T3: diff/changes ---

    @staticmethod
    def get_previous_report(
        session: Session, stock_id: int, before_date_id: int,
    ) -> FactDeepDiveReport | None:
        """지정 date_id 이전의 최신 리포트. diff 감지용."""
        stmt = (
            select(FactDeepDiveReport)
            .where(
                FactDeepDiveReport.stock_id == stock_id,
                FactDeepDiveReport.date_id < before_date_id,
            )
            .order_by(FactDeepDiveReport.date_id.desc())
            .limit(1)
        )
        return session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def insert_changes_batch(
        session: Session, date_id: int, stock_id: int, ticker: str,
        changes: list,
    ) -> int:
        """변경 감지 결과 일괄 INSERT. 반환: INSERT 건수."""
        for c in changes:
            session.add(FactDeepDiveChange(
                date_id=date_id,
                stock_id=stock_id,
                ticker=ticker.upper(),
                change_type=c.change_type,
                description=c.description,
                severity=c.severity,
            ))
        session.flush()
        return len(changes)

    @staticmethod
    def get_changes_by_date(
        session: Session, date_id: int,
    ) -> list[FactDeepDiveChange]:
        """날짜별 변경 목록 (알림용). ORDER BY severity DESC."""
        severity_order = {"critical": 0, "warning": 1, "info": 2}
        stmt = (
            select(FactDeepDiveChange)
            .where(FactDeepDiveChange.date_id == date_id)
        )
        results = list(session.execute(stmt).scalars().all())
        results.sort(key=lambda c: severity_order.get(c.severity, 9))
        return results

    @staticmethod
    def get_changes_by_ticker(
        session: Session, ticker: str, limit: int = 60,
    ) -> list[FactDeepDiveChange]:
        """종목별 변경 이력 (히스토리 페이지용). ORDER BY date_id DESC."""
        stmt = (
            select(FactDeepDiveChange)
            .where(FactDeepDiveChange.ticker == ticker.upper())
            .order_by(FactDeepDiveChange.date_id.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())

    # --- T4: forecast 평가 ---

    @staticmethod
    def get_matured_forecasts(
        session: Session, as_of_date: date,
    ) -> list[FactDeepDiveForecast]:
        """만기 도래한 미평가 예측 조회. Python에서 만기 필터링."""
        from datetime import timedelta

        from src.db.helpers import id_to_date

        HORIZON_DAYS = {"1M": 30, "3M": 90, "6M": 180}

        all_pending = list(session.execute(
            select(FactDeepDiveForecast)
            .where(FactDeepDiveForecast.actual_price.is_(None))
        ).scalars().all())

        matured = []
        for f in all_pending:
            forecast_date = id_to_date(f.date_id)
            maturity_date = forecast_date + timedelta(
                days=HORIZON_DAYS.get(f.horizon, 30),
            )
            if maturity_date <= as_of_date:
                matured.append(f)
        return matured

    @staticmethod
    def update_forecast_actual(
        session: Session, forecast_id: int,
        actual_price: float, actual_date: date, hit_range: bool,
    ) -> None:
        """만기 도래 예측의 실제 가격/적중 여부 업데이트."""
        stmt = (
            select(FactDeepDiveForecast)
            .where(FactDeepDiveForecast.forecast_id == forecast_id)
        )
        forecast = session.execute(stmt).scalar_one()
        forecast.actual_price = actual_price
        forecast.actual_date = actual_date
        forecast.hit_range = hit_range
        session.flush()

    @staticmethod
    def get_all_evaluated_forecasts(
        session: Session,
    ) -> list[FactDeepDiveForecast]:
        """평가 완료된 전체 예측 (리더보드용)."""
        stmt = (
            select(FactDeepDiveForecast)
            .where(FactDeepDiveForecast.hit_range.isnot(None))
            .order_by(FactDeepDiveForecast.ticker, FactDeepDiveForecast.horizon)
        )
        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def get_evaluated_forecasts_by_ticker(
        session: Session, ticker: str,
    ) -> list[FactDeepDiveForecast]:
        """종목별 평가 완료 예측."""
        stmt = (
            select(FactDeepDiveForecast)
            .where(
                FactDeepDiveForecast.hit_range.isnot(None),
                FactDeepDiveForecast.ticker == ticker.upper(),
            )
            .order_by(FactDeepDiveForecast.horizon)
        )
        return list(session.execute(stmt).scalars().all())

    # --- T5: actions 조회 ---

    @staticmethod
    def get_actions_by_ticker(
        session: Session, ticker: str, limit: int = 60,
    ) -> list[FactDeepDiveAction]:
        """종목별 액션 이력 (히스토리 페이지용). ORDER BY date_id DESC LIMIT :limit."""
        stmt = (
            select(FactDeepDiveAction)
            .where(FactDeepDiveAction.ticker == ticker.upper())
            .order_by(FactDeepDiveAction.date_id.desc())
            .limit(limit)
        )
        return list(session.execute(stmt).scalars().all())


# ────────────────────────────────────────────────────────────────────────
# Phase 12b: 알림 히스토리 Repository
# ────────────────────────────────────────────────────────────────────────


_SEVERITY_RANK = {"critical": 3, "warning": 2, "info": 1}


class AlertRepository:
    """Deep Dive 알림 영구 저장 + 조회/확인."""

    @staticmethod
    def persist_batch(
        session: Session,
        date_id: int,
        stock_id_lookup: dict[str, int],
        alerts,
    ) -> int:
        """알림 리스트 영구 저장 (INSERT OR IGNORE로 일일 dedup).

        Args:
            date_id: 알림 발화 날짜 ID
            stock_id_lookup: {ticker: stock_id} 매핑. 없는 ticker는 스킵.
            alerts: AlertTrigger 리스트

        Returns:
            실제 INSERT된 건수 (중복 제외).
        """
        if not alerts:
            return 0

        rows: list[dict] = []
        for a in alerts:
            stock_id = stock_id_lookup.get(a.ticker)
            if stock_id is None:
                continue
            rows.append({
                "date_id": date_id,
                "stock_id": stock_id,
                "ticker": a.ticker,
                "trigger_type": a.trigger_type,
                "severity": a.severity,
                "message": a.message,
                "current_price": float(a.current_price) if a.current_price else None,
                "reference_price": (
                    float(a.reference_price)
                    if getattr(a, "reference_price", None) is not None
                    else None
                ),
                "context_json": None,
                "acknowledged": False,
            })

        if not rows:
            return 0

        # UniqueConstraint 기반 일일 dedup — INSERT OR IGNORE
        stmt = sqlite_insert(FactDeepDiveAlert).values(rows)
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["ticker", "trigger_type", "date_id"],
        )
        result = session.execute(stmt)
        session.flush()
        return result.rowcount or 0

    @staticmethod
    def get_recent(
        session: Session,
        days: int = 30,
        severity_min: str | None = None,
        ack_filter: str | None = None,  # 'unread' | 'read' | None
        ticker: str | None = None,
        limit: int = 200,
    ) -> list[FactDeepDiveAlert]:
        """최근 N일 알림 조회 (필터 지원)."""
        from datetime import date as date_type, timedelta

        cutoff_date_id = date_to_id(date_type.today() - timedelta(days=days))

        stmt = (
            select(FactDeepDiveAlert)
            .where(FactDeepDiveAlert.date_id >= cutoff_date_id)
            .order_by(
                FactDeepDiveAlert.date_id.desc(),
                FactDeepDiveAlert.alert_id.desc(),
            )
            .limit(limit)
        )

        if severity_min:
            rank = _SEVERITY_RANK.get(severity_min, 0)
            keep = [s for s, r in _SEVERITY_RANK.items() if r >= rank]
            if keep:
                stmt = stmt.where(FactDeepDiveAlert.severity.in_(keep))

        if ack_filter == "unread":
            stmt = stmt.where(FactDeepDiveAlert.acknowledged.is_(False))
        elif ack_filter == "read":
            stmt = stmt.where(FactDeepDiveAlert.acknowledged.is_(True))

        if ticker:
            stmt = stmt.where(FactDeepDiveAlert.ticker == ticker.upper())

        return list(session.execute(stmt).scalars().all())

    @staticmethod
    def acknowledge(session: Session, alert_id: int) -> bool:
        """단일 알림 확인 처리. 존재하지 않으면 False."""
        alert = session.get(FactDeepDiveAlert, alert_id)
        if alert is None:
            return False
        if not alert.acknowledged:
            alert.acknowledged = True
            alert.acknowledged_at = datetime.now()
            session.flush()
        return True

    @staticmethod
    def acknowledge_all(session: Session, date_id: int | None = None) -> int:
        """모든 미확인 알림을 확인 처리. date_id가 주어지면 해당 날짜만."""
        from sqlalchemy import update

        stmt = (
            update(FactDeepDiveAlert)
            .where(FactDeepDiveAlert.acknowledged.is_(False))
            .values(acknowledged=True, acknowledged_at=datetime.now())
        )
        if date_id is not None:
            stmt = stmt.where(FactDeepDiveAlert.date_id == date_id)
        result = session.execute(stmt)
        session.flush()
        return result.rowcount or 0

    @staticmethod
    def count_unread(session: Session, days: int = 30) -> int:
        """미확인 알림 건수 (최근 N일)."""
        from datetime import date as date_type, timedelta
        from sqlalchemy import func as sa_func

        cutoff_date_id = date_to_id(date_type.today() - timedelta(days=days))
        stmt = (
            select(sa_func.count())
            .select_from(FactDeepDiveAlert)
            .where(
                FactDeepDiveAlert.acknowledged.is_(False),
                FactDeepDiveAlert.date_id >= cutoff_date_id,
            )
        )
        return int(session.execute(stmt).scalar() or 0)
