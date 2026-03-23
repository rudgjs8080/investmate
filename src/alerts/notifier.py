"""알림 모듈 -- 데일리 리포트 요약을 외부 채널로 전송한다."""

from __future__ import annotations

import logging
from datetime import date

logger = logging.getLogger(__name__)


def send_daily_summary(
    run_date: date,
    market_mood: str,
    top_tickers: list[str],
    market_score: int | None = None,
    channel: str | None = None,
    buy_signal_count: int = 0,
    sell_signal_count: int = 0,
    vix: float | None = None,
) -> bool:
    """데일리 리포트 요약을 알림으로 전송한다.

    Args:
        run_date: 실행일
        market_mood: 시장 분위기 (강세/중립/약세)
        top_tickers: 추천 종목 티커 리스트
        market_score: 시장 점수 (1-10)
        channel: 알림 채널 (email/telegram/slack)
        buy_signal_count: 매수 시그널 수
        sell_signal_count: 매도 시그널 수
        vix: VIX 지수

    Returns:
        전송 성공 여부
    """
    if not channel:
        logger.info("알림 채널 미설정, 스킵")
        return False

    tickers_str = ", ".join(top_tickers[:5])
    vix_str = f"VIX: {vix:.1f}" if vix is not None else ""
    signal_str = f"시그널: 매수 {buy_signal_count}건 / 매도 {sell_signal_count}건"
    message = (
        f"[Investmate] {run_date.isoformat()} 데일리 리포트\n"
        f"시장: {market_mood} ({market_score or '-'}/10)"
        + (f" | {vix_str}" if vix_str else "") + "\n"
        f"{signal_str}\n"
        f"추천: {tickers_str}\n"
        f"상세: reports/{run_date.isoformat()}.md"
    )

    if channel == "telegram":
        return _send_telegram(message)
    elif channel == "slack":
        return _send_slack(message)
    elif channel == "email":
        return _send_email(message, run_date)
    else:
        logger.warning("알 수 없는 알림 채널: %s", channel)
        return False


def _send_telegram(message: str) -> bool:
    """텔레그램 봇으로 메시지를 전송한다."""
    import os

    token = os.getenv("INVESTMATE_TELEGRAM_TOKEN")
    chat_id = os.getenv("INVESTMATE_TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("텔레그램 설정 없음 (INVESTMATE_TELEGRAM_TOKEN, INVESTMATE_TELEGRAM_CHAT_ID)")
        return False

    try:
        import requests
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(url, json={"chat_id": chat_id, "text": message}, timeout=10)
        resp.raise_for_status()
        logger.info("텔레그램 알림 전송 완료")
        return True
    except Exception as e:
        logger.error("텔레그램 전송 실패: %s", e)
        return False


def _send_slack(message: str) -> bool:
    """슬랙 웹훅으로 메시지를 전송한다."""
    import os

    webhook_url = os.getenv("INVESTMATE_SLACK_WEBHOOK")
    if not webhook_url:
        logger.warning("슬랙 설정 없음 (INVESTMATE_SLACK_WEBHOOK)")
        return False

    try:
        import requests
        resp = requests.post(webhook_url, json={"text": message}, timeout=10)
        resp.raise_for_status()
        logger.info("슬랙 알림 전송 완료")
        return True
    except Exception as e:
        logger.error("슬랙 전송 실패: %s", e)
        return False


def _send_email(message: str, run_date: date) -> bool:
    """이메일로 리포트를 전송한다."""
    import os
    import smtplib
    from email.mime.text import MIMEText

    smtp_user = os.getenv("INVESTMATE_SMTP_USER")
    smtp_pass = os.getenv("INVESTMATE_SMTP_PASS")
    to_email = os.getenv("INVESTMATE_EMAIL_TO")
    if not all([smtp_user, smtp_pass, to_email]):
        logger.warning("이메일 설정 없음 (INVESTMATE_SMTP_USER, INVESTMATE_SMTP_PASS, INVESTMATE_EMAIL_TO)")
        return False

    try:
        msg = MIMEText(message, "plain", "utf-8")
        msg["Subject"] = f"[Investmate] {run_date.isoformat()} 데일리 리포트"
        msg["From"] = smtp_user
        msg["To"] = to_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logger.info("이메일 알림 전송 완료")
        return True
    except Exception as e:
        logger.error("이메일 전송 실패: %s", e)
        return False
