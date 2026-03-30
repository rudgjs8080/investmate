"""AI 주간 시장 코멘터리 생성기 — Claude SDK를 통한 30년 베테랑 분석."""

from __future__ import annotations

import logging
from pathlib import Path

from src.reports.weekly_models import WeeklyReport

logger = logging.getLogger(__name__)


def build_weekly_commentary_prompt(report: WeeklyReport) -> str:
    """WeeklyReport를 30년차 애널리스트 코멘터리 프롬프트로 변환한다."""
    lines: list[str] = []
    _w = lines.append

    _w("당신은 월스트리트에서 30년 경력의 시니어 시장 애널리스트입니다.")
    _w("아래 주간 리포트 데이터를 바탕으로 전문적이면서도 읽기 쉬운 주간 시장 코멘터리를 작성하세요.")
    _w("한국어로 작성하고, 산문체(prose)로 자연스럽게 서술하세요.")
    _w("")

    # 기간
    _w(f"[기간] {report.week_start} ~ {report.week_end} (거래일 {report.trading_days}일)")
    _w("")

    # Executive Summary
    es = report.executive_summary
    _w("[시장 요약]")
    _w(f"- {es.market_oneliner}")
    if es.sp500_weekly_return_pct is not None:
        _w(f"- S&P 500 주간 수익률: {es.sp500_weekly_return_pct:+.1f}%")
    if es.vix_start is not None and es.vix_end is not None:
        _w(f"- VIX: {es.vix_start:.1f} → {es.vix_end:.1f}")
    if es.vix_high is not None:
        _w(f"- VIX 주간 고점: {es.vix_high:.1f}")
    _w(f"- 시장 체제: {es.regime_end}")
    _w("")

    # Performance
    pr = report.performance_review
    _w("[추천 성과]")
    _w(f"- 총 {pr.total_unique_picks}개 추천, 승 {pr.win_count} / 패 {pr.loss_count}")
    if pr.win_rate_pct is not None:
        _w(f"- 승률: {pr.win_rate_pct:.1f}%")
    if pr.avg_return_pct is not None:
        _w(f"- 평균 수익률: {pr.avg_return_pct:+.2f}%")
    if pr.best_pick:
        _w(f"- 베스트: {pr.best_pick.ticker} ({pr.best_pick.weekly_return_pct:+.2f}%)" if pr.best_pick.weekly_return_pct else f"- 베스트: {pr.best_pick.ticker}")
    if pr.worst_pick:
        _w(f"- 워스트: {pr.worst_pick.ticker} ({pr.worst_pick.weekly_return_pct:+.2f}%)" if pr.worst_pick.weekly_return_pct else f"- 워스트: {pr.worst_pick.ticker}")
    _w("")

    # Conviction picks
    if report.conviction_picks:
        _w("[확신 종목]")
        for c in report.conviction_picks:
            ret_str = f" ({c.weekly_return_pct:+.2f}%)" if c.weekly_return_pct is not None else ""
            _w(f"- {c.ticker} ({c.name}): {c.days_recommended}일 추천, "
               f"평균 {c.avg_total_score:.1f}점, AI {c.ai_consensus}{ret_str}")
        _w("")

    # Sector rotation
    if report.sector_rotation:
        _w("[섹터 로테이션]")
        for s in report.sector_rotation[:5]:
            ret = f"{s.weekly_return_pct:+.2f}%" if s.weekly_return_pct is not None else "-"
            _w(f"- {s.sector}: {ret} | 모멘텀 {s.momentum_delta} | 추천 {s.pick_count}건")
        _w("")

    # Macro
    ms = report.macro_summary
    _w("[매크로]")
    if ms.us_10y_start is not None and ms.us_10y_end is not None:
        _w(f"- 10Y 금리: {ms.us_10y_start:.2f}% → {ms.us_10y_end:.2f}%")
    if ms.dollar_start is not None and ms.dollar_end is not None:
        _w(f"- 달러 인덱스: {ms.dollar_start:.1f} → {ms.dollar_end:.1f}")
    if ms.gold_start is not None and ms.gold_end is not None:
        _w(f"- 금: ${ms.gold_start:.0f} → ${ms.gold_end:.0f}")
    if ms.oil_start is not None and ms.oil_end is not None:
        _w(f"- 유가: ${ms.oil_start:.1f} → ${ms.oil_end:.1f}")
    _w("")

    # Risk
    rd = report.risk_dashboard
    if rd:
        _w("[리스크]")
        _w(f"- VIX 노출: {rd.vix_exposure}")
        if rd.top_sector and rd.max_sector_concentration_pct is not None:
            _w(f"- 섹터 집중도: {rd.top_sector} {rd.max_sector_concentration_pct:.0f}%")
        _w("")

    # 요구 형식
    _w("[요구 형식]")
    _w("아래 5개 소제목으로 총 500-800자 한국어 코멘터리를 작성하세요:")
    _w("1. 주간 시장 총평 — 시장 움직임에 대한 해석과 인사이트")
    _w("2. 확신 종목 심층 의견 — 왜 이 종목들이 반복 추천되었는지 분석")
    _w("3. 섹터 로테이션 시사점 — 자금 흐름과 섹터 전략")
    _w("4. 다음 주 전략 — 구체적인 매매 전략 제안")
    _w("5. 리스크 포인트 — 주의해야 할 리스크 요소")
    _w("")
    _w("각 소제목은 '### 1. 주간 시장 총평' 형태로 작성하세요.")
    _w("투자 참고용이며 투자 권유가 아님을 명시하세요.")

    return "\n".join(lines)


def generate_weekly_commentary(
    report: WeeklyReport,
    model: str | None = None,
    timeout: int = 300,
) -> str | None:
    """Claude를 호출하여 주간 코멘터리를 생성한다.

    Returns:
        Korean prose commentary (~500-800 words), or None on failure.
    """
    from src.config import get_settings

    settings = get_settings()
    if not settings.ai_enabled:
        logger.info("AI 비활성화 — 주간 코멘터리 스킵")
        return None

    prompt = build_weekly_commentary_prompt(report)
    target_model = model or getattr(settings, "ai_model_commentary", None) or "claude-sonnet-4-20250514"

    # 스트리밍 우선, SDK fallback
    try:
        from src.ai.claude_analyzer import run_claude_analysis_streaming

        result = run_claude_analysis_streaming(prompt, timeout, target_model)
        if result:
            logger.info("주간 코멘터리 생성 완료 (streaming, %d자)", len(result))
            return result
    except Exception as e:
        logger.warning("스트리밍 코멘터리 실패: %s", e)

    try:
        from src.ai.claude_analyzer import run_claude_analysis_sdk

        result = run_claude_analysis_sdk(prompt, timeout, target_model)
        if result:
            logger.info("주간 코멘터리 생성 완료 (sdk, %d자)", len(result))
            return result
    except Exception as e:
        logger.warning("SDK 코멘터리 실패: %s", e)

    logger.error("주간 코멘터리 생성 실패 — 모든 백엔드 실패")
    return None


def save_commentary(commentary: str, year: int, week_number: int) -> Path:
    """코멘터리를 Markdown 파일로 저장한다."""
    output_dir = Path("reports/weekly")
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{year}-W{week_number:02d}_ai_commentary.md"
    path.write_text(
        f"# AI 주간 코멘터리 — {year}-W{week_number:02d}\n\n{commentary}",
        encoding="utf-8",
    )
    logger.info("AI 코멘터리 저장: %s", path)
    return path
