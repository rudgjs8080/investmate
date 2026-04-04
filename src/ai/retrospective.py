"""AI 예측 복기 엔진 — 과거 예측을 AI가 복기하고 교훈을 추출한다."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.helpers import date_to_id
from src.db.models import (
    DimDate,
    DimStock,
    FactAIRetrospective,
    FactDailyPrice,
    FactDailyRecommendation,
    FactMacroIndicator,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
LOOKBACK_TRADING_DAYS = 20


@dataclass(frozen=True)
class RetrospectiveCandidate:
    """복기 대상 추천 데이터."""

    recommendation_id: int
    ticker: str
    sector: str | None
    ai_approved: bool
    ai_confidence: int | None
    ai_reason: str | None
    ai_target_price: float | None
    ai_stop_loss: float | None
    price_at_rec: float
    return_20d: float
    max_gain_pct: float
    max_loss_pct: float
    price_path: tuple[float, ...]
    regime_at_rec: str | None
    vix_at_rec: float | None


# ---------------------------------------------------------------------------
# 20거래일 전 날짜 조회
# ---------------------------------------------------------------------------


def _find_trading_date_ago(
    session: Session,
    run_date_id: int,
    trading_days: int = LOOKBACK_TRADING_DAYS,
) -> int | None:
    """run_date_id 기준 N거래일 전의 date_id를 반환한다."""
    stmt = (
        select(DimDate.date_id)
        .where(
            DimDate.is_trading_day == True,
            DimDate.date_id < run_date_id,
        )
        .order_by(DimDate.date_id.desc())
        .offset(trading_days - 1)
        .limit(1)
    )
    return session.scalar(stmt)


# ---------------------------------------------------------------------------
# 가격 경로 계산
# ---------------------------------------------------------------------------


def compute_price_path(
    session: Session,
    stock_id: int,
    rec_date_id: int,
    base_price: float,
    days: int = LOOKBACK_TRADING_DAYS,
) -> tuple[tuple[float, ...], float, float]:
    """추천일 이후 N일간 일별 누적 수익률, 최대 상승, 최대 하락을 계산한다.

    Returns:
        (일별 수익률 튜플, max_gain_pct, max_loss_pct)
    """
    stmt = (
        select(FactDailyPrice.adj_close)
        .where(
            FactDailyPrice.stock_id == stock_id,
            FactDailyPrice.date_id > rec_date_id,
        )
        .order_by(FactDailyPrice.date_id.asc())
        .limit(days)
    )
    prices = list(session.scalars(stmt).all())

    if not prices or base_price <= 0:
        return ((), 0.0, 0.0)

    returns = tuple(
        round((float(p) / base_price - 1) * 100, 2) for p in prices
    )
    max_gain = max(returns) if returns else 0.0
    max_loss = min(returns) if returns else 0.0

    return (returns, max_gain, max_loss)


# ---------------------------------------------------------------------------
# 복기 후보 조회
# ---------------------------------------------------------------------------


def find_retrospective_candidates(
    session: Session,
    run_date_id: int,
) -> list[RetrospectiveCandidate]:
    """20거래일 전 추천 중 아직 복기하지 않은 건을 조회한다."""
    target_date_id = _find_trading_date_ago(session, run_date_id)
    if target_date_id is None:
        logger.debug("20거래일 전 날짜를 찾을 수 없음")
        return []

    # 이미 복기된 recommendation_id
    already_done = set(
        session.scalars(
            select(FactAIRetrospective.recommendation_id)
        ).all()
    )

    # 해당 날짜의 AI 분석 완료 추천
    stmt = (
        select(FactDailyRecommendation)
        .where(
            FactDailyRecommendation.run_date_id == target_date_id,
            FactDailyRecommendation.ai_approved.isnot(None),
            FactDailyRecommendation.return_20d.isnot(None),
        )
    )
    recs = list(session.scalars(stmt).all())

    # 매크로 (VIX) 조회
    macro = session.scalar(
        select(FactMacroIndicator).where(
            FactMacroIndicator.date_id <= target_date_id
        ).order_by(FactMacroIndicator.date_id.desc()).limit(1)
    )
    vix_at_rec = float(macro.vix) if macro and macro.vix else None

    # 시장 체제 추정 (간략)
    regime_at_rec = _estimate_regime(macro) if macro else None

    candidates: list[RetrospectiveCandidate] = []
    for rec in recs:
        if rec.recommendation_id in already_done:
            continue

        price = float(rec.price_at_recommendation or 0)
        if price <= 0:
            continue

        # 종목 섹터 조회
        stock = session.get(DimStock, rec.stock_id)
        sector = None
        if stock and stock.sector_id:
            from src.db.models import DimSector
            sec = session.get(DimSector, stock.sector_id)
            sector = sec.name if sec else None

        price_path, max_gain, max_loss = compute_price_path(
            session, rec.stock_id, target_date_id, price
        )

        candidates.append(RetrospectiveCandidate(
            recommendation_id=rec.recommendation_id,
            ticker=stock.ticker if stock else "???",
            sector=sector,
            ai_approved=bool(rec.ai_approved),
            ai_confidence=int(rec.ai_confidence) if rec.ai_confidence else None,
            ai_reason=rec.ai_reason,
            ai_target_price=float(rec.ai_target_price) if rec.ai_target_price else None,
            ai_stop_loss=float(rec.ai_stop_loss) if rec.ai_stop_loss else None,
            price_at_rec=price,
            return_20d=float(rec.return_20d),
            max_gain_pct=max_gain,
            max_loss_pct=max_loss,
            price_path=price_path,
            regime_at_rec=regime_at_rec,
            vix_at_rec=vix_at_rec,
        ))

    logger.info("복기 후보 %d건 (날짜: %d)", len(candidates), target_date_id)
    return candidates


def _estimate_regime(macro: FactMacroIndicator) -> str:
    """매크로 지표로 시장 체제를 간략 추정한다."""
    from src.ai.regime import classify_regime_from_record
    return classify_regime_from_record(macro)


# ---------------------------------------------------------------------------
# 복기 프롬프트 생성
# ---------------------------------------------------------------------------

RETROSPECTIVE_TOOL = {
    "name": "submit_retrospective",
    "description": "과거 예측 복기 결과를 제출한다.",
    "input_schema": {
        "type": "object",
        "required": ["retrospectives"],
        "properties": {
            "retrospectives": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["ticker", "analysis", "lesson", "category"],
                    "properties": {
                        "ticker": {"type": "string"},
                        "analysis": {
                            "type": "string",
                            "description": "성공/실패 원인 분석 (한국어, 200자 이내)",
                        },
                        "lesson": {
                            "type": "string",
                            "description": "핵심 교훈 한 줄 (한국어, 50자 이내)",
                        },
                        "category": {
                            "type": "string",
                            "enum": ["sector", "regime", "timing", "valuation", "general"],
                        },
                    },
                },
            },
        },
    },
}


def build_retrospective_prompt(
    candidates: list[RetrospectiveCandidate],
) -> str:
    """복기 프롬프트를 생성한다."""
    lines: list[str] = []
    w = lines.append

    w("당신은 과거 주식 예측을 복기하는 투자 분석가입니다.")
    w("각 예측에 대해 성공 또는 실패 원인을 구체적으로 분석하고,")
    w("향후 예측에 적용할 핵심 교훈을 정확히 1개 추출하세요.")
    w("")
    w("교훈 작성 규칙:")
    w("- 한국어로 50자 이내의 명령형 규칙으로 작성")
    w("- 구체적 조건과 행동을 포함 (예: 'VIX 25+ 시 기술주 신뢰도 7 이상 부여하지 말 것')")
    w("- 뻔한 말(분산투자 하세요 등) 금지, 이 케이스에서만 배울 수 있는 교훈만")
    w("")
    w("<retrospective_batch>")

    for i, c in enumerate(candidates, 1):
        result = "성공" if c.return_20d > 0 else "실패"
        decision = "추천" if c.ai_approved else "제외"

        w(f"## 예측 {i}: {c.ticker} ({decision})")
        w(f"- AI 결정: {decision} (신뢰도 {c.ai_confidence})")
        if c.ai_reason:
            reason_short = c.ai_reason[:300]
            w(f"- AI 근거: \"{reason_short}\"")
        if c.ai_target_price:
            w(f"- 목표가: ${c.ai_target_price:.2f} / 손절가: ${c.ai_stop_loss:.2f}" if c.ai_stop_loss else f"- 목표가: ${c.ai_target_price:.2f}")
        w(f"- 추천 시 가격: ${c.price_at_rec:.2f}")
        w(f"- 실제 20일 수익률: {c.return_20d:+.1f}% ({result})")
        w(f"- 기간 최대 상승: {c.max_gain_pct:+.1f}% / 최대 하락: {c.max_loss_pct:+.1f}%")
        if c.price_path:
            path_str = ", ".join(f"{r:+.1f}" for r in c.price_path[:10])
            if len(c.price_path) > 10:
                path_str += f", ... ({c.return_20d:+.1f}%)"
            w(f"- 가격 경로: [{path_str}]")
        if c.regime_at_rec:
            w(f"- 시장 체제: {c.regime_at_rec}, VIX: {c.vix_at_rec:.1f}" if c.vix_at_rec else f"- 시장 체제: {c.regime_at_rec}")
        if c.sector:
            w(f"- 섹터: {c.sector}")
        w("")

    w("</retrospective_batch>")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 복기 실행
# ---------------------------------------------------------------------------


def run_retrospective(session: Session, run_date: date) -> int:
    """예측 복기를 실행하고 교훈을 저장한다.

    Returns:
        복기 완료 건수.
    """
    from src.ai.lesson_store import LessonInput, store_lessons
    from src.config import get_settings

    run_date_id = date_to_id(run_date)
    settings = get_settings()

    if not settings.ai_enabled:
        logger.debug("AI 비활성화 — 복기 스킵")
        return 0

    candidates = find_retrospective_candidates(session, run_date_id)
    if not candidates:
        logger.info("복기 대상 없음")
        return 0

    total_done = 0

    # 배치 단위로 처리
    for batch_start in range(0, len(candidates), BATCH_SIZE):
        batch = candidates[batch_start:batch_start + BATCH_SIZE]
        prompt = build_retrospective_prompt(batch)

        result = _call_retrospective_ai(prompt, settings)
        if result is None:
            logger.warning("복기 AI 호출 실패 (배치 %d)", batch_start)
            continue

        retrospectives = result.get("retrospectives", [])
        ticker_map = {c.ticker: c for c in batch}

        lesson_inputs: list[LessonInput] = []

        for retro in retrospectives:
            ticker = retro.get("ticker", "")
            candidate = ticker_map.get(ticker)
            if candidate is None:
                continue

            # 복기 기록 저장
            retro_record = FactAIRetrospective(
                run_date_id=run_date_id,
                recommendation_id=candidate.recommendation_id,
                ticker=ticker,
                original_ai_reason=candidate.ai_reason,
                actual_return_20d=candidate.return_20d,
                max_gain_pct=candidate.max_gain_pct,
                max_loss_pct=candidate.max_loss_pct,
                price_path_summary=json.dumps(list(candidate.price_path)),
                retrospective_text=retro.get("analysis", ""),
                lesson_id=None,
            )
            session.add(retro_record)
            session.flush()

            # 교훈 입력 준비
            lesson_text = retro.get("lesson", "")
            category = retro.get("category", "general")
            if lesson_text:
                lesson_inputs.append(LessonInput(
                    lesson_text=lesson_text,
                    category=category,
                    source_recommendation_id=candidate.recommendation_id,
                    source_ticker=ticker,
                    source_sector=candidate.sector,
                    source_regime=candidate.regime_at_rec,
                    source_vix_level=candidate.vix_at_rec,
                    source_return_20d=candidate.return_20d,
                ))

            total_done += 1

        session.commit()

        # 교훈 저장
        if lesson_inputs:
            stored = store_lessons(session, lesson_inputs, run_date_id)
            logger.info("배치 교훈 %d건 저장", stored)

    logger.info("복기 완료: %d건", total_done)
    return total_done


def _call_retrospective_ai(prompt: str, settings: object) -> dict | None:
    """복기용 AI를 호출한다 (Tool Use)."""
    try:
        from anthropic import Anthropic

        client = Anthropic()
        from src.ai.constants import get_analysis_model
        model = getattr(settings, "ai_model_analysis", None) or get_analysis_model()
        timeout = getattr(settings, "ai_timeout", 300)

        message = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=[RETROSPECTIVE_TOOL],
            tool_choice={"type": "tool", "name": "submit_retrospective"},
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
        )

        for block in message.content:
            if block.type == "tool_use":
                logger.info("복기 Tool Use 완료")
                return block.input

        return None
    except ImportError:
        logger.warning("anthropic 패키지 미설치 — 복기 스킵")
        return None
    except Exception as e:
        logger.warning("복기 AI 호출 실패: %s", e)
        return None
