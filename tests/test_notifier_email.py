"""이메일 알림 테스트 — SMTP 자동감지, PDF 첨부."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.alerts.notifier import (
    _detect_smtp_provider,
    send_weekly_report_email,
)


def test_detect_smtp_naver():
    host, port = _detect_smtp_provider("user@naver.com")
    assert host == "smtp.naver.com"
    assert port == 465


def test_detect_smtp_gmail():
    host, port = _detect_smtp_provider("user@gmail.com")
    assert host == "smtp.gmail.com"
    assert port == 465


def test_detect_smtp_daum():
    host, port = _detect_smtp_provider("user@daum.net")
    assert host == "smtp.daum.net"
    assert port == 465


def test_detect_smtp_kakao():
    host, port = _detect_smtp_provider("user@kakao.com")
    assert host == "smtp.kakao.com"
    assert port == 465


def test_detect_smtp_unknown_defaults_gmail():
    host, port = _detect_smtp_provider("user@custom.org")
    assert host == "smtp.gmail.com"
    assert port == 465


def test_send_weekly_email_no_smtp_config():
    """SMTP 미설정 시 False 반환."""
    with patch.dict("os.environ", {}, clear=True):
        result = send_weekly_report_email(
            year=2026, week_number=13,
            market_oneliner="테스트",
        )
    assert result is False


@patch("smtplib.SMTP_SSL")
def test_send_weekly_email_with_pdf(mock_smtp_ssl, tmp_path):
    """PDF 첨부 이메일 전송 테스트 (mock SMTP)."""
    pdf_path = tmp_path / "test.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test content")

    mock_server = MagicMock()
    mock_smtp_ssl.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_ssl.return_value.__exit__ = MagicMock(return_value=False)

    with patch.dict("os.environ", {
        "INVESTMATE_SMTP_USER": "test@naver.com",
        "INVESTMATE_SMTP_PASS": "testpass",
        "INVESTMATE_EMAIL_TO": "kkh_8080@naver.com",
    }):
        result = send_weekly_report_email(
            year=2026, week_number=13,
            market_oneliner="강세 지속",
            sp500_return_pct=1.5,
            vix_end=14.5,
            win_rate_pct=60.0,
            pdf_path=str(pdf_path),
            commentary_excerpt="테스트 코멘터리",
        )

    mock_smtp_ssl.assert_called_once_with("smtp.naver.com", 465)


@patch("smtplib.SMTP_SSL")
def test_send_weekly_email_no_pdf(mock_smtp_ssl):
    """PDF 없이도 이메일 전송 성공."""
    mock_server = MagicMock()
    mock_smtp_ssl.return_value.__enter__ = MagicMock(return_value=mock_server)
    mock_smtp_ssl.return_value.__exit__ = MagicMock(return_value=False)

    with patch.dict("os.environ", {
        "INVESTMATE_SMTP_USER": "test@gmail.com",
        "INVESTMATE_SMTP_PASS": "testpass",
        "INVESTMATE_EMAIL_TO": "recipient@test.com",
    }):
        result = send_weekly_report_email(
            year=2026, week_number=13,
            market_oneliner="횡보",
            pdf_path=None,
        )

    mock_smtp_ssl.assert_called_once_with("smtp.gmail.com", 465)
