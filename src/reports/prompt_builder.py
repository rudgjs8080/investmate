"""프롬프트 생성기 — Claude AI 분석용 프롬프트 조립 (enriched report 활용)."""

from __future__ import annotations

import concurrent.futures
import logging
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from src.reports.assembler import assemble_enriched_report
from src.reports.explainer import explain_stock, summarize_market
from src.reports.report_models import EnrichedDailyReport

logger = logging.getLogger(__name__)

_SIGNAL_KR = {
    "golden_cross": "골든크로스",
    "death_cross": "데드크로스",
    "rsi_oversold": "RSI과매도",
    "rsi_overbought": "RSI과매수",
    "macd_bullish": "MACD매수전환",
    "macd_bearish": "MACD매도전환",
    "bb_lower_break": "볼린저하단돌파",
    "bb_upper_break": "볼린저상단돌파",
    "stoch_bullish": "스토캐스틱매수",
    "stoch_bearish": "스토캐스틱매도",
}


def _collect_enriched_safe(tickers: list[str], recommendations: tuple) -> tuple[dict, dict]:
    """보강 데이터를 안전하게 수집한다."""
    try:
        from src.ai.data_enricher import compute_sector_per_averages, fetch_enriched_stock_data
        enriched_data = fetch_enriched_stock_data(tickers)
        tickers_with_sector = [
            (r.ticker, r.sector or "기타", r.fundamental.per)
            for r in recommendations
        ]
        sector_per_avgs = compute_sector_per_averages(tickers_with_sector)
        logger.info("AI 보강 데이터: %d종목 수집", len(enriched_data))
        return enriched_data, sector_per_avgs
    except Exception as e:
        logger.warning("AI 보강 데이터 수집 실패: %s", e)
        return {}, {}


def _collect_events_safe(tickers: list[str], run_date: date) -> tuple[dict, tuple | None]:
    """이벤트 캘린더를 안전하게 수집한다."""
    try:
        from src.data.event_collector import collect_earnings_calendar, get_next_fomc_date
        events_data = collect_earnings_calendar(tickers, run_date)
        fomc_info = get_next_fomc_date(run_date)
        logger.info("이벤트 캘린더: %d종목 수집", len(events_data))
        return events_data, fomc_info
    except Exception as e:
        logger.debug("이벤트 캘린더 수집 실패: %s", e)
        return {}, None


def _collect_feedback_safe(session: Session) -> object | None:
    """AI 피드백을 안전하게 수집한다."""
    try:
        from src.ai.feedback import calculate_ai_performance, collect_ai_feedback
        collect_ai_feedback(session)
        return calculate_ai_performance(session)
    except Exception as e:
        logger.debug("AI 피드백 수집 실패 (초기에는 정상): %s", e)
        return None


def build_prompt(
    session: Session, run_date_id: int, run_date: date,
) -> str:
    """스크리닝 결과 기반으로 Claude AI 분석 프롬프트를 생성한다.

    보강 데이터 / 이벤트 캘린더 / AI 피드백을 병렬로 수집한다.
    """
    report = assemble_enriched_report(session, run_date, run_date_id)
    tickers = [r.ticker for r in report.recommendations]

    # 3가지 독립 수집을 병렬 실행
    enriched_data: dict = {}
    sector_per_avgs: dict = {}
    events_data: dict = {}
    fomc_info: tuple | None = None
    ai_feedback: object | None = None

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        fut_enrich = executor.submit(
            _collect_enriched_safe, tickers, report.recommendations,
        )
        fut_events = executor.submit(_collect_events_safe, tickers, run_date)
        fut_feedback = executor.submit(_collect_feedback_safe, session)

        try:
            enriched_data, sector_per_avgs = fut_enrich.result(timeout=30)
        except Exception:
            pass

        try:
            events_data, fomc_info = fut_events.result(timeout=15)
        except Exception:
            pass

        try:
            ai_feedback = fut_feedback.result(timeout=10)
        except Exception:
            pass

    return _render_prompt(report, enriched_data, sector_per_avgs, ai_feedback, events_data, fomc_info)


def _translate_signal(code: str) -> str:
    """시그널 코드를 한국어로 번역한다."""
    return _SIGNAL_KR.get(code, code)


def _add_chain_of_thought(lines: list[str]) -> None:
    """명시적 사고 체인을 프롬프트에 추가한다."""
    lines.append("")
    lines.append("## 분석 프로세스 (단계적으로 사고하세요)")
    lines.append("")
    lines.append("각 종목에 대해 다음 순서로 분석하세요:")
    lines.append("1. **시장 환경 판단**: 현재 레짐이 이 종목에 유리한가?")
    lines.append("2. **기술적 분석**: 시그널 강도, 지지/저항 수준 대비 위치")
    lines.append("3. **펀더멘털 검증**: PEG, FCF 품질, F-Score가 매수를 지지하는가?")
    lines.append("4. **수급 확인**: 내부자/기관의 최근 행동이 방향성을 확인하는가?")
    lines.append("5. **리스크 평가**: 실적발표/FOMC 임박, 섹터 과밀, 상관관계 위험")
    lines.append("6. **종합 판단**: 위 5단계를 종합하여 매수/제외 결정 + 근거")
    lines.append("")
    lines.append("## Bull vs Bear 분석")
    lines.append("")
    lines.append("각 종목에 대해 Bull Case(매수 근거 3가지)와 Bear Case(리스크 3가지)를 먼저 나열하세요.")
    lines.append("그 다음 어느 쪽이 더 강한지 판단하여 최종 결정을 내리세요.")


def _render_prompt(
    report: EnrichedDailyReport,
    enriched_data: dict | None = None,
    sector_per_avgs: dict | None = None,
    ai_feedback: object | None = None,
    events_data: dict | None = None,
    fomc_info: tuple | None = None,
) -> str:
    """EnrichedDailyReport + 보강 데이터 → 프롬프트 텍스트."""
    enriched_data = enriched_data or {}
    sector_per_avgs = sector_per_avgs or {}
    parts: list[str] = []
    _w = parts.append

    _w("# 역할")
    _w("당신은 월가 20년 경력의 CFA 자격 시니어 포트폴리오 매니저입니다.")
    _w("아래는 S&P 500 전 종목을 5차원 정량 분석(기술적 25% + 기본적 25% + 수급 15% + 외부 15% + 모멘텀 20%)으로")
    _w("스크리닝한 결과입니다. 이 데이터를 기반으로 정성적 판단을 더해주세요.")
    _w("")
    _w("# 분석 규칙")
    _w("- 모든 판단에 반드시 **2개 이상의 데이터 근거**를 제시하세요 (RSI+MACD, PER+성장률 등)")
    _w("- 각 종목에 1-10 신뢰도와 LOW/MEDIUM/HIGH 리스크를 반드시 부여하세요")
    _w("- 신뢰도 8 이상: 반드시 3개 이상 강한 근거 제시 (예: RSI 과매도 + 실적 서프라이즈 + 애널리스트 상향)")
    _w("- 신뢰도 5 이하: 왜 확신이 부족한지 구체적 이유 제시")
    _w("- 매수 추천 시 반드시 3단계 분할 매수 가격대와 각 비중(%) 제시")
    _w("- 목표가 설정 시 '왜 그 가격인지' 근거 제시 (예: 52주 고점 $200 대비 85% 수준)")
    _w("- 손절가 설정 시 기술적 지지선 기반 근거 제시 (예: SMA60 $175 하회 시)")
    _w("- 섹터 쏠림이 심하면 반드시 경고하고 대안 섹터 종목 제시")
    _w("- **제외 종목에 대해서도** '어떤 조건이 바뀌면 재검토 가능한지' 제시")
    _w("- 각 종목 분석 시 **판단 로직을 단계적으로 서술**: (1)시장 환경 고려→(2)기술적 체크→(3)밸류에이션→(4)수급→(5)종합")
    _w("")
    # AI 스타일 지시
    try:
        from src.config import get_settings
        style = get_settings().ai_style
    except Exception:
        style = "balanced"
    _w(f"# 투자 스타일: {style}")
    _w(get_style_instruction(style))
    _w("")
    _w(f"# 데이터 기준일: {report.run_date.isoformat()}")
    _w(f"분석 대상: S&P 500 전 종목 약 {report.total_stocks_analyzed}개 → 스크리닝 TOP {len(report.recommendations)}")
    _w("")

    # 시장 환경
    m = report.macro
    _w("## 시장 환경 요약")
    vix_str = f"{m.vix:.1f} ({m.vix_status})" if m.vix else "-"
    _w(f"- VIX: {vix_str}")
    _w(f"- S&P 500: {m.sp500_close:,.2f}" if m.sp500_close else "- S&P 500: -")
    _w(f"  - 20일선 {m.sp500_trend}")
    if m.us_10y_yield:
        _w(f"- 10년 국채 금리: {m.us_10y_yield:.2f}%")
    if m.yield_spread is not None:
        _w(f"- 장단기 스프레드: {m.yield_spread:+.2f}%p")
    if m.dollar_index:
        _w(f"- 달러 인덱스: {m.dollar_index:.2f}")
    _w(f"- 시장 환경 종합 점수: {m.market_score or '-'}/10 ({m.mood})")
    # VIX 수준 컨텍스트
    if m.vix:
        if m.vix < 15:
            _w("  → VIX 매우 낮음: 시장 안일 (역사적 하위 20%), 급변 가능성 경계")
        elif m.vix < 20:
            _w("  → VIX 정상 범위: 안정적 시장 환경")
        elif m.vix < 30:
            _w("  → VIX 주의 구간: 불확실성 증가, 방어적 전략 고려")
        else:
            _w("  → VIX 공포 구간: 극단적 불안, 현금 비중 확대 필요")
    _w(f"- 한줄 요약: {summarize_market(m)}")
    # FOMC 일정
    if fomc_info:
        fomc_date, fomc_days = fomc_info
        _w(f"- **다음 FOMC**: {fomc_date.isoformat()} ({fomc_days}일 후)")
        if fomc_days <= 7:
            _w("  → **FOMC 임박: 금리 결정 전 변동성 확대 예상, 신규 포지션 주의**")
    _w("")

    # AI 과거 성과 피드백 (자기 교정용)
    if ai_feedback and ai_feedback.total_predictions > 0:
        _w("## 과거 AI 분석 성과 (자기 교정 참고)")
        _w(f"- 총 예측: {ai_feedback.total_predictions}건 (추천 {ai_feedback.ai_approved_count} / 제외 {ai_feedback.ai_excluded_count})")
        if ai_feedback.win_rate_approved is not None:
            _w(f"- AI 추천 종목 승률: {ai_feedback.win_rate_approved}% (20일 기준)")
        if ai_feedback.avg_return_approved is not None:
            _w(f"- AI 추천 종목 평균 수익: {ai_feedback.avg_return_approved:+.2f}%")
        if ai_feedback.win_rate_excluded is not None:
            _w(f"- AI 제외 종목 승률: {ai_feedback.win_rate_excluded}% (낮을수록 제외 판단 정확)")
        if ai_feedback.direction_accuracy is not None:
            _w(f"- 방향 예측 정확도: {ai_feedback.direction_accuracy}%")
        if ai_feedback.overestimate_rate is not None:
            _w(f"- 목표가 과대추정 비율: {ai_feedback.overestimate_rate}%")
        if ai_feedback.sector_accuracy:
            _w("- 섹터별 승률:")
            for sector, acc in sorted(ai_feedback.sector_accuracy.items(), key=lambda x: -x[1]):
                _w(f"  - {sector}: {acc}%")
        if ai_feedback.confidence_calibration:
            _w("- 신뢰도별 실제 승률 (교정 참고):")
            for conf, acc in sorted(ai_feedback.confidence_calibration.items()):
                _w(f"  - 신뢰도 {conf}: 승률 {acc}%")
        # 적응형 지시
        if ai_feedback.overestimate_rate and ai_feedback.overestimate_rate > 60:
            _w("")
            _w("**[주의] 과거 목표가를 과대추정하는 경향이 있습니다. 이번에는 보수적 목표가를 설정하세요.**")
        if ai_feedback.win_rate_approved is not None and ai_feedback.win_rate_approved < 45:
            _w("")
            _w("**[주의] 최근 추천 승률이 낮습니다. 더 엄격한 기준을 적용하세요.**")
        if ai_feedback.win_rate_approved is not None and ai_feedback.win_rate_approved > 65:
            _w("")
            _w("**[참고] 최근 추천 승률이 높습니다. 현재 전략이 잘 작동하고 있으니 유지하세요.**")
        if ai_feedback.avg_target_error_pct and abs(ai_feedback.avg_target_error_pct) > 5:
            direction = "높게" if ai_feedback.avg_target_error_pct > 0 else "낮게"
            _w(f"**[교정] 목표가를 평균 {abs(ai_feedback.avg_target_error_pct):.1f}% {direction} 설정하는 경향이 있습니다. 이번에는 보정하세요.**")
        # 섹터별 가이드
        if ai_feedback.sector_accuracy:
            weak_sectors = [s for s, a in ai_feedback.sector_accuracy.items() if a < 40]
            strong_sectors = [s for s, a in ai_feedback.sector_accuracy.items() if a > 70]
            if weak_sectors:
                _w(f"**[약점 섹터] {', '.join(weak_sectors)} — 이 섹터 추천 시 특히 신중하세요.**")
            if strong_sectors:
                _w(f"**[강점 섹터] {', '.join(strong_sectors)} — 이 섹터에서 좋은 성과를 보이고 있습니다.**")
        _w("")

    # TOP N 종목

    _w(f"## 스크리닝 결과 TOP {len(report.recommendations)} ({report.total_stocks_analyzed}개 중 선별)")
    _w("")

    for rec in report.recommendations:
        t = rec.technical
        f = rec.fundamental
        sm = rec.smart_money
        e = rec.earnings

        chg = f" ({rec.price_change_pct:+.1f}%)" if rec.price_change_pct is not None else ""
        explanation = explain_stock(rec)

        # 컴팩트 데이터 카드 형식
        _w(f"### {rec.rank}. {rec.ticker} -- {rec.name} | {rec.sector or '-'} | ${rec.price:,.2f}{chg}")
        _w(f"종합 {rec.total_score:.1f}/10 | 기술 {rec.technical_score:.1f} | 기본 {rec.fundamental_score:.1f} | 수급 {rec.smart_money_score:.1f} | 외부 {rec.external_score:.1f} | 모멘텀 {rec.momentum_score:.1f}")
        rsi_str = f"{t.rsi:.0f}" if t.rsi is not None else "-"
        per_str = f"{f.per:.1f}" if f.per is not None else "-"
        debt_str = f"{f.debt_ratio:.0%}" if f.debt_ratio is not None else "-"
        dy_str = ""
        if f.dividend_yield and f.dividend_yield > 0:
            dy_pct = f.dividend_yield * 100 if abs(f.dividend_yield) < 1 else f.dividend_yield
            dy_str = f" | 배당 {dy_pct:.1f}%"
        _w(f"RSI {rsi_str}({t.rsi_status}) | MACD {t.macd_status} | MA {t.sma_alignment} | PER {per_str} | ROE {_fmt_roe(f.roe)} | 부채 {debt_str}{dy_str}")
        _w(f"추천 근거: {rec.recommendation_reason}")

        # 활성 시그널
        if t.signals:
            sig_strs = [f"{s.direction}/{_translate_signal(s.signal_type)}({s.strength})" for s in t.signals]
            _w(f"- 활성 시그널: {', '.join(sig_strs)}")

        # 애널리스트
        total_a = sm.analyst_strong_buy + sm.analyst_buy + sm.analyst_hold + sm.analyst_sell + sm.analyst_strong_sell
        if total_a > 0:
            buy_total = sm.analyst_strong_buy + sm.analyst_buy
            sell_total = sm.analyst_sell + sm.analyst_strong_sell
            _w(f"- 애널리스트: Buy {buy_total} / Hold {sm.analyst_hold} / Sell {sell_total}")
            if sm.target_mean:
                upside = f" (상승여력 {sm.upside_pct:+.1f}%)" if sm.upside_pct is not None else ""
                _w(f"- 평균 목표가: ${sm.target_mean:,.2f}{upside}")

        # 내부자
        if sm.insider_net_value is not None:
            _w(f"- 내부자: {sm.insider_summary}")

        # 실적
        if e.latest_period:
            eps = f"EPS {e.eps_surprise_pct:+.1f}%" if e.eps_surprise_pct is not None else "EPS -"
            _w(f"- 실적 ({e.latest_period}): {eps}, 연속 상회 {e.beat_streak}분기")

        # 리스크
        if rec.risk_factors:
            _w(f"- 리스크: {'; '.join(rec.risk_factors)}")

        # 보강 데이터 (yfinance 실시간)
        ed = enriched_data.get(rec.ticker)
        if ed:
            extra_parts = []
            if ed.high_52w and ed.low_52w:
                extra_parts.append(f"52주 ${ed.low_52w:,.0f}~${ed.high_52w:,.0f}")
            if ed.pct_from_52w_high is not None:
                extra_parts.append(f"고점 대비 {ed.pct_from_52w_high:+.1f}%")
            if ed.beta is not None:
                extra_parts.append(f"Beta {ed.beta:.2f}")
            if extra_parts:
                _w(f"- 가격 위치: {' | '.join(extra_parts)}")

            val_parts = []
            if ed.forward_per is not None:
                val_parts.append(f"선행PER {ed.forward_per:.1f}")
            if ed.peg_ratio is not None:
                val_parts.append(f"PEG {ed.peg_ratio:.2f}")
            sector_avg = sector_per_avgs.get(rec.sector or "기타")
            if sector_avg and f.per:
                premium = ((f.per / sector_avg) - 1) * 100
                val_parts.append(f"섹터PER평균 {sector_avg:.0f} ({'프리미엄' if premium > 0 else '할인'} {abs(premium):.0f}%)")
            if val_parts:
                _w(f"- 밸류에이션: {' | '.join(val_parts)}")

            growth_parts = []
            if ed.revenue_growth is not None:
                growth_parts.append(f"매출성장 {ed.revenue_growth:+.1f}%")
            if ed.earnings_growth is not None:
                growth_parts.append(f"이익성장 {ed.earnings_growth:+.1f}%")
            if ed.free_cashflow is not None:
                fcf_b = ed.free_cashflow / 1e9
                growth_parts.append(f"FCF ${fcf_b:.1f}B")
            if growth_parts:
                _w(f"- 성장/수익성: {' | '.join(growth_parts)}")

            if ed.institutional_pct is not None:
                _w(f"- 기관 보유: {ed.institutional_pct:.1f}% | 공매도: {ed.short_pct_float or 0:.1f}%")

            # 애널리스트 목표가 범위
            if ed.target_low_price and ed.target_high_price and ed.target_mean_price:
                _w(f"- 목표가 범위: ${ed.target_low_price:,.0f}~${ed.target_high_price:,.0f} (평균 ${ed.target_mean_price:,.0f})")
            if ed.recommendation_mean is not None:
                rec_label = {1: "Strong Buy", 2: "Buy", 3: "Hold", 4: "Sell", 5: "Strong Sell"}
                label = rec_label.get(round(ed.recommendation_mean), f"{ed.recommendation_mean:.1f}")
                _w(f"- 애널리스트 컨센서스: {label} ({ed.recommendation_mean:.1f}/5)")

        # 이벤트 캘린더
        events_data = events_data or {}
        ev = events_data.get(rec.ticker)
        if ev:
            if ev.is_pre_earnings and ev.next_earnings:
                _w(f"- **[주의] 실적 발표 {ev.next_earnings.days_until}일 후 ({ev.next_earnings.earnings_date}) — 변동성 확대 예상**")
            elif ev.is_post_earnings and ev.recent_earnings:
                _w(f"- 최근 실적 발표 완료 ({ev.recent_earnings.earnings_date})")
            elif ev.next_earnings:
                _w(f"- 다음 실적: {ev.next_earnings.earnings_date} ({ev.next_earnings.days_until}일 후)")

        _w("")

    # 섹터 분포
    sector_counts: dict[str, int] = {}
    for rec in report.recommendations:
        sector = rec.sector or "기타"
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
    if sector_counts:
        _w("## 추천 종목 섹터 분포")
        for sector, count in sorted(sector_counts.items(), key=lambda x: x[1], reverse=True):
            _w(f"- {sector}: {count}개")
            if count >= 3:
                _w(f"  → **[경고] {sector} 쏠림: {count}종목 집중. 동반 하락 리스크 주의**")
        # Beta 요약
        betas = []
        for rec in report.recommendations:
            ed = enriched_data.get(rec.ticker)
            if ed and ed.beta is not None:
                betas.append((rec.ticker, ed.beta))
        if betas:
            high_beta = [(t, b) for t, b in betas if b > 1.3]
            low_beta = [(t, b) for t, b in betas if b < 0.7]
            if high_beta:
                names = ", ".join(f"{t}({b:.2f})" for t, b in high_beta)
                _w(f"- **고베타 종목**: {names} → 시장 하락 시 증폭 리스크")
            if low_beta:
                names = ", ".join(f"{t}({b:.2f})" for t, b in low_beta)
                _w(f"- **저베타 방어주**: {names} → 약세장 방어 역할")
        _w("")

    # Chain-of-Thought 분석 프로세스
    _add_chain_of_thought(parts)

    # 분석 요청
    _w("## 분석 요청")
    _w("")
    _w("위 데이터를 바탕으로 **시니어 포트폴리오 매니저** 관점에서 다음을 분석해주세요:")
    _w("")
    _w(f"1. **시장 체제 판단**: 현재 시장이 강세/약세/횡보/전환기 중 어디에 해당하는지,")
    _w("   그 근거를 VIX, 금리, S&P 500 추세 등으로 설명해주세요.")
    _w("")
    _w(f"2. **최종 매수 추천**: TOP {len(report.recommendations)} 중 실제 매수를 추천하는 종목을 선정하고,")
    _w("   각 종목에 대해:")
    _w("   - 신뢰도 (1-10)")
    _w("   - 리스크 수준 (LOW/MEDIUM/HIGH)")
    _w("   - 구체적 매수 전략 (분할 매수 가격대, 비중)")
    _w("   - 익절/손절 전략 (목표가, 손절가, 부분 익절 기준)")
    _w("   을 제시해주세요.")
    _w("")
    _w(f"3. **제외 종목**: TOP {len(report.recommendations)} 중 매수를 추천하지 않는 종목이 있다면,")
    _w("   구체적 이유와 어떤 조건이 바뀌면 재검토할 수 있는지 설명해주세요.")
    _w("")
    _w("4. **포트폴리오 구성**: 추천 종목 전체를 하나의 포트폴리오로 볼 때,")
    _w("   - 섹터 집중도 리스크")
    _w("   - 적정 포지션 사이징 (각 종목 비중)")
    _w("   - 전체 포트폴리오 리스크/리워드")
    _w("   를 평가해주세요.")
    _w("")
    _w("5. **시장 리스크**: 향후 1-4주 내 주의해야 할 매크로 이벤트나 리스크를 경고해주세요.")
    _w("")
    _w("6. **시나리오 분석**: 추천 포트폴리오 전체에 대해 20일 후 기준:")
    _w("   - Best case (확률 25%): 예상 수익률 + 트리거 이벤트")
    _w("   - Base case (확률 50%): 예상 수익률 + 근거")
    _w("   - Worst case (확률 25%): 예상 손실률 + 리스크 이벤트")
    _w("   을 **각 종목별**로 제시하고, 포트폴리오 전체 기대수익률도 계산해주세요.")
    _w("")
    _w("7. **상관관계 경고**: 같은 섹터 종목, 같은 매크로 민감도를 가진 종목이")
    _w("   동시에 추천되면 분산 부족 리스크를 경고해주세요.")
    _w("   예: Energy 2종목 동시 추천 → 유가 하락 시 동반 하락 리스크.")
    _w("")
    _w("## 필수 응답 형식")
    _w("분석 내용을 서술한 후, **반드시** 마지막에 아래 JSON을 포함해주세요.")
    _w("모든 필드를 빠짐없이 채워주세요:")
    _w("```json")
    _w('{')
    _w('  "approved": ["TICKER1", "TICKER2"],')
    _w('  "excluded": ["TICKER3"],')
    _w('  "analysis": [')
    _w('    {')
    _w('      "ticker": "TICKER1",')
    _w('      "reason": "추천 이유 (2-3문장)",')
    _w('      "confidence": 8,')
    _w('      "risk_level": "LOW",')
    _w('      "target_price": 200,')
    _w('      "stop_loss": 170,')
    _w('      "entry_strategy": "$185 근처 분할 매수 (1차 50%, 2차 $180에서 50%)",')
    _w('      "exit_strategy": "목표가 $200 도달 시 50% 익절, 나머지 $220까지 홀드"')
    _w("    },")
    _w('    {')
    _w('      "ticker": "TICKER3",')
    _w('      "reason": "제외 이유 (2-3문장)",')
    _w('      "confidence": 3,')
    _w('      "risk_level": "HIGH",')
    _w('      "target_price": null,')
    _w('      "stop_loss": null,')
    _w('      "entry_strategy": "",')
    _w('      "exit_strategy": ""')
    _w("    }")
    _w("  ],")
    _w('  "portfolio": {')
    _w('    "market_outlook": "현재 시장 체제 한줄 요약",')
    _w('    "sector_balance": "섹터 집중도 평가",')
    _w('    "overall_risk": "LOW/MEDIUM/HIGH",')
    _w('    "position_sizing": "포지션 사이징 조언"')
    _w("  }")
    _w("}")
    _w("```")
    _w("")
    _w("※ 본 분석은 투자 참고용이며 투자 권유가 아닙니다.")

    return "\n".join(parts)


def save_prompt(prompt: str, run_date: date) -> Path:
    """프롬프트를 파일로 저장한다."""
    reports_dir = Path("reports/prompts")
    reports_dir.mkdir(parents=True, exist_ok=True)

    path = reports_dir / f"{run_date.isoformat()}_prompt.txt"
    path.write_text(prompt, encoding="utf-8")

    logger.info("프롬프트 저장: %s", path)
    return path


def build_deep_dive_prompt(
    approved_tickers: list[str],
    report: "EnrichedDailyReport",
) -> str:
    """AI 승인 종목에 대한 딥다이브 프롬프트를 생성한다.

    1차 스크리닝 후 승인된 종목만 대상으로 심층 분석을 요청.
    """
    parts: list[str] = []
    _w = parts.append

    _w("# Round 2: 딥다이브 심층 분석")
    _w("")
    _w("1차 스크리닝에서 아래 종목들을 매수 추천했습니다.")
    _w("이제 각 종목에 대해 **실행 가능한 수준**의 구체적 분석을 해주세요.")
    _w("")

    approved_recs = [r for r in report.recommendations if r.ticker in approved_tickers]
    for rec in approved_recs:
        t = rec.technical
        f = rec.fundamental
        sm = rec.smart_money

        _w(f"## {rec.ticker} -- {rec.name} | {rec.sector}")
        _w(f"현재가 ${rec.price:,.2f} | 종합 {rec.total_score:.1f}/10")
        rsi_str = f"RSI {t.rsi:.0f}" if t.rsi else "RSI -"
        per_str = f"PER {f.per:.1f}" if f.per else "PER -"
        _w(f"{rsi_str} | {per_str} | MACD {t.macd_status} | MA {t.sma_alignment}")
        if sm.target_mean and sm.upside_pct is not None:
            _w(f"애널리스트 목표가 ${sm.target_mean:,.0f} (상승여력 {sm.upside_pct:+.1f}%)")
        _w("")
        _w("**분석 요청:**")
        _w("1. **3단계 분할 매수 전략**: 가격대별 비중(%), 매수 트리거 조건")
        _w("2. **익절 전략**: 부분 익절 기준 2-3단계 (가격 + 비중)")
        _w("3. **손절 전략**: 하드 손절가 + 시간 기반 손절 (N일 후 목표 미달시)")
        _w("4. **20일 시나리오**: Best(25%) / Base(50%) / Worst(25%) 수익률 + 근거")
        _w("5. **핵심 촉매**: 향후 30일 내 주가 영향 이벤트 (실적, 제품, 규제)")
        _w("6. **최대 리스크**: 가장 큰 단일 리스크 요인과 발생 확률")
        _w("")

    _w("## 포트폴리오 전체 분석")
    _w(f"위 {len(approved_recs)}종목을 $100,000 포트폴리오에 배분한다면:")
    _w("- 각 종목 배분 비중 (%)")
    _w("- 포트폴리오 전체 기대 수익률 (20일)")
    _w("- 최대 손실 시나리오")
    _w("")
    _w("## 응답 형식")
    _w("자유롭게 분석한 후, 마지막에 아래 JSON을 포함해주세요:")
    _w("```json")
    _w('{')
    _w('  "approved": ["TICKER1", ...],')
    _w('  "excluded": [],')
    _w('  "analysis": [')
    _w('    {"ticker": "TICKER1", "reason": "심층 분석 결과", "confidence": 8, "risk_level": "LOW",')
    _w('     "target_price": 000, "stop_loss": 000,')
    _w('     "entry_strategy": "1차 $X (50%), 2차 $Y (30%), 3차 $Z (20%)",')
    _w('     "exit_strategy": "$A 도달 시 30% 익절, $B에서 추가 30%, 나머지 $C까지"}')
    _w("  ]")
    _w("}")
    _w("```")

    return "\n".join(parts)


def build_unified_prompt(
    session: Session,
    run_date_id: int,
    run_date: date,
    deep_dive: bool = True,
) -> str:
    """Round 1 스크리닝 + Round 2 딥다이브를 단일 프롬프트로 생성한다.

    단일 API 호출로 스크리닝 분석과 딥다이브를 동시에 수행할 수 있다.
    deep_dive=False 이면 기존 build_prompt 와 동일.
    """
    base = build_prompt(session, run_date_id, run_date)

    if not deep_dive:
        return base

    lines = [base]
    lines.append("")
    lines.append("## PART 2: 상위 추천 종목 딥다이브")
    lines.append("")
    lines.append("위에서 매수 추천한 종목 중 상위 3개에 대해 추가 분석:")
    lines.append("1. 3단계 분할 매수 전략 (가격대별 비중)")
    lines.append("2. 시나리오 분석 (Best 25% / Base 50% / Worst 25%)")
    lines.append("3. 20일 내 핵심 촉매")
    lines.append("4. 포트폴리오 $100K 배분 비율")
    lines.append("")
    lines.append("Tool의 deep_dive 필드에 결과를 포함하세요.")

    return "\n".join(lines)


def get_style_instruction(style: str) -> str:
    """AI 스타일에 따른 추가 지시사항을 반환한다."""
    instructions = {
        "aggressive": (
            "공격적 투자 관점에서 분석하세요. "
            "높은 수익 잠재력을 가진 3-5개 종목에 집중하고, "
            "모멘텀과 성장성을 우선시하세요."
        ),
        "balanced": (
            "균형 잡힌 투자 관점에서 분석하세요. "
            "수익성과 안전성을 동시에 고려하여 5-7개 종목을 추천하고, "
            "섹터 분산을 중요하게 보세요."
        ),
        "conservative": (
            "보수적 투자 관점에서 분석하세요. "
            "안정성과 배당수익률을 우선시하고, "
            "높은 변동성 종목은 신중하게 판단하세요. 7-10개 분산 투자를 권장하세요."
        ),
    }
    return instructions.get(style, instructions["balanced"])


def _fmt_roe(v: float | None) -> str:
    if v is None:
        return "-"
    pct = v * 100 if abs(v) < 1 else v
    return f"{pct:.1f}%"
