"""AI 목표가/손절가 캘리브레이션 — 과거 편향 기반 자동 보정."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    FactAIFeedback,
    FactCalibrationCell,
    FactDailyRecommendation,
    FactMacroIndicator,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalibrationResult:
    """캘리브레이션 결과."""

    target_adjustment: float = 1.0  # 목표가 보정 계수 (1.0 = 보정 없음)
    stop_adjustment: float = 1.0  # 손절가 보정 계수
    is_optimistic: bool = False  # 목표가 과대추정 성향
    is_pessimistic: bool = False  # 목표가 과소추정 성향
    sample_size: int = 0
    avg_target_error_pct: float = 0.0


def calculate_calibration(
    session: Session, cutoff_date_id: int | None = None,
) -> CalibrationResult:
    """과거 AI 예측의 편향을 분석하여 보정 계수를 계산한다.

    Args:
        session: DB 세션.
        cutoff_date_id: 이 날짜 이전 추천의 피드백만 사용 (look-ahead bias 방지).
            None이면 전체 피드백 사용 (하위 호환).

    Returns:
        CalibrationResult with adjustment factors.
    """
    stmt = (
        select(FactAIFeedback)
        .where(FactAIFeedback.ai_approved == True)  # noqa: E712
        .where(FactAIFeedback.target_error_pct.isnot(None))
    )
    if cutoff_date_id is not None:
        # look-ahead bias 방지: cutoff 이전 추천에 대한 피드백만 사용
        stmt = stmt.where(
            FactAIFeedback.recommendation_id.in_(
                select(FactDailyRecommendation.recommendation_id)
                .where(FactDailyRecommendation.run_date_id <= cutoff_date_id)
            )
        )
    feedbacks = session.execute(stmt).scalars().all()

    if len(feedbacks) < 5:
        return CalibrationResult(sample_size=len(feedbacks))

    errors = [float(f.target_error_pct) for f in feedbacks]
    avg_error = sum(errors) / len(errors)

    # 양수 에러 = 과대추정 (목표가 > 실제), 음수 = 과소추정
    is_optimistic = avg_error > 3.0  # 평균 3% 이상 과대추정
    is_pessimistic = avg_error < -3.0

    # 보정 계수: 과대추정 시 목표가를 낮춤
    if is_optimistic:
        target_adj = max(0.85, 1.0 - avg_error / 100)  # 최대 15% 하향
    elif is_pessimistic:
        target_adj = min(1.15, 1.0 - avg_error / 100)  # 최대 15% 상향
    else:
        target_adj = 1.0

    # 손절가: 실제로 손절 타격률 기반 보정
    stop_hits = [f for f in feedbacks if f.stop_hit is True]
    if len(stop_hits) > len(feedbacks) * 0.3:
        # 30% 이상 손절 타격 → 손절가가 너무 가까움 → 넓힘
        stop_adj = 0.95  # 5% 더 넓게
    else:
        stop_adj = 1.0

    return CalibrationResult(
        target_adjustment=round(target_adj, 3),
        stop_adjustment=round(stop_adj, 3),
        is_optimistic=is_optimistic,
        is_pessimistic=is_pessimistic,
        sample_size=len(feedbacks),
        avg_target_error_pct=round(avg_error, 2),
    )


def apply_calibration(parsed: list[dict], calibration: CalibrationResult) -> list[dict]:
    """AI 응답에 캘리브레이션을 적용한다.

    Args:
        parsed: parse_ai_response 결과.
        calibration: 캘리브레이션 결과.

    Returns:
        보정된 parsed 리스트 (원본 수정).
    """
    if calibration.sample_size < 5:
        return parsed  # 데이터 부족 → 보정 안 함

    for p in parsed:
        if not p.get("ai_approved"):
            continue

        if p.get("ai_target_price") and calibration.target_adjustment != 1.0:
            original = p["ai_target_price"]
            p["ai_target_price"] = round(original * calibration.target_adjustment, 2)
            logger.debug(
                "%s 목표가 보정: $%.0f → $%.0f (계수 %.3f)",
                p.get("ticker"), original, p["ai_target_price"], calibration.target_adjustment,
            )

        if p.get("ai_stop_loss") and calibration.stop_adjustment != 1.0:
            original = p["ai_stop_loss"]
            p["ai_stop_loss"] = round(original * calibration.stop_adjustment, 2)

    return parsed


# ---------------------------------------------------------------------------
# 조건별 캘리브레이션 (regime × sector × confidence × event)
# ---------------------------------------------------------------------------

CONFIDENCE_RANGES = {
    (1, 3): "1-3",
    (4, 6): "4-6",
    (7, 8): "7-8",
    (9, 10): "9-10",
}

MIN_CELL_SAMPLES = 3


@dataclass(frozen=True)
class CalibrationCell:
    """조건별 캘리브레이션 셀."""

    regime: str
    sector: str
    confidence_range: str
    has_event: bool
    sample_count: int
    win_rate: float
    avg_return: float


def _confidence_to_range(confidence: int) -> str:
    """신뢰도를 범위 문자열로 변환한다."""
    for (lo, hi), label in CONFIDENCE_RANGES.items():
        if lo <= confidence <= hi:
            return label
    return "1-3"


def _estimate_regime_from_macro(session: Session, date_id: int) -> str:
    """특정 날짜의 시장 체제를 매크로 데이터로 추정한다."""
    macro = session.scalar(
        select(FactMacroIndicator)
        .where(FactMacroIndicator.date_id <= date_id)
        .order_by(FactMacroIndicator.date_id.desc())
        .limit(1)
    )
    if macro is None:
        return "range"

    vix = float(macro.vix) if macro.vix else 20
    sp_close = float(macro.sp500_close) if macro.sp500_close else 0
    sp_sma20 = float(macro.sp500_sma20) if macro.sp500_sma20 else 0

    if vix > 30:
        return "crisis"
    if vix > 25 and sp_close < sp_sma20:
        return "bear"
    if vix < 20 and sp_close > sp_sma20:
        return "bull"
    return "range"


def build_condition_calibration(
    session: Session,
    cutoff_date_id: int,
) -> list[CalibrationCell]:
    """피드백 데이터를 조건별로 집계하여 캘리브레이션 셀을 생성/갱신한다.

    Args:
        cutoff_date_id: 이 날짜 이전 추천만 사용 (look-ahead 보호).

    Returns:
        생성된 CalibrationCell 리스트.
    """
    stmt = (
        select(FactAIFeedback)
        .where(
            FactAIFeedback.ai_approved == True,
            FactAIFeedback.return_20d.isnot(None),
            FactAIFeedback.ai_confidence.isnot(None),
            FactAIFeedback.run_date_id <= cutoff_date_id,
        )
    )
    feedbacks = list(session.scalars(stmt).all())

    if not feedbacks:
        return []

    # 매크로 캐시: date_id → regime
    regime_cache: dict[int, str] = {}

    # 셀 집계: (regime, sector, conf_range, has_event) → [return_20d, ...]
    cells: dict[tuple[str, str, str, bool], list[float]] = {}

    for fb in feedbacks:
        date_id = fb.run_date_id

        if date_id not in regime_cache:
            regime_cache[date_id] = _estimate_regime_from_macro(session, date_id)

        regime = regime_cache[date_id]
        sector = fb.sector or "Unknown"
        conf_range = _confidence_to_range(int(fb.ai_confidence))
        # 이벤트 여부: 단순 근사 (향후 확장 가능)
        has_event = False

        key = (regime, sector, conf_range, has_event)
        cells.setdefault(key, []).append(float(fb.return_20d))

    # DB 갱신
    result_cells: list[CalibrationCell] = []

    for (regime, sector, conf_range, has_event), returns in cells.items():
        if len(returns) < MIN_CELL_SAMPLES:
            continue

        win_count = sum(1 for r in returns if r > 0)
        win_rate = round(win_count / len(returns) * 100, 1)
        avg_ret = round(sum(returns) / len(returns), 2)

        cell = CalibrationCell(
            regime=regime,
            sector=sector,
            confidence_range=conf_range,
            has_event=has_event,
            sample_count=len(returns),
            win_rate=win_rate,
            avg_return=avg_ret,
        )
        result_cells.append(cell)

        # UPSERT
        existing = session.scalar(
            select(FactCalibrationCell).where(
                FactCalibrationCell.regime == regime,
                FactCalibrationCell.sector == sector,
                FactCalibrationCell.confidence_range == conf_range,
                FactCalibrationCell.has_event == has_event,
            )
        )
        if existing:
            existing.sample_count = len(returns)
            existing.win_rate = win_rate
            existing.avg_return = avg_ret
            existing.last_updated_id = cutoff_date_id
        else:
            session.add(FactCalibrationCell(
                regime=regime,
                sector=sector,
                confidence_range=conf_range,
                has_event=has_event,
                sample_count=len(returns),
                win_rate=win_rate,
                avg_return=avg_ret,
                last_updated_id=cutoff_date_id,
            ))

    session.commit()
    logger.info("조건별 캘리브레이션 %d셀 갱신", len(result_cells))
    return result_cells


def get_condition_calibration(
    session: Session,
    regime: str,
    sector: str,
    confidence: int,
    has_event: bool = False,
) -> float | None:
    """특정 조건의 실제 승률을 조회한다 (폴백 체인).

    폴백: 정확 → 이벤트 무시 → 섹터 무시 → 체제 무시.
    """
    conf_range = _confidence_to_range(confidence)

    # 1단계: 정확 매칭
    cell = _find_cell(session, regime, sector, conf_range, has_event)
    if cell and cell.sample_count >= MIN_CELL_SAMPLES:
        return float(cell.win_rate)

    # 2단계: 이벤트 무시
    for evt in [True, False]:
        cell = _find_cell(session, regime, sector, conf_range, evt)
        if cell and cell.sample_count >= MIN_CELL_SAMPLES:
            return float(cell.win_rate)

    # 3단계: 섹터 무시 → 같은 regime + conf_range 평균
    stmt = (
        select(
            func.sum(FactCalibrationCell.win_rate * FactCalibrationCell.sample_count),
            func.sum(FactCalibrationCell.sample_count),
        )
        .where(
            FactCalibrationCell.regime == regime,
            FactCalibrationCell.confidence_range == conf_range,
            FactCalibrationCell.sample_count >= MIN_CELL_SAMPLES,
        )
    )
    row = session.execute(stmt).one_or_none()
    if row and row[1] and row[1] >= MIN_CELL_SAMPLES:
        return round(float(row[0]) / float(row[1]), 1)

    # 4단계: 체제 무시 → conf_range만
    stmt = (
        select(
            func.sum(FactCalibrationCell.win_rate * FactCalibrationCell.sample_count),
            func.sum(FactCalibrationCell.sample_count),
        )
        .where(
            FactCalibrationCell.confidence_range == conf_range,
            FactCalibrationCell.sample_count >= MIN_CELL_SAMPLES,
        )
    )
    row = session.execute(stmt).one_or_none()
    if row and row[1] and row[1] >= MIN_CELL_SAMPLES:
        return round(float(row[0]) / float(row[1]), 1)

    return None


def _find_cell(
    session: Session,
    regime: str,
    sector: str,
    conf_range: str,
    has_event: bool,
) -> FactCalibrationCell | None:
    """정확한 조건의 캘리브레이션 셀을 조회한다."""
    return session.scalar(
        select(FactCalibrationCell).where(
            FactCalibrationCell.regime == regime,
            FactCalibrationCell.sector == sector,
            FactCalibrationCell.confidence_range == conf_range,
            FactCalibrationCell.has_event == has_event,
        )
    )


def format_calibration_for_prompt(
    session: Session,
    cutoff_date_id: int,
) -> str | None:
    """프롬프트에 삽입할 조건별 캘리브레이션 테이블을 생성한다."""
    cells = list(
        session.scalars(
            select(FactCalibrationCell)
            .where(FactCalibrationCell.sample_count >= MIN_CELL_SAMPLES)
            .order_by(FactCalibrationCell.sample_count.desc())
            .limit(30)
        ).all()
    )

    if not cells:
        return None

    lines: list[str] = []
    lines.append("| 체제 | 섹터 | 신뢰도 | 샘플 | 승률 | 평균수익 |")
    lines.append("|------|------|--------|------|------|----------|")

    for c in cells:
        lines.append(
            f"| {c.regime} | {c.sector} | {c.confidence_range} "
            f"| {c.sample_count} | {c.win_rate:.0f}% | {c.avg_return:+.1f}% |"
        )

    return "\n".join(lines)
