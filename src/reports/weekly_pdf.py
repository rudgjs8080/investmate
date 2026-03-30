"""주간 리포트 PDF 생성기 — fpdf2 기반 전문 PDF."""

from __future__ import annotations

import logging
import platform
import shutil
from pathlib import Path

from src.reports.weekly_charts import generate_all_charts
from src.reports.weekly_models import WeeklyReport

logger = logging.getLogger(__name__)

# 한글 폰트 탐색 경로
_FONT_PATHS = [
    Path("assets/fonts/NanumGothic.ttf"),
    Path("C:/Windows/Fonts/malgun.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/nanum/NanumGothic.ttf"),
    Path("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc"),
]

_BOLD_FONT_PATHS = [
    Path("assets/fonts/NanumGothicBold.ttf"),
    Path("C:/Windows/Fonts/malgunbd.ttf"),
    Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf"),
    Path("/usr/share/fonts/nanum/NanumGothicBold.ttf"),
    Path("/usr/share/fonts/google-noto-cjk/NotoSansCJK-Bold.ttc"),
]


def _find_font(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


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
            raise FileNotFoundError("한글 폰트를 찾을 수 없습니다. assets/fonts/NanumGothic.ttf를 추가하세요.")

        self.pdf.add_font("Kr", "", str(font_path))

        bold_path = _find_font(_BOLD_FONT_PATHS)
        if bold_path:
            self.pdf.add_font("Kr", "B", str(bold_path))
        else:
            self.pdf.add_font("Kr", "B", str(font_path))

    def _section_title(self, title: str) -> None:
        self.pdf.set_font("Kr", "B", 13)
        self.pdf.set_text_color(99, 102, 241)  # indigo
        self.pdf.cell(0, 10, title, new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.ln(2)

    def _body_text(self, text: str, size: int = 9) -> None:
        self.pdf.set_font("Kr", "", size)
        self.pdf.multi_cell(0, 5, text)
        self.pdf.ln(2)

    def _kpi_row(self, label: str, value: str) -> None:
        self.pdf.set_font("Kr", "", 9)
        self.pdf.cell(60, 6, label, new_x="RIGHT")
        self.pdf.set_font("Kr", "B", 9)
        self.pdf.cell(0, 6, value, new_x="LMARGIN", new_y="NEXT")

    def _table_header(self, cols: list[tuple[str, int]]) -> None:
        self.pdf.set_font("Kr", "B", 8)
        self.pdf.set_fill_color(243, 244, 246)
        for label, w in cols:
            self.pdf.cell(w, 6, label, border=1, fill=True, align="C")
        self.pdf.ln()

    def _table_row(self, vals: list[tuple[str, int]], align: str = "C") -> None:
        self.pdf.set_font("Kr", "", 8)
        for val, w in vals:
            self.pdf.cell(w, 5, val, border=1, align=align)
        self.pdf.ln()

    def render_cover(self) -> None:
        r = self.report
        self.pdf.add_page()
        self.pdf.ln(30)
        self.pdf.set_font("Kr", "B", 24)
        self.pdf.set_text_color(99, 102, 241)
        self.pdf.cell(0, 15, "Investmate", align="C", new_x="LMARGIN", new_y="NEXT")
        self.pdf.set_font("Kr", "B", 16)
        self.pdf.set_text_color(55, 65, 81)
        self.pdf.cell(0, 12, f"주간 투자 리포트 {r.year}-W{r.week_number:02d}", align="C", new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(5)
        self.pdf.set_font("Kr", "", 11)
        self.pdf.set_text_color(107, 114, 128)
        self.pdf.cell(0, 8, f"{r.week_start} ~ {r.week_end} | 거래일 {r.trading_days}일", align="C", new_x="LMARGIN", new_y="NEXT")
        self.pdf.ln(10)

        # 핵심 KPI
        es = r.executive_summary
        self.pdf.set_font("Kr", "B", 11)
        self.pdf.set_text_color(0, 0, 0)
        self.pdf.multi_cell(0, 7, es.market_oneliner, align="C")
        self.pdf.ln(8)

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
            self.pdf.set_font("Kr", "", 10)
            self.pdf.cell(0, 8, " | ".join(kpis), align="C", new_x="LMARGIN", new_y="NEXT")

        self.pdf.ln(20)
        self.pdf.set_font("Kr", "", 8)
        self.pdf.set_text_color(156, 163, 175)
        self.pdf.cell(0, 6, "※ 본 리포트는 투자 참고용이며 투자 권유가 아닙니다.", align="C", new_x="LMARGIN", new_y="NEXT")
        self.pdf.cell(0, 6, f"생성일: {r.generated_at}", align="C", new_x="LMARGIN", new_y="NEXT")

    def render_ai_commentary(self) -> None:
        if not self.commentary:
            return
        self.pdf.add_page()
        self._section_title("AI 주간 코멘터리")
        self._body_text(self.commentary)

    def render_performance(self) -> None:
        pr = self.report.performance_review
        self.pdf.add_page()
        self._section_title("주간 추천 성과")

        if pr.total_unique_picks == 0:
            self._body_text("이번 주 추천 종목이 없습니다.")
            return

        self._kpi_row("총 추천 종목:", f"{pr.total_unique_picks}개")
        self._kpi_row("승/패:", f"{pr.win_count} / {pr.loss_count}")
        if pr.win_rate_pct is not None:
            self._kpi_row("승률:", f"{pr.win_rate_pct:.1f}%")
        if pr.avg_return_pct is not None:
            self._kpi_row("평균 수익률:", f"{pr.avg_return_pct:+.2f}%")
        if pr.best_pick:
            ret = f" ({pr.best_pick.weekly_return_pct:+.2f}%)" if pr.best_pick.weekly_return_pct else ""
            self._kpi_row("베스트:", f"{pr.best_pick.ticker}{ret}")
        if pr.worst_pick:
            ret = f" ({pr.worst_pick.weekly_return_pct:+.2f}%)" if pr.worst_pick.weekly_return_pct else ""
            self._kpi_row("워스트:", f"{pr.worst_pick.ticker}{ret}")
        self.pdf.ln(4)

        # 종목 테이블
        if pr.all_picks:
            cols = [("종목", 40), ("섹터", 35), ("추천일", 18), ("순위", 18), ("수익률", 25), ("AI", 25)]
            self._table_header(cols)
            for p in pr.all_picks:
                ret = f"{p.weekly_return_pct:+.2f}%" if p.weekly_return_pct is not None else "-"
                ai = f"추천{p.ai_approved_days}" if p.ai_approved_days > 0 else ("-" if p.ai_rejected_days == 0 else f"제외{p.ai_rejected_days}")
                self._table_row([
                    (f"{p.ticker}", 40), (p.sector or "-", 35),
                    (str(p.days_recommended), 18), (f"{p.avg_rank:.0f}", 18),
                    (ret, 25), (ai, 25),
                ])

    def render_conviction_picks(self) -> None:
        if not self.report.conviction_picks:
            return
        self.pdf.ln(6)
        self._section_title("확신 종목 (Conviction Picks)")
        cols = [("종목", 35), ("추천일", 18), ("연속", 15), ("점수", 20), ("수익률", 25), ("AI", 20)]
        self._table_header(cols)
        for c in self.report.conviction_picks:
            ret = f"{c.weekly_return_pct:+.2f}%" if c.weekly_return_pct is not None else "-"
            self._table_row([
                (f"{c.ticker}", 35), (str(c.days_recommended), 18),
                (str(c.consecutive_days), 15), (f"{c.avg_total_score:.1f}", 20),
                (ret, 25), (c.ai_consensus, 20),
            ])

    def render_sector_rotation(self) -> None:
        if not self.report.sector_rotation:
            return
        self.pdf.ln(6)
        self._section_title("섹터 로테이션")
        cols = [("섹터", 45), ("수익률", 25), ("거래량", 25), ("모멘텀", 25), ("추천", 20)]
        self._table_header(cols)
        for s in self.report.sector_rotation:
            ret = f"{s.weekly_return_pct:+.2f}%" if s.weekly_return_pct is not None else "-"
            vol = f"{s.volume_change_pct:+.1f}%" if s.volume_change_pct is not None else "-"
            self._table_row([
                (s.sector, 45), (ret, 25), (vol, 25),
                (s.momentum_delta, 25), (str(s.pick_count), 20),
            ])

    def render_macro(self) -> None:
        ms = self.report.macro_summary
        self.pdf.add_page()
        self._section_title("매크로 환경")

        if ms.daily_scores:
            scores_str = " → ".join(
                f"{s}/10" if s is not None else "-" for _, s in ms.daily_scores
            )
            self._body_text(f"시장 점수 추이: {scores_str}")

        rows = [
            ("10Y 국채", ms.us_10y_start, ms.us_10y_end, "%"),
            ("13W 국채", ms.us_13w_start, ms.us_13w_end, "%"),
            ("스프레드", ms.spread_start, ms.spread_end, "%p"),
            ("달러", ms.dollar_start, ms.dollar_end, ""),
            ("금 ($/oz)", ms.gold_start, ms.gold_end, ""),
            ("유가 ($/bbl)", ms.oil_start, ms.oil_end, ""),
        ]
        cols = [("지표", 35), ("주초", 30), ("주말", 30), ("변동", 30)]
        self._table_header(cols)
        for label, start, end, suffix in rows:
            if start is None and end is None:
                continue
            s = f"{start:.2f}{suffix}" if start is not None else "-"
            e = f"{end:.2f}{suffix}" if end is not None else "-"
            d = f"{end - start:+.2f}{suffix}" if start is not None and end is not None else "-"
            self._table_row([(label, 35), (s, 30), (e, 30), (d, 30)])

    def render_charts(self) -> None:
        """차트를 삽입한다."""
        temp_dir = None
        try:
            temp_dir, chart_paths = generate_all_charts(self.report)
            for chart_path in chart_paths:
                if self.pdf.get_y() > 200:
                    self.pdf.add_page()
                self.pdf.ln(4)
                self.pdf.image(str(chart_path), w=170)
                self.pdf.ln(2)
        except Exception as e:
            logger.warning("차트 삽입 실패: %s", e)
        finally:
            if temp_dir and Path(temp_dir).exists():
                shutil.rmtree(temp_dir, ignore_errors=True)

    def render_outlook(self) -> None:
        ol = self.report.outlook
        self.pdf.ln(6)
        self._section_title("다음 주 전망")
        self._body_text(f"전략: {ol.regime_strategy}")
        if ol.watchlist_sectors:
            self._body_text(f"관심 섹터: {', '.join(ol.watchlist_sectors)}")
        if ol.avoid_sectors:
            self._body_text(f"주의 섹터: {', '.join(ol.avoid_sectors)}")
        if ol.rebalancing_suggestion:
            self._body_text(f"리밸런싱: {ol.rebalancing_suggestion}")

    def build(self) -> bytes:
        """전체 PDF를 빌드하여 bytes로 반환한다."""
        self.render_cover()
        self.render_ai_commentary()
        self.render_performance()
        self.render_conviction_picks()
        self.render_sector_rotation()
        self.render_macro()
        self.render_charts()
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
