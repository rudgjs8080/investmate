"""주간 리포트 PDF 생성기 — fpdf2 기반 전문 PDF."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from src.reports.weekly_charts import generate_all_charts
from src.reports.weekly_models import WeeklyReport

logger = logging.getLogger(__name__)

# 한글 폰트 탐색 경로 (TTF만 — fpdf2는 TTC 미지원)
_FONT_PATHS = [
    Path("assets/fonts/NanumGothic-Regular.ttf"),
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
]

_BOLD_FONT_PATHS = [
    Path("assets/fonts/NanumGothic-Bold.ttf"),
    Path("C:/Windows/Fonts/malgunbd.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
]

# 유효 페이지 폭 (A4 210mm - 좌우 마진 10mm*2)
_PAGE_W = 190


def _find_font(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def _cols(pcts: list[tuple[str, float]]) -> list[tuple[str, int]]:
    """퍼센트 기반 컬럼 너비 계산."""
    return [(label, int(_PAGE_W * pct)) for label, pct in pcts]


class WeeklyReportPDF:
    """주간 리포트 PDF 문서 빌더."""

    def __init__(self, report: WeeklyReport, commentary: str | None = None):
        from fpdf import FPDF

        self.report = report
        self.commentary = commentary
        self.pdf = FPDF()
        self.pdf.set_auto_page_break(auto=True, margin=20)
        self._setup_fonts()

    def _setup_fonts(self) -> None:
        font_path = _find_font(_FONT_PATHS)
        if not font_path:
            raise FileNotFoundError(
                "한글 폰트를 찾을 수 없습니다. assets/fonts/NanumGothic-Regular.ttf를 추가하세요."
            )
        self.pdf.add_font("Kr", "", str(font_path))
        bold_path = _find_font(_BOLD_FONT_PATHS)
        self.pdf.add_font("Kr", "B", str(bold_path or font_path))

    def _ensure_space(self, min_mm: int = 40) -> None:
        """최소 여백이 부족하면 새 페이지를 추가한다."""
        if self.pdf.get_y() + min_mm > 277:  # A4 297 - 20 margin
            self.pdf.add_page()

    def _section_title(self, title: str) -> None:
        self._ensure_space(20)
        self.pdf.set_font("Kr", "B", 12)
        self.pdf.set_text_color(99, 102, 241)
        self.pdf.cell(0, 9, title, new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.ln(2)

    def _body(self, text: str, size: int = 9) -> None:
        self.pdf.set_font("Kr", "", size)
        self.pdf.multi_cell(0, 5, text)
        self.pdf.ln(1)

    def _kpi(self, label: str, value: str) -> None:
        self.pdf.set_font("Kr", "", 9)
        self.pdf.cell(55, 6, label, new_x="RIGHT")
        self.pdf.set_font("Kr", "B", 9)
        self.pdf.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")

    def _thead(self, cols: list[tuple[str, int]]) -> None:
        self._ensure_space(15)
        self.pdf.set_font("Kr", "B", 7)
        self.pdf.set_fill_color(243, 244, 246)
        for label, w in cols:
            self.pdf.cell(w, 6, label, border=1, fill=True, align="C")
        self.pdf.ln()

    def _trow(self, vals: list[tuple[str, int]]) -> None:
        self.pdf.set_font("Kr", "", 7)
        for val, w in vals:
            self.pdf.cell(w, 5, val[:20], border=1, align="C")
        self.pdf.ln()

    # ── 표지 ──

    def render_cover(self) -> None:
        r = self.report
        es = r.executive_summary
        self.pdf.add_page()
        self.pdf.ln(25)
        self.pdf.set_font("Kr", "B", 24)
        self.pdf.set_text_color(99, 102, 241)
        self.pdf.cell(0, 15, "Investmate", align="C", new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_font("Kr", "B", 15)
        self.pdf.set_text_color(55, 65, 81)
        self.pdf.cell(0, 11, f"주간 투자 리포트 {r.year}-W{r.week_number:02d}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(4)
        self.pdf.set_font("Kr", "", 10)
        self.pdf.set_text_color(107, 114, 128)
        self.pdf.cell(0, 7, f"{r.week_start} ~ {r.week_end} | 거래일 {r.trading_days}일", align="C", new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(8)
        self.pdf.set_font("Kr", "B", 10)
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.multi_cell(0, 6, es.market_oneliner, align="C")
        self.pdf.ln(6)
        kpis = []
        if es.sp500_weekly_return_pct is not None:
            kpis.append(f"S&P 500: {es.sp500_weekly_return_pct:+.1f}%")
        if es.vix_end is not None:
            kpis.append(f"VIX: {es.vix_end:.1f}")
        if es.weekly_win_rate_pct is not None:
            kpis.append(f"승률: {es.weekly_win_rate_pct:.0f}%")
        if es.weekly_avg_return_pct is not None:
            kpis.append(f"평균 수익: {es.weekly_avg_return_pct:+.2f}%")
        if kpis:
            self.pdf.set_font("Kr", "", 9)
            self.pdf.cell(0, 7, " | ".join(kpis), align="C", new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(15)
        self.pdf.set_font("Kr", "", 7)
        self.pdf.set_text_color(156, 163, 175)
        self.pdf.cell(0, 5, "※ 본 리포트는 투자 참고용이며 투자 권유가 아닙니다.", align="C", new_x="LMARGIN", new_y="NEXT")
        self.pdf.cell(0, 5, f"생성일: {r.generated_at}", align="C", new_x="LMARGIN", new_y="NEXT")

    # ── AI 코멘터리 ──

    def render_ai_commentary(self) -> None:
        if not self.commentary:
            return
        self.pdf.add_page()
        self._section_title("AI 주간 코멘터리")
        self._body(self.commentary)

    # ── 추천 성과 ──

    def render_performance(self) -> None:
        pr = self.report.performance_review
        self.pdf.add_page()
        self._section_title("주간 추천 성과")
        if pr.total_unique_picks == 0:
            self._body("이번 주 추천 종목이 없습니다.")
            return
        self._kpi("총 추천 종목:", f"{pr.total_unique_picks}개")
        self._kpi("승/패:", f"{pr.win_count} / {pr.loss_count}")
        if pr.win_rate_pct is not None:
            self._kpi("승률:", f"{pr.win_rate_pct:.1f}%")
        if pr.avg_return_pct is not None:
            self._kpi("평균 수익률:", f"{pr.avg_return_pct:+.2f}%")
        if pr.best_pick:
            ret = f" ({pr.best_pick.weekly_return_pct:+.2f}%)" if pr.best_pick.weekly_return_pct else ""
            self._kpi("베스트:", f"{pr.best_pick.ticker}{ret}")
        if pr.worst_pick:
            ret = f" ({pr.worst_pick.weekly_return_pct:+.2f}%)" if pr.worst_pick.weekly_return_pct else ""
            self._kpi("워스트:", f"{pr.worst_pick.ticker}{ret}")
        self.pdf.ln(3)
        if pr.all_picks:
            cols = _cols([("종목", 0.20), ("섹터", 0.25), ("추천일", 0.10), ("순위", 0.10), ("수익률", 0.18), ("AI", 0.17)])
            self._thead(cols)
            for p in pr.all_picks:
                ret = f"{p.weekly_return_pct:+.2f}%" if p.weekly_return_pct is not None else "-"
                ai = f"추천{p.ai_approved_days}" if p.ai_approved_days > 0 else ("-" if p.ai_rejected_days == 0 else f"제외{p.ai_rejected_days}")
                sec = (p.sector or "-")[:18]
                self._trow([(p.ticker, cols[0][1]), (sec, cols[1][1]), (str(p.days_recommended), cols[2][1]), (f"{p.avg_rank:.0f}", cols[3][1]), (ret, cols[4][1]), (ai, cols[5][1])])

    # ── 확신 종목 ──

    def render_conviction_picks(self) -> None:
        if not self.report.conviction_picks:
            return
        self._ensure_space(30)
        self._section_title("확신 종목 (Conviction Picks)")
        cols = _cols([("종목", 0.22), ("추천일", 0.12), ("연속", 0.10), ("점수", 0.14), ("수익률", 0.22), ("AI", 0.20)])
        self._thead(cols)
        for c in self.report.conviction_picks:
            ret = f"{c.weekly_return_pct:+.2f}%" if c.weekly_return_pct is not None else "-"
            self._trow([(c.ticker, cols[0][1]), (str(c.days_recommended), cols[1][1]), (str(c.consecutive_days), cols[2][1]), (f"{c.avg_total_score:.1f}", cols[3][1]), (ret, cols[4][1]), (c.ai_consensus, cols[5][1])])

    # ── 확신 종목 기술적 상황 ──

    def render_conviction_technicals(self) -> None:
        techs = self.report.conviction_technicals
        if not techs:
            return
        self._ensure_space(30)
        self._section_title("확신 종목 기술적 상황")
        cols = _cols([("종목", 0.15), ("RSI", 0.10), ("MACD", 0.13), ("SMA", 0.15), ("BB", 0.12), ("지지", 0.17), ("저항", 0.18)])
        self._thead(cols)
        for ct in techs:
            rsi = f"{ct.rsi_14:.0f}" if ct.rsi_14 else "-"
            sup = f"${ct.support_price:,.0f}" if ct.support_price else "-"
            res = f"${ct.resistance_price:,.0f}" if ct.resistance_price else "-"
            self._trow([(ct.ticker, cols[0][1]), (rsi, cols[1][1]), (ct.macd_signal, cols[2][1]), (ct.sma_alignment, cols[3][1]), (ct.bb_position, cols[4][1]), (sup, cols[5][1]), (res, cols[6][1])])

    # ── 섹터 로테이션 ──

    def render_sector_rotation(self) -> None:
        if not self.report.sector_rotation:
            return
        self._ensure_space(30)
        self._section_title("섹터 로테이션")
        cols = _cols([("섹터", 0.30), ("수익률", 0.18), ("거래량", 0.18), ("모멘텀", 0.17), ("추천", 0.17)])
        self._thead(cols)
        for s in self.report.sector_rotation:
            ret = f"{s.weekly_return_pct:+.2f}%" if s.weekly_return_pct is not None else "-"
            vol = f"{s.volume_change_pct:+.1f}%" if s.volume_change_pct is not None else "-"
            self._trow([(s.sector[:25], cols[0][1]), (ret, cols[1][1]), (vol, cols[2][1]), (s.momentum_delta, cols[3][1]), (str(s.pick_count), cols[4][1])])

    # ── 매크로 ──

    def render_macro(self) -> None:
        ms = self.report.macro_summary
        self.pdf.add_page()
        self._section_title("매크로 환경")
        if ms.daily_scores:
            scores = " -> ".join(f"{s}/10" if s is not None else "-" for _, s in ms.daily_scores)
            self._body(f"시장 점수 추이: {scores}")
        rows = [
            ("10Y 국채", ms.us_10y_start, ms.us_10y_end, "%"),
            ("13W 국채", ms.us_13w_start, ms.us_13w_end, "%"),
            ("스프레드", ms.spread_start, ms.spread_end, "%p"),
            ("달러", ms.dollar_start, ms.dollar_end, ""),
            ("금 ($/oz)", ms.gold_start, ms.gold_end, ""),
            ("유가 ($/bbl)", ms.oil_start, ms.oil_end, ""),
        ]
        cols = _cols([("지표", 0.28), ("주초", 0.24), ("주말", 0.24), ("변동", 0.24)])
        self._thead(cols)
        for label, start, end, suffix in rows:
            if start is None and end is None:
                continue
            s = f"{start:.2f}{suffix}" if start is not None else "-"
            e = f"{end:.2f}{suffix}" if end is not None else "-"
            d = f"{end - start:+.2f}{suffix}" if start is not None and end is not None else "-"
            self._trow([(label, cols[0][1]), (s, cols[1][1]), (e, cols[2][1]), (d, cols[3][1])])

    # ── 리스크 대시보드 ──

    def render_risk_dashboard(self) -> None:
        rd = self.report.risk_dashboard
        if not rd:
            return
        self._ensure_space(25)
        self._section_title("리스크 대시보드")
        if rd.top_sector and rd.max_sector_concentration_pct is not None:
            self._body(f"섹터 집중도: {rd.top_sector} ({rd.max_sector_concentration_pct:.0f}%)")
        self._body(f"VIX 노출: {rd.vix_exposure}")
        if rd.portfolio_beta is not None:
            self._body(f"포트폴리오 베타: {rd.portfolio_beta:.2f}")

    # ── 전주 대비 변화 ──

    def render_week_over_week(self) -> None:
        wow = self.report.week_over_week
        if not wow:
            return
        self._ensure_space(25)
        self._section_title("전주 대비 변화")
        if wow.win_rate_delta is not None:
            self._body(f"승률: {wow.prev_win_rate_pct or 0:.1f}% -> {wow.curr_win_rate_pct or 0:.1f}% ({wow.win_rate_delta:+.1f}%p)")
        if wow.return_delta is not None:
            self._body(f"평균 수익: {wow.prev_avg_return_pct or 0:+.2f}% -> {wow.curr_avg_return_pct or 0:+.2f}% ({wow.return_delta:+.2f}%p)")
        if wow.new_sectors_in:
            self._body(f"신규 진입 섹터: {', '.join(wow.new_sectors_in)}")
        if wow.sectors_out:
            self._body(f"이탈 섹터: {', '.join(wow.sectors_out)}")

    # ── 액션 아이템 ──

    def render_action_items(self) -> None:
        items = self.report.action_items
        if not items:
            return
        self._ensure_space(30)
        self._section_title("이번 주 할 일")
        for item in items:
            self.pdf.set_font("Kr", "B", 9)
            self.pdf.cell(0, 6, f"{item.priority}. {item.action}", new_x="LMARGIN", new_y="NEXT")
            self.pdf.set_font("Kr", "", 8)
            self.pdf.set_text_color(107, 114, 128)
            self.pdf.cell(0, 5, f"   {item.rationale}", new_x="LMARGIN", new_y="NEXT")
            self.pdf.set_text_color(0, 0, 0)
            self.pdf.ln(2)

    # ── 차트 ──

    def render_charts(self) -> None:
        temp_dir = None
        try:
            temp_dir, chart_paths = generate_all_charts(self.report)
            for chart_path in chart_paths:
                self._ensure_space(60)
                self.pdf.ln(3)
                self.pdf.image(str(chart_path), w=_PAGE_W)
                self.pdf.ln(2)
        except Exception as e:
            logger.warning("차트 삽입 실패: %s", e)
        finally:
            if temp_dir and Path(temp_dir).exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    # ── 전망 ──

    def render_outlook(self) -> None:
        ol = self.report.outlook
        self._ensure_space(30)
        self._section_title("다음 주 전망")
        self._body(f"전략: {ol.regime_strategy}")
        if ol.watchlist_sectors:
            self._body(f"관심 섹터: {', '.join(ol.watchlist_sectors)}")
        if ol.avoid_sectors:
            self._body(f"주의 섹터: {', '.join(ol.avoid_sectors)}")
        if ol.rebalancing_suggestion:
            self._body(f"리밸런싱: {ol.rebalancing_suggestion}")

    # ── 빌드 ──

    def build(self) -> bytes:
        self.render_cover()
        self.render_ai_commentary()
        self.render_performance()
        self.render_conviction_picks()
        self.render_conviction_technicals()
        self.render_sector_rotation()
        self.render_macro()
        self.render_risk_dashboard()
        self.render_week_over_week()
        self.render_charts()
        self.render_action_items()
        self.render_outlook()
        return self.pdf.output()


def generate_weekly_pdf(
    report: WeeklyReport,
    commentary: str | None = None,
) -> Path:
    """주간 리포트 PDF를 생성한다."""
    pdf_builder = WeeklyReportPDF(report, commentary)
    pdf_bytes = pdf_builder.build()

    output_dir = Path("reports/weekly")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{report.year}-W{report.week_number:02d}.pdf"

    output_path.write_bytes(pdf_bytes)
    logger.info("주간 PDF 생성 완료: %s", output_path)
    return output_path
