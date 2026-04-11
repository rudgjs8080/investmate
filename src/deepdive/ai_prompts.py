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

근거 추적 의무:
- reasoning 300~600자, 구체 수치 인용
- evidence_refs 3~6개 ("layer{N}.field=value" 형태)
- invalidation_conditions 2~3개
- key_levels(support/resistance/stop_loss) 제공

반드시 아래 JSON 형식만 출력하라. 다른 텍스트 없이 JSON만:
{"action_grade":"HOLD","conviction":7,"uncertainty":"medium",\
"reasoning":"구체 수치 인용 종합 판단","what_missing":"반대 의견 강조",\
"key_levels":{"support":가격,"resistance":가격,"stop_loss":가격},\
"next_review_trigger":"재검토 조건",\
"evidence_refs":["layer3.rsi=72","layer1.f_score=8"],\
"invalidation_conditions":["RSI 40 하회"]}
"""


# ──────────────────────────────────────────
# 프롬프트 빌드
# ──────────────────────────────────────────


def build_stock_context(
    entry, layers: dict, current_price: float, daily_change: float,
    pair_results: list | None = None,
    portfolio_context: dict | None = None,
) -> str:
    """<stock_context> XML 블록 빌드. 보유정보 있으면 <holding_context> 삽입.

    portfolio_context: {"sector_weights": {s: pct}, "ticker_weights": {t: pct},
        "max_stock_pct": float, "max_sector_pct": float, "total_names": int}
    """
    parts = [
        "<stock_context>",
        f"종목: {entry.ticker} ({entry.name})",
        f"섹터: {entry.sector or 'Unknown'} | S&P500: {'Yes' if entry.is_sp500 else 'No'}",
        f"현재가: ${current_price:.2f} | 일간: {daily_change:+.2f}%",
    ]

    if portfolio_context:
        parts.append("")
        parts.append("<portfolio_context>")
        total_names = portfolio_context.get("total_names", 0)
        parts.append(f"보유 종목 수: {total_names}")
        sw = portfolio_context.get("sector_weights") or {}
        if sw:
            top_sectors = sorted(sw.items(), key=lambda x: x[1], reverse=True)[:5]
            parts.append(
                "섹터 분포: "
                + ", ".join(f"{s}={v*100:.0f}%" for s, v in top_sectors if s)
            )
        cur_sector = entry.sector or ""
        cur_sector_pct = sw.get(cur_sector, 0.0)
        max_sec = portfolio_context.get("max_sector_pct", 0.30)
        parts.append(
            f"이 종목 섹터({cur_sector}) 기존 비중: {cur_sector_pct*100:.1f}% "
            f"(상한 {max_sec*100:.0f}%, 여유 {(max_sec - cur_sector_pct)*100:+.1f}%p)"
        )
        tw = portfolio_context.get("ticker_weights") or {}
        cur_ticker_pct = tw.get(entry.ticker, 0.0)
        max_stock = portfolio_context.get("max_stock_pct", 0.10)
        parts.append(
            f"이 종목 기존 비중: {cur_ticker_pct*100:.1f}% "
            f"(상한 {max_stock*100:.0f}%, 여유 {(max_stock - cur_ticker_pct)*100:+.1f}%p)"
        )
        parts.append("</portfolio_context>")

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
    portfolio_context: dict | None = None,
) -> AIResult | None:
    """Phase 1 단일 CLI 호출. debate 없음."""
    context = build_stock_context(
        entry, layers, current_price, daily_change,
        portfolio_context=portfolio_context,
    )
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
    max_attempts: int = 2,
) -> str | None:
    """Claude CLI 호출 + 파싱 가능 여부 기반 1회 재시도.

    첫 호출 응답이 비어 있거나 JSON으로 파싱 불가하면 한 번 더 호출.
    max_attempts=1로 내리면 재시도 비활성.
    """
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

    last_output: str | None = None
    for attempt in range(1, max(1, max_attempts) + 1):
        try:
            result = subprocess.run(
                cmd, input=prompt,
                capture_output=True, text=True, timeout=timeout,
                env=env, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            logger.warning("Deep Dive CLI 타임아웃 (시도 %d/%d, %d초)", attempt, max_attempts, timeout)
            continue
        except FileNotFoundError:
            logger.warning("Claude Code CLI를 찾을 수 없습니다")
            return None
        except Exception as e:
            logger.error("Deep Dive CLI 오류 (시도 %d/%d): %s", attempt, max_attempts, e)
            continue

        if result.returncode != 0 or not result.stdout.strip():
            logger.warning(
                "Deep Dive CLI 실패 (시도 %d/%d, 코드 %d): %s",
                attempt, max_attempts, result.returncode, (result.stderr or "")[:200],
            )
            continue

        last_output = result.stdout.strip()
        # JSON 파싱 가능하면 즉시 반환. 불가하면 재시도.
        if _has_parseable_json(last_output):
            logger.info(
                "Deep Dive CLI 분석 완료 (시도 %d/%d, %d자)",
                attempt, max_attempts, len(last_output),
            )
            return last_output
        logger.warning(
            "Deep Dive CLI 응답이 JSON 파싱 불가 (시도 %d/%d), 재시도",
            attempt, max_attempts,
        )

    # 재시도 실패 — 마지막 raw output이라도 반환 (regex fallback 기회).
    return last_output


def _has_parseable_json(raw: str) -> bool:
    """응답에 최소 1개 action_grade 포함 JSON 객체가 있는지."""
    if "{" not in raw:
        return False
    decoder = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch == "{":
            try:
                obj, _ = decoder.raw_decode(raw, i)
                if isinstance(obj, dict) and ("action_grade" in obj or "action" in obj):
                    return True
            except (json.JSONDecodeError, ValueError):
                continue
    return False


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

    # Phase 4: key_levels 구조화 복구 — Synthesizer가 이미 내놓는 데이터를 버리지 않음.
    key_levels = data.get("key_levels") or {}
    support = _safe_float(key_levels.get("support"))
    resistance = _safe_float(key_levels.get("resistance"))
    stop_loss = _safe_float(key_levels.get("stop_loss"))

    evidence = data.get("evidence_refs") or []
    if not isinstance(evidence, (list, tuple)):
        evidence = []
    invalidation = data.get("invalidation_conditions") or []
    if not isinstance(invalidation, (list, tuple)):
        invalidation = []

    return AIResult(
        action_grade=action,
        conviction=conviction,
        uncertainty=uncertainty,
        reasoning=str(data.get("reasoning", ""))[:2000],
        what_missing=data.get("what_missing"),
        support_price=support,
        resistance_price=resistance,
        stop_loss=stop_loss,
        next_review_trigger=data.get("next_review_trigger"),
        evidence_refs=tuple(str(e)[:200] for e in evidence if e),
        invalidation_conditions=tuple(str(c)[:200] for c in invalidation if c),
    )


def _safe_float(value) -> float | None:
    """숫자/문자열 → float. 실패 시 None."""
    if value is None:
        return None
    try:
        f = float(value)
        if f <= 0 or f != f:  # NaN or non-positive
            return None
        return f
    except (TypeError, ValueError):
        return None


def _regex_fallback(raw: str) -> AIResult | None:
    """최후 수단: regex로 핵심 필드 추출."""
    action_m = re.search(r'"action_grade"\s*:\s*"(HOLD|ADD|TRIM|EXIT)"', raw, re.IGNORECASE)
    conv_m = re.search(r'"conviction"\s*:\s*(\d+)', raw)
    if not action_m:
        return None
    support_m = re.search(r'"support"\s*:\s*([\d.]+)', raw)
    resist_m = re.search(r'"resistance"\s*:\s*([\d.]+)', raw)
    stop_m = re.search(r'"stop_loss"\s*:\s*([\d.]+)', raw)
    return AIResult(
        action_grade=action_m.group(1).upper(),
        conviction=max(1, min(10, int(conv_m.group(1)))) if conv_m else 5,
        uncertainty="medium",
        reasoning="AI 응답 파싱 불완전 (regex fallback)",
        what_missing=None,
        support_price=_safe_float(support_m.group(1)) if support_m else None,
        resistance_price=_safe_float(resist_m.group(1)) if resist_m else None,
        stop_loss=_safe_float(stop_m.group(1)) if stop_m else None,
    )
