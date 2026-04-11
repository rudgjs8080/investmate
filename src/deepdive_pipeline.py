"""Deep Dive 전용 파이프라인 오케스트레이터."""

from __future__ import annotations

import json
import logging
import signal
from datetime import date, datetime
from pathlib import Path

from rich.console import Console
from sqlalchemy import select
from sqlalchemy.engine import Engine

from src.config import get_settings
from src.db.engine import get_session
from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import FactCollectionLog, FactDailyPrice
from src.db.repository import (
    CollectionLogRepository,
    DailyPriceRepository,
    DeepDiveRepository,
    StockRepository,
    WatchlistRepository,
)
from src.deepdive.schemas import AIResult, ChangeRecord, DeepDiveResult
from src.deepdive.watchlist_manager import WatchlistEntry, load_watchlist

logger = logging.getLogger(__name__)
console = Console()


class DeepDivePipeline:
    """Deep Dive 전용 8단계 파이프라인.

    DailyPipeline 패턴 복제: signal handling, checkpointing, resilient.
    """

    def __init__(
        self,
        engine: Engine,
        target_date: date | None = None,
        ticker: str | None = None,
        force: bool = False,
        skip_notify: bool = False,
    ):
        self.engine = engine
        self.target_date = target_date or date.today()
        self.ticker = ticker.upper() if ticker else None
        self.force = force
        self.skip_notify = skip_notify
        self.run_date_id = date_to_id(self.target_date)
        self._interrupted = False

        # step 간 데이터 전달
        self._watchlist_entries: list[WatchlistEntry] = []
        self._layer_results: dict[str, dict] = {}
        self._ai_results: dict[str, AIResult] = {}
        self._debate_results: dict = {}  # ticker → CLIDebateResult
        self._pair_results: dict[str, list] = {}       # ticker → list[PeerComparison]
        self._change_results: dict[str, list] = {}     # ticker → list[ChangeRecord]
        self._execution_guides: dict = {}              # ticker → ExecutionGuide

        # 그레이스풀 셧다운
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
            signal.signal(signal.SIGINT, self._handle_signal)
        except (ValueError, OSError):
            pass

    def _handle_signal(self, signum, frame):
        """중단 신호 핸들러."""
        logger.warning("Deep Dive 파이프라인 중단 신호 (signal=%d)", signum)
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

    def _log_step(
        self, step: str, status: str, started: datetime,
        records_count: int = 0, message: str | None = None,
    ) -> None:
        """파이프라인 단계 로깅."""
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

    # ──────────────────────────────────────────
    # 실행
    # ──────────────────────────────────────────

    def run(self) -> None:
        """전체 파이프라인 실행."""
        with get_session(self.engine) as session:
            ensure_date_ids(session, [self.target_date])

        steps = [
            ("dd_s1_load", self.step1_load_watchlist),
            ("dd_s2_collect", self.step2_collect_extras),
            ("dd_s3_compute", self.step3_compute_layers),
            ("dd_s4_pairs", self.step4_pairs),
            ("dd_s5_ai", self.step5_ai_analysis),
            ("dd_s5_5_guide", self.step5_5_execution_guide),
            ("dd_s6_diff", self.step6_diff_detection),
            ("dd_s7_persist", self.step7_persist),
            ("dd_s8_notify", self.step8_notify),
        ]

        console.print(f"\n[bold]Deep Dive 파이프라인 시작[/bold] ({self.target_date})")
        if self.ticker:
            console.print(f"  대상: {self.ticker}")

        step_results: dict[str, dict] = {}

        for step_name, step_fn in steps:
            if self._interrupted:
                console.print("[yellow]중단 신호로 파이프라인 종료[/yellow]")
                self._log_step(step_name, "interrupted", datetime.now())
                break

            if not self.force and self._is_step_done(step_name):
                console.print(f"  [dim]{step_name} 이미 완료, 스킵[/dim]")
                continue

            started = datetime.now()
            console.print(f"\n  [cyan]{step_name}[/cyan] 실행 중...")
            try:
                count = step_fn()
                elapsed = (datetime.now() - started).total_seconds()
                self._log_step(step_name, "success", started, records_count=count)
                step_results[step_name] = {"status": "success", "records": count, "duration": elapsed}
                console.print(f"  [green]완료[/green] ({count}건, {elapsed:.1f}초)")
            except Exception as e:
                logger.error("%s 실패: %s", step_name, e, exc_info=True)
                self._log_step(step_name, "failed", started, message=str(e)[:500])
                step_results[step_name] = {"status": "failed", "error": str(e)}
                console.print(f"  [red]실패: {e}[/red]")

        console.print("\n[bold]Deep Dive 파이프라인 완료[/bold]")

        # 요약 JSON 저장
        self._save_summary(step_results)

    def _save_summary(self, step_results: dict) -> None:
        """실행 요약 JSON 저장."""
        try:
            logs_dir = Path("logs")
            logs_dir.mkdir(exist_ok=True)
            summary = {
                "date": self.target_date.isoformat(),
                "ticker": self.ticker,
                "force": self.force,
                "steps": step_results,
            }
            path = logs_dir / f"{self.target_date.isoformat()}_deepdive_summary.json"
            path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("요약 저장 실패: %s", e)

    # ──────────────────────────────────────────
    # Step 구현
    # ──────────────────────────────────────────

    def step1_load_watchlist(self) -> int:
        """dd_s1_load: 워치리스트 로드 + 자동 등록."""
        with get_session(self.engine) as session:
            entries = load_watchlist(session)

        if self.ticker:
            entries = [e for e in entries if e.ticker == self.ticker]

        self._watchlist_entries = entries
        console.print(f"    워치리스트: {len(entries)}종목")
        return len(entries)

    def step2_collect_extras(self) -> int:
        """dd_s2_collect: 비S&P500 또는 오늘 데이터 없는 종목 수집."""
        collected = 0
        for entry in self._watchlist_entries:
            try:
                with get_session(self.engine) as session:
                    # 오늘 가격 데이터 존재 여부 확인
                    has_today = session.execute(
                        select(FactDailyPrice)
                        .where(
                            FactDailyPrice.stock_id == entry.stock_id,
                            FactDailyPrice.date_id == self.run_date_id,
                        )
                    ).scalar_one_or_none()

                if has_today:
                    continue

                # 데이터 없으면 수집
                collected += self._collect_for_ticker(entry)
            except Exception as e:
                logger.warning("데이터 수집 실패 (%s): %s", entry.ticker, e)
        return collected

    def _collect_for_ticker(self, entry: WatchlistEntry) -> int:
        """단일 종목 데이터 보강."""
        from datetime import timedelta

        count = 0
        try:
            from src.data.providers.yfinance_provider import YFinancePriceProvider

            provider = YFinancePriceProvider()
            start = self.target_date - timedelta(days=7)
            result_tuple = provider.fetch_prices(
                [entry.ticker], start_date=start, end_date=self.target_date,
            )
            prices = result_tuple[0] if isinstance(result_tuple, tuple) else result_tuple
            if prices:
                with get_session(self.engine) as session:
                    ensure_date_ids(
                        session,
                        [p.date for p in prices.get(entry.ticker, [])],
                    )
                    count += DailyPriceRepository.upsert_prices_batch(
                        session, entry.stock_id, prices.get(entry.ticker, []),
                    )
        except Exception as e:
            logger.warning("가격 수집 실패 (%s): %s", entry.ticker, e)
        return count

    def step3_compute_layers(self) -> int:
        """dd_s3_compute: 종목별 6개 레이어 계산."""
        from src.deepdive.layers import compute_all_layers

        computed = 0
        for entry in self._watchlist_entries:
            try:
                with get_session(self.engine) as session:
                    stock = StockRepository.get_by_ticker(session, entry.ticker)
                    sector_id = stock.sector_id if stock else None
                    layers = compute_all_layers(
                        session, entry.stock_id, self.run_date_id,
                        sector_id=sector_id,
                        ticker=entry.ticker,
                        reference_date=self.target_date,
                    )
                self._layer_results[entry.ticker] = layers
                computed += 1
            except Exception as e:
                logger.warning("레이어 계산 실패 (%s): %s", entry.ticker, e)
                self._layer_results[entry.ticker] = {}
        return computed

    def step4_pairs(self) -> int:
        """dd_s4_pairs: 페어 자동 선정 (7일 staleness 체크)."""
        from src.deepdive.pair_analysis import refresh_peers_if_stale

        count = 0
        for entry in self._watchlist_entries:
            try:
                with get_session(self.engine) as session:
                    stock = StockRepository.get_by_ticker(session, entry.ticker)
                    sector_id = stock.sector_id if stock else None
                    peers = refresh_peers_if_stale(
                        session, entry.stock_id, entry.ticker, sector_id,
                    )
                    self._pair_results[entry.ticker] = peers
                    count += len(peers)
            except Exception as e:
                logger.warning("페어 선정 실패 (%s): %s", entry.ticker, e)
        return count

    def step5_ai_analysis(self) -> int:
        """dd_s5_ai: 종목별 3라운드 CLI 토론.

        Phase 11d: INVESTMATE_DEEPDIVE_PARALLEL=true면 라운드 내 Bull/Bear를
        asyncio.gather로 병렬 실행(종목 간 순차는 유지).
        """
        from src.deepdive.ai_debate_cli import run_debate_smart

        settings = get_settings()
        model = settings.ai_model_deepdive
        timeout = settings.deepdive_timeout
        parallel = bool(getattr(settings, "deepdive_parallel", False))
        backend = str(getattr(settings, "deepdive_backend", "cli"))
        analyzed = 0

        # Phase 10: 포트폴리오 컨텍스트 계산 (step5 내부 1회)
        sector_weights, ticker_weights = self._compute_existing_weights()
        portfolio_context = {
            "sector_weights": sector_weights,
            "ticker_weights": ticker_weights,
            "max_stock_pct": float(getattr(settings, "max_single_stock_pct", 0.10)),
            "max_sector_pct": float(getattr(settings, "max_sector_weight_pct", 0.30)),
            "total_names": len(ticker_weights),
        } if sector_weights or ticker_weights else None

        for entry in self._watchlist_entries:
            try:
                current_price, daily_change = self._get_current_price(entry.stock_id)
                layers = self._layer_results.get(entry.ticker, {})

                debate_result = run_debate_smart(
                    entry, layers, current_price, daily_change,
                    timeout=timeout, model=model,
                    pair_results=self._pair_results.get(entry.ticker),
                    portfolio_context=portfolio_context,
                    parallel=parallel,
                    backend=backend,
                )
                if debate_result and debate_result.final_result:
                    self._ai_results[entry.ticker] = debate_result.final_result
                    self._debate_results[entry.ticker] = debate_result
                    analyzed += 1
                    console.print(
                        f"    {entry.ticker}: {debate_result.final_result.action_grade} "
                        f"(conviction={debate_result.final_result.conviction}, "
                        f"consensus={debate_result.consensus_strength})"
                    )
                else:
                    logger.warning("토론 결과 없음: %s", entry.ticker)
            except Exception as e:
                logger.warning("토론 실패 (%s): %s", entry.ticker, e)
        return analyzed

    def _get_current_price(self, stock_id: int) -> tuple[float, float]:
        """최신 가격 + 일간 변화율."""
        with get_session(self.engine) as session:
            prices = list(
                session.execute(
                    select(FactDailyPrice)
                    .where(FactDailyPrice.stock_id == stock_id)
                    .order_by(FactDailyPrice.date_id.desc())
                    .limit(2)
                ).scalars().all()
            )
        if not prices:
            return 0.0, 0.0
        current = float(prices[0].close)
        if len(prices) > 1:
            prev = float(prices[1].close)
            change = ((current - prev) / prev * 100) if prev > 0 else 0.0
        else:
            change = 0.0
        return current, round(change, 2)

    def step5_5_execution_guide(self) -> int:
        """dd_s5_5_guide: AI 결과 + 레이어 + 시나리오 → 정량 매매 가이드.

        Phase 9: 과거 horizon별 hit_rate로 EV 디스카운트 적용.
        실패는 격리 (한 종목 실패가 전체 파이프라인 중단시키지 않음).
        """
        from src.deepdive.execution_guide import compute_execution_guide
        from src.deepdive.forecast_evaluator import (
            apply_hit_rate_discount,
            get_historical_hit_rates,
        )

        settings = get_settings()
        built = 0

        # 포트폴리오 컨텍스트: 기존 holding 기반 섹터/종목 비중 (근사 — 수량*현재가 합)
        sector_weights, ticker_weights = self._compute_existing_weights()

        for entry in self._watchlist_entries:
            ai_result = self._ai_results.get(entry.ticker)
            if ai_result is None:
                continue
            layers = self._layer_results.get(entry.ticker, {})
            debate = self._debate_results.get(entry.ticker)
            scenarios = debate.scenarios if debate else None
            current_price, _ = self._get_current_price(entry.stock_id)

            try:
                guide = compute_execution_guide(
                    current_price=current_price,
                    ai_result=ai_result,
                    layers=layers,
                    scenarios=scenarios,
                    sector=entry.sector,
                    settings=settings,
                    existing_sector_weight=sector_weights.get(entry.sector or "", 0.0),
                    existing_ticker_weight=ticker_weights.get(entry.ticker, 0.0),
                )
                if guide is not None:
                    # Phase 9: hit_rate 디스카운트
                    try:
                        with get_session(self.engine) as session:
                            hit_rates = get_historical_hit_rates(session, entry.ticker)
                        if hit_rates:
                            discounted = apply_hit_rate_discount(
                                guide.expected_value_pct, hit_rates,
                            )
                            # frozen dataclass이므로 dataclasses.replace로 치환
                            import dataclasses as _dc

                            guide = _dc.replace(guide, expected_value_pct=discounted)
                    except Exception as e:
                        logger.warning("hit_rate 디스카운트 실패 (%s): %s", entry.ticker, e)

                    self._execution_guides[entry.ticker] = guide
                    built += 1
            except Exception as e:
                logger.warning("execution guide 실패 (%s): %s", entry.ticker, e)
        return built

    def _compute_existing_weights(self) -> tuple[dict, dict]:
        """기존 holding의 섹터/종목 비중(0~1) 계산. 포트폴리오 총가치 기반.

        holding이 없으면 빈 dict 반환.
        """
        try:
            with get_session(self.engine) as session:
                holdings = WatchlistRepository.get_all_holdings(session)

                if not holdings:
                    return {}, {}

                # 종목별 현재 시장가치
                position_values: dict[str, float] = {}
                sector_values: dict[str, float] = {}
                total_value = 0.0

                for ticker, h in holdings.items():
                    stock = StockRepository.get_by_ticker(session, ticker)
                    if not stock:
                        continue
                    prices = list(
                        session.execute(
                            select(FactDailyPrice)
                            .where(FactDailyPrice.stock_id == stock.stock_id)
                            .order_by(FactDailyPrice.date_id.desc())
                            .limit(1)
                        ).scalars().all()
                    )
                    if not prices:
                        continue
                    price = float(prices[0].close)
                    value = price * float(h.shares)
                    position_values[ticker] = value
                    total_value += value

                    # 섹터
                    from src.db.models import DimSector

                    sector_name = ""
                    if stock.sector_id:
                        sector = session.get(DimSector, stock.sector_id)
                        sector_name = sector.name if sector else ""
                    sector_values[sector_name] = sector_values.get(sector_name, 0.0) + value

            if total_value <= 0:
                return {}, {}

            sector_weights = {s: v / total_value for s, v in sector_values.items() if s}
            ticker_weights = {t: v / total_value for t, v in position_values.items()}
            return sector_weights, ticker_weights
        except Exception as e:
            logger.warning("포지션 비중 계산 실패: %s", e)
            return {}, {}

    def step6_diff_detection(self) -> int:
        """dd_s6_diff: 전일 대비 변경점 추출."""
        from src.deepdive.diff_detector import detect_changes

        count = 0
        for entry in self._watchlist_entries:
            ai_result = self._ai_results.get(entry.ticker)
            if ai_result is None:
                continue
            try:
                with get_session(self.engine) as session:
                    prev_report = DeepDiveRepository.get_previous_report(
                        session, entry.stock_id, self.run_date_id,
                    )
                    prev_forecasts = (
                        DeepDiveRepository.get_forecasts_by_report(
                            session, prev_report.report_id,
                        ) if prev_report else None
                    )
                    debate = self._debate_results.get(entry.ticker)
                    changes = detect_changes(
                        current_ai_result=ai_result,
                        current_layers=self._layer_results.get(entry.ticker, {}),
                        current_forecasts=debate.scenarios if debate else None,
                        previous_report=prev_report,
                        previous_forecasts=prev_forecasts,
                    )
                    self._change_results[entry.ticker] = changes
                    count += len(changes)
            except Exception as e:
                logger.warning("변경감지 실패 (%s): %s", entry.ticker, e)
        return count

    def step7_persist(self) -> int:
        """dd_s7_persist: 보고서 + 액션 이력 + 변경사항 + 만기 예측 INSERT."""
        from src.deepdive.forecast_evaluator import evaluate_matured_forecasts

        inserted = 0

        with get_session(self.engine) as session:
            # 만기 도래 예측 업데이트
            try:
                matured_count = evaluate_matured_forecasts(session, self.target_date)
                if matured_count:
                    logger.info("예측 만기 업데이트: %d건", matured_count)
            except Exception as e:
                logger.warning("예측 만기 업데이트 실패: %s", e)
            # force 모드: 기존 데이터 삭제
            if self.force:
                if self.ticker:
                    stock = StockRepository.get_by_ticker(session, self.ticker)
                    stock_id = stock.stock_id if stock else None
                else:
                    stock_id = None
                deleted = DeepDiveRepository.delete_reports_for_date(
                    session, self.run_date_id, stock_id,
                )
                if deleted:
                    logger.info("force 모드: %d건 기존 데이터 삭제", deleted)

            for entry in self._watchlist_entries:
                ai_result = self._ai_results.get(entry.ticker)
                if ai_result is None:
                    continue

                layers = self._layer_results.get(entry.ticker, {})

                # 이전 액션 조회
                prev_report = DeepDiveRepository.get_latest_report(session, entry.stock_id)
                prev_grade = prev_report.action_grade if prev_report else None
                prev_conv = prev_report.conviction if prev_report else None

                # report_json 구성
                guide = self._execution_guides.get(entry.ticker)
                report_data = {
                    "layers": {
                        k: v.model_dump() if v else None
                        for k, v in layers.items()
                    },
                    "ai_result": ai_result.model_dump(),
                }
                if guide is not None:
                    from src.deepdive.execution_guide import guide_to_dict

                    report_data["execution_guide"] = guide_to_dict(guide)
                # 페어 비교를 UI 표시용으로 보존
                pairs = self._pair_results.get(entry.ticker) or []
                if pairs:
                    report_data["pair_comparisons"] = [
                        {
                            "peer_ticker": p.peer_ticker,
                            "peer_name": p.peer_name,
                            "similarity_score": p.similarity_score,
                            "market_cap_ratio": p.market_cap_ratio,
                            "return_60d_peer": p.return_60d_peer,
                            "return_60d_target": p.return_60d_target,
                            "per_peer": p.per_peer,
                            "per_target": p.per_target,
                        }
                        for p in pairs
                    ]

                debate = self._debate_results.get(entry.ticker)

                try:
                    report = DeepDiveRepository.insert_report(
                        session,
                        date_id=self.run_date_id,
                        stock_id=entry.stock_id,
                        ticker=entry.ticker,
                        action_grade=ai_result.action_grade,
                        conviction=ai_result.conviction,
                        uncertainty=ai_result.uncertainty,
                        report_json=json.dumps(report_data, ensure_ascii=False),
                        layer1_summary=_layer_summary(layers.get("layer1")),
                        layer2_summary=_layer_summary(layers.get("layer2")),
                        layer3_summary=_layer_summary(layers.get("layer3")),
                        layer4_summary=_layer_summary(layers.get("layer4")),
                        layer5_summary=_layer_summary(layers.get("layer5")),
                        layer6_summary=_layer_summary(layers.get("layer6")),
                        ai_bull_text=debate.bull_summary if debate else None,
                        ai_bear_text=debate.bear_summary if debate else None,
                        ai_synthesis=ai_result.reasoning,
                        consensus_strength=debate.consensus_strength if debate else None,
                        what_missing=ai_result.what_missing,
                    )
                    DeepDiveRepository.insert_action(
                        session,
                        date_id=self.run_date_id,
                        stock_id=entry.stock_id,
                        ticker=entry.ticker,
                        action_grade=ai_result.action_grade,
                        conviction=ai_result.conviction,
                        prev_action_grade=prev_grade,
                        prev_conviction=prev_conv,
                    )
                    # 변경사항 저장
                    changes = self._change_results.get(entry.ticker, [])
                    if changes:
                        DeepDiveRepository.insert_changes_batch(
                            session, self.run_date_id, entry.stock_id,
                            entry.ticker, changes,
                        )

                    # 시나리오 예측 저장
                    if debate and debate.scenarios:
                        from src.deepdive.scenarios import parse_scenarios

                        current_price, _ = self._get_current_price(entry.stock_id)
                        forecasts = parse_scenarios(
                            {"scenarios": debate.scenarios}, current_price,
                        )
                        if forecasts:
                            DeepDiveRepository.insert_forecasts_batch(
                                session, report.report_id, self.run_date_id,
                                entry.stock_id, entry.ticker, forecasts,
                            )

                    inserted += 1
                except Exception as e:
                    logger.warning("DB 저장 실패 (%s): %s", entry.ticker, e)

        return inserted

    def step8_notify(self) -> int:
        """dd_s8_notify: 텔레그램 1줄 요약 + 실행 가이드 알림."""
        if self.skip_notify:
            return 0

        from src.alerts.notifier import send_deepdive_summary
        from src.deepdive.alert_engine import (
            build_layer_snapshot,
            evaluate_alerts_batch,
            evaluate_catalyst_alerts,
            format_alerts_summary,
            format_catalyst_block,
        )

        settings = get_settings()
        channels = (settings.notify_channels or "").split(",")
        channel = channels[0].strip() if channels and channels[0].strip() else None

        action_summary: dict[str, int] = {}
        for result in self._ai_results.values():
            action_summary[result.action_grade] = action_summary.get(result.action_grade, 0) + 1

        failed = len(self._watchlist_entries) - len(self._ai_results)

        # 변경 감지 요약
        action_changes = []
        new_risks = []
        for ticker, changes in self._change_results.items():
            for c in changes:
                if c.change_type == "action_changed":
                    action_changes.append(f"{ticker}: {c.description}")
                elif c.change_type == "new_risk":
                    new_risks.append(f"{ticker}: {c.description}")

        # Phase 8 + Phase 11a: 실행 가이드 + invalidation 자동 감지 알림
        alert_entries = []
        for entry in self._watchlist_entries:
            guide = self._execution_guides.get(entry.ticker)
            if guide is None:
                continue
            ai = self._ai_results.get(entry.ticker)
            invalidations = list(ai.invalidation_conditions) if ai else []
            next_review = ai.next_review_trigger if ai else None
            current, prev = self._get_current_and_previous_price(entry.stock_id)
            layers = self._layer_results.get(entry.ticker, {})
            close_history = self._get_recent_closes(entry.stock_id, limit=220)
            layer_snapshot = build_layer_snapshot(
                layers, current_price=current, close_history=close_history,
            )
            alert_entries.append({
                "ticker": entry.ticker,
                "current_price": current,
                "previous_price": prev,
                "execution_guide": guide,
                "invalidation_conditions": invalidations,
                "next_review_trigger": next_review,
                "layer_snapshot": layer_snapshot,
            })

        # Phase 11a: 일일 중복 방지 set (한 run 내에서 공유)
        dedup_keys: set[str] = set()
        alerts = evaluate_alerts_batch(alert_entries, dedup_keys=dedup_keys)

        # Phase 11b: 촉매 캘린더 트리거 — Layer 5 구조화 필드 기반
        catalyst_items: list[tuple[str, tuple]] = []
        for entry in self._watchlist_entries:
            layers = self._layer_results.get(entry.ticker, {})
            layer5 = layers.get("layer5")
            structured = getattr(layer5, "upcoming_catalysts_structured", ()) if layer5 else ()
            if not structured:
                continue
            current_price, _ = self._get_current_price(entry.stock_id)
            if current_price <= 0:
                continue
            catalyst_items.append((entry.ticker, structured))
            cat_triggers = evaluate_catalyst_alerts(
                entry.ticker, current_price, structured,
            )
            alerts.extend(cat_triggers)

        catalyst_block = format_catalyst_block(catalyst_items) if catalyst_items else ""
        alert_summary_text = format_alerts_summary(alerts) if alerts else ""
        if catalyst_block:
            logger.info("Deep Dive 촉매: %d종목", len(catalyst_items))
        if alert_summary_text:
            logger.info("Deep Dive alerts: %d건", len(alerts))
            # new_risks에 critical/warning 알림을 우선 얹음
            for a in alerts:
                if a.severity in ("critical", "warning"):
                    new_risks.append(f"{a.ticker}: {a.message}")

        # Phase 12b: 알림 영구 저장 (Telegram 푸시 전) — dedup은 UniqueConstraint로
        if alerts:
            try:
                from src.db.repository import AlertRepository

                stock_id_lookup = {
                    e.ticker: e.stock_id for e in self._watchlist_entries
                }
                with get_session(self.engine) as session:
                    persisted = AlertRepository.persist_batch(
                        session,
                        date_id=self.run_date_id,
                        stock_id_lookup=stock_id_lookup,
                        alerts=alerts,
                    )
                logger.info("Deep Dive 알림 DB 저장: %d건 신규", persisted)
            except Exception as exc:  # pragma: no cover - 저장 실패 시에도 Telegram은 계속
                logger.warning("알림 DB 저장 실패: %s", exc)

        sent = send_deepdive_summary(
            run_date=self.target_date,
            stock_count=len(self._watchlist_entries),
            action_summary=action_summary,
            failed_count=failed,
            channel=channel,
            action_changes=action_changes,
            new_risks=new_risks,
        )
        return 1 if sent else 0

    def _get_current_and_previous_price(self, stock_id: int) -> tuple[float, float | None]:
        """최신/직전 종가 튜플."""
        with get_session(self.engine) as session:
            rows = list(
                session.execute(
                    select(FactDailyPrice)
                    .where(FactDailyPrice.stock_id == stock_id)
                    .order_by(FactDailyPrice.date_id.desc())
                    .limit(2)
                ).scalars().all()
            )
        if not rows:
            return 0.0, None
        current = float(rows[0].close)
        prev = float(rows[1].close) if len(rows) > 1 else None
        return current, prev

    def _get_recent_closes(self, stock_id: int, limit: int = 220) -> list[float]:
        """최근 N개 종가 이력(오름차순, 오래된 → 최신). Phase 11a snapshot용."""
        try:
            with get_session(self.engine) as session:
                rows = list(
                    session.execute(
                        select(FactDailyPrice)
                        .where(FactDailyPrice.stock_id == stock_id)
                        .order_by(FactDailyPrice.date_id.desc())
                        .limit(limit)
                    ).scalars().all()
                )
        except Exception as e:
            logger.warning("가격 이력 조회 실패 (stock_id=%d): %s", stock_id, e)
            return []
        closes = [float(r.close) for r in rows]
        closes.reverse()
        return closes


def _layer_summary(layer) -> str | None:
    """레이어 결과에서 요약 문자열 생성."""
    if layer is None:
        return None
    for attr in (
        "health_grade", "valuation_grade", "technical_grade",
        "flow_grade", "narrative_grade", "macro_grade",
    ):
        if hasattr(layer, attr):
            return getattr(layer, attr)
    return None
