"""데이터 품질 검증 레이어.

수집된 데이터가 팩트 테이블에 적재되기 전에 통계적·논리적 검증을 수행한다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.data.schemas import DailyPriceData, MacroData

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationIssue:
    """개별 검증 이슈."""

    level: str  # "warning" | "error"
    field: str
    message: str
    ticker: str | None = None


@dataclass
class ValidationResult:
    """검증 결과."""

    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(i.level == "error" for i in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "warning")

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.level == "error")

    def add_warning(self, field: str, message: str, ticker: str | None = None) -> None:
        self.issues.append(ValidationIssue("warning", field, message, ticker))

    def add_error(self, field: str, message: str, ticker: str | None = None) -> None:
        self.issues.append(ValidationIssue("error", field, message, ticker))

    def log_summary(self) -> None:
        if not self.issues:
            return
        for issue in self.issues:
            prefix = f"[{issue.ticker}] " if issue.ticker else ""
            if issue.level == "error":
                logger.error("검증 실패: %s%s — %s", prefix, issue.field, issue.message)
            else:
                logger.warning("검증 경고: %s%s — %s", prefix, issue.field, issue.message)


class PriceValidator:
    """가격 데이터 검증.

    수집된 가격 데이터의 논리적 일관성과 이상치를 검사한다.
    """

    # 전일 대비 가격 변동 허용 범위 (±50%)
    MAX_PRICE_CHANGE_RATIO = 0.5
    # 전일 대비 거래량 급변 배수 (100배)
    MAX_VOLUME_SPIKE_RATIO = 100

    def validate(
        self, prices: list[DailyPriceData], ticker: str | None = None,
    ) -> ValidationResult:
        result = ValidationResult()

        if not prices:
            return result

        for i, price in enumerate(prices):
            # high >= low 검증 (Pydantic model_validator에서도 체크하지만 이중 방어)
            if price.high < price.low:
                result.add_error("high/low", f"high({price.high}) < low({price.low})", ticker)

            # open/close가 high/low 범위 안인지
            if price.open > price.high or price.open < price.low:
                result.add_warning("open", f"open({price.open})이 high/low 범위 밖", ticker)
            if price.close > price.high or price.close < price.low:
                result.add_warning("close", f"close({price.close})이 high/low 범위 밖", ticker)

            # 전일 대비 급격한 변동 감지 (주식분할 후보)
            if i > 0:
                prev_close = prices[i - 1].close
                if prev_close > 0:
                    change_ratio = abs(price.close - prev_close) / prev_close
                    if change_ratio > self.MAX_PRICE_CHANGE_RATIO:
                        result.add_warning(
                            "close",
                            f"전일 대비 {change_ratio:.0%} 변동 (주식분할/오류 가능성)",
                            ticker,
                        )

                # 거래량 급변 감지
                prev_vol = prices[i - 1].volume
                if prev_vol > 0 and price.volume > prev_vol * self.MAX_VOLUME_SPIKE_RATIO:
                    result.add_warning(
                        "volume",
                        f"전일 대비 거래량 {price.volume / prev_vol:.0f}배 급증",
                        ticker,
                    )

        return result


class MacroValidator:
    """매크로 데이터 검증.

    VIX, 금리 등 매크로 지표의 범위와 일관성을 검사한다.
    """

    VALID_RANGES: dict[str, tuple[float, float]] = {
        "vix": (0.0, 100.0),
        "us_10y_yield": (-5.0, 30.0),
        "us_13w_yield": (-5.0, 30.0),
        "dollar_index": (50.0, 200.0),
        "sp500_close": (100.0, 100000.0),
    }

    def validate(self, macro: MacroData) -> ValidationResult:
        result = ValidationResult()

        for field_name, (low, high) in self.VALID_RANGES.items():
            val = getattr(macro, field_name, None)
            if val is not None and not (low <= val <= high):
                result.add_error(
                    field_name,
                    f"값({val})이 허용 범위({low}~{high}) 밖",
                )

        # yield_spread 교차 검증
        if macro.us_10y_yield is not None and macro.us_13w_yield is not None:
            expected_spread = round(macro.us_10y_yield - macro.us_13w_yield, 4)
            if macro.yield_spread is not None:
                diff = abs(macro.yield_spread - expected_spread)
                if diff > 0.01:
                    result.add_warning(
                        "yield_spread",
                        f"계산값({expected_spread})과 저장값({macro.yield_spread}) 불일치",
                    )

        # 필수 지표 누락 체크
        critical_fields = ["vix", "sp500_close"]
        for f in critical_fields:
            if getattr(macro, f, None) is None:
                result.add_warning(f, "필수 매크로 지표 누락")

        return result
