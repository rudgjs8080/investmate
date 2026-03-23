"""알림 모듈 테스트."""

from datetime import date
from unittest.mock import MagicMock, patch

from src.alerts.notifier import send_daily_summary


class TestSendDailySummary:
    def test_no_channel_returns_false(self):
        result = send_daily_summary(
            run_date=date(2026, 3, 19),
            market_mood="강세",
            top_tickers=["AAPL", "MSFT"],
            channel=None,
        )
        assert result is False

    def test_unknown_channel_returns_false(self):
        result = send_daily_summary(
            run_date=date(2026, 3, 19),
            market_mood="약세",
            top_tickers=["AAPL"],
            channel="unknown_channel",
        )
        assert result is False

    @patch.dict("os.environ", {"INVESTMATE_TELEGRAM_TOKEN": "", "INVESTMATE_TELEGRAM_CHAT_ID": ""})
    def test_telegram_no_config_returns_false(self):
        result = send_daily_summary(
            run_date=date(2026, 3, 19),
            market_mood="중립",
            top_tickers=["AAPL"],
            channel="telegram",
        )
        assert result is False

    @patch.dict("os.environ", {"INVESTMATE_SLACK_WEBHOOK": ""})
    def test_slack_no_config_returns_false(self):
        result = send_daily_summary(
            run_date=date(2026, 3, 19),
            market_mood="중립",
            top_tickers=["AAPL"],
            channel="slack",
        )
        assert result is False

    @patch.dict("os.environ", {
        "INVESTMATE_SMTP_USER": "",
        "INVESTMATE_SMTP_PASS": "",
        "INVESTMATE_EMAIL_TO": "",
    })
    def test_email_no_config_returns_false(self):
        result = send_daily_summary(
            run_date=date(2026, 3, 19),
            market_mood="중립",
            top_tickers=["AAPL"],
            channel="email",
        )
        assert result is False

    @patch.dict("os.environ", {
        "INVESTMATE_TELEGRAM_TOKEN": "fake_token",
        "INVESTMATE_TELEGRAM_CHAT_ID": "12345",
    })
    @patch("requests.post")
    def test_telegram_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = send_daily_summary(
            run_date=date(2026, 3, 19),
            market_mood="강세",
            top_tickers=["AAPL", "MU"],
            market_score=8,
            channel="telegram",
        )
        assert result is True
        mock_post.assert_called_once()

    @patch.dict("os.environ", {
        "INVESTMATE_SLACK_WEBHOOK": "https://hooks.slack.com/test",
    })
    @patch("requests.post")
    def test_slack_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = send_daily_summary(
            run_date=date(2026, 3, 19),
            market_mood="약세",
            top_tickers=["EQT"],
            market_score=3,
            channel="slack",
        )
        assert result is True

    @patch.dict("os.environ", {
        "INVESTMATE_SMTP_USER": "test@gmail.com",
        "INVESTMATE_SMTP_PASS": "password",
        "INVESTMATE_EMAIL_TO": "recipient@gmail.com",
    })
    @patch("smtplib.SMTP_SSL")
    def test_email_success(self, mock_smtp_cls):
        mock_server = MagicMock()
        mock_smtp_cls.return_value.__enter__ = MagicMock(return_value=mock_server)
        mock_smtp_cls.return_value.__exit__ = MagicMock(return_value=False)

        result = send_daily_summary(
            run_date=date(2026, 3, 19),
            market_mood="강세",
            top_tickers=["AAPL"],
            market_score=8,
            channel="email",
        )
        assert result is True

    @patch.dict("os.environ", {
        "INVESTMATE_TELEGRAM_TOKEN": "token",
        "INVESTMATE_TELEGRAM_CHAT_ID": "123",
    })
    @patch("requests.post", side_effect=Exception("network error"))
    def test_telegram_failure(self, mock_post):
        result = send_daily_summary(
            run_date=date(2026, 3, 19),
            market_mood="중립",
            top_tickers=["AAPL"],
            channel="telegram",
        )
        assert result is False
