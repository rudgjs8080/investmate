"""Deep Dive AI 프롬프트 빌더 + CLI 호출 — Phase 1: 단일 호출 (debate 없음)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from src.deepdive.schemas import AIResult

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# 시스템 프롬프트
# ──────────────────────────────────────────

DEEPDIVE_SYSTEM_PROMPT = """\
너는 30년 경력 수석 CIO다. 제공된 데이터를 기반으로 종목을 분석하고 최종 판단을 내려라.
단계적으로 깊이 사고한 뒤 결론을 내려라.

판단 기준:
1. 펀더멘털 건전성 (Layer 1: F-Score, Z-Score, 마진 추세, ROE)
2. 기술적 추세와 모멘텀 (Layer 3: 추세 정렬, RSI, S/R, 52주 위치)
3. 수급 흐름 (Layer 4: 내부자, 공매도, 애널리스트)
4. 리스크/보상 비대칭성

보유자 관점 (보유 종목만):
- HOLD = 현 포지션 유지 + 모니터링
- ADD = 추가 매수 (확신 높을 때만)
- TRIM = 일부 매도, 비중 축소
- EXIT = 전량 매도 (확신 높을 때만)
- +30% 이상 수익 → 일부 이익실현 검토
- -15% 이상 손실 → 손절 검토

비보유 종목:
- HOLD = 관망
- ADD = 신규 매수 고려

반드시 아래 JSON 형식만 출력하라. 다른 텍스트 없이 JSON만:
{"action_grade":"HOLD", "conviction":7, "uncertainty":"medium", \
"reasoning":"200자 이내 종합 판단", "what_missing":"반대 의견 강조"}
"""


# ──────────────────────────────────────────
# 프롬프트 빌드
# ──────────────────────────────────────────


def build_stock_context(
    entry, layers: dict, current_price: float, daily_change: float,
    pair_results: list | None = None,
) -> str:
    """<stock_context> XML 블록 빌드. 보유정보 있으면 <holding_context> 삽입."""
    parts = [
        "<stock_context>",
        f"종목: {entry.ticker} ({entry.name})",
        f"섹터: {entry.sector or 'Unknown'} | S&P500: {'Yes' if entry.is_sp500 else 'No'}",
        f"현재가: ${current_price:.2f} | 일간: {daily_change:+.2f}%",
    ]

    # 보유정보 주입
    if entry.holding is not None:
        h = entry.holding
        pnl_pct = (current_price - h.avg_cost) / h.avg_cost * 100 if h.avg_cost > 0 else 0
        pnl_amount = (current_price - h.avg_cost) * h.shares
        position_value = current_price * h.shares
        holding_days = ""
        if h.opened_at:
            from datetime import date

            days = (date.today() - h.opened_at).days
            holding_days = f"\n보유 기간: {days}일"
        parts.append("")
        parts.append("<holding_context>")
        parts.append(f"보유 수량: {h.shares}주 | 평단가: ${h.avg_cost:.2f}")
        parts.append(f"보유 수익률: {pnl_pct:+.1f}% (${pnl_amount:+,.0f})")
        parts.append(f"포지션 가치: ${position_value:,.0f}{holding_days}")
        parts.append("</holding_context>")

    # 레이어 데이터
    layer1 = layers.get("layer1")
    if layer1:
        parts.append("")
        parts.append("<layer1_fundamental>")
        parts.append(f"건전성 등급: {layer1.health_grade}")
        parts.append(f"F-Score: {layer1.f_score}/9 | Z-Score: {layer1.z_score or 'N/A'}")
        parts.append(f"마진 추세: {layer1.margin_trend}")
        parts.append(f"Operating Margin: {layer1.operating_margin or 'N/A'}%")
        parts.append(f"ROE: {layer1.roe or 'N/A'}% | 부채비율: {layer1.debt_ratio or 'N/A'}%")
        parts.append(f"실적 Beat 연속: {layer1.earnings_beat_streak}분기")
        parts.append("</layer1_fundamental>")

    layer3 = layers.get("layer3")
    if layer3:
        parts.append("")
        parts.append("<layer3_technical>")
        parts.append(f"기술적 등급: {layer3.technical_grade}")
        parts.append(f"추세 정렬: {layer3.trend_alignment}")
        parts.append(f"52주 위치: {layer3.position_52w_pct:.1f}%")
        parts.append(f"RSI(14): {layer3.rsi or 'N/A'}")
        parts.append(f"MACD: {layer3.macd_signal or 'N/A'}")
        parts.append(f"지지: ${layer3.nearest_support or 'N/A'} | 저항: ${layer3.nearest_resistance or 'N/A'}")
        parts.append(f"변동성 레짐: {layer3.atr_regime}")
        parts.append("</layer3_technical>")

    layer4 = layers.get("layer4")
    if layer4:
        parts.append("")
        parts.append("<layer4_flow>")
        parts.append(f"수급 등급: {layer4.flow_grade}")
        parts.append(f"내부자 90일 순: ${layer4.insider_net_90d:,.0f} ({layer4.insider_signal})")
        parts.append(f"공매도 비율: {layer4.short_pct_float or 'N/A'}%")
        parts.append(f"애널리스트 매수비율: {layer4.analyst_buy_pct or 'N/A'}%")
        parts.append(f"목표가 업사이드: {layer4.analyst_target_upside or 'N/A'}%")
        parts.append(f"기관 동향: {layer4.institutional_change or 'N/A'}")
        parts.append("</layer4_flow>")

    layer2 = layers.get("layer2")
    if layer2:
        parts.append("")
        parts.append("<layer2_valuation>")
        parts.append(f"밸류에이션 등급: {layer2.valuation_grade}")
        parts.append(f"PER 5년 백분위: {layer2.per_5y_percentile or 'N/A'}%")
        parts.append(f"PBR 5년 백분위: {layer2.pbr_5y_percentile or 'N/A'}%")
        parts.append(f"섹터 PER 프리미엄: {layer2.sector_per_premium or 'N/A'}%")
        parts.append(f"DCF 내재 성장률: {layer2.dcf_implied_growth or 'N/A'}%")
        parts.append(f"PEG: {layer2.peg_ratio or 'N/A'} | FCF Yield: {layer2.fcf_yield or 'N/A'}%")
        parts.append("</layer2_valuation>")

    layer5 = layers.get("layer5")
    if layer5:
        parts.append("")
        parts.append("<layer5_narrative>")
        parts.append(f"내러티브 등급: {layer5.narrative_grade}")
        parts.append(f"감성 추이: 30일={layer5.sentiment_30d or 'N/A'} | 60일={layer5.sentiment_60d or 'N/A'} | 90일={layer5.sentiment_90d or 'N/A'}")
        parts.append(f"감성 방향: {layer5.sentiment_trend}")
        if layer5.upcoming_catalysts:
            parts.append(f"임박 촉매: {', '.join(layer5.upcoming_catalysts)}")
        if layer5.risk_events:
            parts.append(f"리스크 이벤트: {', '.join(layer5.risk_events)}")
        parts.append("</layer5_narrative>")

    layer6 = layers.get("layer6")
    if layer6:
        parts.append("")
        parts.append("<layer6_macro>")
        parts.append(f"거시 민감도 등급: {layer6.macro_grade}")
        parts.append(f"VIX 베타: {layer6.beta_vix or 'N/A'} | 10Y 베타: {layer6.beta_10y or 'N/A'} | Dollar 베타: {layer6.beta_dollar or 'N/A'}")
        parts.append(f"섹터 모멘텀 순위: {layer6.sector_momentum_rank or 'N/A'}/{layer6.sector_momentum_total or 'N/A'}")
        parts.append(f"현재 레짐: {layer6.current_regime or 'N/A'} | 레짐 평균 수익률: {layer6.regime_avg_return or 'N/A'}%")
        parts.append("</layer6_macro>")

    if pair_results:
        parts.append("")
        parts.append("<pair_comparison>")
        parts.append(f"Top {len(pair_results)} 동종 페어:")
        for p in pair_results:
            parts.append(
                f"  {p.peer_ticker} ({p.peer_name}): "
                f"시총비={p.market_cap_ratio:.1f}x, "
                f"60일수익률={p.return_60d_peer:+.1f}% vs 대상 {p.return_60d_target:+.1f}%, "
                f"PER={p.per_peer or 'N/A'} vs {p.per_target or 'N/A'}, "
                f"유사도={p.similarity_score:.2f}"
            )
        parts.append("</pair_comparison>")

    parts.append("</stock_context>")
    return "\n".join(parts)


def build_deepdive_prompt(stock_context: str) -> str:
    """유저 프롬프트 빌드."""
    return (
        "아래 종목 데이터를 분석하여 최종 판단을 JSON으로 출력하라.\n\n"
        f"{stock_context}\n\n"
        "투자 참고용이며 투자 권유가 아닙니다."
    )


# ──────────────────────────────────────────
# AI 호출
# ──────────────────────────────────────────


def run_deepdive_simple(
    entry, layers: dict, current_price: float, daily_change: float,
    timeout: int = 600, model: str = "opus",
) -> AIResult | None:
    """Phase 1 단일 CLI 호출. debate 없음."""
    context = build_stock_context(entry, layers, current_price, daily_change)
    prompt = build_deepdive_prompt(context)
    raw = run_deepdive_cli(prompt, DEEPDIVE_SYSTEM_PROMPT, timeout, model)
    if raw is None:
        return None
    return _parse_ai_response(raw)


def run_deepdive_cli(
    prompt: str,
    system_prompt: str | None = None,
    timeout: int = 600,
    model: str = "opus",
) -> str | None:
    """Claude CLI 호출. --model opus --system-prompt."""
    claude_path = shutil.which("claude")
    if not claude_path:
        logger.warning("Claude Code CLI를 찾을 수 없습니다")
        return None

    cmd = [claude_path, "-p", "--model", model]
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]

    env = os.environ.copy()
    node_path = shutil.which("node")
    if node_path:
        env["PATH"] = str(Path(node_path).parent) + os.pathsep + env.get("PATH", "")

    try:
        result = subprocess.run(
            cmd, input=prompt,
            capture_output=True, text=True, timeout=timeout,
            env=env, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info("Deep Dive CLI 분석 완료 (%d자)", len(result.stdout))
            return result.stdout.strip()
        logger.warning("Deep Dive CLI 실패 (코드 %d): %s", result.returncode, result.stderr[:200])
        return None
    except subprocess.TimeoutExpired:
        logger.warning("Deep Dive CLI 타임아웃 (%d초)", timeout)
        return None
    except FileNotFoundError:
        logger.warning("Claude Code CLI를 찾을 수 없습니다")
        return None
    except Exception as e:
        logger.error("Deep Dive CLI 오류: %s", e)
        return None


# ──────────────────────────────────────────
# 응답 파싱
# ──────────────────────────────────────────


def _parse_ai_response(raw: str) -> AIResult | None:
    """JSON 파싱 + regex fallback."""
    # 1차: ```json 블록
    json_match = re.search(r"```json\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if json_match:
        data = _try_parse(json_match.group(1))
        if data:
            return _dict_to_result(data)

    # 2차: 점진적 JSON 추출
    decoder = json.JSONDecoder()
    for i, char in enumerate(raw):
        if char == "{":
            try:
                obj, _ = decoder.raw_decode(raw, i)
                if isinstance(obj, dict) and "action_grade" in obj:
                    return _dict_to_result(obj)
            except (json.JSONDecodeError, ValueError):
                continue

    # 3차: regex fallback
    return _regex_fallback(raw)


def _try_parse(text: str) -> dict | None:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _dict_to_result(data: dict) -> AIResult | None:
    action = data.get("action_grade", "HOLD").upper()
    if action not in ("HOLD", "ADD", "TRIM", "EXIT"):
        action = "HOLD"
    conviction = max(1, min(10, int(data.get("conviction", 5))))
    uncertainty = data.get("uncertainty", "medium").lower()
    if uncertainty not in ("low", "medium", "high"):
        uncertainty = "medium"
    return AIResult(
        action_grade=action,
        conviction=conviction,
        uncertainty=uncertainty,
        reasoning=str(data.get("reasoning", ""))[:500],
        what_missing=data.get("what_missing"),
    )


def _regex_fallback(raw: str) -> AIResult | None:
    """최후 수단: regex로 핵심 필드 추출."""
    action_m = re.search(r'"action_grade"\s*:\s*"(HOLD|ADD|TRIM|EXIT)"', raw, re.IGNORECASE)
    conv_m = re.search(r'"conviction"\s*:\s*(\d+)', raw)
    if not action_m:
        return None
    return AIResult(
        action_grade=action_m.group(1).upper(),
        conviction=max(1, min(10, int(conv_m.group(1)))) if conv_m else 5,
        uncertainty="medium",
        reasoning="AI 응답 파싱 불완전",
        what_missing=None,
    )
