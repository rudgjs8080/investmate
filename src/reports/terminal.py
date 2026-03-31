"""Rich 터미널 출력 모듈."""

from __future__ import annotations

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.analysis.signals import calculate_composite_strength
from src.analysis.technical import INDICATOR_COLUMNS
from src.reports.report_models import EnrichedDailyReport

try:
    from src.reports.generator import ReportData
except ImportError:
    ReportData = None  # type: ignore[assignment,misc]

console = Console()

DISCLAIMER = "※ 투자 참고용이며 투자 권유가 아닙니다."

INDICATOR_LABELS = {
    "sma_5": "SMA (5)", "sma_20": "SMA (20)", "sma_60": "SMA (60)",
    "sma_120": "SMA (120)", "ema_12": "EMA (12)", "ema_26": "EMA (26)",
    "rsi_14": "RSI (14)", "macd": "MACD", "macd_signal": "MACD Signal",
    "macd_hist": "MACD Hist", "bb_upper": "BB 상단", "bb_middle": "BB 중단",
    "bb_lower": "BB 하단", "stoch_k": "Stoch %K", "stoch_d": "Stoch %D",
}


def render_stock_report(report: ReportData) -> None:
    """종목 상세 리포트를 터미널에 출력한다."""
    change_color = "green" if report.price_change >= 0 else "red"

    # 헤더 패널
    console.print(Panel(
        f"[bold]{report.name}[/bold] ({report.ticker}) | {report.market}\n"
        f"현재가: {report.current_price:,.2f} "
        f"[{change_color}]{report.price_change:+,.2f} "
        f"({report.price_change_pct:+.1f}%)[/{change_color}]",
        title="종목 분석 리포트",
    ))

    # 기술적 지표
    latest = report.indicators_df.iloc[-1]
    ind_table = Table(title="기술적 지표")
    ind_table.add_column("지표", style="cyan")
    ind_table.add_column("값", justify="right")

    for col in INDICATOR_COLUMNS:
        val = latest.get(col)
        if val is not None and not (isinstance(val, float) and pd.isna(val)):
            ind_table.add_row(
                INDICATOR_LABELS.get(col, col), f"{val:,.2f}"
            )

    console.print(ind_table)

    # 시그널
    if report.signals:
        sig_table = Table(title="활성 시그널")
        sig_table.add_column("시그널", style="cyan")
        sig_table.add_column("방향", justify="center")
        sig_table.add_column("강도", justify="right")
        sig_table.add_column("설명")

        for s in report.signals:
            dir_color = {"BUY": "green", "SELL": "red"}.get(s.direction, "yellow")
            sig_table.add_row(
                s.signal_type,
                f"[{dir_color}]{s.direction}[/{dir_color}]",
                str(s.strength),
                s.description,
            )

        composite = calculate_composite_strength(list(report.signals))
        console.print(sig_table)
        console.print(f"복합 강도: [bold]{composite}[/bold] / 10")
    else:
        console.print("[dim]현재 활성 시그널 없음[/dim]")

    # 기본적 분석
    fs = report.fundamental_score
    fund_table = Table(title="기본적 분석 점수")
    fund_table.add_column("항목", style="cyan")
    fund_table.add_column("점수", justify="right")

    fund_table.add_row("PER", f"{fs.per_score:.1f}")
    fund_table.add_row("PBR", f"{fs.pbr_score:.1f}")
    fund_table.add_row("ROE", f"{fs.roe_score:.1f}")
    fund_table.add_row("부채비율", f"{fs.debt_score:.1f}")
    fund_table.add_row("성장성", f"{fs.growth_score:.1f}")
    if fs.dividend_yield is not None and fs.dividend_yield > 0:
        dy_pct = fs.dividend_yield * 100 if abs(fs.dividend_yield) < 1 else fs.dividend_yield
        fund_table.add_row("배당수익률", f"{dy_pct:.2f}%")
    fund_table.add_row("[bold]종합[/bold]", f"[bold]{fs.composite_score:.1f}[/bold] ({fs.summary})")

    console.print(fund_table)

    # AI 분석 결과
    if report.ai_approved is not None:
        if report.ai_approved:
            conf = f" (신뢰도 {report.ai_confidence}/10)" if report.ai_confidence else ""
            risk = f" | 리스크: {report.ai_risk_level}" if report.ai_risk_level else ""
            console.print(f"[bold green]AI 분석: 추천{conf}{risk}[/bold green]")
            if report.ai_reason:
                console.print(f"  [dim]{report.ai_reason}[/dim]")
            if report.ai_entry_strategy:
                console.print(f"  [cyan]매수:[/cyan] {report.ai_entry_strategy}")
            if report.ai_exit_strategy:
                console.print(f"  [cyan]익절/손절:[/cyan] {report.ai_exit_strategy}")
            if report.ai_target_price or report.ai_stop_loss:
                parts = []
                if report.ai_target_price:
                    parts.append(f"목표 ${report.ai_target_price:,.0f}")
                if report.ai_stop_loss:
                    parts.append(f"손절 ${report.ai_stop_loss:,.0f}")
                console.print(f"  {' | '.join(parts)}")
        else:
            console.print(f"[bold red]AI 분석: 제외[/bold red]")
            if report.ai_reason:
                console.print(f"  [dim]{report.ai_reason}[/dim]")
    elif report.ai_approved is None:
        console.print("[dim]AI 분석: 미실행[/dim]")
    console.print()

    # 최근 뉴스
    if report.news:
        news_table = Table(title="최근 뉴스")
        news_table.add_column("날짜", style="dim", no_wrap=True)
        news_table.add_column("제목")
        news_table.add_column("출처", style="dim")

        for article in report.news[:5]:
            pub = article.published_at.strftime("%Y-%m-%d") if article.published_at else "-"
            news_table.add_row(pub, article.title[:80], article.source)

        console.print(news_table)

    # 30일 가격 추이 (간단 ASCII)
    if report.price_history_30d:
        _render_price_sparkline(report.price_history_30d)

    console.print(f"\n[dim italic]{DISCLAIMER}[/dim italic]")


def render_watchlist_dashboard(stocks_data: list[dict]) -> None:
    """워치리스트 대시보드를 출력한다."""
    table = Table(title="워치리스트 대시보드")
    table.add_column("티커", style="cyan", no_wrap=True)
    table.add_column("종목명")
    table.add_column("현재가", justify="right")
    table.add_column("변동", justify="right")
    table.add_column("시그널", justify="center")
    table.add_column("RSI", justify="right")

    for data in stocks_data:
        change_color = "green" if data.get("change", 0) >= 0 else "red"
        signal_count = data.get("signal_count", 0)
        sig_str = f"[yellow]{signal_count}[/yellow]" if signal_count > 0 else "0"

        table.add_row(
            data["ticker"],
            data["name"],
            f"{data.get('price', 0):,.2f}",
            f"[{change_color}]{data.get('change_pct', 0):+.1f}%[/{change_color}]",
            sig_str,
            f"{data.get('rsi', '-')}",
        )

    console.print(table)
    console.print(f"\n[dim italic]{DISCLAIMER}[/dim italic]")


def render_collection_summary(result) -> None:  # noqa: ANN001
    """수집 결과 요약을 출력한다."""
    table = Table(title="수집 결과")
    table.add_column("종목", style="cyan")
    table.add_column("상태", justify="center")
    table.add_column("가격", justify="right")
    table.add_column("재무", justify="right")
    table.add_column("뉴스", justify="right")

    for r in result.results:
        status_colors = {"success": "green", "skipped": "yellow", "failed": "red"}
        status_labels = {"success": "성공", "skipped": "스킵", "failed": "실패"}
        color = status_colors.get(r.status, "white")
        label = status_labels.get(r.status, r.status)

        table.add_row(
            r.ticker,
            f"[{color}]{label}[/{color}]",
            str(r.prices_count),
            str(r.financials_count),
            str(r.news_count),
        )

    console.print(table)


def render_daily_report(report: EnrichedDailyReport) -> None:
    """데일리 리포트를 Rich 터미널로 출력한다 (두괄식)."""
    from src.reports.explainer import market_investment_opinion, summarize_market, summarize_recommendations_oneliner

    m = report.macro

    # 실행 요약
    duration_str = ""
    if report.pipeline_duration_sec:
        mins = int(report.pipeline_duration_sec // 60)
        secs = int(report.pipeline_duration_sec % 60)
        duration_str = f" | 소요 {mins}분 {secs}초"

    console.print(Panel(
        f"[bold]{report.run_date.isoformat()}[/bold] | "
        f"분석 {report.total_stocks_analyzed}개 | "
        f"추천 {len(report.recommendations)}개{duration_str}",
        title="[bold cyan]데일리 투자 리포트[/bold cyan]",
        subtitle=DISCLAIMER,
    ))

    # 핵심 요약 (두괄식)
    market_summary = summarize_market(m)
    recs_oneliner = summarize_recommendations_oneliner(report.recommendations)
    opinion = market_investment_opinion(m, len(report.recommendations))
    sector_counts: dict[str, int] = {}
    for rec in report.recommendations:
        s = rec.sector or "기타"
        sector_counts[s] = sector_counts.get(s, 0) + 1
    sector_str = " | ".join(f"{s} {c}" for s, c in sorted(sector_counts.items(), key=lambda x: -x[1]))

    console.print(Panel(
        f"[bold]시장:[/bold] {market_summary}\n"
        f"[bold]추천:[/bold] {recs_oneliner}\n"
        f"[bold]의견:[/bold] {opinion}\n"
        f"[bold]섹터:[/bold] {sector_str}",
        title="[bold yellow]핵심 요약 (30초 브리핑)[/bold yellow]",
        border_style="yellow",
    ))

    # 시장 환경
    macro_table = Table(title="시장 환경", show_lines=True)
    macro_table.add_column("지표", style="cyan", no_wrap=True)
    macro_table.add_column("값", justify="right")
    macro_table.add_column("상태", justify="center")

    mood_color = {"강세": "green", "약세": "red"}.get(m.mood, "yellow")
    macro_table.add_row(
        "시장 점수", f"[bold]{m.market_score or '-'}/10[/bold]",
        f"[{mood_color}]{m.mood}[/{mood_color}]",
    )
    vix_color = {"안정": "green", "주의": "yellow", "위험": "red"}.get(m.vix_status, "white")
    macro_table.add_row("VIX", f"{m.vix:.2f}" if m.vix else "-", f"[{vix_color}]{m.vix_status}[/{vix_color}]")
    sp_trend_color = "green" if m.sp500_trend == "상승" else "red"
    macro_table.add_row("S&P 500", f"{m.sp500_close:,.2f}" if m.sp500_close else "-", f"[{sp_trend_color}]20일선 {m.sp500_trend}[/{sp_trend_color}]")
    macro_table.add_row("10년 국채", f"{m.us_10y_yield:.2f}%" if m.us_10y_yield else "-", "")
    macro_table.add_row("달러 인덱스", f"{m.dollar_index:.2f}" if m.dollar_index else "-", "")
    if m.yield_spread is not None:
        spread_color = "green" if m.yield_spread > 0 else "red"
        macro_table.add_row("장단기 스프레드", f"[{spread_color}]{m.yield_spread:+.2f}%p[/{spread_color}]", "")
    console.print(macro_table)

    # TOP N 요약 테이블
    top_table = Table(title=f"매수 추천 TOP {len(report.recommendations)}")
    top_table.add_column("순위", justify="center", style="bold")
    top_table.add_column("종목", style="cyan")
    top_table.add_column("섹터", style="dim")
    top_table.add_column("현재가", justify="right")
    top_table.add_column("등락", justify="right")
    top_table.add_column("종합", justify="center", style="bold yellow")
    top_table.add_column("기술", justify="center")
    top_table.add_column("기본", justify="center")
    top_table.add_column("수급", justify="center")
    top_table.add_column("모멘텀", justify="center")

    for rec in report.recommendations:
        chg = ""
        if rec.price_change_pct is not None:
            chg_color = "green" if rec.price_change_pct >= 0 else "red"
            chg = f"[{chg_color}]{rec.price_change_pct:+.1f}%[/{chg_color}]"
        top_table.add_row(
            str(rec.rank), rec.ticker, rec.sector or "-",
            f"${rec.price:,.2f}", chg,
            f"{rec.total_score:.1f}", f"{rec.technical_score:.1f}",
            f"{rec.fundamental_score:.1f}", f"{rec.smart_money_score:.1f}",
            f"{rec.momentum_score:.1f}",
        )
    console.print(top_table)

    # 포지션 사이징 테이블 (비중 데이터가 있을 때만)
    has_sizing = any(
        rec.position_weight is not None for rec in report.recommendations
    )
    if has_sizing:
        sizing_table = Table(title="추천 비중 (포지션 사이징)")
        sizing_table.add_column("종목", style="cyan")
        sizing_table.add_column("비중", justify="right", style="bold")
        sizing_table.add_column("손절가", justify="right")
        sizing_table.add_column("전략", justify="center", style="dim")

        total_weight = 0.0
        for rec in report.recommendations:
            if rec.position_weight is None:
                continue
            w = rec.position_weight
            total_weight += w
            stop = ""
            if rec.trailing_stop is not None:
                stop = f"${rec.trailing_stop:,.2f}"
            sizing_table.add_row(
                rec.ticker,
                f"{w:.1%}",
                stop,
                rec.sizing_strategy or "-",
            )

        cash_weight = max(0.0, 1.0 - total_weight)
        sizing_table.add_row(
            "[dim]현금[/dim]", f"[dim]{cash_weight:.1%}[/dim]", "", "",
        )
        console.print(sizing_table)

    # 실행 비용 요약 (비용 데이터가 있을 때만)
    has_cost = any(
        rec.total_cost_bps is not None for rec in report.recommendations
    )
    if has_cost:
        cost_table = Table(title="실행 현황")
        cost_table.add_column("종목", style="cyan")
        cost_table.add_column("스프레드", justify="right")
        cost_table.add_column("시장충격", justify="right")
        cost_table.add_column("총 비용", justify="right", style="bold")

        for rec in report.recommendations:
            if rec.total_cost_bps is None:
                continue
            cost_table.add_row(
                rec.ticker,
                f"{rec.spread_cost_bps:.1f}bps" if rec.spread_cost_bps else "-",
                f"{rec.impact_cost_bps:.1f}bps" if rec.impact_cost_bps else "-",
                f"{rec.total_cost_bps:.1f}bps",
            )
        console.print(cost_table)

    # 종목별 상세
    for rec in report.recommendations:
        _render_stock_panel(rec)

    # 시그널 요약
    console.print(Panel(
        f"[bold]총 {len(report.all_signals)}개[/bold] 시그널 발생 "
        f"([green]매수 {report.buy_signal_count}[/green] / "
        f"[red]매도 {report.sell_signal_count}[/red])",
        title="시그널 요약",
    ))

    console.print(f"\n[dim italic]{DISCLAIMER}[/dim italic]")


def _render_stock_panel(rec) -> None:
    """추천 종목 1개를 Rich Panel로 출력한다."""
    t = rec.technical
    f = rec.fundamental
    sm = rec.smart_money
    e = rec.earnings

    from src.reports.explainer import explain_stock as _explain
    explanation = _explain(rec)

    parts = []
    chg = f" ({rec.price_change_pct:+.1f}%)" if rec.price_change_pct is not None else ""
    parts.append(f"현재가: ${rec.price:,.2f}{chg} | 섹터: {rec.sector or '-'}")
    parts.append(f"[bold]{explanation.headline}[/bold]")
    parts.append("")

    # 기술적
    parts.append("[cyan]기술적 분석[/cyan]")
    parts.append(f"  RSI: {t.rsi:.1f} ({t.rsi_status})" if t.rsi else "  RSI: -")
    parts.append(f"  MACD: {t.macd_status} | 이동평균: {t.sma_alignment} | 볼린저: {t.bb_position}")
    if t.volume_ratio:
        parts.append(f"  거래량: 20일 평균 대비 {t.volume_ratio:.0%}")
    from src.reports.explainer import _translate_signals
    for s in t.signals:
        color = "green" if s.direction == "BUY" else "red"
        kr_name = _translate_signals([s.signal_type])[0]
        dir_kr = "매수" if s.direction == "BUY" else "매도"
        parts.append(f"  [{color}][{dir_kr}] {kr_name} ({s.strength}/10)[/{color}]")

    # 기본적
    parts.append("")
    parts.append(f"[cyan]기본적 분석[/cyan] (종합 {f.composite_score:.1f} --{f.summary})")
    parts.append(f"  PER {_fv(f.per)}({f.per_score:.0f}) | PBR {_fv(f.pbr)}({f.pbr_score:.0f}) | ROE {_froe(f.roe)}({f.roe_score:.0f}) | 부채 {_frat(f.debt_ratio)}({f.debt_score:.0f})")

    # 애널리스트
    total_a = sm.analyst_strong_buy + sm.analyst_buy + sm.analyst_hold + sm.analyst_sell + sm.analyst_strong_sell
    if total_a > 0:
        parts.append("")
        parts.append("[cyan]수급/스마트머니[/cyan]")
        buy_total = sm.analyst_strong_buy + sm.analyst_buy
        sell_total = sm.analyst_sell + sm.analyst_strong_sell
        parts.append(f"  애널리스트: Buy {buy_total} / Hold {sm.analyst_hold} / Sell {sell_total}")
        if sm.target_mean:
            upside = f" ({sm.upside_pct:+.1f}%)" if sm.upside_pct is not None else ""
            parts.append(f"  목표가: ${sm.target_mean:,.2f}{upside}")
        parts.append(f"  내부자: {sm.insider_summary}")

    # 실적
    if e.latest_period:
        parts.append("")
        eps = f"EPS {e.eps_surprise_pct:+.1f}%" if e.eps_surprise_pct is not None else "EPS -"
        parts.append(f"[cyan]실적[/cyan] ({e.latest_period}): {eps} | 연속 상회: {e.beat_streak}분기")

    # 리스크
    parts.append("")
    parts.append("[yellow]리스크 요인[/yellow]")
    for r in rec.risk_factors:
        parts.append(f"  [!] {r}")

    console.print(Panel(
        "\n".join(parts),
        title=f"[bold]#{rec.rank} {rec.ticker} --{rec.name}[/bold] (종합 {rec.total_score:.1f})",
        border_style="blue",
    ))


def _fv(v) -> str:
    return f"{v:.1f} " if v is not None else "- "


def _froe(v) -> str:
    if v is None:
        return "- "
    pct = v * 100 if abs(v) < 1 else v
    return f"{pct:.1f}% "


def _frat(v) -> str:
    if v is None:
        return "- "
    return f"{v:.0%} "


def _render_price_sparkline(price_history: tuple) -> None:
    """30일 가격 추이를 ASCII 차트로 출력한다."""
    if not price_history:
        return

    prices = [p["close"] for p in price_history]
    min_p = min(prices)
    max_p = max(prices)
    price_range = max_p - min_p

    if price_range == 0:
        return

    height = 8
    width = min(len(prices), 60)

    # 가격을 width로 리샘플링
    step = max(1, len(prices) // width)
    sampled = prices[::step][:width]

    bars = "▁▂▃▄▅▆▇█"
    chart_chars = []
    for p in sampled:
        idx = int((p - min_p) / price_range * (len(bars) - 1))
        chart_chars.append(bars[idx])

    console.print(f"\n30일 추이: {''.join(chart_chars)}")
    console.print(f"           {min_p:,.0f} ~ {max_p:,.0f}")
