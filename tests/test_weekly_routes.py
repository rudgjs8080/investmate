"""주간 리포트 웹 라우트 테스트."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.web.routes.weekly_report import _load_report_summary


@pytest.fixture
def sample_json(tmp_path):
    """테스트용 주간 리포트 JSON 생성."""
    data = {
        "year": 2026,
        "week_number": 13,
        "week_start": "2026-03-23",
        "week_end": "2026-03-27",
        "trading_days": 5,
        "executive_summary": {
            "market_oneliner": "테스트",
            "sp500_weekly_return_pct": 1.5,
            "vix_end": 15.0,
            "regime_end": "bull",
            "weekly_win_rate_pct": 60.0,
            "weekly_avg_return_pct": 1.2,
        },
        "performance_review": {
            "total_unique_picks": 10,
            "win_count": 6,
            "loss_count": 4,
        },
        "conviction_picks": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
    }
    path = tmp_path / "2026-W13.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_load_report_summary(sample_json):
    """JSON에서 요약 데이터를 정상 추출한다."""
    summary = _load_report_summary(sample_json)
    assert summary is not None
    assert summary["week_id"] == "2026-W13"
    assert summary["sp500_return"] == 1.5
    assert summary["regime"] == "bull"
    assert summary["total_picks"] == 10
    assert summary["conviction_count"] == 2


def test_load_report_summary_missing_file():
    """존재하지 않는 파일은 None을 반환한다."""
    result = _load_report_summary(Path("/nonexistent/file.json"))
    assert result is None


def test_load_report_summary_invalid_json(tmp_path):
    """잘못된 JSON은 None을 반환한다."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    result = _load_report_summary(bad)
    assert result is None
