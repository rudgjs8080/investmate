"""Phase 11a: Invalidation 조건 자연어 파서 + 평가.

AIResult.invalidation_conditions (tuple[str])를 룰 기반으로 파싱해
ParsedCondition AST로 변환하고, LayerSnapshot 기반으로 조건 충족 여부를 평가한다.

순수 함수 모듈 — DB 접근 없음.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParsedCondition:
    """파싱된 단일 무효화 조건."""

    raw: str                # 원문 (UI 표시용)
    indicator: str          # rsi | macd_signal | sma_20 | sma_50 | sma_200
                            # | high_52w | low_52w | f_score | sector_per_premium
    op: str                 # lt | gt | le | ge | below_close | above_close | cross_down | cross_up
    value: float | None     # 기준값 (이평선 이탈·크로스는 None)


@dataclass(frozen=True)
class ParseResult:
    """parse_conditions 결과 — 성공/실패 분리."""

    parsed: tuple[ParsedCondition, ...]
    unparsed: tuple[str, ...]


@dataclass(frozen=True)
class LayerSnapshot:
    """평가용 현재 상태 스냅샷 — layers dict 축약 뷰."""

    rsi: float | None
    macd_hist: float | None
    macd_hist_prev: float | None
    sma_20: float | None
    sma_50: float | None
    sma_200: float | None
    high_52w: float | None
    low_52w: float | None
    f_score: int | None
    sector_per_premium_pct: float | None
    close: float


# ──────────────────────────────────────────
# 정규식 패턴 테이블
# ──────────────────────────────────────────

_NUM = r"(\d+(?:\.\d+)?)"

# (pattern, indicator, op, value_group_idx | None)
_RSI_LT = re.compile(rf"RSI\s*{_NUM}\s*(?:하회|미만|아래|<)", re.IGNORECASE)
_RSI_LE = re.compile(rf"RSI\s*{_NUM}\s*이하", re.IGNORECASE)
_RSI_GT = re.compile(rf"RSI\s*{_NUM}\s*(?:상회|초과|돌파|>)", re.IGNORECASE)
_RSI_GE = re.compile(rf"RSI\s*{_NUM}\s*이상", re.IGNORECASE)

_SMA_BELOW = re.compile(r"(20|50|200)\s*일\s*이평(?:선)?\s*(?:이탈|하회|아래)")
_SMA_ABOVE = re.compile(r"(20|50|200)\s*일\s*이평(?:선)?\s*(?:돌파|상회)")

_MACD_DEAD = re.compile(r"MACD\s*(?:데드|dead)?\s*크로스", re.IGNORECASE)
_MACD_GOLDEN = re.compile(r"MACD\s*(?:골든|golden)\s*크로스", re.IGNORECASE)
# dead가 없고 golden도 없으면 기본은 dead로 보지 않음 — 모호성 방지

_52W_LOW = re.compile(r"52\s*주\s*(?:신)?\s*저")
_52W_HIGH = re.compile(r"52\s*주\s*(?:신)?\s*고")

_F_SCORE_LT = re.compile(rf"F[-\s]?Score\s*{_NUM}\s*(?:미만|<)", re.IGNORECASE)
_F_SCORE_LE = re.compile(rf"F[-\s]?Score\s*{_NUM}\s*이하", re.IGNORECASE)

_SECTOR_PER_GT = re.compile(
    rf"섹터\s*PER\s*프리미엄\s*{_NUM}\s*%?\s*(?:초과|>|상회)", re.IGNORECASE,
)
_SECTOR_PER_GE = re.compile(
    rf"섹터\s*PER\s*프리미엄\s*{_NUM}\s*%?\s*이상", re.IGNORECASE,
)


def _try_parse_single(raw: str) -> ParsedCondition | None:
    """한 문자열을 파싱. 실패 시 None."""
    text = raw.strip()
    if not text:
        return None

    # RSI — golden cross가 아닐 때만
    if not _MACD_GOLDEN.search(text) and not _MACD_DEAD.search(text):
        for pat, op in (
            (_RSI_LT, "lt"),
            (_RSI_LE, "le"),
            (_RSI_GT, "gt"),
            (_RSI_GE, "ge"),
        ):
            m = pat.search(text)
            if m:
                return ParsedCondition(
                    raw=raw, indicator="rsi", op=op, value=float(m.group(1)),
                )

    # SMA below/above close
    m = _SMA_BELOW.search(text)
    if m:
        return ParsedCondition(
            raw=raw, indicator=f"sma_{m.group(1)}",
            op="below_close", value=None,
        )
    m = _SMA_ABOVE.search(text)
    if m:
        return ParsedCondition(
            raw=raw, indicator=f"sma_{m.group(1)}",
            op="above_close", value=None,
        )

    # MACD cross — golden 먼저 검사 (dead는 "골든"이 없을 때)
    if _MACD_GOLDEN.search(text):
        return ParsedCondition(
            raw=raw, indicator="macd_signal", op="cross_up", value=None,
        )
    if _MACD_DEAD.search(text):
        return ParsedCondition(
            raw=raw, indicator="macd_signal", op="cross_down", value=None,
        )

    # 52주 신고/신저
    if _52W_LOW.search(text):
        return ParsedCondition(
            raw=raw, indicator="low_52w", op="below_close", value=None,
        )
    if _52W_HIGH.search(text):
        return ParsedCondition(
            raw=raw, indicator="high_52w", op="above_close", value=None,
        )

    # F-Score
    m = _F_SCORE_LT.search(text)
    if m:
        return ParsedCondition(
            raw=raw, indicator="f_score", op="lt", value=float(m.group(1)),
        )
    m = _F_SCORE_LE.search(text)
    if m:
        return ParsedCondition(
            raw=raw, indicator="f_score", op="le", value=float(m.group(1)),
        )

    # 섹터 PER 프리미엄
    m = _SECTOR_PER_GT.search(text)
    if m:
        return ParsedCondition(
            raw=raw, indicator="sector_per_premium", op="gt", value=float(m.group(1)),
        )
    m = _SECTOR_PER_GE.search(text)
    if m:
        return ParsedCondition(
            raw=raw, indicator="sector_per_premium", op="ge", value=float(m.group(1)),
        )

    return None


def parse_conditions(raws: Sequence[str]) -> ParseResult:
    """여러 조건 문자열을 일괄 파싱.

    빈 문자열 / 공백만 있는 문자열은 무시(파싱 성공/실패 모두 제외).
    매칭 실패한 비어있지 않은 문자열은 unparsed로 담기고 warning 로그.
    """
    parsed: list[ParsedCondition] = []
    unparsed: list[str] = []
    for raw in raws:
        if raw is None:
            continue
        if not raw.strip():
            continue
        cond = _try_parse_single(raw)
        if cond is None:
            logger.warning("invalidation 파싱 실패: %s", raw)
            unparsed.append(raw)
        else:
            parsed.append(cond)
    return ParseResult(parsed=tuple(parsed), unparsed=tuple(unparsed))


# ──────────────────────────────────────────
# 평가
# ──────────────────────────────────────────


def _compare(actual: float, op: str, value: float) -> bool:
    if op == "lt":
        return actual < value
    if op == "le":
        return actual <= value
    if op == "gt":
        return actual > value
    if op == "ge":
        return actual >= value
    return False


def evaluate_condition(cond: ParsedCondition, snap: LayerSnapshot) -> bool:
    """단일 조건 평가. 필요 지표 누락 시 False(조용한 스킵 — 평가 불가와 미충족 구분은 호출측에서)."""
    ind = cond.indicator

    if ind == "rsi":
        if snap.rsi is None or cond.value is None:
            return False
        return _compare(snap.rsi, cond.op, cond.value)

    if ind in ("sma_20", "sma_50", "sma_200"):
        sma = getattr(snap, ind)
        if sma is None:
            return False
        if cond.op == "below_close":
            return snap.close < sma
        if cond.op == "above_close":
            return snap.close > sma
        return False

    if ind == "macd_signal":
        if snap.macd_hist is None or snap.macd_hist_prev is None:
            return False
        if cond.op == "cross_down":
            return snap.macd_hist_prev > 0 >= snap.macd_hist
        if cond.op == "cross_up":
            return snap.macd_hist_prev < 0 <= snap.macd_hist
        return False

    if ind == "low_52w":
        if snap.low_52w is None:
            return False
        return snap.close < snap.low_52w

    if ind == "high_52w":
        if snap.high_52w is None:
            return False
        return snap.close > snap.high_52w

    if ind == "f_score":
        if snap.f_score is None or cond.value is None:
            return False
        return _compare(float(snap.f_score), cond.op, cond.value)

    if ind == "sector_per_premium":
        if snap.sector_per_premium_pct is None or cond.value is None:
            return False
        return _compare(snap.sector_per_premium_pct, cond.op, cond.value)

    return False
