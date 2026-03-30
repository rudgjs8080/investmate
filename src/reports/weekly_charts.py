"""주간 리포트 차트 생성 — matplotlib 기반 정적 차트."""

from __future__ import annotations

import logging
import platform
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _setup_korean_font() -> None:
    """matplotlib 한글 폰트를 설정한다."""
    import matplotlib

    matplotlib.use("Agg")  # headless
    import matplotlib.pyplot as plt

    if platform.system() == "Windows":
        plt.rcParams["font.family"] = "Malgun Gothic"
    else:
        plt.rcParams["font.family"] = "NanumGothic"
    plt.rcParams["axes.unicode_minus"] = False


def generate_vix_chart(
    vix_series: tuple[tuple[str, float | None], ...],
    output_dir: Path,
) -> Path | None:
    """VIX 주간 추이 라인 차트."""
    if not vix_series:
        return None
    try:
        _setup_korean_font()
        import matplotlib.pyplot as plt

        dates = [d[5:] for d, _ in vix_series]  # MM-DD
        values = [v if v is not None else 0 for _, v in vix_series]

        fig, ax = plt.subplots(figsize=(5, 2.5))
        ax.plot(dates, values, color="#6366f1", linewidth=2, marker="o", markersize=4)
        ax.fill_between(dates, values, alpha=0.1, color="#6366f1")
        ax.axhline(y=20, color="#f59e0b", linestyle="--", linewidth=0.8, alpha=0.6)
        ax.axhline(y=25, color="#ef4444", linestyle="--", linewidth=0.8, alpha=0.6)
        ax.set_title("VIX 주간 추이", fontsize=10, fontweight="bold")
        ax.set_ylabel("VIX", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3)
        fig.tight_layout()

        path = output_dir / "vix_chart.png"
        fig.savefig(str(path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        logger.warning("VIX 차트 생성 실패: %s", e)
        return None


def generate_signal_chart(
    daily_buy: tuple[tuple[str, int], ...],
    daily_sell: tuple[tuple[str, int], ...],
    output_dir: Path,
) -> Path | None:
    """매수/매도 시그널 스택 바 차트."""
    if not daily_buy:
        return None
    try:
        _setup_korean_font()
        import matplotlib.pyplot as plt
        import numpy as np

        dates = [d[5:] for d, _ in daily_buy]
        buys = [c for _, c in daily_buy]
        sells = [c for _, c in daily_sell]

        x = np.arange(len(dates))
        width = 0.6

        fig, ax = plt.subplots(figsize=(5, 2.5))
        ax.bar(x, buys, width, label="매수", color="#10b981")
        ax.bar(x, sells, width, bottom=buys, label="매도", color="#ef4444")
        ax.set_xticks(x)
        ax.set_xticklabels(dates, fontsize=7)
        ax.set_title("시그널 트렌드", fontsize=10, fontweight="bold")
        ax.legend(fontsize=7, loc="upper right")
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3, axis="y")
        fig.tight_layout()

        path = output_dir / "signal_chart.png"
        fig.savefig(str(path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        logger.warning("시그널 차트 생성 실패: %s", e)
        return None


def generate_sector_chart(
    sectors: tuple,
    output_dir: Path,
) -> Path | None:
    """섹터별 주간 수익률 수평 바 차트."""
    if not sectors:
        return None
    try:
        _setup_korean_font()
        import matplotlib.pyplot as plt

        valid = [(s.sector[:10], s.weekly_return_pct) for s in sectors if s.weekly_return_pct is not None]
        if not valid:
            return None
        names, returns = zip(*valid)
        colors = ["#10b981" if r > 0 else "#ef4444" for r in returns]

        fig, ax = plt.subplots(figsize=(5, max(2.5, len(names) * 0.3)))
        ax.barh(names, returns, color=colors)
        ax.set_title("섹터별 주간 수익률 (%)", fontsize=10, fontweight="bold")
        ax.tick_params(labelsize=7)
        ax.axvline(x=0, color="gray", linewidth=0.5)
        ax.grid(True, alpha=0.3, axis="x")
        fig.tight_layout()

        path = output_dir / "sector_chart.png"
        fig.savefig(str(path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        logger.warning("섹터 차트 생성 실패: %s", e)
        return None


def generate_macro_score_chart(
    daily_scores: tuple[tuple[str, int | None], ...],
    output_dir: Path,
) -> Path | None:
    """시장 점수 일별 추이."""
    if not daily_scores:
        return None
    try:
        _setup_korean_font()
        import matplotlib.pyplot as plt

        dates = [d[5:] for d, _ in daily_scores]
        scores = [s if s is not None else 5 for _, s in daily_scores]
        colors = ["#10b981" if s >= 7 else ("#ef4444" if s <= 3 else "#6366f1") for s in scores]

        fig, ax = plt.subplots(figsize=(5, 2.5))
        ax.bar(dates, scores, color=colors)
        ax.set_ylim(0, 10)
        ax.set_title("시장 점수 추이 (1-10)", fontsize=10, fontweight="bold")
        ax.tick_params(labelsize=7)
        ax.grid(True, alpha=0.3, axis="y")
        fig.tight_layout()

        path = output_dir / "macro_score_chart.png"
        fig.savefig(str(path), dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path
    except Exception as e:
        logger.warning("매크로 점수 차트 생성 실패: %s", e)
        return None


def generate_all_charts(report) -> tuple[Path, list[Path]]:
    """모든 차트를 생성한다.

    Returns:
        (temp_dir, chart_paths) — 호출자가 temp_dir 정리 책임.
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="investmate_charts_"))
    charts: list[Path] = []

    vix = generate_vix_chart(report.macro_summary.vix_series, temp_dir)
    if vix:
        charts.append(vix)

    sig = generate_signal_chart(
        report.signal_trend.daily_buy_counts,
        report.signal_trend.daily_sell_counts,
        temp_dir,
    )
    if sig:
        charts.append(sig)

    sec = generate_sector_chart(report.sector_rotation, temp_dir)
    if sec:
        charts.append(sec)

    macro = generate_macro_score_chart(report.macro_summary.daily_scores, temp_dir)
    if macro:
        charts.append(macro)

    return temp_dir, charts
