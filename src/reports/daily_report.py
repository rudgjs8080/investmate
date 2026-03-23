"""데일리 리포트 생성기 -- 두괄식(핵심 요약 먼저) + 초보자 친화 설명 포함."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from src.reports.assembler import assemble_enriched_report
from src.reports.explainer import (
    explain_stock,
    market_investment_opinion,
    summarize_market,
    summarize_recommendations_oneliner,
    _translate_signals,
)
from src.reports.report_models import (
    EnrichedDailyReport,
    StockRecommendationDetail,
)

logger = logging.getLogger(__name__)

DISCLAIMER = "※ 본 리포트는 투자 참고용이며 투자 권유가 아닙니다."


def generate_and_save_report(
    session: Session, run_date: date, run_date_id: int,
) -> int:
    """데일리 리포트를 생성하고 파일로 저장한다."""
    report = assemble_enriched_report(session, run_date, run_date_id)

    reports_dir = Path("reports/daily")
    reports_dir.mkdir(parents=True, exist_ok=True)

    _save_json(report, reports_dir / f"{run_date.isoformat()}.json")
    _save_markdown(report, reports_dir / f"{run_date.isoformat()}.md")

    # 품질 검증
    _log_report_quality(report)

    logger.info("리포트 저장 완료: %s", reports_dir)
    return 1


def _log_report_quality(report: EnrichedDailyReport) -> None:
    """리포트 데이터 품질을 로깅한다."""
    total_recs = len(report.recommendations)
    if total_recs == 0:
        logger.warning("리포트 품질: 추천 종목 0개")
        return

    rsi_filled = sum(1 for r in report.recommendations if r.technical.rsi is not None)
    macd_filled = sum(1 for r in report.recommendations if r.technical.macd is not None)
    per_filled = sum(1 for r in report.recommendations if r.fundamental.per is not None)
    signals_count = sum(len(r.technical.signals) for r in report.recommendations)
    news_count = sum(len(r.news) for r in report.recommendations)

    logger.info(
        "리포트 품질: %d종목 | RSI %d/%d | MACD %d/%d | PER %d/%d | 시그널 %d건 | 뉴스 %d건",
        total_recs, rsi_filled, total_recs, macd_filled, total_recs,
        per_filled, total_recs, signals_count, news_count,
    )


# ──────────────────────────────────────────
# JSON 리포트
# ──────────────────────────────────────────


def _save_json(report: EnrichedDailyReport, path: Path) -> None:
    """구조화된 JSON 리포트를 저장한다."""
    data = asdict(report)
    data["run_date"] = report.run_date.isoformat()
    data["disclaimer"] = DISCLAIMER

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ──────────────────────────────────────────
# Markdown 리포트 (두괄식)
# ──────────────────────────────────────────


def _save_markdown(report: EnrichedDailyReport, path: Path) -> None:
    """두괄식 Markdown 리포트를 저장한다."""
    lines: list[str] = []
    _w = lines.append

    _w(f"# 오늘의 투자 리포트 -- {report.run_date.isoformat()}")
    _w("")
    _w(f"> {DISCLAIMER}")
    _w("")

    # ── 섹션 1: 핵심 요약 (30초 브리핑) ──
    _w("## 핵심 요약 (30초 브리핑)")
    _w("")

    market_summary = summarize_market(report.macro)
    _w(f"**시장 분위기:** {market_summary}")
    _w("")

    recs_oneliner = summarize_recommendations_oneliner(report.recommendations)
    _w(f"**오늘의 추천:** {recs_oneliner}")

    # AI 추천 하이라이트
    ai_top = [r for r in report.recommendations if r.ai_approved is True and r.ai_confidence and r.ai_confidence >= 7]
    if ai_top:
        ai_names = ", ".join(f"**{r.ticker}**({r.ai_confidence}/10)" for r in ai_top[:5])
        _w(f"**AI 고신뢰 추천:** {ai_names}")

    # 실적 발표 임박 경고
    pre_earnings = [r.ticker for r in report.recommendations if r.is_pre_earnings]
    if pre_earnings:
        _w(f"**[!] 실적 발표 임박:** {', '.join(pre_earnings)} — 변동성 주의")
    _w("")

    opinion = market_investment_opinion(report.macro, len(report.recommendations))
    _w(f"**투자 의견:** {opinion}")
    _w("")

    # 섹터 분포
    sector_counts: dict[str, int] = {}
    for rec in report.recommendations:
        s = rec.sector or "기타"
        sector_counts[s] = sector_counts.get(s, 0) + 1
    sector_str = " | ".join(f"{s} {c}개" for s, c in sorted(sector_counts.items(), key=lambda x: -x[1]))
    _w(f"**섹터 분포:** {sector_str}")
    _w("")

    # AI 분석 통계
    ai_approved = [r for r in report.recommendations if r.ai_approved is True]
    ai_excluded = [r for r in report.recommendations if r.ai_approved is False]
    ai_none = [r for r in report.recommendations if r.ai_approved is None]
    if ai_approved or ai_excluded:
        confs = [r.ai_confidence for r in ai_approved if r.ai_confidence]
        avg_conf = f" (평균 신뢰도 {sum(confs)/len(confs):.1f}/10)" if confs else ""
        _w(f"**AI 분석:** 추천 {len(ai_approved)}개{avg_conf} | 제외 {len(ai_excluded)}개")
    elif ai_none:
        _w("**AI 분석:** *미실행*")
    _w("")

    # 간단 요약 테이블
    if report.recommendations:
        _w("| 순위 | 종목 | 한줄 요약 | 종합 | AI |")
        _w("|:----:|------|----------|:----:|:---:|")
        for rec in report.recommendations:
            explanation = explain_stock(rec)
            if rec.ai_approved is True:
                ai_badge = f"추천{f' {rec.ai_confidence}' if rec.ai_confidence else ''}"
            elif rec.ai_approved is False:
                ai_badge = "제외"
            else:
                ai_badge = "-"
            _w(f"| {rec.rank} | **{rec.ticker}** ({rec.name}) | {explanation.headline} | {rec.total_score:.1f} | {ai_badge} |")
        _w("")

    # ── 섹션 2: 추천 종목 카드 ──
    _w(f"## 추천 종목 상세 (TOP {len(report.recommendations)})")
    _w("")

    for rec in report.recommendations:
        _render_stock_card(lines, rec)

    # ── 섹션 2.5: AI 포트폴리오 분석 ──
    _render_ai_portfolio_summary(lines, report)

    # ── 섹션 3: 시장 환경 상세 ──
    _render_market_detail(lines, report)

    # ── 섹션 4: 시그널 발생 종목 ──
    _render_signals_section(lines, report)

    # 면책
    _w("---")
    _w(f"*{DISCLAIMER}*")
    _w(f"생성일: {report.run_date.isoformat()}")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ──────────────────────────────────────────
# 종목 카드 렌더링 (두괄식)
# ──────────────────────────────────────────


def _render_stock_card(lines: list[str], rec: StockRecommendationDetail) -> None:
    """종목별 카드 -- 한줄 요약 먼저, 상세는 아래."""
    _w = lines.append
    t = rec.technical
    f = rec.fundamental
    sm = rec.smart_money
    e = rec.earnings

    explanation = explain_stock(rec)

    chg_str = f" ({rec.price_change_pct:+.1f}%)" if rec.price_change_pct is not None else ""
    cap_str = f" | 시총 ${_fmt_large(f.market_cap)}" if f.market_cap else ""

    _w("---")
    _w("")
    _w(f"### {rec.rank}위: {rec.ticker} -- {rec.name}")
    _w("")
    _w(f"**${rec.price:,.2f}{chg_str}** | {rec.sector or '-'}{cap_str} | 종합 **{rec.total_score:.1f}**/10")
    _w("")

    # 한줄 요약 (헤드라인)
    _w(f"> {explanation.headline}")
    _w("")

    # 왜 추천하나요? (초보자 설명)
    _w("**왜 추천하나요?**")
    _w("")
    _w(explanation.why_recommended)
    _w("")

    # 숫자로 보면
    _w("**숫자로 보면:**")
    _w(f"`{explanation.numbers_backing}`")
    _w("")

    # 주의할 점
    _w("**주의할 점:**")
    _w(explanation.risk_simple)
    _w("")

    # AI 분석 결과
    if rec.ai_approved is None:
        _w("**AI 분석:** *미실행*")
    elif rec.ai_approved:
        conf_str = f" (신뢰도: {rec.ai_confidence}/10)" if rec.ai_confidence else ""
        risk_str = f" | 리스크: {rec.ai_risk_level}" if rec.ai_risk_level else ""
        _w(f"**AI 분석:** **추천**{conf_str}{risk_str}")
        if rec.ai_reason:
            _w(f"> {rec.ai_reason}")
        # 매매 전략
        if rec.ai_entry_strategy:
            _w(f"> **매수 전략:** {rec.ai_entry_strategy}")
        if rec.ai_exit_strategy:
            _w(f"> **익절/손절:** {rec.ai_exit_strategy}")
        if rec.ai_target_price or rec.ai_stop_loss:
            parts = []
            if rec.ai_target_price:
                parts.append(f"목표가 ${rec.ai_target_price:,.0f}")
            if rec.ai_stop_loss:
                parts.append(f"손절가 ${rec.ai_stop_loss:,.0f}")
            _w(f"> {' | '.join(parts)}")
    else:
        _w(f"**AI 분석:** **제외**")
        if rec.ai_reason:
            _w(f"> {rec.ai_reason}")
    _w("")

    # ── 상세 데이터 ──
    _w("<details>")
    _w("<summary>상세 데이터 펼치기</summary>")
    _w("")

    # 기술적 분석
    _w("**기술적 분석**")
    _w("")
    _w("| 지표 | 값 | 상태 |")
    _w("|------|-----|------|")
    _w(f"| RSI(14) | {_fmt_val(t.rsi)} | {t.rsi_status} |")
    _w(f"| MACD | {_fmt_val(t.macd)} | {t.macd_status} |")
    _w(f"| MACD 히스토그램 | {_fmt_val(t.macd_hist)} | |")
    _w(f"| 이동평균 배열 | SMA5/20/60 | {t.sma_alignment} |")
    _w(f"| 볼린저밴드 | | {t.bb_position} |")
    if t.stoch_k is not None:
        stoch_d_str = f"{t.stoch_d:.1f}" if t.stoch_d is not None else "-"
        _w(f"| 스토캐스틱 | K={t.stoch_k:.1f} / D={stoch_d_str} | |")
    if t.volume_ratio is not None:
        vol_label = "급증" if t.volume_ratio > 1.5 else ("활발" if t.volume_ratio > 1.0 else "저조")
        _w(f"| 거래량 | 20일 평균 대비 {t.volume_ratio:.0%} | {vol_label} |")
    _w("")

    if t.signals:
        _w("**활성 시그널:**")
        for s in t.signals:
            kr_name = _translate_signals([s.signal_type])[0]
            direction_kr = "매수" if s.direction == "BUY" else "매도"
            _w(f"- [{direction_kr}] **{kr_name}** (강도 {s.strength}/10) -- {s.description}")
        _w("")

    # 기본적 분석
    _w(f"**기본적 분석** (종합 {f.composite_score:.1f}/10 -- {f.summary})")
    _w("")
    _w("| 항목 | 값 | 점수 |")
    _w("|------|-----|:----:|")
    _w(f"| PER | {_fmt_val(f.per)} | {f.per_score:.1f} |")
    _w(f"| PBR | {_fmt_val(f.pbr)} | {f.pbr_score:.1f} |")
    _w(f"| ROE | {_fmt_roe(f.roe)} | {f.roe_score:.1f} |")
    _w(f"| 부채비율 | {_fmt_ratio(f.debt_ratio)} | {f.debt_score:.1f} |")
    _w(f"| 매출성장 | | {f.growth_score:.1f} |")
    if f.dividend_yield is not None and f.dividend_yield > 0:
        dy_pct = f.dividend_yield * 100 if abs(f.dividend_yield) < 1 else f.dividend_yield
        _w(f"| 배당수익률 | {dy_pct:.2f}% | - |")
    _w("")

    # 수급/스마트머니
    total_analysts = sm.analyst_strong_buy + sm.analyst_buy + sm.analyst_hold + sm.analyst_sell + sm.analyst_strong_sell
    if total_analysts > 0 or sm.insider_net_value is not None:
        _w("**수급/스마트머니**")
        _w("")
        if total_analysts > 0:
            buy_total = sm.analyst_strong_buy + sm.analyst_buy
            sell_total = sm.analyst_sell + sm.analyst_strong_sell
            _w(f"- 애널리스트: Buy {buy_total} / Hold {sm.analyst_hold} / Sell {sell_total} (총 {total_analysts}명)")
            if sm.target_mean:
                upside_str = f" (상승여력 {sm.upside_pct:+.1f}%)" if sm.upside_pct is not None else ""
                _w(f"  - 평균 목표가: ${sm.target_mean:,.2f}{upside_str}")
        if sm.insider_net_value is not None:
            _w(f"- 내부자: {sm.insider_summary}")
        if sm.short_pct is not None:
            _w(f"- 공매도: 유통주식 대비 {sm.short_pct:.1f}%")
        if sm.top_institutions:
            _w(f"- 주요 기관 보유:")
            for inst_name, inst_value in sm.top_institutions[:3]:
                _w(f"  - {inst_name}: ${_fmt_large(inst_value)}" if inst_value else f"  - {inst_name}")
        _w("")

    # 실적
    if e.latest_period:
        _w("**실적 서프라이즈**")
        _w("")
        eps_str = f"EPS {e.eps_surprise_pct:+.1f}%" if e.eps_surprise_pct is not None else "EPS -"
        _w(f"- {e.latest_period}: {eps_str}")
        if e.beat_streak > 1:
            _w(f"- 연속 실적 상회: {e.beat_streak}분기")
        _w("")

    # 뉴스
    if rec.news:
        _w("**관련 뉴스**")
        _w("")
        for n in rec.news[:3]:
            if n.sentiment_score:
                label = "긍정" if n.sentiment_score > 0 else "부정"
                sent = f" [{label} {n.sentiment_score:+.1f}]"
            else:
                sent = ""
            _w(f"- [{n.published_at or '-'}] {n.title} ({n.source}){sent}")
        _w("")

    _w("</details>")
    _w("")


# ──────────────────────────────────────────
# 시장 환경 상세
# ──────────────────────────────────────────


def _render_ai_portfolio_summary(lines: list[str], report: EnrichedDailyReport) -> None:
    """AI 포트폴리오 종합 분석 섹션."""
    _w = lines.append

    # AI 추천/제외 집계
    approved = [r for r in report.recommendations if r.ai_approved is True]
    excluded = [r for r in report.recommendations if r.ai_approved is False]
    not_reviewed = [r for r in report.recommendations if r.ai_approved is None]

    if not approved and not excluded:
        if not_reviewed:
            _w("## AI 포트폴리오 분석")
            _w("")
            _w("*AI 분석이 실행되지 않았습니다. 프롬프트 파일을 Claude.ai에 수동으로 질의해주세요.*")
            _w("")
        return

    _w("## AI 포트폴리오 분석")
    _w("")

    # 집계 통계
    total = len(report.recommendations)
    _w(f"**AI 추천:** {len(approved)}/{total}개 종목 | **제외:** {len(excluded)}개")
    if approved:
        confidences = [r.ai_confidence for r in approved if r.ai_confidence is not None]
        if confidences:
            avg_conf = sum(confidences) / len(confidences)
            _w(f"**평균 신뢰도:** {avg_conf:.1f}/10")

    _w("")

    # 추천 종목 한줄 요약
    if approved:
        _w("**AI 추천 종목:**")
        for r in approved:
            conf = f" [{r.ai_confidence}/10]" if r.ai_confidence else ""
            risk = f" ({r.ai_risk_level})" if r.ai_risk_level else ""
            _w(f"- **{r.ticker}**{conf}{risk}: {r.ai_reason or r.recommendation_reason}")
        _w("")

    if excluded:
        _w("**AI 제외 종목:**")
        for r in excluded:
            _w(f"- ~~{r.ticker}~~: {r.ai_reason or '이유 없음'}")
        _w("")

    _w("---")
    _w("")


def _render_market_detail(lines: list[str], report: EnrichedDailyReport) -> None:
    _w = lines.append
    m = report.macro

    _w("## 시장 환경 상세")
    _w("")
    _w("| 지표 | 값 | 상태 |")
    _w("|------|-----|------|")
    _w(f"| **시장 점수** | **{m.market_score or '-'}/10** | **{m.mood}** |")
    _w(f"| VIX | {_fmt_val(m.vix)} | {m.vix_status} |")
    _w(f"| S&P 500 | {_fmt_price(m.sp500_close)} | 20일선 {m.sp500_trend} |")
    if m.sp500_sma20:
        _w(f"| S&P 500 SMA20 | {_fmt_price(m.sp500_sma20)} | |")
    _w(f"| 10년 국채 | {_fmt_pct(m.us_10y_yield)} | |")
    _w(f"| 13주 국채 | {_fmt_pct(m.us_13w_yield)} | |")
    if m.yield_spread is not None:
        spread_label = "정상" if m.yield_spread > 0 else "역전"
        _w(f"| 장단기 스프레드 | {m.yield_spread:+.2f}%p | {spread_label} |")
    _w(f"| 달러 인덱스 | {_fmt_val(m.dollar_index)} | |")
    _w("")

    # 실행 정보
    _w(f"- 분석 종목: {report.total_stocks_analyzed}개 (S&P 500)")
    _w(f"- 필터 통과: {report.stocks_passed_filter}개")
    if report.pipeline_duration_sec:
        mins = int(report.pipeline_duration_sec // 60)
        secs = int(report.pipeline_duration_sec % 60)
        _w(f"- 소요 시간: {mins}분 {secs}초")
    _w("")


# ──────────────────────────────────────────
# 시그널 섹션
# ──────────────────────────────────────────


def _render_signals_section(lines: list[str], report: EnrichedDailyReport) -> None:
    _w = lines.append

    _w("## 전체 시그널 발생 종목")
    _w("")
    _w(f"총 **{len(report.all_signals)}**개 시그널 "
       f"(매수 {report.buy_signal_count} / 매도 {report.sell_signal_count})")
    _w("")

    buy_signals = [s for s in report.all_signals if s.direction == "BUY"]
    sell_signals = [s for s in report.all_signals if s.direction == "SELL"]

    if buy_signals:
        _w(f"### 매수 시그널 ({len(buy_signals)}건)")
        _w("")
        _w("| 종목 | 시그널 | 강도 | 설명 |")
        _w("|------|--------|:----:|------|")
        for s in buy_signals[:30]:
            kr = _translate_signals([s.signal_type])[0]
            _w(f"| {s.ticker} | {kr} | {s.strength}/10 | {s.description} |")
        if len(buy_signals) > 30:
            _w(f"| ... | 외 {len(buy_signals) - 30}건 | | |")
        _w("")

    if sell_signals:
        _w(f"### 매도 시그널 ({len(sell_signals)}건)")
        _w("")
        _w("| 종목 | 시그널 | 강도 | 설명 |")
        _w("|------|--------|:----:|------|")
        for s in sell_signals[:30]:
            kr = _translate_signals([s.signal_type])[0]
            _w(f"| {s.ticker} | {kr} | {s.strength}/10 | {s.description} |")
        if len(sell_signals) > 30:
            _w(f"| ... | 외 {len(sell_signals) - 30}건 | | |")
        _w("")


# ──────────────────────────────────────────
# 포맷 헬퍼
# ──────────────────────────────────────────


def _fmt_val(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}"


def _fmt_price(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:,.2f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.2f}%"


def _fmt_roe(v: float | None) -> str:
    if v is None:
        return "-"
    pct = v * 100 if abs(v) < 1 else v
    return f"{pct:.1f}%"


def _fmt_ratio(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.1%}"


def _fmt_large(v: float | None) -> str:
    if v is None:
        return "-"
    if abs(v) >= 1e12:
        return f"{v / 1e12:.2f}T"
    if abs(v) >= 1e9:
        return f"{v / 1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.0f}M"
    return f"{v:,.0f}"
