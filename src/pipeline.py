"""데일리 배치 파이프라인 오케스트레이터."""

from __future__ import annotations

import json
import logging
import signal
from datetime import date, datetime, timedelta

from rich.console import Console
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.engine import get_session
from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import FactCollectionLog
from src.db.repository import (
    CollectionLogRepository,
    DailyPriceRepository,
    MacroRepository,
    NewsRepository,
    RecommendationRepository,
    SignalRepository,
    StockRepository,
)

logger = logging.getLogger(__name__)
console = Console()


class DailyPipeline:
    """6단계 데일리 파이프라인."""

    def __init__(self, engine: Engine, target_date: date | None = None,
                 top_n: int = 10, skip_notify: bool = False):
        self.engine = engine
        self.target_date = target_date or date.today()
        self.top_n = top_n
        self.skip_notify = skip_notify
        self.run_date_id = date_to_id(self.target_date)
        # step 간 데이터 전달용
        self._sector_momentum: dict[str, float] | None = None
        # 그레이스풀 셧다운
        self._interrupted = False
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
            signal.signal(signal.SIGINT, self._handle_signal)
        except (ValueError, OSError):
            pass  # Not main thread or signal not available

    def _handle_signal(self, signum, frame):
        """중단 신호 핸들러."""
        logger.warning("파이프라인 중단 신호 수신 (signal=%d)", signum)
        self._interrupted = True

    def _is_step_done(self, step_name: str) -> bool:
        """오늘 이미 성공 완료된 스텝인지 확인."""
        with get_session(self.engine) as session:
            log = session.execute(
                select(FactCollectionLog)
                .where(
                    FactCollectionLog.run_date_id == self.run_date_id,
                    FactCollectionLog.step == step_name,
                    FactCollectionLog.status == "success",
                )
            ).scalar_one_or_none()
            return log is not None

    def run(self, step: int | None = None, force: bool = False) -> None:
        """전체 파이프라인 또는 특정 단계를 실행한다."""
        import json as _json
        from pathlib import Path

        pipeline_start = datetime.now()
        step_results: dict[str, dict] = {}

        with get_session(self.engine) as session:
            ensure_date_ids(session, [self.target_date])

        # STEP 0: execution_price 채우기 + 과거 추천 수익률 자동 업데이트
        if step is None:
            started = datetime.now()
            console.print("\n[cyan]STEP 0[/cyan] 성과 업데이트")
            try:
                from src.analysis.performance import (
                    fill_execution_prices,
                    update_recommendation_returns,
                )

                with get_session(self.engine) as session:
                    ep_count = fill_execution_prices(session)
                    if ep_count > 0:
                        console.print(f"  execution_price 채움: {ep_count}건")
                    count = update_recommendation_returns(session)
                self._log_step("step0_performance", "success", started, records_count=count + ep_count)
                console.print(f"  [green]완료[/green] ({count}건)")
            except Exception as e:
                logger.error("STEP 0 실패: %s", e, exc_info=True)
                self._log_step("step0_performance", "failed", started, message=str(e))
                console.print(f"  [red]실패: {e}[/red]")

        # STEP 0.5: AI 예측 복기 (20거래일 전 추천 복기 + 교훈 추출)
        if step is None and not self._interrupted:
            started = datetime.now()
            console.print("\n[cyan]STEP 0.5[/cyan] AI 예측 복기")
            try:
                from src.ai.retrospective import run_retrospective

                with get_session(self.engine) as session:
                    retro_count = run_retrospective(session, self.target_date)
                self._log_step(
                    "step0_5_retrospective", "success", started,
                    records_count=retro_count,
                )
                console.print(f"  [green]완료[/green] ({retro_count}건)")
            except Exception as e:
                logger.error("STEP 0.5 실패: %s", e, exc_info=True)
                self._log_step(
                    "step0_5_retrospective", "failed", started, message=str(e),
                )
                console.print(f"  [red]실패: {e}[/red]")

        steps = [
            (1, "step1_collect", self.step1_collect),
            (2, "step2_analyze", self.step2_analyze),
            (3, "step3_external", self.step3_external),
            (4, "step4_screen", self.step4_screen),
            (4.5, "step4_5_ai", self.step4_5_ai_analysis),
            (4.6, "step4_6_sizing", self.step4_6_position_sizing),
            (4.7, "step4_7_factors", self.step4_7_factor_returns),
            (5, "step5_report", self.step5_report),
            (6, "step6_notify", self.step6_notify),
        ]

        if step is not None:
            steps = [(n, name, fn) for n, name, fn in steps if n == step]

        console.print(f"\n[bold]데일리 파이프라인 시작[/bold] ({self.target_date})")

        for num, name, fn in steps:
            if self._interrupted:
                started = datetime.now()
                console.print("[yellow]중단 신호로 파이프라인 종료[/yellow]")
                self._log_step(name, "interrupted", started, message="Signal interrupted")
                break

            if not force and self._is_step_done(name):
                console.print(f"  [dim]STEP {num} {name} -- 이미 완료, 스킵[/dim]")
                step_results[name] = {"status": "skipped", "records": 0, "duration_sec": 0}
                continue

            started = datetime.now()
            console.print(f"\n[cyan]STEP {num}[/cyan] {name}")
            try:
                records = fn()
                self._log_step(name, "success", started, records_count=records or 0)
                console.print(f"  [green]완료[/green] ({records or 0}건)")
                step_results[name] = {
                    "status": "success",
                    "records": records or 0,
                    "duration_sec": int((datetime.now() - started).total_seconds()),
                }
            except Exception as e:
                logger.error("STEP %d 실패: %s", num, e, exc_info=True)
                self._log_step(name, "failed", started, message=str(e))
                console.print(f"  [red]실패: {e}[/red]")
                step_results[name] = {
                    "status": "failed",
                    "records": 0,
                    "duration_sec": int((datetime.now() - started).total_seconds()),
                }

        # 파이프라인 전체 요약
        pipeline_end = datetime.now()
        total_elapsed = int((pipeline_end - pipeline_start).total_seconds())

        summary = {
            "date": str(self.target_date),
            "total_duration_sec": total_elapsed,
            "steps": step_results,
        }

        summary_path = Path("logs") / f"{self.target_date}_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                _json.dump(summary, f, ensure_ascii=False, indent=2)
            logger.info("파이프라인 요약 저장: %s", summary_path)
        except Exception:
            pass

        console.print(f"\n[bold green]파이프라인 완료[/bold green] ({self.target_date})")

    def step1_collect(self) -> int:
        """S&P 500 데이터 수집."""
        from src.data.yahoo_client import batch_download_prices, fetch_financial_data

        total = 0
        settings = get_settings()

        with get_session(self.engine) as session:
            stocks = StockRepository.get_sp500_active(session)
            if not stocks:
                console.print("  [yellow]S&P 500 종목 없음. 'db init' 실행 필요.[/yellow]")
                return 0

            # 마지막 수집일 판단 (전체 종목 중 최소)
            tickers = [s.ticker for s in stocks]
            stock_map = {s.ticker: s for s in stocks}

            # 적시성 판단: 5개 샘플 종목 중 최소 날짜 사용
            sample_indices = [0]
            for idx in [100, 200, 300, 400]:
                if idx < len(stocks):
                    sample_indices.append(idx)
            sample_dates = []
            for idx in sample_indices:
                d = DailyPriceRepository.get_last_date(session, stocks[idx].stock_id)
                if d is not None:
                    sample_dates.append(d)
            last_date = min(sample_dates) if sample_dates else None

            price_skip = False
            if last_date is not None:
                start_date = last_date + timedelta(days=1)
                if start_date > self.target_date:
                    console.print("  [dim]가격 데이터 이미 최신, 재무/매크로만 수집[/dim]")
                    price_skip = True
            else:
                days = {"1mo": 30, "3mo": 90, "6mo": 180, "1y": 365, "2y": 730}
                start_date = self.target_date - timedelta(
                    days=days.get(settings.history_period, 730)
                )

            # 배치 다운로드 (가격이 최신이면 스킵)
            if not price_skip:
                batch_size = getattr(settings, "batch_size", 50)
                prices_data, failed = batch_download_prices(
                    tickers, start_date, self.target_date, batch_size=batch_size,
                )
                if failed:
                    logger.info("가격 수집 실패 %d개 종목", len(failed))

                # DB 저장 (개별 종목 에러 격리)
                failed_stocks: list[str] = []
                for ticker, prices in prices_data.items():
                    try:
                        stock = stock_map.get(ticker)
                        if stock is None or not prices:
                            continue
                        price_dicts = [p.model_dump() for p in prices]
                        count = DailyPriceRepository.upsert_prices_batch(
                            session, stock.stock_id, price_dicts
                        )
                        total += count
                        if total > 0 and total % 2500 == 0:
                            session.flush()
                    except Exception as e:
                        logger.error("가격 저장 실패 [%s]: %s", ticker, e)
                        failed_stocks.append(ticker)

                if failed_stocks:
                    logger.warning(
                        "가격 수집 실패 종목 %d개: %s",
                        len(failed_stocks), failed_stocks[:10],
                    )

        # 재무 데이터 수집 (ThreadPoolExecutor로 병렬 수집)
        console.print("  [dim]재무 데이터 병렬 수집 중...[/dim]")
        fin_count = 0

        with get_session(self.engine) as session:
            stocks = StockRepository.get_sp500_active(session)
            stock_map = {s.ticker: s for s in stocks}

        # API 호출은 세션 밖에서 병렬 수행
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_one(ticker: str):
            try:
                return ticker, fetch_financial_data(ticker)
            except Exception as e:
                logger.warning("재무 수집 실패 [%s]: %s", ticker, e)
                return ticker, ([], None)

        results = {}
        tickers_list = [s.ticker for s in stocks]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_one, t): t for t in tickers_list}
            done_count = 0
            for future in as_completed(futures):
                ticker, (fins, val) = future.result()
                if fins or val:
                    results[ticker] = (fins, val)
                done_count += 1
                if done_count % 100 == 0:
                    console.print(f"  [dim]재무 수집 {done_count}/{len(tickers_list)}...[/dim]")

        # DB 저장은 단일 세션에서 순차 처리
        with get_session(self.engine) as session:
            stocks = StockRepository.get_sp500_active(session)
            stock_map = {s.ticker: s for s in stocks}

            for ticker, (fins, val) in results.items():
                stock = stock_map.get(ticker)
                if stock is None:
                    continue

                try:
                    if fins:
                        from src.db.repository import FinancialRepository
                        fin_dicts = [f.model_dump() for f in fins]
                        FinancialRepository.upsert(session, stock.stock_id, fin_dicts)
                        fin_count += len(fins)

                    if val:
                        from src.db.repository import ValuationRepository
                        ensure_date_ids(session, [val.date])
                        val_dict = val.model_dump()
                        d = val_dict.pop("date")
                        val_dict["date_id"] = date_to_id(d)
                        ValuationRepository.upsert(session, stock.stock_id, [val_dict])
                        fin_count += 1
                except Exception as e:
                    logger.warning("재무 저장 실패 [%s]: %s", ticker, e)

            session.flush()

        total += fin_count
        console.print(f"  [dim]재무 데이터 {fin_count}건 수집 완료[/dim]")

        # 강화 데이터 수집 (내부자, 기관, 애널리스트, 실적, 공매도)
        try:
            from src.data.enhanced_collector import collect_all_enhanced

            console.print("  [dim]강화 데이터 병렬 수집 중 (내부자/기관/애널리스트/실적)...[/dim]")
            with get_session(self.engine) as session:
                stocks = StockRepository.get_sp500_active(session)
                counts = collect_all_enhanced(
                    session, stocks, self.target_date, batch_size=50,
                )
            enhanced_total = sum(counts.values())
            total += enhanced_total
            console.print(
                f"  [dim]강화 데이터 수집 완료: 내부자 {counts['insider']}건, "
                f"기관 {counts['institutional']}건, "
                f"애널리스트 {counts['analyst']}건, "
                f"실적 {counts['earnings']}건[/dim]"
            )
        except Exception as e:
            logger.warning("강화 데이터 수집 실패: %s", e)

        # 시장 뉴스 수집
        try:
            from src.data.news_scraper import scrape_market_news
            from src.analysis.external import analyze_news_sentiment

            articles = scrape_market_news(count=20)
            if articles:
                with get_session(self.engine) as session:
                    article_dicts = [
                        {
                            "title": a.title,
                            "summary": a.summary,
                            "url": a.url,
                            "source": a.source,
                            "published_at": a.published_at,
                            "sentiment_score": None,
                        }
                        for a in articles
                    ]
                    news_count = NewsRepository.upsert_by_url(session, article_dicts)
                    total += news_count
                    console.print(f"  [dim]시장 뉴스 {news_count}건 수집[/dim]")

                # 감성 점수 계산 및 저장
                sentiment = analyze_news_sentiment(articles)
                if sentiment != 0.0:
                    console.print(f"  [dim]뉴스 감성: {sentiment:+.2f}[/dim]")
        except Exception as e:
            logger.warning("뉴스 수집 실패: %s", e)

        # 매크로 수집
        try:
            from src.data.macro_collector import collect_macro

            macro = collect_macro(self.target_date)
            with get_session(self.engine) as session:
                ensure_date_ids(session, [self.target_date])
                MacroRepository.upsert(session, self.run_date_id, {
                    k: v for k, v in macro.model_dump().items()
                    if k != "date" and v is not None
                })
            total += 1
        except Exception as e:
            logger.warning("매크로 수집 실패: %s", e)

        return total

    def step2_analyze(self) -> int:
        """전 종목 기술적 지표 계산 + 시그널 판단."""
        from src.analysis.signals import detect_signals
        from src.analysis.technical import (
            calculate_indicators,
            load_date_map,
            prices_to_dataframe,
            store_indicators,
        )

        total = 0
        failed_stocks: list[str] = []

        with get_session(self.engine) as session:
            stocks = StockRepository.get_sp500_active(session)
            signal_type_map = SignalRepository.get_signal_type_map(session)
            date_map = load_date_map(session)

            # 종목별 마지막 저장 date_id 조회 (증분 모드)
            from sqlalchemy import func
            from sqlalchemy import select as sa_sel
            from src.db.models import FactIndicatorValue
            last_dates_stmt = (
                sa_sel(
                    FactIndicatorValue.stock_id,
                    func.max(FactIndicatorValue.date_id),
                )
                .group_by(FactIndicatorValue.stock_id)
            )
            last_dates = dict(session.execute(last_dates_stmt).all())

            for i, stock in enumerate(stocks):
                try:
                    df = prices_to_dataframe(session, stock.stock_id, date_map=date_map)
                    if df.empty or len(df) < 20:
                        continue

                    indicators_df = calculate_indicators(df)
                    last_did = last_dates.get(stock.stock_id)
                    count = store_indicators(
                        session, stock.stock_id, indicators_df,
                        last_stored_date_id=last_did,
                        auto_flush=False,
                    )
                    total += count

                    # 시그널 감지
                    detected = detect_signals(indicators_df, stock.stock_id)
                    if detected:
                        date_id = date_to_id(indicators_df.index[-1])
                        ensure_date_ids(session, [indicators_df.index[-1]])

                        # 기존 시그널 삭제 (재실행 시 중복 방지)
                        from src.db.models import FactSignal as FS
                        session.query(FS).filter(
                            FS.stock_id == stock.stock_id,
                            FS.date_id == date_id,
                        ).delete()

                        signal_dicts = [
                            {
                                "signal_type_id": signal_type_map.get(s.signal_type),
                                "strength": s.strength,
                                "description": s.description,
                            }
                            for s in detected
                            if signal_type_map.get(s.signal_type) is not None
                        ]
                        SignalRepository.create_signals_batch(
                            session, stock.stock_id, date_id, signal_dicts
                        )

                except Exception as e:
                    logger.warning("분석 실패 [%s]: %s", stock.ticker, e)
                    failed_stocks.append(stock.ticker)

                # 50종목마다 flush (메모리 관리)
                if (i + 1) % 50 == 0:
                    session.flush()
                    console.print(f"  [dim]분석 진행 {i + 1}/{len(stocks)}...[/dim]")

            # 최종 flush
            session.flush()

        if failed_stocks:
            logger.warning(
                "분석 실패 종목 %d개: %s",
                len(failed_stocks), failed_stocks[:10],
            )

        return total

    def step3_external(self) -> int:
        """외부 요인 분석 (매크로 점수, 섹터 모멘텀)."""
        from src.analysis.external import analyze_macro, calculate_sector_momentum

        with get_session(self.engine) as session:
            macro = MacroRepository.get_latest(session)
            if macro is None:
                return 0

            from src.data.schemas import MacroData

            macro_data = MacroData(
                date=self.target_date,
                vix=float(macro.vix) if macro.vix else None,
                us_10y_yield=float(macro.us_10y_yield) if macro.us_10y_yield else None,
                us_13w_yield=float(macro.us_13w_yield) if macro.us_13w_yield else None,
                dollar_index=float(macro.dollar_index) if macro.dollar_index else None,
                sp500_close=float(macro.sp500_close) if macro.sp500_close else None,
                sp500_sma20=float(macro.sp500_sma20) if macro.sp500_sma20 else None,
            )

            # 전일 매크로 조회 (추세 분석용)
            previous_macro_data = None
            prev = MacroRepository.get_previous(session, self.run_date_id)
            if prev is not None:
                previous_macro_data = MacroData(
                    date=self.target_date,
                    vix=float(prev.vix) if prev.vix else None,
                    us_10y_yield=float(prev.us_10y_yield) if prev.us_10y_yield else None,
                    us_13w_yield=float(prev.us_13w_yield) if prev.us_13w_yield else None,
                    dollar_index=float(prev.dollar_index) if prev.dollar_index else None,
                    sp500_close=float(prev.sp500_close) if prev.sp500_close else None,
                    sp500_sma20=float(prev.sp500_sma20) if prev.sp500_sma20 else None,
                )

            market_score = analyze_macro(macro_data, previous_macro_data)
            macro.market_score = market_score
            session.flush()

            # 섹터별 모멘텀 계산 (최근 20일 평균 수익률)
            try:
                from src.analysis.technical import load_date_map, prices_to_dataframe

                stocks = StockRepository.get_sp500_active(session)
                step3_date_map = load_date_map(session)
                sector_returns: dict[str, list[float]] = {}

                for stock in stocks:
                    if not stock.sector:
                        continue
                    df = prices_to_dataframe(session, stock.stock_id, date_map=step3_date_map)
                    if df.empty or len(df) < 20:
                        continue
                    ret_20d = (float(df["close"].iloc[-1]) - float(df["close"].iloc[-20])) / float(df["close"].iloc[-20]) * 100
                    sector_name = stock.sector.sector_name
                    sector_returns.setdefault(sector_name, []).append(ret_20d)

                avg_returns = {
                    sector: sum(rets) / len(rets)
                    for sector, rets in sector_returns.items()
                    if rets
                }
                self._sector_momentum = calculate_sector_momentum(avg_returns)
                logger.info("섹터 모멘텀: %s", self._sector_momentum)
            except Exception as e:
                logger.warning("섹터 모멘텀 계산 실패: %s", e)

        return 1

    def step4_screen(self) -> int:
        """스크리닝 + 랭킹."""
        from src.analysis.external import analyze_macro
        from src.analysis.screener import screen_and_rank

        with get_session(self.engine) as session:
            macro = MacroRepository.get_latest(session)
            market_score = macro.market_score if macro else 5

            # 섹터 모멘텀이 없으면 여기서 계산
            sector_mom = self._sector_momentum
            if sector_mom is None:
                try:
                    from src.analysis.external import calculate_sector_momentum
                    from src.analysis.technical import prices_to_dataframe

                    all_stocks = StockRepository.get_sp500_active(session)
                    sector_rets: dict[str, list[float]] = {}
                    for st in all_stocks:
                        if not st.sector:
                            continue
                        df = prices_to_dataframe(session, st.stock_id)
                        if df.empty or len(df) < 20:
                            continue
                        ret = (float(df["close"].iloc[-1]) - float(df["close"].iloc[-20])) / float(df["close"].iloc[-20]) * 100
                        sector_rets.setdefault(st.sector.sector_name, []).append(ret)
                    avg = {s: sum(r) / len(r) for s, r in sector_rets.items() if r}
                    sector_mom = calculate_sector_momentum(avg)
                except Exception as e:
                    logger.warning("step4 섹터 모멘텀 계산 실패: %s", e)

            # 상대 강도 (RS) 계산
            rs_ranks = None
            try:
                from src.analysis.relative_strength import calculate_rs_ranks
                rs_ranks = calculate_rs_ranks(session, self.target_date)
                if rs_ranks:
                    logger.info("상대 강도 계산 완료: %d 종목", len(rs_ranks))
            except Exception as e:
                logger.warning("RS 계산 실패: %s", e)

            _settings = get_settings()
            recommendations = screen_and_rank(
                session, self.target_date,
                top_n=self.top_n,
                market_score=market_score or 5,
                sector_momentum=sector_mom,
                rs_ranks=rs_ranks,
                scoring_mode=_settings.factor_scoring_mode,
            )

            if not recommendations:
                logger.info("스크리닝 결과: 추천 종목 없음")
                return 0

            # ML 리랭킹 (데이터 충분 시 자동 활성화)
            try:
                from src.ml.scorer import MLScorer
                ml_scorer = MLScorer()
                if ml_scorer.is_ready(session):
                    cand_dicts = [
                        {"stock_id": r.stock_id, "ticker": r.ticker,
                         "total_score": r.total_score}
                        for r in recommendations
                    ]
                    reranked = ml_scorer.rank(session, cand_dicts)
                    # 재랭킹된 점수/순위 반영
                    score_map = {d["stock_id"]: d["total_score"] for d in reranked}
                    for i, r in enumerate(recommendations):
                        if r.stock_id in score_map:
                            r.total_score = score_map[r.stock_id]
                    recommendations.sort(key=lambda x: x.total_score, reverse=True)
                    for i, r in enumerate(recommendations):
                        r.rank = i + 1
                    logger.info("ML 리랭킹 적용 완료")
                else:
                    status = ml_scorer.get_status(session)
                    logger.info("ML: %s", status["status"])
            except Exception as e:
                logger.debug("ML 리랭킹 스킵: %s", e)

            if recommendations:
                # 같은 날짜 기존 추천 삭제 (재실행 시 중복 방지)
                from src.db.models import FactDailyRecommendation
                session.query(FactDailyRecommendation).filter(
                    FactDailyRecommendation.run_date_id == self.run_date_id
                ).delete()
                session.flush()

                rec_dicts = [
                    {
                        "stock_id": r.stock_id,
                        "rank": r.rank,
                        "total_score": r.total_score,
                        "technical_score": r.technical_score,
                        "fundamental_score": r.fundamental_score,
                        "smart_money_score": r.smart_money_score,
                        "external_score": r.external_score,
                        "momentum_score": r.momentum_score,
                        "recommendation_reason": r.recommendation_reason,
                        "price_at_recommendation": r.price_at_recommendation,
                    }
                    for r in recommendations
                ]
                RecommendationRepository.create_batch(
                    session, self.run_date_id, rec_dicts
                )

            # 추천 종목 개별 뉴스 수집 + 감성 분석 + 연결
            try:
                from src.data.news_scraper import scrape_news
                from src.analysis.external import analyze_news_sentiment
                from sqlalchemy import select as sa_select
                from src.db.models import DimStock as DimStockModel, FactNews

                for r in recommendations[:self.top_n]:
                    stock = session.execute(
                        sa_select(DimStockModel).where(DimStockModel.stock_id == r.stock_id)
                    ).scalar_one_or_none()
                    if stock is None:
                        continue

                    articles = scrape_news(stock.ticker, count=5)
                    if articles:
                        # 감성 분석
                        sentiment = analyze_news_sentiment(articles)
                        article_dicts = [
                            {
                                "title": a.title, "summary": a.summary,
                                "url": a.url, "source": a.source,
                                "published_at": a.published_at,
                                "sentiment_score": sentiment,
                            }
                            for a in articles
                        ]
                        NewsRepository.upsert_by_url(session, article_dicts)
                        # 뉴스-종목 연결
                        for a in articles:
                            news_row = session.execute(
                                sa_select(FactNews).where(FactNews.url == a.url)
                            ).scalar_one_or_none()
                            if news_row:
                                NewsRepository.link_to_stocks(
                                    session, news_row.news_id, [r.stock_id], relevance=0.8
                                )

                console.print(f"  [dim]추천 종목 뉴스 {self.top_n}개 수집 완료[/dim]")
            except Exception as e:
                logger.warning("추천 종목 뉴스 수집 실패: %s", e)

            # 과거 추천 사후 수익률 업데이트
            try:
                from src.analysis.screener import update_recommendation_returns
                from src.analysis.technical import load_date_map, prices_to_dataframe

                ret_date_map = load_date_map(session)
                prices_map = {}
                for stock in StockRepository.get_sp500_active(session):
                    df = prices_to_dataframe(session, stock.stock_id, date_map=ret_date_map)
                    if not df.empty:
                        prices_map[stock.stock_id] = df

                ret_updated = update_recommendation_returns(session, prices_map)
                if ret_updated:
                    console.print(f"  [dim]과거 추천 수익률 {ret_updated}건 업데이트[/dim]")
            except Exception as e:
                logger.warning("수익률 업데이트 실패: %s", e)

            return len(recommendations)

    def step5_report(self) -> int:
        """데일리 리포트 생성."""
        from src.reports.terminal import render_daily_report
        from src.reports.assembler import assemble_enriched_report

        # AI 피드백 자동 수집 (멀티 호라이즌, 시간 감쇠 가중치 포함)
        try:
            from src.ai.feedback import collect_multi_horizon_feedback
            from src.config import get_settings
            settings = get_settings()
            horizons = [int(h.strip()) for h in settings.feedback_horizons.split(",")]
            halflife = settings.feedback_decay_halflife
            with get_session(self.engine) as session:
                fb_count = collect_multi_horizon_feedback(
                    session, horizons=horizons, halflife_days=halflife,
                )
                if fb_count:
                    console.print(f"  [dim]AI 피드백 {fb_count}건 수집 (호라이즌: {horizons})[/dim]")
        except Exception as e:
            logger.debug("AI 피드백 수집 스킵: %s", e)

        # 교훈 관리: 만료 + 효과성 업데이트 + 조건별 캘리브레이션 갱신
        try:
            from src.ai.lesson_store import expire_old_lessons, update_lesson_effectiveness
            from src.ai.calibrator import build_condition_calibration
            from src.db.helpers import date_to_id as _d2i

            with get_session(self.engine) as session:
                expired = expire_old_lessons(session, self.target_date)
                eff_updated = update_lesson_effectiveness(session, self.target_date)
                cutoff_id = _d2i(self.target_date - timedelta(days=35))
                cal_horizons = ["5d", "10d", "20d", "60d"]
                cal_cells = build_condition_calibration(session, cutoff_id, horizons=cal_horizons)
                parts: list[str] = []
                if expired:
                    parts.append(f"만료 {expired}건")
                if eff_updated:
                    parts.append(f"효과성 {eff_updated}건")
                if cal_cells:
                    parts.append(f"캘리브레이션 {len(cal_cells)}셀")
                if parts:
                    console.print(f"  [dim]교훈 관리: {', '.join(parts)}[/dim]")
        except Exception as e:
            logger.debug("교훈 관리 스킵: %s", e)

        with get_session(self.engine) as session:
            report = assemble_enriched_report(session, self.target_date, self.run_date_id)

            # 어제 대비 비교
            diff_summary = None
            try:
                from src.reports.comparator import compare_recommendations, format_diff_summary
                from src.db.models import FactDailyRecommendation, DimStock

                # 전일 추천 조회
                prev_recs = (
                    session.query(FactDailyRecommendation)
                    .filter(FactDailyRecommendation.run_date_id < self.run_date_id)
                    .order_by(FactDailyRecommendation.run_date_id.desc())
                    .limit(20)
                    .all()
                )
                if prev_recs:
                    prev_date_id = prev_recs[0].run_date_id
                    prev_recs = [r for r in prev_recs if r.run_date_id == prev_date_id]

                    ticker_map = {s.stock_id: s.ticker for s in session.query(DimStock).all()}
                    name_map = {s.stock_id: s.name for s in session.query(DimStock).all()}

                    today_list = [
                        {"ticker": r.ticker, "name": r.name or r.ticker, "rank": r.rank}
                        for r in report.recommendations
                    ]
                    yesterday_list = [
                        {"ticker": ticker_map.get(r.stock_id, "?"), "name": name_map.get(r.stock_id, "?"), "rank": r.rank}
                        for r in prev_recs
                    ]

                    prev_macro = MacroRepository.get_by_date_id(session, prev_date_id) if hasattr(MacroRepository, 'get_by_date_id') else None
                    prev_market_score = prev_macro.market_score if prev_macro and prev_macro.market_score else 5

                    diff = compare_recommendations(
                        today_list, yesterday_list,
                        today_market_score=report.macro.market_score or 5,
                        yesterday_market_score=prev_market_score,
                    )
                    diff_summary = format_diff_summary(diff)
                    if diff.has_changes:
                        console.print(f"  [dim]vs 어제: {diff_summary}[/dim]")
            except Exception as e:
                logger.debug("리포트 비교 스킵: %s", e)

        # 터미널 출력
        try:
            render_daily_report(report)
        except Exception as e:
            logger.warning("터미널 출력 실패: %s", e)

        # 파일 저장 (assembler 재호출 대신 직접 저장)
        from src.reports.daily_report import _save_json, _save_markdown
        from pathlib import Path
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        _save_json(report, reports_dir / f"{self.target_date.isoformat()}.json")
        _save_markdown(report, reports_dir / f"{self.target_date.isoformat()}.md")
        logger.info("리포트 저장 완료: %s", reports_dir)

        return 1

    def step4_5_ai_analysis(self) -> int:
        """Claude Code CLI를 통한 AI 분석 (필수 단계).

        CLI 미설치 시 프롬프트만 저장하고 ai_approved=None 유지.
        """
        settings = get_settings()
        if not settings.ai_enabled:
            logger.info("AI 분석 비활성화 (INVESTMATE_AI_ENABLED=false)")
            console.print("  [dim]AI 분석 비활성화됨[/dim]")
            return 0

        from src.ai.claude_analyzer import (
            is_claude_available,
            parse_ai_response,
            run_analysis,
            save_analysis,
        )
        from src.reports.prompt_builder import build_prompt, build_unified_prompt, save_prompt

        # 통합 프롬프트 생성 (Round 1+2 단일 호출 시도)
        with get_session(self.engine) as session:
            prompt = build_unified_prompt(
                session, self.run_date_id, self.target_date, deep_dive=True,
            )
            save_prompt(prompt, self.target_date)

        if not is_claude_available():
            console.print("  [yellow]Claude Code CLI 미설치 -- 프롬프트만 저장, AI 분석은 수동으로 실행하세요[/yellow]")
            console.print(f"  [dim]프롬프트: reports/{self.target_date.isoformat()}_prompt.txt[/dim]")
            return 0

        settings = get_settings()

        # ── 토론 모드 분기 ──
        if settings.ai_mode == "debate":
            try:
                from src.ai.debate import run_debate, save_debate_rounds

                console.print("  [dim]멀티 에이전트 토론 모드[/dim]")

                # 제약 규칙 생성
                _debate_constraints = None
                try:
                    from src.ai.feedback import generate_constraint_rules
                    from src.analysis.regime import detect_regime

                    with get_session(self.engine) as _dsess:
                        _dregime = detect_regime(_dsess)
                        _dmacro = MacroRepository.get_latest(_dsess)
                        _dvix = float(_dmacro.vix) if _dmacro and _dmacro.vix else None
                        _debate_constraints = generate_constraint_rules(
                            _dsess, vix=_dvix, regime=_dregime.regime,
                        )
                except Exception as e:
                    logger.debug("토론 제약 생성 실패: %s", e)

                debate_result = run_debate(
                    stock_data_prompt=prompt,
                    constraints=_debate_constraints,
                    model=settings.ai_model_analysis,
                    timeout=settings.ai_timeout,
                )
                parsed = debate_result.final_parsed
                tool_deep_dive = debate_result.deep_dive

                # 토론 라운드 DB 저장
                with get_session(self.engine) as session:
                    save_debate_rounds(session, self.run_date_id, debate_result)

                console.print(
                    f"  [dim]토론 완료: 합의 {debate_result.consensus_strength}, "
                    f"{len(parsed)}종목 판정[/dim]"
                )

                if not parsed:
                    console.print("  [yellow]토론 결과 파싱 실패 — legacy 모드 폴백[/yellow]")
                    _skip_legacy = False
                else:
                    # 토론 성공 — calibration/validation으로 점프
                    # (Phase 5~7 코드와 동일한 parsed 처리)
                    save_analysis(json.dumps(
                        {"debate_mode": True, "consensus": debate_result.consensus_strength,
                         "parsed_count": len(parsed)},
                        ensure_ascii=False, indent=2,
                    ), self.target_date)
                    # 아래 legacy 분석 건너뛰고 바로 calibration으로
                    ai_backend = "debate"
                    # goto calibration phase (skip legacy block)
                    # Python doesn't have goto, so we use a flag
                    _skip_legacy = True
            except Exception as e:
                logger.warning("토론 모드 실패, legacy 폴백: %s", e)
                console.print(f"  [yellow]토론 실패: {e} — legacy 폴백[/yellow]")
                _skip_legacy = False
        else:
            _skip_legacy = False

        if not _skip_legacy:
            # ── 레거시 모드 (기존 단일 호출) ──
            from src.ai.cache import get_cached_response, save_cached_response
            cached = get_cached_response(self.target_date, prompt)
            ai_backend = "cached"
            if cached:
                console.print("  [dim]AI 캐시 히트 -- 이전 분석 결과 재사용[/dim]")
                response: dict | str | None = cached
            else:
                # Round 1: Tool Use -> SDK -> CLI 순으로 시도
                console.print("  [dim]AI Round 1: 스크리닝 분석 중...[/dim]")
                response, ai_backend = run_analysis(
                    prompt,
                    timeout=settings.ai_timeout,
                    model=settings.ai_model_analysis,
                )
            if response is None:
                console.print("  [yellow]AI Round 1 실패, 재시도 중...[/yellow]")
                response, ai_backend = run_analysis(prompt[:len(prompt) // 2], timeout=180)
                if response is None:
                    console.print("  [red]AI 분석 실패 -- 프롬프트만 저장됨[/red]")
                    return 0

            logger.info("AI 백엔드: %s", ai_backend)

            # Tool Use dict인 경우 파싱
            if isinstance(response, dict):
                console.print("  [dim]Tool Use 구조화 출력 수신[/dim]")
                from src.ai.claude_analyzer import _try_parse_json
                parsed = _try_parse_json(json.dumps(response))
                if parsed is None:
                    parsed = []
                    analysis_map = {
                        item["ticker"]: item
                        for item in response.get("analysis", [])
                        if "ticker" in item
                    }
                    for ticker in response.get("approved", []):
                        entry: dict = {"ticker": ticker, "ai_approved": True}
                        item = analysis_map.get(ticker, {})
                        entry["ai_reason"] = item.get("reason", "")
                        if item.get("target_price"):
                            entry["ai_target_price"] = float(item["target_price"])
                        if item.get("stop_loss"):
                            entry["ai_stop_loss"] = float(item["stop_loss"])
                        if item.get("confidence"):
                            entry["ai_confidence"] = max(1, min(10, int(item["confidence"])))
                        if item.get("risk_level"):
                            entry["ai_risk_level"] = str(item["risk_level"]).upper()
                        if item.get("entry_strategy"):
                            entry["entry_strategy"] = str(item["entry_strategy"])
                        if item.get("exit_strategy"):
                            entry["exit_strategy"] = str(item["exit_strategy"])
                        parsed.append(entry)
                    for ticker in response.get("excluded", []):
                        entry = {"ticker": ticker, "ai_approved": False}
                        item = analysis_map.get(ticker, {})
                        entry["ai_reason"] = item.get("reason", "")
                        parsed.append(entry)
                tool_deep_dive = response.get("deep_dive")
                save_analysis(json.dumps(response, ensure_ascii=False, indent=2), self.target_date)
            else:
                tool_deep_dive = None
                save_analysis(response, self.target_date)
                if not cached:
                    save_cached_response(self.target_date, prompt, response)
                console.print("  [dim]AI 응답 수신 완료, 파싱 중...[/dim]")
                parsed = parse_ai_response(response)

        # AI 캘리브레이션 (과거 편향 기반 보정)
        try:
            from src.ai.calibrator import apply_calibration, calculate_calibration
            with get_session(self.engine) as session:
                # look-ahead bias 방지: ~25 거래일(35일) 이전 추천 피드백만 사용
                cutoff = date_to_id(self.target_date - timedelta(days=35))
                calibration = calculate_calibration(session, cutoff_date_id=cutoff)
            if calibration.sample_size >= 5:
                parsed = apply_calibration(parsed, calibration)
                adj_info = f"목표가x{calibration.target_adjustment}"
                if calibration.is_optimistic:
                    adj_info += " (과대추정 보정)"
                console.print(f"  [dim]캘리브레이션 적용: {adj_info}[/dim]")
        except Exception as e:
            logger.debug("캘리브레이션 스킵: %s", e)

        # AI 결과 검증 + 자동 보정
        from src.ai.validator import validate_ai_results
        with get_session(self.engine) as session:
            recs_for_prices = RecommendationRepository.get_by_date(session, self.run_date_id)
            price_map = {}
            for r in recs_for_prices:
                if r.price_at_recommendation:
                    from sqlalchemy import select as _sel
                    from src.db.models import DimStock as _DS
                    st = session.execute(_sel(_DS).where(_DS.stock_id == r.stock_id)).scalar_one_or_none()
                    if st:
                        price_map[st.ticker] = float(r.price_at_recommendation)
        ai_warnings = validate_ai_results(parsed, price_map)

        # 제약 규칙 강제 적용
        try:
            from src.ai.feedback import generate_constraint_rules
            from src.ai.validator import enforce_constraints
            from src.analysis.regime import detect_regime

            with get_session(self.engine) as _sess:
                _regime = detect_regime(_sess)
                _macro = MacroRepository.get_latest(_sess)
                _vix = float(_macro.vix) if _macro and _macro.vix else None
                _constraints = generate_constraint_rules(
                    _sess, vix=_vix, regime=_regime.regime,
                )
            constraint_warnings = enforce_constraints(parsed, _constraints, price_map)
            ai_warnings.extend(constraint_warnings)
            if constraint_warnings:
                console.print(
                    f"  [yellow]제약 적용: {len(constraint_warnings)}건 수정[/yellow]"
                )
        except Exception as e:
            logger.warning("제약 규칙 적용 실패: %s", e)

        if ai_warnings:
            console.print(f"  [yellow]AI 검증 경고 {len(ai_warnings)}건[/yellow]")

        updated = 0

        with get_session(self.engine) as session:
            from sqlalchemy import select as sel
            from src.db.models import DimStock

            recs = RecommendationRepository.get_by_date(session, self.run_date_id)
            rec_map = {}
            for rec in recs:
                stock = session.execute(
                    sel(DimStock).where(DimStock.stock_id == rec.stock_id)
                ).scalar_one_or_none()
                if stock:
                    rec_map[stock.ticker] = rec

            # AI가 언급한 종목 업데이트
            mentioned_tickers: set[str] = set()
            for p in parsed:
                ticker = p.get("ticker")
                if not ticker:
                    continue
                mentioned_tickers.add(ticker)
                rec = rec_map.get(ticker)
                if rec is None:
                    continue
                rec.ai_approved = p.get("ai_approved", True)
                rec.ai_reason = p.get("ai_reason")
                if p.get("ai_target_price"):
                    rec.ai_target_price = p["ai_target_price"]
                if p.get("ai_stop_loss"):
                    rec.ai_stop_loss = p["ai_stop_loss"]
                if p.get("ai_confidence"):
                    rec.ai_confidence = p["ai_confidence"]
                if p.get("ai_risk_level"):
                    rec.ai_risk_level = p["ai_risk_level"]
                if p.get("entry_strategy"):
                    rec.ai_entry_strategy = p["entry_strategy"]
                if p.get("exit_strategy"):
                    rec.ai_exit_strategy = p["exit_strategy"]
                updated += 1

            # AI가 언급하지 않은 종목 → 기본 승인 (benefit of the doubt)
            for ticker, rec in rec_map.items():
                if ticker not in mentioned_tickers and rec.ai_approved is None:
                    rec.ai_approved = True
                    rec.ai_confidence = 5
                    rec.ai_reason = "AI가 명시적으로 제외하지 않음 (수치 스크리닝 통과)"
                    updated += 1

            session.flush()

        # Round 2: 딥다이브 (추천 종목이 3개 이상이면)
        # Tool Use로 deep_dive가 이미 포함되었으면 Round 2 스킵
        approved_tickers = [p["ticker"] for p in parsed if p.get("ai_approved")]
        if tool_deep_dive and len(tool_deep_dive) > 0:
            console.print(f"  [dim]Tool Use에 딥다이브 포함 -- Round 2 스킵[/dim]")
            # deep_dive 결과로 entry/exit 전략 업데이트
            with get_session(self.engine) as session:
                recs = RecommendationRepository.get_by_date(session, self.run_date_id)
                dd_rec_map = {}
                for rec in recs:
                    stock = session.execute(
                        sel(DimStock).where(DimStock.stock_id == rec.stock_id)
                    ).scalar_one_or_none()
                    if stock:
                        dd_rec_map[stock.ticker] = rec
                for dd_item in tool_deep_dive:
                    rec = dd_rec_map.get(dd_item.get("ticker"))
                    if rec and dd_item.get("entry_plan"):
                        rec.ai_entry_strategy = dd_item["entry_plan"]
                session.flush()
        elif len(approved_tickers) >= 3:
            try:
                from src.reports.prompt_builder import build_deep_dive_prompt
                from src.reports.assembler import assemble_enriched_report as _assemble
                with get_session(self.engine) as session:
                    dd_report = _assemble(session, self.target_date, self.run_date_id)
                dd_prompt = build_deep_dive_prompt(approved_tickers, dd_report)
                console.print(f"  [dim]AI Round 2: 딥다이브 분석 ({len(approved_tickers)}종목)...[/dim]")
                dd_response, _ = run_analysis(dd_prompt, timeout=180)
                dd_parsed: list[dict] = []
                dd_text_response: str | None = None
                if isinstance(dd_response, dict):
                    from src.ai.claude_analyzer import _try_parse_json
                    dd_parsed = _try_parse_json(json.dumps(dd_response)) or []
                    dd_text_response = json.dumps(dd_response, ensure_ascii=False, indent=2)
                elif dd_response:
                    dd_parsed = parse_ai_response(dd_response)
                    dd_text_response = dd_response

                if dd_parsed:
                    with get_session(self.engine) as session:
                        recs = RecommendationRepository.get_by_date(session, self.run_date_id)
                        dd_rec_map = {}
                        for rec in recs:
                            stock = session.execute(
                                sel(DimStock).where(DimStock.stock_id == rec.stock_id)
                            ).scalar_one_or_none()
                            if stock:
                                dd_rec_map[stock.ticker] = rec
                        for p in dd_parsed:
                            rec = dd_rec_map.get(p.get("ticker"))
                            if rec and p.get("entry_strategy"):
                                rec.ai_entry_strategy = p["entry_strategy"]
                            if rec and p.get("exit_strategy"):
                                rec.ai_exit_strategy = p["exit_strategy"]
                            if rec and p.get("ai_target_price"):
                                rec.ai_target_price = p["ai_target_price"]
                            if rec and p.get("ai_stop_loss"):
                                rec.ai_stop_loss = p["ai_stop_loss"]
                        session.flush()
                    console.print("  [dim]Round 2 완료: 전략 업데이트[/dim]")

                if dd_text_response:
                    from pathlib import Path as _P
                    dd_path = _P("reports/ai_analysis") / f"{self.target_date.isoformat()}_ai_deep_dive.md"
                    dd_path.parent.mkdir(parents=True, exist_ok=True)
                    dd_path.write_text(dd_text_response, encoding="utf-8")
            except Exception as e:
                logger.warning("AI Round 2 실패 (Round 1 결과는 유지): %s", e)

        console.print(f"  AI 분석 완료: {updated}개 종목 업데이트")
        return updated

    def step4_6_position_sizing(self) -> int:
        """포지션 사이징 + 리스크 제약 + 손절가 산출."""
        settings = get_settings()
        if not settings.sizing_enabled:
            console.print("  [dim]포지션 사이징 비활성화됨[/dim]")
            return 0

        from src.db.models import FactDailyRecommendation
        from src.portfolio.drawdown_manager import (
            DrawdownConfig,
            apply_drawdown_reduction,
            check_portfolio_drawdown,
            compute_stop_loss,
        )
        from src.portfolio.position_sizer import PositionSizingInput, size_positions
        from src.portfolio.risk_constraints import (
            RiskConstraints,
            check_and_adjust,
        )

        with get_session(self.engine) as session:
            recs = RecommendationRepository.get_by_date(session, self.run_date_id)
            if not recs:
                console.print("  [dim]추천 종목 없음, 사이징 스킵[/dim]")
                return 0

            # AI 승인 종목만 (ai_approved=True 또는 None)
            approved = [r for r in recs if r.ai_approved is not False]
            if not approved:
                console.print("  [dim]AI 승인 종목 없음[/dim]")
                return 0

            # 종목 정보 배치 로드 (N+1 방지)
            from sqlalchemy import select as sel
            from src.db.models import DimStock

            stock_ids = [rec.stock_id for rec in approved]
            stocks = session.execute(
                sel(DimStock).where(DimStock.stock_id.in_(stock_ids))
            ).scalars().all()
            stock_by_id = {s.stock_id: s for s in stocks}

            ticker_map: dict[int, str] = {}
            sector_map: dict[str, str | None] = {}
            for sid, stock in stock_by_id.items():
                ticker_map[sid] = stock.ticker
                sector_name = None
                if stock.sector:
                    sector_name = stock.sector.sector_name
                sector_map[stock.ticker] = sector_name

            # 60일 가격 히스토리 로드
            import numpy as np
            import pandas as pd

            price_data: dict[str, pd.Series] = {}
            ohlc_data: dict[str, dict] = {}  # ticker -> {highs, lows, closes}
            volume_data: dict[str, float] = {}

            lookback_start = self.target_date - timedelta(days=120)
            for rec in approved:
                ticker = ticker_map.get(rec.stock_id)
                if not ticker:
                    continue
                prices = DailyPriceRepository.get_prices(
                    session, rec.stock_id,
                    start_date=lookback_start, end_date=self.target_date,
                )
                if not prices or len(prices) < 10:
                    continue
                closes = [float(p.close) for p in prices]
                price_data[ticker] = pd.Series(closes)
                ohlc_data[ticker] = {
                    "highs": [float(p.high) for p in prices],
                    "lows": [float(p.low) for p in prices],
                    "closes": closes,
                }
                if prices:
                    vol = float(prices[-1].volume) if prices[-1].volume else 0.0
                    volume_data[ticker] = vol

            if not price_data:
                console.print("  [dim]가격 데이터 부족, 사이징 스킵[/dim]")
                return 0

            # 수익률 행렬 + 공분산
            from src.portfolio.optimizer import _build_return_matrix

            returns_df = _build_return_matrix(price_data)
            tickers_order = list(returns_df.columns)
            cov_matrix = None
            if len(returns_df) >= 10 and len(tickers_order) >= 2:
                try:
                    from sklearn.covariance import LedoitWolf
                    lw = LedoitWolf()
                    cov_matrix = lw.fit(returns_df.values).covariance_
                except Exception:
                    cov_matrix = returns_df.cov().values

            # 연환산 변동성 계산
            annual_vols: dict[str, float] = {}
            for ticker in tickers_order:
                if ticker in returns_df.columns:
                    daily_vol = returns_df[ticker].std()
                    annual_vols[ticker] = float(daily_vol * np.sqrt(252))

            # 사이징 입력 구성
            inputs = []
            rec_by_ticker: dict[str, FactDailyRecommendation] = {}
            for rec in approved:
                ticker = ticker_map.get(rec.stock_id)
                if not ticker or ticker not in tickers_order:
                    continue
                rec_by_ticker[ticker] = rec
                inputs.append(PositionSizingInput(
                    ticker=ticker,
                    stock_id=rec.stock_id,
                    volatility=annual_vols.get(ticker, 0.20),
                    ai_confidence=rec.ai_confidence,
                    sector=sector_map.get(ticker),
                    price=float(rec.price_at_recommendation or 0),
                    daily_volume=volume_data.get(ticker),
                ))

            if not inputs:
                return 0

            # 포지션 사이징
            target_vol = settings.target_volatility_pct / 100.0
            sizing_result = size_positions(
                inputs=inputs,
                cov_matrix=cov_matrix,
                strategy=settings.sizing_strategy,
                target_vol=target_vol,
                risk_free_rate=settings.risk_free_rate_pct / 100.0,
            )

            # 리스크 제약 적용
            risk_constraints = RiskConstraints(
                max_single_stock_pct=settings.max_single_stock_pct,
                max_sector_pct=settings.max_sector_weight_pct,
                daily_var_limit=settings.daily_var_limit_pct / 100.0,
            )
            constraint_result = check_and_adjust(
                weights=sizing_result.weights,
                sector_map=sector_map,
                cov_matrix=cov_matrix,
                tickers_order=tickers_order,
                constraints=risk_constraints,
            )

            final_weights = constraint_result.adjusted_weights

            # 드로다운 확인 + 축소
            dd_config = DrawdownConfig(
                portfolio_trailing_stop_pct=settings.portfolio_trailing_stop_pct / 100.0,
                atr_stop_multiplier=settings.atr_stop_multiplier,
            )
            dd_state = check_portfolio_drawdown(session, self.run_date_id, dd_config)
            if dd_state.is_triggered:
                final_weights = apply_drawdown_reduction(final_weights, dd_state)
                console.print(
                    f"  [yellow]드로다운 트리거: {dd_state.drawdown_pct:.1%} "
                    f"→ 노출도 {dd_state.exposure_multiplier:.0%}[/yellow]"
                )

            # 턴오버 관리
            from src.portfolio.turnover import (
                TurnoverConfig,
                apply_hold_rules,
                calculate_turnover,
                get_previous_weights,
            )

            turnover_config = TurnoverConfig(
                annualized_warn_threshold=settings.turnover_warn_threshold,
                hold_score_floor_pct=settings.turnover_hold_floor_pct,
            )
            old_weights = get_previous_weights(session, self.run_date_id)
            turnover_stats = None

            if old_weights:
                scores_map = {
                    ticker_map.get(r.stock_id, ""): float(r.total_score)
                    for r in approved if r.stock_id in ticker_map
                }
                stop_map: dict[str, bool] = {}
                final_weights = apply_hold_rules(
                    final_weights, old_weights, scores_map, stop_map,
                    turnover_config,
                )
                turnover_stats = calculate_turnover(
                    final_weights, old_weights, turnover_config,
                )
                if turnover_stats.is_excessive:
                    console.print(
                        f"  [yellow]턴오버 경고: 연환산 "
                        f"{turnover_stats.annualized_turnover:.0%}[/yellow]"
                    )

            # 실행 비용 계산
            cost_result = None
            if settings.execution_cost_enabled:
                from src.portfolio.execution_cost import (
                    ExecutionCostConfig,
                    estimate_portfolio_cost,
                )

                # ADTV (20일 평균 거래량) 계산
                adtv_map: dict[str, float] = {}
                for ticker, ohlc in ohlc_data.items():
                    volumes = []
                    prices_list = DailyPriceRepository.get_prices(
                        session,
                        next(
                            (r.stock_id for r in approved
                             if ticker_map.get(r.stock_id) == ticker),
                            0,
                        ),
                        start_date=self.target_date - timedelta(days=40),
                        end_date=self.target_date,
                    )
                    for p in prices_list[-20:]:
                        if p.volume:
                            volumes.append(float(p.volume))
                    if volumes:
                        adtv_map[ticker] = sum(volumes) / len(volumes)

                exec_config = ExecutionCostConfig(
                    enabled=True,
                    spread_bps=settings.spread_bps,
                    impact_coefficient=settings.impact_coefficient,
                    max_participation_rate=settings.max_participation_rate,
                )
                price_map_for_cost = {
                    ticker_map.get(r.stock_id, ""): float(r.price_at_recommendation or 0)
                    for r in approved if r.stock_id in ticker_map
                }
                cost_result = estimate_portfolio_cost(
                    weights=final_weights,
                    price_map=price_map_for_cost,
                    volatility_map={t: v / np.sqrt(252) for t, v in annual_vols.items()},
                    adtv_map=adtv_map,
                    config=exec_config,
                )
                if cost_result.capacity_limited_tickers:
                    console.print(
                        f"  [yellow]용량 초과: "
                        f"{', '.join(cost_result.capacity_limited_tickers)}[/yellow]"
                    )

            # 손절가 계산 + DB 업데이트
            updated = 0
            cost_map = {}
            if cost_result:
                cost_map = {cb.ticker: cb for cb in cost_result.breakdowns}

            for rec in approved:
                ticker = ticker_map.get(rec.stock_id)
                if not ticker:
                    continue

                weight = final_weights.get(ticker, 0.0)
                rec.position_weight = round(weight, 6)
                rec.sizing_strategy = settings.sizing_strategy

                # 실행 비용 기록
                cb = cost_map.get(ticker)
                if cb:
                    rec.spread_cost_bps = cb.spread_bps
                    rec.impact_cost_bps = cb.impact_bps
                    rec.total_cost_bps = cb.total_bps

                # 턴오버 기록
                if turnover_stats:
                    rec.daily_turnover = turnover_stats.daily_turnover

                # ATR 손절가
                ohlc = ohlc_data.get(ticker)
                if ohlc and rec.price_at_recommendation:
                    sl = compute_stop_loss(
                        ticker=ticker,
                        entry_price=float(rec.price_at_recommendation),
                        ai_stop_loss=float(rec.ai_stop_loss) if rec.ai_stop_loss else None,
                        highs=ohlc["highs"],
                        lows=ohlc["lows"],
                        closes=ohlc["closes"],
                        config=dd_config,
                    )
                    rec.trailing_stop = sl.stop_price
                    rec.atr_stop = sl.stop_price if sl.stop_type == "atr" else None

                updated += 1

            # AI 거부 종목은 비중 0
            for rec in recs:
                if rec.ai_approved is False:
                    rec.position_weight = 0.0
                    rec.sizing_strategy = settings.sizing_strategy

            session.flush()

            # 경고 출력
            for v in constraint_result.violations:
                console.print(f"  [yellow]제약 위반: {v.description}[/yellow]")
            for w in constraint_result.warnings:
                console.print(f"  [dim]경고: {w.description}[/dim]")

            total_exposure = sum(final_weights.values())
            cash = max(0.0, 1.0 - total_exposure)
            cost_info = ""
            if cost_result:
                cost_info = f", 비용: {cost_result.portfolio_avg_cost_bps:.1f}bps"
                if cost_result.max_aum_estimate:
                    cost_info += f", 용량: ${cost_result.max_aum_estimate:,.0f}"
            console.print(
                f"  총 노출도: {total_exposure:.1%}, 현금: {cash:.1%}, "
                f"전략: {settings.sizing_strategy}{cost_info}"
            )

        return updated

    def step4_7_factor_returns(self) -> int:
        """팩터 수익률 계산 + 저장."""
        try:
            from src.analysis.factor_returns import (
                compute_daily_factor_returns,
                store_factor_returns,
            )

            with get_session(self.engine) as session:
                spreads = compute_daily_factor_returns(session, self.target_date)
                if not spreads:
                    console.print("  [dim]팩터 수익률 데이터 부족[/dim]")
                    return 0
                count = store_factor_returns(session, spreads)
            return count
        except Exception as e:
            logger.warning("팩터 수익률 계산 실패: %s", e)
            return 0

    def step6_notify(self) -> int:
        """알림 발송."""
        if self.skip_notify:
            console.print("  [dim]알림 스킵[/dim]")
            return 0

        settings = get_settings()
        channel = getattr(settings, "notify_channels", None)
        if not channel:
            console.print("  [dim]알림 채널 미설정[/dim]")
            return 0

        from src.alerts.notifier import send_daily_summary

        with get_session(self.engine) as session:
            macro = MacroRepository.get_latest(session)
            recs = RecommendationRepository.get_by_date(session, self.run_date_id)
            tickers = []
            for rec in recs:
                stock = session.execute(
                    __import__("sqlalchemy").select(
                        __import__("src.db.models", fromlist=["DimStock"]).DimStock
                    ).where(
                        __import__("src.db.models", fromlist=["DimStock"]).DimStock.stock_id == rec.stock_id
                    )
                ).scalar_one_or_none()
                if stock:
                    tickers.append(stock.ticker)

        mood = "미정"
        score = None
        if macro:
            score = macro.market_score
            mood = "강세" if (score or 0) >= 7 else ("중립" if (score or 0) >= 4 else "약세")

        success = send_daily_summary(
            run_date=self.target_date,
            market_mood=mood,
            top_tickers=tickers,
            market_score=score,
            channel=channel,
        )
        return 1 if success else 0

    def _log_step(
        self, step: str, status: str, started: datetime,
        records_count: int = 0, message: str | None = None,
    ) -> None:
        """파이프라인 단계를 로깅한다."""
        try:
            with get_session(self.engine) as session:
                ensure_date_ids(session, [self.target_date])
                CollectionLogRepository.log_step(
                    session, self.run_date_id, step, status,
                    started_at=started, finished_at=datetime.now(),
                    records_count=records_count, message=message,
                )
        except Exception as e:
            logger.error("로그 기록 실패: %s", e)
