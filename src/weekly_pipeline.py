"""주간 리포트 파이프라인 오케스트레이터 — 일요일 실행."""

from __future__ import annotations

import json
import logging
import signal
from datetime import date, datetime, timedelta
from pathlib import Path

from rich.console import Console
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db.engine import get_session
from src.db.helpers import date_to_id, ensure_date_ids
from src.db.models import FactCollectionLog
from src.db.repository import CollectionLogRepository

logger = logging.getLogger(__name__)
console = Console()


class WeeklyPipeline:
    """2단계 주간 리포트 파이프라인."""

    def __init__(
        self, engine: Engine,
        year: int | None = None,
        week: int | None = None,
        skip_notify: bool = False,
        skip_email: bool = False,
    ):
        self.engine = engine
        self.skip_notify = skip_notify
        self.skip_email = skip_email

        # 기본값: 직전 주 (일요일 실행 → 토요일의 ISO week)
        target = date.today() - timedelta(days=1)
        iso = target.isocalendar()
        self.year = year or iso[0]
        self.week = week or iso[1]

        # run_date_id: 실행일 기준 (오늘)
        self.run_date = date.today()
        self.run_date_id = date_to_id(self.run_date)

        # 그레이스풀 셧다운
        self._interrupted = False
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
            signal.signal(signal.SIGINT, self._handle_signal)
        except (ValueError, OSError):
            pass

    def _handle_signal(self, signum: int, frame: object) -> None:
        logger.warning("주간 파이프라인 중단 신호 수신 (signal=%d)", signum)
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
        try:
            with get_session(self.engine) as session:
                ensure_date_ids(session, [self.run_date])
                CollectionLogRepository.log_step(
                    session, self.run_date_id, step, status,
                    started_at=started, finished_at=datetime.now(),
                    records_count=records_count, message=message,
                )
        except Exception as e:
            logger.error("로그 기록 실패: %s", e)

    def run(self, force: bool = False) -> None:
        """주간 리포트 파이프라인을 실행한다."""
        pipeline_start = datetime.now()

        with get_session(self.engine) as session:
            ensure_date_ids(session, [self.run_date])

        console.print(
            f"\n[bold]주간 리포트 파이프라인 시작[/bold] "
            f"({self.year}-W{self.week:02d})"
        )

        # STEP 1: 주간 리포트 생성
        report = None
        if not self._interrupted:
            step_name = "weekly_report"
            if not force and self._is_step_done(step_name):
                console.print(f"  [dim]STEP 1 {step_name} -- 이미 완료, 스킵[/dim]")
            else:
                started = datetime.now()
                console.print("\n[cyan]STEP 1[/cyan] 주간 리포트 생성")
                try:
                    from src.reports.weekly_report import generate_and_save_weekly_report

                    with get_session(self.engine) as session:
                        report = generate_and_save_weekly_report(
                            session, self.year, self.week,
                        )
                    self._log_step(step_name, "success", started, records_count=1)
                    console.print("  [green]완료[/green]")
                except Exception as e:
                    logger.error("STEP 1 실패: %s", e, exc_info=True)
                    self._log_step(step_name, "failed", started, message=str(e))
                    console.print(f"  [red]실패: {e}[/red]")

        # STEP 2: AI 주간 코멘터리
        commentary = None
        if not self._interrupted and report:
            step_name = "weekly_ai_commentary"
            if not force and self._is_step_done(step_name):
                console.print(f"  [dim]STEP 2 {step_name} -- 이미 완료, 스킵[/dim]")
            else:
                started = datetime.now()
                console.print("\n[cyan]STEP 2[/cyan] AI 주간 코멘터리 생성")
                try:
                    from src.reports.weekly_commentary import (
                        generate_weekly_commentary,
                        save_commentary,
                    )

                    settings = get_settings()
                    commentary = generate_weekly_commentary(
                        report,
                        model=settings.ai_model_commentary,
                    )
                    if commentary:
                        save_commentary(commentary, self.year, self.week)
                        console.print(f"  [green]완료[/green] ({len(commentary)}자)")
                    else:
                        console.print("  [yellow]AI 코멘터리 생성 스킵 (비활성화 또는 실패)[/yellow]")
                    self._log_step(step_name, "success", started, records_count=1 if commentary else 0)
                except Exception as e:
                    logger.error("STEP 2 실패: %s", e, exc_info=True)
                    self._log_step(step_name, "failed", started, message=str(e))
                    console.print(f"  [red]실패: {e}[/red]")

        # STEP 3: PDF 생성
        pdf_path = None
        if not self._interrupted and report:
            step_name = "weekly_pdf"
            if not force and self._is_step_done(step_name):
                console.print(f"  [dim]STEP 3 {step_name} -- 이미 완료, 스킵[/dim]")
            else:
                started = datetime.now()
                console.print("\n[cyan]STEP 3[/cyan] PDF 생성")
                try:
                    from src.reports.weekly_pdf import generate_weekly_pdf

                    pdf_path = generate_weekly_pdf(report, commentary)
                    self._log_step(step_name, "success", started, records_count=1)
                    console.print(f"  [green]완료[/green] ({pdf_path})")
                except Exception as e:
                    logger.error("STEP 3 실패: %s", e, exc_info=True)
                    self._log_step(step_name, "failed", started, message=str(e))
                    console.print(f"  [red]실패: {e}[/red]")

        # STEP 4: 이메일 발송
        if not self._interrupted and not self.skip_email and report:
            step_name = "weekly_email"
            if not force and self._is_step_done(step_name):
                console.print(f"  [dim]STEP 4 {step_name} -- 이미 완료, 스킵[/dim]")
            else:
                started = datetime.now()
                console.print("\n[cyan]STEP 4[/cyan] 이메일 발송")
                try:
                    self._send_email(report, pdf_path, commentary)
                    self._log_step(step_name, "success", started)
                    console.print("  [green]완료[/green]")
                except Exception as e:
                    logger.error("STEP 4 실패: %s", e, exc_info=True)
                    self._log_step(step_name, "failed", started, message=str(e))
                    console.print(f"  [red]실패: {e}[/red]")

        # STEP 5: 기타 알림 (텔레그램/슬랙)
        if not self._interrupted and not self.skip_notify:
            step_name = "weekly_notify"
            if not force and self._is_step_done(step_name):
                console.print(f"  [dim]STEP 5 {step_name} -- 이미 완료, 스킵[/dim]")
            else:
                started = datetime.now()
                console.print("\n[cyan]STEP 5[/cyan] 알림 발송")
                try:
                    self._send_notification(report)
                    self._log_step(step_name, "success", started)
                    console.print("  [green]완료[/green]")
                except Exception as e:
                    logger.error("STEP 5 실패: %s", e, exc_info=True)
                    self._log_step(step_name, "failed", started, message=str(e))
                    console.print(f"  [red]실패: {e}[/red]")

        # 완료 요약
        duration = int((datetime.now() - pipeline_start).total_seconds())
        console.print(f"\n[bold green]주간 파이프라인 완료[/bold green] (소요: {duration}초)")

        # JSON 요약 저장
        self._save_summary(duration)

    def _send_email(self, report: object, pdf_path: object | None, commentary: str | None) -> None:
        """주간 리포트 이메일을 발송한다."""
        from src.alerts.notifier import send_weekly_report_email

        es = report.executive_summary
        picks_data = [
            {
                "ticker": c.ticker,
                "days_recommended": c.days_recommended,
                "ai_consensus": c.ai_consensus,
                "weekly_return_pct": c.weekly_return_pct,
            }
            for c in report.conviction_picks[:5]
        ]
        send_weekly_report_email(
            year=self.year,
            week_number=self.week,
            market_oneliner=es.market_oneliner,
            sp500_return_pct=es.sp500_weekly_return_pct,
            vix_end=es.vix_end,
            win_rate_pct=es.weekly_win_rate_pct,
            pdf_path=str(pdf_path) if pdf_path else None,
            commentary_excerpt=commentary,
            conviction_picks=picks_data,
        )

    def _send_notification(self, report: object | None) -> None:
        """주간 알림을 발송한다."""
        from src.alerts.notifier import send_weekly_summary

        settings = get_settings()
        channel = getattr(settings, "notify_channels", None)

        if report is None:
            logger.info("리포트 없음, 알림 스킵")
            return

        es = report.executive_summary
        conviction_tickers = [c.ticker for c in report.conviction_picks[:5]]

        send_weekly_summary(
            year=self.year,
            week_number=self.week,
            market_regime=es.regime_end,
            sp500_return_pct=es.sp500_weekly_return_pct,
            win_rate_pct=es.weekly_win_rate_pct,
            conviction_tickers=conviction_tickers,
            channel=channel,
        )

    def _save_summary(self, duration_sec: int) -> None:
        """JSON 요약을 logs/에 저장한다."""
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        summary = {
            "type": "weekly",
            "year": self.year,
            "week": self.week,
            "duration_sec": duration_sec,
            "generated_at": datetime.now().isoformat(),
        }
        path = log_dir / f"{self.year}-W{self.week:02d}_summary.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
