"""주간 리포트 생성기 — 두괄식 Markdown + JSON 출력."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from sqlalchemy.orm import Session

from src.reports.weekly_assembler import assemble_weekly_report
from src.reports.weekly_models import (
    ConvictionPick,
    SectorRotationEntry,
    WeeklyReport,
)

logger = logging.getLogger(__name__)

DISCLAIMER = "※ 본 리포트는 투자 참고용이며 투자 권유가 아닙니다."


def generate_and_save_weekly_report(
    session: Session, year: int, week_number: int,
) -> WeeklyReport:
    """주간 리포트를 생성하고 파일로 저장한다."""
    report = assemble_weekly_report(session, year, week_number)

    reports_dir = Path("reports/weekly")
    reports_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{year}-W{week_number:02d}"
    _save_json(report, reports_dir / f"{filename}.json")
    _save_markdown(report, reports_dir / f"{filename}.md")

    logger.info("주간 리포트 저장 완료: %s", reports_dir / filename)
    return report


# ──────────────────────────────────────────
# JSON
# ──────────────────────────────────────────


def _save_json(report: WeeklyReport, path: Path) -> None:
    data = asdict(report)
    data["disclaimer"] = DISCLAIMER
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ──────────────────────────────────────────
# Markdown (두괄식 — 핵심 요약 먼저)
# ──────────────────────────────────────────


def _save_markdown(report: WeeklyReport, path: Path) -> None:
    lines: list[str] = []
    _w = lines.append

    _w(f"# 주간 투자 리포트 — {report.year}-W{report.week_number:02d}")
    _w(f"> {report.week_start} ~ {report.week_end} | 거래일 {report.trading_days}일")
    _w("")
    _w(f"> {DISCLAIMER}")
    _w("")

    _render_ai_commentary_section(lines, report)
    _render_executive_summary(lines, report)
    _render_performance_review(lines, report)
    _render_best_worst_detail(lines, report)
    _render_conviction_picks(lines, report)
    _render_conviction_technicals(lines, report)
    _render_sector_rotation(lines, report)
    _render_macro_summary(lines, report)
    _render_risk_dashboard(lines, report)
    _render_signal_trend(lines, report)
    _render_ai_accuracy(lines, report)
    _render_win_rate_trend(lines, report)
    _render_week_over_week(lines, report)
    _render_action_items(lines, report)
    _render_outlook(lines, report)

    _w("---")
    _w(f"*{DISCLAIMER}*")
    _w(f"생성일: {report.generated_at}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ──────────────────────────────────────────
# 섹션 1: Executive Summary
# ──────────────────────────────────────────


def _render_executive_summary(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    es = report.executive_summary

    _w("## 1분 브리핑")
    _w("")
    _w(f"> **{es.market_oneliner}**")
    _w("")

    # 핵심 지표 한줄
    parts: list[str] = []
    if es.sp500_weekly_return_pct is not None:
        arrow = "+" if es.sp500_weekly_return_pct > 0 else ""
        parts.append(f"S&P 500: {arrow}{es.sp500_weekly_return_pct:.1f}%")
    if es.vix_end is not None:
        parts.append(f"VIX: {es.vix_end:.1f}")
        if es.vix_high is not None and es.vix_low is not None:
            parts.append(f"(주간 {es.vix_low:.1f}~{es.vix_high:.1f})")
    if parts:
        _w(f"**주간 지표:** {' | '.join(parts)}")
        _w("")

    if es.regime_changed:
        _w(f"**[!] 시장 체제 변화:** {es.regime_start} -> {es.regime_end}")
        _w("")

    perf_parts: list[str] = []
    if es.weekly_win_rate_pct is not None:
        perf_parts.append(f"승률 {es.weekly_win_rate_pct:.0f}%")
    if es.weekly_avg_return_pct is not None:
        perf_parts.append(f"평균 수익률 {es.weekly_avg_return_pct:+.2f}%")
    if perf_parts:
        _w(f"**주간 추천 성과:** {' | '.join(perf_parts)}")
        _w("")


# ──────────────────────────────────────────
# 섹션 2: Performance Review
# ──────────────────────────────────────────


def _render_performance_review(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    pr = report.performance_review

    _w("## 주간 추천 성과 리뷰")
    _w("")

    if pr.total_unique_picks == 0:
        _w("*이번 주 추천 종목이 없습니다.*")
        _w("")
        return

    _w(f"총 **{pr.total_unique_picks}**개 종목 추천 | "
       f"승 {pr.win_count} / 패 {pr.loss_count} | "
       f"승률 **{_fmt_pct(pr.win_rate_pct)}** | "
       f"평균 수익률 **{_fmt_return(pr.avg_return_pct)}**")
    _w("")

    # 베스트/워스트
    if pr.best_pick:
        _w(f"- **베스트 픽:** {pr.best_pick.ticker} ({pr.best_pick.name}) "
           f"{_fmt_return(pr.best_pick.weekly_return_pct)}")
    if pr.worst_pick:
        _w(f"- **워스트 픽:** {pr.worst_pick.ticker} ({pr.worst_pick.name}) "
           f"{_fmt_return(pr.worst_pick.weekly_return_pct)}")

    # AI 비교
    if pr.ai_approved_avg_return is not None or pr.ai_rejected_avg_return is not None:
        _w(f"- **AI 승인 종목 평균:** {_fmt_return(pr.ai_approved_avg_return)} | "
           f"**AI 제외 종목 평균:** {_fmt_return(pr.ai_rejected_avg_return)}")
    _w("")

    # 전체 종목 테이블
    if pr.all_picks:
        _w("| 종목 | 섹터 | 추천일수 | 평균순위 | 주간수익률 | AI |")
        _w("|------|------|:-------:|:-------:|:---------:|:---:|")
        for p in pr.all_picks:
            ai_str = (
                f"추천{p.ai_approved_days}일" if p.ai_approved_days > 0
                else (f"제외{p.ai_rejected_days}일" if p.ai_rejected_days > 0 else "-")
            )
            _w(f"| **{p.ticker}** ({p.name}) | {p.sector or '-'} | "
               f"{p.days_recommended} | {p.avg_rank:.0f} | "
               f"{_fmt_return(p.weekly_return_pct)} | {ai_str} |")
        _w("")


# ──────────────────────────────────────────
# 섹션 3: Conviction Picks
# ──────────────────────────────────────────


def _render_conviction_picks(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    _w("## 확신 종목 (Conviction Picks)")
    _w("")

    if not report.conviction_picks:
        _w("*이번 주 3일 이상 연속 추천된 확신 종목이 없습니다.*")
        _w("")
        return

    _w(f"> 주 {report.trading_days}거래일 중 "
       f"{max(2, int(report.trading_days * 0.6))}일 이상 추천된 종목")
    _w("")

    _w("| 종목 | 섹터 | 추천일수 | 연속 | 평균순위 | 평균점수 | 주간수익률 | AI |")
    _w("|------|------|:-------:|:---:|:-------:|:-------:|:---------:|:---:|")
    for c in report.conviction_picks:
        _w(f"| **{c.ticker}** ({c.name}) | {c.sector or '-'} | "
           f"{c.days_recommended} | {c.consecutive_days} | "
           f"{c.avg_rank:.0f} | {c.avg_total_score:.1f} | "
           f"{_fmt_return(c.weekly_return_pct)} | {c.ai_consensus} |")
    _w("")


# ──────────────────────────────────────────
# 섹션 4: Sector Rotation
# ──────────────────────────────────────────


def _render_sector_rotation(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    _w("## 섹터 로테이션")
    _w("")

    if not report.sector_rotation:
        _w("*섹터 데이터가 없습니다.*")
        _w("")
        return

    _w("| 섹터 | 주간수익률 | 거래량변화 | 모멘텀 | 추천수 |")
    _w("|------|:---------:|:---------:|:-----:|:-----:|")
    for s in report.sector_rotation:
        _w(f"| {s.sector} | {_fmt_return(s.weekly_return_pct)} | "
           f"{_fmt_return(s.volume_change_pct)} | {s.momentum_delta} | {s.pick_count} |")
    _w("")


# ──────────────────────────────────────────
# 섹션 5: Macro Summary
# ──────────────────────────────────────────


def _render_macro_summary(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    ms = report.macro_summary

    _w("## 매크로 환경 주간 변화")
    _w("")

    # 시장 점수 일별 추이
    if ms.daily_scores:
        _w("**시장 점수 추이:**")
        score_parts = [
            f"{d}: **{s}/10**" if s is not None else f"{d}: -"
            for d, s in ms.daily_scores
        ]
        _w(" → ".join(score_parts))
        _w("")

    # 주요 지표 변동
    _w("| 지표 | 주초 | 주말 | 변동 |")
    _w("|------|:----:|:----:|:----:|")
    _render_macro_row(lines, "10년 국채", ms.us_10y_start, ms.us_10y_end, "%")
    _render_macro_row(lines, "13주 국채", ms.us_13w_start, ms.us_13w_end, "%")
    _render_macro_row(lines, "장단기 스프레드", ms.spread_start, ms.spread_end, "%p")
    _render_macro_row(lines, "달러 인덱스", ms.dollar_start, ms.dollar_end, "")
    _render_macro_row(lines, "금 ($/oz)", ms.gold_start, ms.gold_end, "")
    _render_macro_row(lines, "유가 ($/bbl)", ms.oil_start, ms.oil_end, "")
    _w("")


def _render_macro_row(
    lines: list[str], label: str,
    start: float | None, end: float | None, suffix: str,
) -> None:
    if start is None and end is None:
        return
    s = f"{start:.2f}{suffix}" if start is not None else "-"
    e = f"{end:.2f}{suffix}" if end is not None else "-"
    delta = ""
    if start is not None and end is not None:
        diff = end - start
        delta = f"{diff:+.2f}{suffix}"
    lines.append(f"| {label} | {s} | {e} | {delta} |")


# ──────────────────────────────────────────
# 섹션 6: Signal Trend
# ──────────────────────────────────────────


def _render_signal_trend(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    st = report.signal_trend

    _w("## 시그널 트렌드")
    _w("")

    if not st.daily_buy_counts and not st.daily_sell_counts:
        _w("*시그널 데이터가 없습니다.*")
        _w("")
        return

    _w("| 날짜 | 매수 시그널 | 매도 시그널 | 비율 |")
    _w("|------|:---------:|:---------:|:----:|")
    for (d, buy), (_, sell) in zip(st.daily_buy_counts, st.daily_sell_counts):
        total = buy + sell
        ratio = f"{buy}:{sell}" if total > 0 else "-"
        _w(f"| {d} | {buy} | {sell} | {ratio} |")
    _w("")

    if st.most_frequent_signal:
        _w(f"**가장 빈번한 시그널:** {st.most_frequent_signal}")
    if st.avg_strength_change is not None:
        direction = "강화" if st.avg_strength_change > 0 else "약화"
        _w(f"**평균 시그널 강도:** 전주 대비 {st.avg_strength_change:+.1f} ({direction})")
    _w("")


# ──────────────────────────────────────────
# 섹션 7: AI Accuracy
# ──────────────────────────────────────────


def _render_ai_accuracy(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    ai = report.ai_accuracy

    _w("## AI 예측 정확도")
    _w("")

    if ai.total_reviewed == 0:
        _w("*이번 주 AI 리뷰 데이터가 없습니다.*")
        _w("")
        return

    _w(f"- **AI 리뷰 종목:** {ai.total_reviewed}개")
    if ai.approval_rate_pct is not None:
        _w(f"- **승인율:** {ai.approval_rate_pct:.1f}%")
    if ai.direction_accuracy_pct is not None:
        _w(f"- **방향 정확도:** {ai.direction_accuracy_pct:.1f}%")
    if ai.confidence_vs_return_corr is not None:
        _w(f"- **신뢰도-수익률 상관:** {ai.confidence_vs_return_corr:.2f}")
    _w("")


# ──────────────────────────────────────────
# 섹션 8: Outlook
# ──────────────────────────────────────────


def _render_action_items(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    if not report.action_items:
        return
    _w("## 이번 주 할 일")
    _w("")
    for item in report.action_items:
        _w(f"**{item.priority}.** {item.action}")
        _w(f"   > {item.rationale}")
        _w("")


def _render_outlook(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    ol = report.outlook

    _w("## 다음 주 전망")
    _w("")
    _w(f"**전략:** {ol.regime_strategy}")
    _w("")

    if ol.watchlist_sectors:
        _w(f"**관심 섹터:** {', '.join(ol.watchlist_sectors)}")
    if ol.avoid_sectors:
        _w(f"**주의 섹터:** {', '.join(ol.avoid_sectors)}")
    if ol.rebalancing_suggestion:
        _w(f"**리밸런싱:** {ol.rebalancing_suggestion}")
    _w("")


# ──────────────────────────────────────────
# 포맷 헬퍼
# ──────────────────────────────────────────


def _render_ai_commentary_section(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    if not report.ai_commentary:
        return
    _w("## AI 주간 코멘터리")
    _w("")
    _w(report.ai_commentary)
    _w("")


def _render_best_worst_detail(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    if not report.best_worst_detail:
        return
    _w("## 베스트/워스트 상세 분석")
    _w("")
    _w("| 종목 | 수익률 | RSI | MACD Hist | SMA 배열 | 거래량 | 원인 |")
    _w("|------|:------:|:---:|:---------:|:-------:|:-----:|------|")
    for d in report.best_worst_detail:
        _w(f"| **{d.ticker}** ({d.name}) | {_fmt_return(d.weekly_return_pct)} | "
           f"{d.rsi_14 or '-'} | {d.macd_histogram or '-'} | {d.sma_alignment} | "
           f"{_fmt_pct(d.volume_vs_avg_pct)} | {d.catalyst_note} |")
    _w("")


def _render_conviction_technicals(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    if not report.conviction_technicals:
        return
    _w("## 확신 종목 기술적 상황")
    _w("")
    _w("| 종목 | RSI | MACD | SMA 배열 | BB 위치 | 지지 | 저항 |")
    _w("|------|:---:|:----:|:-------:|:------:|:----:|:----:|")
    for ct in report.conviction_technicals:
        sup = f"${ct.support_price:,.0f}" if ct.support_price else "-"
        res = f"${ct.resistance_price:,.0f}" if ct.resistance_price else "-"
        _w(f"| **{ct.ticker}** ({ct.name}) | {ct.rsi_14 or '-'} | {ct.macd_signal} | "
           f"{ct.sma_alignment} | {ct.bb_position} | {sup} | {res} |")
    _w("")


def _render_risk_dashboard(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    rd = report.risk_dashboard
    if not rd:
        return
    _w("## 리스크 대시보드")
    _w("")
    if rd.top_sector and rd.max_sector_concentration_pct is not None:
        _w(f"- **섹터 집중도:** {rd.top_sector} ({rd.max_sector_concentration_pct:.0f}%)")
    _w(f"- **VIX 노출:** {rd.vix_exposure}")
    if rd.portfolio_beta is not None:
        _w(f"- **포트폴리오 베타:** {rd.portfolio_beta:.2f}")
    _w("")


def _render_win_rate_trend(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    wrt = report.win_rate_trend
    if not wrt or not wrt.weekly_rates:
        return
    _w("## 적중률 4주 트렌드")
    _w("")
    parts = [
        f"{wid}: **{_fmt_pct(rate)}**" if rate is not None else f"{wid}: -"
        for wid, rate in wrt.weekly_rates
    ]
    _w(" → ".join(parts))
    if wrt.four_week_avg_pct is not None:
        _w(f"\n4주 평균: **{_fmt_pct(wrt.four_week_avg_pct)}** ({wrt.trend_direction})")
    _w("")


def _render_week_over_week(lines: list[str], report: WeeklyReport) -> None:
    _w = lines.append
    wow = report.week_over_week
    if not wow:
        return
    _w("## 이전 주 대비 변화")
    _w("")
    if wow.win_rate_delta is not None:
        _w(f"- **승률:** {_fmt_pct(wow.prev_win_rate_pct)} → {_fmt_pct(wow.curr_win_rate_pct)} "
           f"({wow.win_rate_delta:+.1f}%p)")
    if wow.return_delta is not None:
        _w(f"- **평균 수익률:** {_fmt_return(wow.prev_avg_return_pct)} → {_fmt_return(wow.curr_avg_return_pct)} "
           f"({wow.return_delta:+.2f}%p)")
    if wow.new_sectors_in:
        _w(f"- **신규 진입 섹터:** {', '.join(wow.new_sectors_in)}")
    if wow.sectors_out:
        _w(f"- **이탈 섹터:** {', '.join(wow.sectors_out)}")
    _w("")


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.1f}%"


def _fmt_return(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:+.2f}%"
