"""리포트 모듈 공용 상수.

분석 임계값, 차트 색상/크기, PDF 폰트 크기 등
여러 리포트 파일에서 공유하는 상수를 정의한다.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 분석 임계값
# ---------------------------------------------------------------------------
RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
RSI_STRONG_BUY = 35
RSI_STRONG_SELL = 65

PER_LOW = 15
PER_FAIR = 20
PER_HIGH = 30

DEBT_RATIO_HIGH = 0.6
ROE_HIGH = 0.15

# ---------------------------------------------------------------------------
# 차트
# ---------------------------------------------------------------------------
CHART_PRIMARY_COLOR = "#6366f1"
CHART_WARNING_COLOR = "#f59e0b"
CHART_DANGER_COLOR = "#ef4444"
CHART_SUCCESS_COLOR = "#10b981"
CHART_NEUTRAL_COLOR = "#94a3b8"

CHART_DEFAULT_SIZE = (5, 2.5)
CHART_DPI = 200

# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
PDF_FONT_TITLE = 24
PDF_FONT_SECTION = 15
PDF_FONT_SUBSECTION = 12
PDF_FONT_BODY = 10
PDF_FONT_SMALL = 9
PDF_FONT_TINY = 7

PDF_COLOR_PRIMARY = (99, 102, 241)
PDF_COLOR_TEXT = (55, 65, 81)
PDF_COLOR_MUTED = (107, 114, 128)
