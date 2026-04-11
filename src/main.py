"""CLI 진입점 (Click) -- S&P 500 배치 파이프라인."""

from __future__ import annotations

import logging
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

# Windows cp949 인코딩 문제 방지
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

import click
from rich.console import Console
from rich.table import Table

from src.config import get_settings
from src.db.engine import create_db_engine, get_session, init_db

console = Console()


import json as _json


class _JsonFormatter(logging.Formatter):
    """파일 로그용 JSON 포매터."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            log_data["exception"] = self.formatException(record.exc_info)
        return _json.dumps(log_data, ensure_ascii=False)


def _setup_logging(target_date: date) -> None:
    """파일(JSON) + 콘솔(텍스트) 로깅을 설정한다."""
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"{target_date.isoformat()}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(_JsonFormatter())

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )

    logging.basicConfig(
        level=logging.INFO,
        handlers=[file_handler, console_handler],
        force=True,
    )


@click.group(help="investmate — S&P 500 AI 투자 가이드")
def cli() -> None:
    """investmate CLI."""


# ──────────────────────────────────────────
# run — 핵심 명령
# ──────────────────────────────────────────
@cli.command(help="데일리 파이프라인 전체 실행 (핵심 명령)")
@click.option("--date", "run_date", default=None, help="실행 날짜 (YYYY-MM-DD)")
@click.option("--top", "top_n", default=10, help="추천 종목 수 (기본: 10)")
@click.option("--skip-notify", is_flag=True, help="알림 발송 스킵")
@click.option("--step", "step_num", default=None, type=int, help="특정 단계만 실행 (1-6)")
@click.option("--force", is_flag=True, help="완료된 스텝도 강제 재실행")
def run(run_date: str | None, top_n: int, skip_notify: bool, step_num: int | None, force: bool) -> None:
    """6단계 데일리 파이프라인을 실행한다."""
    from src.pipeline import DailyPipeline

    target = _parse_date(run_date) if run_date else date.today()
    _setup_logging(target)

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    # 기존 DB 스키마를 ORM 모델에 맞춰 자동 업그레이드
    from src.db.migrate import ensure_schema
    ensure_schema(engine)

    pipeline = DailyPipeline(
        engine, target_date=target, top_n=top_n, skip_notify=skip_notify,
    )
    pipeline.run(step=step_num, force=force)


# ──────────────────────────────────────────
# report — 리포트 조회
# ──────────────────────────────────────────
@cli.group(help="리포트 조회")
def report() -> None:
    """리포트 명령어 그룹."""


@report.command(help="가장 최근 리포트 출력")
def latest() -> None:
    """최근 리포트를 출력한다."""
    reports_dir = Path("reports/daily")
    if not reports_dir.exists():
        console.print("[yellow]리포트가 없습니다. 'investmate run'을 먼저 실행하세요.[/yellow]")
        return

    md_files = sorted(reports_dir.glob("*.md"), reverse=True)
    if not md_files:
        console.print("[yellow]리포트 파일이 없습니다.[/yellow]")
        return

    content = md_files[0].read_text(encoding="utf-8")
    from rich.markdown import Markdown

    console.print(Markdown(content))


@report.command(help="특정 날짜 리포트 조회")
@click.argument("report_date")
def show(report_date: str) -> None:
    """특정 날짜의 리포트를 출력한다."""
    md_path = Path("reports/daily") / f"{report_date}.md"
    if not md_path.exists():
        console.print(f"[red]{report_date} 리포트가 없습니다.[/red]")
        return

    from rich.markdown import Markdown

    console.print(Markdown(md_path.read_text(encoding="utf-8")))


@report.command(name="list", help="저장된 리포트 목록")
def list_reports() -> None:
    """저장된 리포트 목록을 출력한다."""
    reports_dir = Path("reports/daily")
    if not reports_dir.exists():
        console.print("[dim]리포트 없음[/dim]")
        return

    md_files = sorted(reports_dir.glob("*.md"), reverse=True)
    if not md_files:
        console.print("[dim]리포트 없음[/dim]")
        return

    table = Table(title="저장된 리포트")
    table.add_column("날짜", style="cyan")
    table.add_column("파일 크기", justify="right")

    for f in md_files[:30]:
        size = f"{f.stat().st_size / 1024:.1f} KB"
        table.add_row(f.stem, size)

    console.print(table)


@report.command(name="weekly", help="주간 리포트 생성")
@click.option("--year", default=None, type=int, help="연도 (기본: 직전 주)")
@click.option("--week", default=None, type=int, help="주차 (ISO week number)")
@click.option("--skip-notify", is_flag=True, help="알림 발송 스킵")
@click.option("--skip-email", is_flag=True, help="이메일 발송 스킵")
@click.option("--force", is_flag=True, help="체크포인트 무시, 재실행")
def weekly(year: int | None, week: int | None, skip_notify: bool, skip_email: bool, force: bool) -> None:
    """주간 리포트를 생성한다."""
    target_date = date.today()
    _setup_logging(target_date)

    engine = create_db_engine()
    init_db(engine)

    from src.weekly_pipeline import WeeklyPipeline

    pipeline = WeeklyPipeline(
        engine, year=year, week=week,
        skip_notify=skip_notify, skip_email=skip_email,
    )
    pipeline.run(force=force)


@report.command(name="weekly-latest", help="가장 최근 주간 리포트 출력")
def weekly_latest() -> None:
    """최근 주간 리포트를 출력한다."""
    reports_dir = Path("reports/weekly")
    if not reports_dir.exists():
        console.print("[yellow]주간 리포트가 없습니다. 'investmate report weekly'를 먼저 실행하세요.[/yellow]")
        return

    md_files = sorted(reports_dir.glob("*.md"), reverse=True)
    if not md_files:
        console.print("[yellow]주간 리포트 파일이 없습니다.[/yellow]")
        return

    from rich.markdown import Markdown

    console.print(Markdown(md_files[0].read_text(encoding="utf-8")))


@report.command(name="weekly-show", help="특정 주차 주간 리포트 조회")
@click.argument("week_id")
def weekly_show(week_id: str) -> None:
    """특정 주차의 주간 리포트를 출력한다. (예: 2026-W13)"""
    md_path = Path("reports/weekly") / f"{week_id}.md"
    if not md_path.exists():
        console.print(f"[red]{week_id} 주간 리포트가 없습니다.[/red]")
        return

    from rich.markdown import Markdown

    console.print(Markdown(md_path.read_text(encoding="utf-8")))


# ──────────────────────────────────────────
# stock — 개별 종목 상세
# ──────────────────────────────────────────
@cli.command(help="개별 종목 상세 조회")
@click.argument("ticker")
@click.option("--export", "export_fmt", type=click.Choice(["json", "csv", "md"]),
              default=None, help="내보내기 형식")
def stock(ticker: str, export_fmt: str | None) -> None:
    """개별 종목 상세를 조회한다."""
    from rich.panel import Panel

    from src.analysis.technical import (
        INDICATOR_COLUMNS,
        calculate_indicators,
        prices_to_dataframe,
    )
    from src.db.repository import StockRepository

    ticker = ticker.upper()
    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        stock_obj = StockRepository.get_by_ticker(session, ticker)
        if stock_obj is None:
            console.print(f"[red]{ticker} 종목을 찾을 수 없습니다.[/red]")
            return

        df = prices_to_dataframe(session, stock_obj.stock_id)
        if df.empty:
            console.print(f"[yellow]{ticker}의 가격 데이터가 없습니다.[/yellow]")
            return

        indicators_df = calculate_indicators(df)
        latest = indicators_df.iloc[-1]

        # 헤더
        price_change = 0.0
        change_pct = 0.0
        if len(indicators_df) > 1:
            prev = indicators_df.iloc[-2]["close"]
            price_change = latest["close"] - prev
            change_pct = (price_change / prev * 100) if prev else 0

        color = "green" if price_change >= 0 else "red"
        market_code = stock_obj.market.code if stock_obj.market else "?"

        console.print(Panel(
            f"[bold]{stock_obj.name}[/bold] ({ticker}) | {market_code}\n"
            f"현재가: {latest['close']:.2f} "
            f"[{color}]{price_change:+.2f} ({change_pct:+.1f}%)[/{color}]",
            title="종목 상세",
        ))

        # 기술적 지표
        ind_table = Table(title="기술적 지표")
        ind_table.add_column("지표", style="cyan")
        ind_table.add_column("값", justify="right")

        import pandas as pd
        for col in INDICATOR_COLUMNS:
            val = latest.get(col)
            if val is not None and not pd.isna(val):
                ind_table.add_row(col.upper(), f"{val:,.2f}")

        console.print(ind_table)

        # 시그널
        from src.analysis.signals import detect_signals

        detected = detect_signals(indicators_df, stock_obj.stock_id)
        if detected:
            sig_table = Table(title="활성 시그널")
            sig_table.add_column("시그널", style="cyan")
            sig_table.add_column("방향")
            sig_table.add_column("강도", justify="right")

            for s in detected:
                c = {"BUY": "green", "SELL": "red"}.get(s.direction, "yellow")
                sig_table.add_row(s.signal_type, f"[{c}]{s.direction}[/{c}]", str(s.strength))

            console.print(sig_table)

        console.print("\n[dim italic]※ 투자 참고용이며 투자 권유가 아닙니다.[/dim italic]")


# ──────────────────────────────────────────
# history — 히스토리
# ──────────────────────────────────────────
@cli.group(help="히스토리 조회")
def history() -> None:
    """히스토리 명령어 그룹."""


@history.command(name="signals", help="과거 시그널 이력")
@click.argument("ticker")
def history_signals(ticker: str) -> None:
    """과거 시그널 이력을 조회한다."""
    from src.db.helpers import id_to_date
    from src.db.repository import SignalRepository, StockRepository

    ticker = ticker.upper()
    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        stock_obj = StockRepository.get_by_ticker(session, ticker)
        if stock_obj is None:
            console.print(f"[red]{ticker} 종목을 찾을 수 없습니다.[/red]")
            return

        signals = SignalRepository.get_by_stock(session, stock_obj.stock_id)
        if not signals:
            console.print(f"[dim]{ticker} 시그널 이력 없음[/dim]")
            return

        table = Table(title=f"{ticker} 시그널 이력")
        table.add_column("날짜", style="dim")
        table.add_column("시그널", style="cyan")
        table.add_column("강도", justify="right")

        for s in signals:
            d = id_to_date(s.date_id)
            table.add_row(str(d), f"#{s.signal_type_id}", str(s.strength))

        console.print(table)


@history.command(name="recommendations", help="과거 추천 이력 + 사후 수익률")
def history_recommendations() -> None:
    """과거 추천 이력을 조회한다."""
    from src.db.helpers import id_to_date
    from src.db.models import FactDailyRecommendation
    from sqlalchemy import select

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        from src.db.models import DimStock

        stmt = (
            select(FactDailyRecommendation)
            .order_by(FactDailyRecommendation.run_date_id.desc())
            .limit(50)
        )
        recs = session.execute(stmt).scalars().all()

        if not recs:
            console.print("[dim]추천 이력 없음[/dim]")
            return

        # stock_id → ticker 매핑
        stock_ids = {r.stock_id for r in recs}
        ticker_map = {}
        for sid in stock_ids:
            stock = session.execute(
                select(DimStock).where(DimStock.stock_id == sid)
            ).scalar_one_or_none()
            if stock:
                ticker_map[sid] = stock.ticker

        table = Table(title="추천 이력")
        table.add_column("날짜", style="dim")
        table.add_column("순위", justify="right")
        table.add_column("종목", style="cyan")
        table.add_column("점수", justify="right")
        table.add_column("1D", justify="right")
        table.add_column("5D", justify="right")
        table.add_column("10D", justify="right")
        table.add_column("20D", justify="right")

        for r in recs:
            d = id_to_date(r.run_date_id)
            ticker = ticker_map.get(r.stock_id, f"#{r.stock_id}")
            r1 = f"{float(r.return_1d):+.1f}%" if r.return_1d else "-"
            r5 = f"{float(r.return_5d):+.1f}%" if r.return_5d else "-"
            r10 = f"{float(r.return_10d):+.1f}%" if r.return_10d else "-"
            r20 = f"{float(r.return_20d):+.1f}%" if r.return_20d else "-"
            table.add_row(
                str(d), str(r.rank), ticker,
                f"{float(r.total_score):.1f}", r1, r5, r10, r20,
            )

        console.print(table)


@history.command(name="performance", help="추천 성과 요약")
@click.option("--days", default=90, help="조회 기간 (일)")
def history_performance(days: int) -> None:
    """추천 성과를 집계하여 출력한다."""
    from src.analysis.performance import calculate_performance

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        report = calculate_performance(session, days=days)

    if report.total_recommendations == 0:
        console.print("[dim]추천 이력 없음[/dim]")
        return

    console.print(f"\n[bold]추천 성과 요약[/bold] (최근 {days}일)")
    console.print(f"총 추천: {report.total_recommendations}건 | 수익률 데이터: {report.with_return_data}건\n")

    # 수익률 테이블
    perf_table = Table(title="기간별 성과")
    perf_table.add_column("", style="bold")
    perf_table.add_column("1일", justify="right")
    perf_table.add_column("5일", justify="right")
    perf_table.add_column("10일", justify="right")
    perf_table.add_column("20일", justify="right")

    def _fmt(v: float | None) -> str:
        return f"{v:+.1f}%" if v is not None else "-"

    def _fmt_wr(v: float | None) -> str:
        return f"{v:.1f}%" if v is not None else "-"

    perf_table.add_row("승률", _fmt_wr(report.win_rate_1d), _fmt_wr(report.win_rate_5d),
                        _fmt_wr(report.win_rate_10d), _fmt_wr(report.win_rate_20d))
    perf_table.add_row("평균 수익률 (순수익률, 거래비용 차감)", _fmt(report.avg_return_1d), _fmt(report.avg_return_5d),
                        _fmt(report.avg_return_10d), _fmt(report.avg_return_20d))
    console.print(perf_table)

    # 최고/최저
    if report.best_pick:
        console.print(f"\n[green]최고:[/green] {report.best_pick[0]} {report.best_pick[1]:+.1f}% ({report.best_pick[2]})")
    if report.worst_pick:
        console.print(f"[red]최저:[/red] {report.worst_pick[0]} {report.worst_pick[1]:+.1f}% ({report.worst_pick[2]})")

    # 섹터별
    if report.by_sector:
        console.print("\n[bold]섹터별 수익률 (20일):[/bold]")
        for sector, ret in sorted(report.by_sector.items(), key=lambda x: -x[1]):
            color = "green" if ret > 0 else "red"
            console.print(f"  [{color}]{sector:.<30} {ret:+.1f}%[/{color}]")

    # AI 승인 비교
    if report.ai_approved_avg_20d is not None:
        console.print(f"\n[bold]AI 승인 종목:[/bold] {report.ai_approved_avg_20d:+.1f}% (20일)")
        console.print(f"[bold]전체 평균:[/bold]   {report.all_avg_20d:+.1f}% (20일)")

    # 최근 추천
    if report.recent_picks:
        console.print("")
        recent_table = Table(title="최근 추천")
        recent_table.add_column("날짜", style="dim")
        recent_table.add_column("종목", style="cyan")
        recent_table.add_column("점수", justify="right")
        recent_table.add_column("1D", justify="right")
        recent_table.add_column("10D", justify="right")
        recent_table.add_column("20D", justify="right")
        recent_table.add_column("AI", justify="center")

        for p in report.recent_picks:
            ai_str = "[green]V[/green]" if p["ai_approved"] else ""
            recent_table.add_row(
                p["date"], p["ticker"], f"{p['score']:.1f}",
                _fmt(p["return_1d"]), _fmt(p["return_10d"]), _fmt(p["return_20d"]),
                ai_str,
            )
        console.print(recent_table)

    console.print(f"\n[dim italic]※ 투자 참고용이며 투자 권유가 아닙니다.[/dim italic]")


@history.command(name="pipeline", help="파이프라인 실행 이력")
def history_pipeline() -> None:
    """파이프라인 실행 이력을 조회한다."""
    from src.db.repository import CollectionLogRepository

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        from src.db.models import FactCollectionLog
        from sqlalchemy import select

        logs = session.execute(
            select(FactCollectionLog)
            .order_by(FactCollectionLog.started_at.desc())
            .limit(30)
        ).scalars().all()

        if not logs:
            console.print("[dim]실행 이력 없음[/dim]")
            return

        table = Table(title="파이프라인 실행 이력")
        table.add_column("날짜", style="dim")
        table.add_column("단계", style="cyan")
        table.add_column("상태")
        table.add_column("레코드", justify="right")
        table.add_column("소요시간")

        for log in logs:
            color = {"success": "green", "failed": "red"}.get(log.status, "yellow")
            duration = ""
            if log.finished_at and log.started_at:
                delta = log.finished_at - log.started_at
                duration = f"{delta.total_seconds():.1f}s"

            table.add_row(
                log.started_at.strftime("%m-%d %H:%M"),
                log.step,
                f"[{color}]{log.status}[/{color}]",
                str(log.records_count),
                duration,
            )

        console.print(table)


# ──────────────────────────────────────────
# db — DB 관리
# ──────────────────────────────────────────
@cli.group(help="데이터베이스 관리")
def db() -> None:
    """DB 관리 명령어 그룹."""


@db.command(help="데이터베이스 초기화 + S&P 500 시딩")
def init() -> None:
    """DB 테이블 생성 + 디멘션 시딩."""
    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    init_db(engine)

    from src.db.seed import seed_dimensions

    console.print("[dim]디멘션 데이터 시딩 중...[/dim]")
    seed_dimensions(engine)

    console.print("[dim]S&P 500 종목 시딩 중...[/dim]")
    try:
        from src.data.sp500 import sync_sp500

        with get_session(engine) as session:
            from src.db.repository import StockRepository

            market_id = StockRepository.resolve_market_id(session, "US")
            if market_id:
                result = sync_sp500(session, market_id)
                console.print(f"[green]OK[/green] S&P 500 시딩 완료: {result['total']}개 종목")
            else:
                console.print("[red]US 시장이 시딩되지 않았습니다.[/red]")
    except Exception as e:
        console.print(f"[yellow]S&P 500 시딩 실패 (나중에 'db update-sp500'으로 재시도): {e}[/yellow]")

    console.print(f"[green]OK[/green] 데이터베이스 초기화 완료: {settings.db_path}")


@db.command(help="데이터베이스 상태 확인")
def status() -> None:
    """DB 상태를 출력한다."""
    from sqlalchemy import func, select

    from src.db.models import (
        BridgeNewsStock,
        DimDate,
        DimIndicatorType,
        DimMarket,
        DimSector,
        DimSignalType,
        DimStock,
        FactCollectionLog,
        FactDailyPrice,
        FactDailyRecommendation,
        FactFinancial,
        FactIndicatorValue,
        FactMacroIndicator,
        FactNews,
        FactSignal,
        FactValuation,
    )

    settings = get_settings()
    db_file = Path(settings.db_path)

    if not db_file.exists():
        console.print("[yellow]DB 파일 없음. 'investmate db init' 실행 필요.[/yellow]")
        return

    size_mb = db_file.stat().st_size / (1024 * 1024)
    console.print(f"DB 경로: {db_file.resolve()}")
    console.print(f"DB 크기: {size_mb:.2f} MB")

    engine = create_db_engine(settings.db_path)
    with get_session(engine) as session:
        table = Table(title="테이블별 레코드 수")
        table.add_column("테이블", style="cyan")
        table.add_column("레코드 수", justify="right", style="green")

        models = [
            (DimMarket, "dim_markets"), (DimSector, "dim_sectors"),
            (DimDate, "dim_date"), (DimStock, "dim_stocks"),
            (DimIndicatorType, "dim_indicator_types"),
            (DimSignalType, "dim_signal_types"),
            (FactDailyPrice, "fact_daily_prices"),
            (FactIndicatorValue, "fact_indicator_values"),
            (FactFinancial, "fact_financials"),
            (FactValuation, "fact_valuations"),
            (FactSignal, "fact_signals"),
            (FactMacroIndicator, "fact_macro_indicators"),
            (FactDailyRecommendation, "fact_daily_recommendations"),
            (FactNews, "fact_news"),
            (BridgeNewsStock, "bridge_news_stock"),
            (FactCollectionLog, "fact_collection_logs"),
        ]

        for model, label in models:
            count = session.execute(
                select(func.count()).select_from(model)
            ).scalar_one()
            table.add_row(label, f"{count:,}")

        console.print(table)


@db.command(help="데이터베이스 백업")
def backup() -> None:
    """DB 파일을 백업한다."""
    settings = get_settings()
    db_file = Path(settings.db_path)

    if not db_file.exists():
        console.print("[red]DB 파일 없음.[/red]")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_file.parent / f"{db_file.stem}_backup_{timestamp}{db_file.suffix}"
    shutil.copy2(db_file, backup_path)
    console.print(f"[green]OK[/green] 백업 완료: {backup_path}")


@db.command(name="update-sp500", help="S&P 500 구성 종목 업데이트")
def update_sp500() -> None:
    """S&P 500 구성 종목을 업데이트한다."""
    from src.data.sp500 import sync_sp500
    from src.db.repository import StockRepository

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        market_id = StockRepository.resolve_market_id(session, "US")
        if not market_id:
            console.print("[red]US 시장을 찾을 수 없습니다. 'db init'을 먼저 실행하세요.[/red]")
            return

        result = sync_sp500(session, market_id)
        console.print(
            f"[green]OK[/green] S&P 500 업데이트 완료: "
            f"추가 {result['added']}, 제외 {result['removed']}, 전체 {result['total']}"
        )


@db.command(name="backfill-fg", help="Fear & Greed Index 히스토리 백필 (~1년)")
def backfill_fg() -> None:
    """CNN Fear & Greed Index 히스토리를 DB에 백필한다."""
    from src.data.fear_greed import backfill_fear_greed_to_db

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    console.print("[cyan]Fear & Greed 히스토리 백필 시작...[/cyan]")
    count = backfill_fear_greed_to_db(engine)
    console.print(f"[green]완료[/green] {count}건 적재")


# ──────────────────────────────────────────
# config — 설정 관리
# ──────────────────────────────────────────
@cli.group(help="설정 관리")
def config() -> None:
    """설정 명령어 그룹."""


@config.command(name="show", help="현재 설정 확인")
def config_show() -> None:
    """현재 설정을 출력한다."""
    settings = get_settings()
    table = Table(title="현재 설정")
    table.add_column("항목", style="cyan")
    table.add_column("값")

    for key, value in settings.model_dump().items():
        table.add_row(key, str(value))

    console.print(table)


@config.command(name="set", help="설정 변경")
@click.argument("key")
@click.argument("value")
def config_set(key: str, value: str) -> None:
    """설정 값을 변경한다."""
    from src.config import _load_json_config, save_config

    current = _load_json_config()
    current[key] = value
    save_config(current)
    console.print(f"[green]OK[/green] {key} = {value}")


# ──────────────────────────────────────────
# prompt — 프롬프트 조회
# ──────────────────────────────────────────
@cli.group(help="AI 분석 프롬프트 조회")
def prompt() -> None:
    """프롬프트 명령어 그룹."""


@prompt.command(name="latest", help="가장 최근 프롬프트 출력")
def prompt_latest() -> None:
    reports_dir = Path("reports/prompts")
    files = sorted(reports_dir.glob("*_prompt.txt"), reverse=True)
    if not files:
        console.print("[dim]프롬프트 없음[/dim]")
        return
    console.print(files[0].read_text(encoding="utf-8"))


@prompt.command(name="show", help="특정 날짜 프롬프트 조회")
@click.argument("report_date")
def prompt_show(report_date: str) -> None:
    path = Path("reports/prompts") / f"{report_date}_prompt.txt"
    if not path.exists():
        console.print(f"[red]{report_date} 프롬프트 없음[/red]")
        return
    console.print(path.read_text(encoding="utf-8"))


# ──────────────────────────────────────────
# ai — AI 분석 결과 조회
# ──────────────────────────────────────────
@cli.group(help="AI 분석 결과 조회")
def ai() -> None:
    """AI 분석 명령어 그룹."""


@ai.command(name="latest", help="가장 최근 AI 분석 결과 출력")
def ai_latest() -> None:
    reports_dir = Path("reports/ai_analysis")
    files = sorted(reports_dir.glob("*_ai_analysis.md"), reverse=True)
    if not files:
        console.print("[dim]AI 분석 결과 없음[/dim]")
        return
    from rich.markdown import Markdown
    console.print(Markdown(files[0].read_text(encoding="utf-8")))


@ai.command(name="show", help="특정 날짜 AI 분석 결과 조회")
@click.argument("report_date")
def ai_show(report_date: str) -> None:
    path = Path("reports/ai_analysis") / f"{report_date}_ai_analysis.md"
    if not path.exists():
        console.print(f"[red]{report_date} AI 분석 없음[/red]")
        return
    from rich.markdown import Markdown
    console.print(Markdown(path.read_text(encoding="utf-8")))


@ai.command(name="rerun", help="AI 분석만 재실행")
def ai_rerun() -> None:
    """최근 프롬프트를 재사용하여 AI 분석만 재실행하고 DB에 반영한다."""
    from src.ai.claude_analyzer import parse_ai_response, run_claude_analysis, save_analysis
    from src.db.helpers import date_to_id
    from src.db.repository import RecommendationRepository

    reports_dir = Path("reports/prompts")
    files = sorted(reports_dir.glob("*_prompt.txt"), reverse=True)
    if not files:
        console.print("[red]프롬프트 없음. 먼저 'investmate run'을 실행하세요.[/red]")
        return

    prompt_text = files[0].read_text(encoding="utf-8")
    run_date = date.fromisoformat(files[0].stem.split("_")[0])
    console.print(f"[dim]프롬프트: {files[0].name} ({run_date})[/dim]")

    response = run_claude_analysis(prompt_text)
    if not response:
        console.print("[red]AI 분석 실패[/red]")
        return

    save_analysis(response, run_date)
    parsed = parse_ai_response(response)

    # DB 업데이트
    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    run_date_id = date_to_id(run_date)

    with get_session(engine) as session:
        from sqlalchemy import select as _sel
        from src.db.models import DimStock

        recs = RecommendationRepository.get_by_date(session, run_date_id)
        rec_map = {}
        for rec in recs:
            stock = session.execute(_sel(DimStock).where(DimStock.stock_id == rec.stock_id)).scalar_one_or_none()
            if stock:
                rec_map[stock.ticker] = rec

        updated = 0
        mentioned: set[str] = set()
        for p in parsed:
            ticker = p.get("ticker")
            if not ticker:
                continue
            mentioned.add(ticker)
            rec = rec_map.get(ticker)
            if rec is None:
                continue
            rec.ai_approved = p.get("ai_approved", True)
            rec.ai_reason = p.get("ai_reason")
            for key in ("ai_target_price", "ai_stop_loss", "ai_confidence", "ai_risk_level"):
                if p.get(key):
                    setattr(rec, key, p[key])
            if p.get("entry_strategy"):
                rec.ai_entry_strategy = p["entry_strategy"]
            if p.get("exit_strategy"):
                rec.ai_exit_strategy = p["exit_strategy"]
            updated += 1

        # 미언급 종목 기본 승인
        for ticker, rec in rec_map.items():
            if ticker not in mentioned and rec.ai_approved is None:
                rec.ai_approved = True
                rec.ai_confidence = 5
                rec.ai_reason = "AI가 명시적으로 제외하지 않음"
                updated += 1

        session.flush()

    approved = sum(1 for p in parsed if p.get("ai_approved"))
    excluded = sum(1 for p in parsed if not p.get("ai_approved", True))
    console.print(f"[green]OK[/green] {updated}종목 DB 반영 (추천 {approved} / 제외 {excluded})")


@ai.command(name="performance", help="AI 분석 성과 요약")
def ai_performance() -> None:
    """AI 예측 정확도를 분석하여 표시한다."""
    from src.ai.feedback import calculate_ai_performance, collect_ai_feedback

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        collect_ai_feedback(session)
        perf = calculate_ai_performance(session)

    if perf.total_predictions == 0:
        console.print("[yellow]AI 피드백 데이터 없음. 파이프라인을 먼저 실행하세요.[/yellow]")
        return

    table = Table(title="AI 분석 성과 요약")
    table.add_column("항목", style="cyan")
    table.add_column("값", style="green")

    table.add_row("총 예측", str(perf.total_predictions))
    table.add_row("AI 추천", str(perf.ai_approved_count))
    table.add_row("AI 제외", str(perf.ai_excluded_count))
    if perf.win_rate_approved is not None:
        table.add_row("추천 종목 승률", f"{perf.win_rate_approved:.1f}%")
    if perf.avg_return_approved is not None:
        table.add_row("추천 종목 평균수익", f"{perf.avg_return_approved:+.2f}%")
    if perf.win_rate_excluded is not None:
        table.add_row("제외 종목 승률 (낮을수록 좋음)", f"{perf.win_rate_excluded:.1f}%")
    if perf.direction_accuracy is not None:
        table.add_row("방향 예측 정확도", f"{perf.direction_accuracy:.1f}%")
    if perf.overestimate_rate is not None:
        table.add_row("목표가 과대추정 비율", f"{perf.overestimate_rate:.1f}%")

    console.print(table)

    if perf.sector_accuracy:
        st = Table(title="섹터별 AI 승률")
        st.add_column("섹터", style="cyan")
        st.add_column("승률", justify="right")
        for sector, acc in sorted(perf.sector_accuracy.items(), key=lambda x: -x[1]):
            st.add_row(sector, f"{acc:.1f}%")
        console.print(st)

    if perf.confidence_calibration:
        ct = Table(title="신뢰도별 실제 승률 (교정)")
        ct.add_column("신뢰도", style="cyan")
        ct.add_column("실제 승률", justify="right")
        for conf, acc in sorted(perf.confidence_calibration.items()):
            ct.add_row(str(conf), f"{acc:.1f}%")
        console.print(ct)


# ──────────────────────────────────────────
# ml — ML 모델 관리
# ──────────────────────────────────────────
@cli.group(help="ML 모델 관리")
def ml() -> None:
    """ML 명령어 그룹."""


@ml.command(name="status", help="ML 모델 상태 확인")
def ml_status() -> None:
    from src.ml.scorer import MLScorer

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        scorer = MLScorer()
        status = scorer.get_status(session)

    table = Table(title="ML 모델 상태")
    table.add_column("항목", style="cyan")
    table.add_column("값")

    table.add_row("상태", status["status"])
    table.add_row("데이터 축적", f"{status['data_days']}/{status['min_required']}일")
    table.add_row("모델 수", str(status["models_count"]))
    table.add_row("활성화", "[green]YES[/green]" if status["is_ready"] else "[yellow]NO[/yellow]")

    console.print(table)


@ml.command(name="train", help="수동 모델 학습")
def ml_train() -> None:
    from src.ml.trainer import train_return_model
    from src.ml.features import build_training_data

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        df = build_training_data(session)

    if df.empty:
        console.print("[yellow]학습 데이터 부족. 최소 60거래일 데이터가 필요합니다.[/yellow]")
        return

    console.print("[dim]모델 학습 중...[/dim]")
    train_return_model(df)
    console.print("[green]OK[/green] 모델 학습 완료")


@ml.command(name="evaluate", help="모델 성능 평가")
def ml_evaluate() -> None:
    from src.ml.evaluator import evaluate_model

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        result = evaluate_model(session)

    console.print(f"상태: {result['status']}")
    if "message" in result:
        console.print(f"메시지: {result['message']}")
    if "accuracy" in result:
        console.print(f"정확도: {result['accuracy']}%")
        console.print(f"Precision@10: {result['precision_at_10']}%")
        console.print(f"양수 예측 평균 수익: {result['avg_return_positive']}%")
        console.print(f"음수 예측 평균 수익: {result['avg_return_negative']}%")
        console.print(f"평가 기간: {result['data_days']}일, {result['total_predictions']}건")


# ──────────────────────────────────────────
# backtest — 백테스트
# ──────────────────────────────────────────
@cli.group(help="백테스트")
def backtest() -> None:
    """백테스트 명령어 그룹."""


@backtest.command("run", help="과거 추천 데이터로 백테스트 실행")
@click.option("--start", required=True, help="시작일 (YYYY-MM-DD)")
@click.option("--end", required=True, help="종료일 (YYYY-MM-DD)")
@click.option("--top", "top_n", default=10, help="상위 N개 (기본: 10)")
def backtest_run(start: str, end: str, top_n: int) -> None:
    """백테스트를 실행하고 결과를 출력한다."""
    from src.backtest.engine import BacktestConfig, BacktestEngine

    config = BacktestConfig(
        start_date=_parse_date(start),
        end_date=_parse_date(end),
        top_n=top_n,
    )

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        bt = BacktestEngine()
        result = bt.run(session, config)

    if result.total_recommendations == 0:
        console.print("[yellow]해당 기간에 추천 데이터가 없습니다.[/yellow]")
        return

    table = Table(title=f"백테스트 결과 ({start} ~ {end})")
    table.add_column("항목", style="cyan")
    table.add_column("값", style="green")

    table.add_row("거래일 수", str(result.total_days))
    table.add_row("총 추천 수", str(result.total_recommendations))
    if result.avg_return_1d is not None:
        table.add_row("평균 수익률 (1일, 거래비용 차감)", f"{result.avg_return_1d:+.2f}%")
    if result.avg_return_5d is not None:
        table.add_row("평균 수익률 (5일, 거래비용 차감)", f"{result.avg_return_5d:+.2f}%")
    if result.avg_return_20d is not None:
        table.add_row("평균 수익률 (20일, 거래비용 차감)", f"{result.avg_return_20d:+.2f}%")
    if result.win_rate_1d is not None:
        table.add_row("승률 (1일)", f"{result.win_rate_1d:.1f}%")
    if result.win_rate_5d is not None:
        table.add_row("승률 (5일)", f"{result.win_rate_5d:.1f}%")
    if result.win_rate_20d is not None:
        table.add_row("승률 (20일)", f"{result.win_rate_20d:.1f}%")
    if result.sharpe_ratio is not None:
        table.add_row("샤프 비율", f"{result.sharpe_ratio:.3f}")
    if result.max_drawdown is not None:
        table.add_row("최대 낙폭", f"{result.max_drawdown:.2f}%")
    if result.best_pick:
        t, r, d = result.best_pick
        table.add_row("최고 종목", f"{t} +{r:.1f}% ({d})")
    if result.worst_pick:
        t, r, d = result.worst_pick
        table.add_row("최저 종목", f"{t} {r:.1f}% ({d})")

    console.print(table)


@backtest.command("compare-weights", help="가중치 비교 백테스트")
@click.option("--start", required=True, help="시작일 (YYYY-MM-DD)")
@click.option("--end", required=True, help="종료일 (YYYY-MM-DD)")
def backtest_compare(start: str, end: str) -> None:
    """기본 가중치 vs 대안 가중치를 비교한다."""
    from src.backtest.comparator import DEFAULT_WEIGHTS, compare_weights

    weight_sets = [
        ("기본", DEFAULT_WEIGHTS),
        ("기술 중심", {"technical": 0.35, "fundamental": 0.20, "smart_money": 0.10, "external": 0.10, "momentum": 0.25}),
        ("펀더멘털 중심", {"technical": 0.15, "fundamental": 0.40, "smart_money": 0.15, "external": 0.10, "momentum": 0.20}),
        ("모멘텀 중심", {"technical": 0.20, "fundamental": 0.15, "smart_money": 0.10, "external": 0.15, "momentum": 0.40}),
    ]

    settings = get_settings()
    engine = create_db_engine(settings.db_path)

    with get_session(engine) as session:
        results = compare_weights(
            session, _parse_date(start), _parse_date(end), weight_sets,
        )

    if not results:
        console.print("[yellow]해당 기간에 추천 데이터가 없습니다.[/yellow]")
        return

    table = Table(title="가중치 비교 결과")
    table.add_column("전략", style="cyan")
    table.add_column("추천 수")
    table.add_column("평균 수익률 (20일)")
    table.add_column("승률 (20일)")

    for r in results:
        avg = f"{r.avg_return_20d:+.2f}%" if r.avg_return_20d is not None else "-"
        wr = f"{r.win_rate_20d:.1f}%" if r.win_rate_20d is not None else "-"
        table.add_row(r.label, str(r.total_picks), avg, wr)

    console.print(table)


def _parse_date(date_str: str) -> date:
    """YYYY-MM-DD 형식의 문자열을 date로 변환한다."""
    return date.fromisoformat(date_str)


# ──────────────────────────────────────────
# watchlist — 개인 워치리스트 관리
# ──────────────────────────────────────────


@cli.group(help="개인 워치리스트 관리")
def watchlist() -> None:
    """워치리스트 CLI 그룹."""


@watchlist.command("add", help="워치리스트에 종목 추가")
@click.argument("ticker")
@click.option("--shares", default=None, type=int, help="보유 수량")
@click.option("--avg-cost", default=None, type=float, help="평균 매수가")
def watchlist_add(ticker: str, shares: int | None, avg_cost: float | None) -> None:
    """워치리스트에 종목을 추가한다."""
    from src.db.migrate import ensure_schema
    from src.db.repository import WatchlistRepository
    from src.deepdive.watchlist_manager import ensure_stock_registered

    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    ensure_schema(engine)
    init_db(engine)

    with get_session(engine) as session:
        WatchlistRepository.add_ticker(session, ticker)
        ensure_stock_registered(session, ticker)
        if shares is not None and avg_cost is not None:
            WatchlistRepository.set_holding(session, ticker, shares, avg_cost)
            console.print(
                f"[green]{ticker.upper()} 추가 완료[/green] "
                f"(보유: {shares}주 @ ${avg_cost:.2f})"
            )
        else:
            console.print(f"[green]{ticker.upper()} 추가 완료[/green]")


@watchlist.command("remove", help="워치리스트에서 종목 제거")
@click.argument("ticker")
def watchlist_remove(ticker: str) -> None:
    """워치리스트에서 종목을 제거한다 (soft delete)."""
    from src.db.migrate import ensure_schema
    from src.db.repository import WatchlistRepository

    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    ensure_schema(engine)
    init_db(engine)

    with get_session(engine) as session:
        removed = WatchlistRepository.remove_ticker(session, ticker)
    if removed:
        console.print(f"[yellow]{ticker.upper()} 제거 완료[/yellow]")
    else:
        console.print(f"[red]{ticker.upper()} 을(를) 찾을 수 없습니다[/red]")


@watchlist.command("list", help="워치리스트 조회")
def watchlist_list() -> None:
    """워치리스트를 표시한다."""
    from src.db.migrate import ensure_schema
    from src.db.repository import WatchlistRepository

    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    ensure_schema(engine)
    init_db(engine)

    with get_session(engine) as session:
        items = WatchlistRepository.get_active(session)
        holdings = WatchlistRepository.get_all_holdings(session)

    if not items:
        console.print("[dim]워치리스트가 비어 있습니다[/dim]")
        return

    table = Table(title="워치리스트")
    table.add_column("티커", style="bold")
    table.add_column("보유")
    table.add_column("추가일")

    for item in items:
        holding = holdings.get(item.ticker)
        if holding:
            h_str = f"{holding.shares}주 @ ${float(holding.avg_cost):.2f}"
        else:
            h_str = "-"
        added = item.added_at.strftime("%Y-%m-%d") if item.added_at else "-"
        table.add_row(item.ticker, h_str, added)

    console.print(table)


@watchlist.command("set-holding", help="보유 정보 설정")
@click.argument("ticker")
@click.option("--shares", required=True, type=int, help="보유 수량")
@click.option("--avg-cost", required=True, type=float, help="평균 매수가")
@click.option("--opened-at", default=None, help="매수일 (YYYY-MM-DD)")
def watchlist_set_holding(
    ticker: str, shares: int, avg_cost: float, opened_at: str | None,
) -> None:
    """보유 정보를 설정/갱신한다."""
    from src.db.migrate import ensure_schema
    from src.db.repository import WatchlistRepository

    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    ensure_schema(engine)
    init_db(engine)

    opened = _parse_date(opened_at) if opened_at else None
    with get_session(engine) as session:
        WatchlistRepository.set_holding(session, ticker, shares, avg_cost, opened)
    console.print(
        f"[green]{ticker.upper()} 보유정보 설정[/green]: "
        f"{shares}주 @ ${avg_cost:.2f}"
    )


# ──────────────────────────────────────────
# deepdive — Deep Dive 개인 분석
# ──────────────────────────────────────────


@cli.group(help="Deep Dive 개인 분석")
def deepdive() -> None:
    """Deep Dive CLI 그룹."""


@deepdive.command("run", help="Deep Dive 파이프라인 실행")
@click.option("--date", "run_date", default=None, help="분석 날짜 (YYYY-MM-DD)")
@click.option("--ticker", default=None, help="특정 종목만 분석")
@click.option("--force", is_flag=True, help="체크포인트 무시 재실행")
@click.option("--skip-notify", is_flag=True, help="알림 스킵")
def deepdive_run(
    run_date: str | None, ticker: str | None, force: bool, skip_notify: bool,
) -> None:
    """Deep Dive 파이프라인을 실행한다."""
    from src.db.migrate import ensure_schema
    from src.deepdive_pipeline import DeepDivePipeline

    target_date = _parse_date(run_date) if run_date else date.today()
    _setup_logging(target_date)

    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    ensure_schema(engine)
    init_db(engine)

    pipeline = DeepDivePipeline(
        engine=engine,
        target_date=target_date,
        ticker=ticker,
        force=force,
        skip_notify=skip_notify,
    )
    pipeline.run()


@deepdive.command("latest", help="최신 Deep Dive 리포트 조회")
@click.option("--ticker", default=None, help="특정 종목만 표시")
def deepdive_latest(ticker: str | None) -> None:
    """가장 최근 Deep Dive 리포트(들)을 콘솔에 출력한다."""
    import json as _json

    from src.db.repository import DeepDiveRepository, StockRepository

    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    with get_session(engine) as session:
        if ticker:
            stock = StockRepository.get_by_ticker(session, ticker.upper())
            if not stock:
                console.print(f"[red]종목을 찾을 수 없음: {ticker}[/red]")
                return
            report = DeepDiveRepository.get_latest_report(session, stock.stock_id)
            reports = [report] if report else []
        else:
            reports = DeepDiveRepository.get_latest_reports_all(session)

        if not reports:
            console.print("[yellow]Deep Dive 리포트 없음[/yellow]")
            return

        for r in reports:
            console.print(
                f"\n[bold cyan]{r.ticker}[/bold cyan] — "
                f"{r.action_grade} (conviction {r.conviction}/10, "
                f"consensus {r.consensus_strength or 'N/A'})"
            )
            if r.ai_synthesis:
                console.print(f"  [dim]{r.ai_synthesis[:300]}[/dim]")
            # execution_guide 출력
            try:
                rd = _json.loads(r.report_json or "{}")
                guide = rd.get("execution_guide")
                if guide:
                    console.print(
                        f"  [green]Buy Zone[/green]: "
                        f"${guide.get('buy_zone_low'):.2f}~${guide.get('buy_zone_high'):.2f}  "
                        f"[red]Stop[/red]: ${guide.get('stop_loss'):.2f}  "
                        f"Target 3M: ${guide.get('target_3m') or 0:.2f}  "
                        f"EV 3M: {guide.get('expected_value_pct', {}).get('3M', 0):+.1f}%  "
                        f"R/R: {guide.get('risk_reward_ratio') or 0:.1f}  "
                        f"Size: {guide.get('suggested_position_pct', 0):.1f}%"
                    )
                    warns = guide.get("portfolio_fit_warnings") or []
                    for w in warns:
                        console.print(f"  [red]⚠ {w}[/red]")
            except (ValueError, TypeError, AttributeError):
                pass


@deepdive.command("status", help="Deep Dive 파이프라인 실행 상태")
def deepdive_status() -> None:
    """최근 Deep Dive 파이프라인 실행 요약을 출력한다."""
    from sqlalchemy import select

    from src.db.models import FactCollectionLog

    settings = get_settings()
    engine = create_db_engine(settings.db_path)
    with get_session(engine) as session:
        logs = list(
            session.execute(
                select(FactCollectionLog)
                .where(FactCollectionLog.step.like("dd_%"))
                .order_by(FactCollectionLog.run_date_id.desc(), FactCollectionLog.step)
                .limit(50)
            ).scalars().all()
        )

    if not logs:
        console.print("[yellow]Deep Dive 실행 기록 없음[/yellow]")
        return

    from collections import defaultdict

    by_date = defaultdict(list)
    for log in logs:
        by_date[log.run_date_id].append(log)

    for date_id in sorted(by_date.keys(), reverse=True)[:5]:
        console.print(f"\n[bold]Run {date_id}[/bold]")
        for log in by_date[date_id]:
            status_color = {
                "success": "green",
                "failed": "red",
                "interrupted": "yellow",
            }.get(log.status, "white")
            console.print(
                f"  [{status_color}]{log.step}[/{status_color}]: "
                f"{log.status} ({log.records_count} records)"
            )


# ──────────────────────────────────────────
# db backfill — 데이터 백필
# ──────────────────────────────────────────
@db.command("backfill-macro", help="매크로 히스토리 백필 (VIX/금리/달러/S&P500)")
@click.option("--days", default=730, help="수집 기간 (기본: 730일 = 2년)")
def db_backfill_macro(days: int) -> None:
    """과거 매크로 데이터를 한번에 수집한다."""
    from src.data.backfill_macro import backfill_macro
    console.print(f"[dim]매크로 백필 시작: 최근 {days}일[/dim]")
    count = backfill_macro(days)
    console.print(f"[green]완료[/green]: {count}건 저장")


# ──────────────────────────────────────────
# web — 웹 대시보드
# ──────────────────────────────────────────
@cli.command(help="웹 대시보드 실행")
@click.option("--host", default="127.0.0.1", help="호스트 (기본: localhost)")
@click.option("--port", default=8000, type=int, help="포트 (기본: 8000)")
def web(host: str, port: int) -> None:
    """브라우저 기반 투자 분석 대시보드를 실행한다."""
    import uvicorn
    from src.web.app import create_app

    console.print(f"[bold green]Investmate 대시보드 시작[/bold green]: http://{host}:{port}")
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    cli()
