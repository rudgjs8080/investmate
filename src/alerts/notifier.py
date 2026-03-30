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


def send_weekly_summary(
    year: int,
    week_number: int,
    market_regime: str,
    sp500_return_pct: float | None = None,
    win_rate_pct: float | None = None,
    conviction_tickers: list[str] | None = None,
    channel: str | None = None,
) -> bool:
    """주간 리포트 요약을 알림으로 전송한다."""
    if not channel:
        logger.info("알림 채널 미설정, 스킵")
        return False

    regime_kr = {"bull": "강세", "bear": "약세", "range": "횡보", "crisis": "위기"}.get(
        market_regime, "횡보"
    )
    sp500_str = f"S&P500 {sp500_return_pct:+.1f}%" if sp500_return_pct is not None else ""
    win_str = f"승률 {win_rate_pct:.0f}%" if win_rate_pct is not None else ""
    tickers = ", ".join(conviction_tickers[:5]) if conviction_tickers else "-"

    message = (
        f"[Investmate] {year}-W{week_number:02d} 주간 리포트\n"
        f"시장: {regime_kr}"
        + (f" | {sp500_str}" if sp500_str else "") + "\n"
        + (f"주간 {win_str} | " if win_str else "")
        + f"확신 종목: {tickers}\n"
        f"상세: reports/weekly/{year}-W{week_number:02d}.md"
    )

    if channel == "telegram":
        return _send_telegram(message)
    elif channel == "slack":
        return _send_slack(message)
    elif channel == "email":
        return _send_email(message, date.today())
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


def _detect_smtp_provider(smtp_user: str) -> tuple[str, int]:
    """이메일 도메인으로 SMTP 서버를 자동 감지한다."""
    domain = smtp_user.split("@")[-1].lower()
    providers = {
        "naver.com": ("smtp.naver.com", 465),
        "gmail.com": ("smtp.gmail.com", 465),
        "hanmail.net": ("smtp.daum.net", 465),
        "daum.net": ("smtp.daum.net", 465),
        "kakao.com": ("smtp.kakao.com", 465),
        "outlook.com": ("smtp.office365.com", 587),
    }
    return providers.get(domain, ("smtp.gmail.com", 465))


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

        host, port = _detect_smtp_provider(smtp_user)
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logger.info("이메일 알림 전송 완료 (%s)", host)
        return True
    except Exception as e:
        logger.error("이메일 전송 실패: %s", e)
        return False


def send_weekly_report_email(
    year: int,
    week_number: int,
    market_oneliner: str,
    sp500_return_pct: float | None = None,
    vix_end: float | None = None,
    win_rate_pct: float | None = None,
    pdf_path: str | None = None,
    commentary_excerpt: str | None = None,
) -> bool:
    """주간 리포트 PDF를 첨부하여 이메일로 전송한다."""
    import os
    import smtplib
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email import encoders
    from pathlib import Path

    smtp_user = os.getenv("INVESTMATE_SMTP_USER")
    smtp_pass = os.getenv("INVESTMATE_SMTP_PASS")
    to_email = os.getenv("INVESTMATE_EMAIL_TO")
    if not all([smtp_user, smtp_pass, to_email]):
        logger.warning("이메일 설정 없음 — 주간 리포트 이메일 스킵")
        return False

    # HTML 본문
    sp500_str = f"S&P 500: {sp500_return_pct:+.1f}%" if sp500_return_pct is not None else ""
    vix_str = f"VIX: {vix_end:.1f}" if vix_end is not None else ""
    win_str = f"승률: {win_rate_pct:.0f}%" if win_rate_pct is not None else ""
    kpis = " | ".join(filter(None, [sp500_str, vix_str, win_str]))

    commentary_html = ""
    if commentary_excerpt:
        excerpt = commentary_excerpt[:300].replace("\n", "<br>")
        commentary_html = f"<p style='color:#6b7280;font-size:13px;margin-top:12px;'>{excerpt}...</p>"

    html_body = f"""
    <div style="font-family:'Malgun Gothic','NanumGothic',sans-serif;max-width:600px;margin:0 auto;">
        <div style="background:linear-gradient(135deg,#6366f1,#06b6d4);padding:24px;border-radius:12px 12px 0 0;">
            <h2 style="color:white;margin:0;">Investmate 주간 리포트</h2>
            <p style="color:rgba(255,255,255,0.85);margin:4px 0 0;">{year}-W{week_number:02d}</p>
        </div>
        <div style="background:white;padding:20px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px;">
            <p style="font-size:15px;font-weight:bold;color:#1f2937;">{market_oneliner}</p>
            <p style="color:#6b7280;font-size:13px;">{kpis}</p>
            {commentary_html}
            <hr style="border:none;border-top:1px solid #e5e7eb;margin:16px 0;">
            <p style="color:#9ca3af;font-size:11px;">
                {"PDF가 첨부되어 있습니다. 상세 내용은 첨부 파일을 확인하세요." if pdf_path else ""}
            </p>
            <p style="color:#9ca3af;font-size:10px;">※ 본 리포트는 투자 참고용이며 투자 권유가 아닙니다.</p>
        </div>
    </div>
    """

    try:
        msg = MIMEMultipart("mixed")
        msg["Subject"] = f"[Investmate] {year}-W{week_number:02d} 주간 투자 리포트"
        msg["From"] = smtp_user
        msg["To"] = to_email

        msg.attach(MIMEText(html_body, "html", "utf-8"))

        # PDF 첨부
        if pdf_path:
            pdf_file = Path(pdf_path)
            if pdf_file.exists():
                part = MIMEBase("application", "pdf")
                part.set_payload(pdf_file.read_bytes())
                encoders.encode_base64(part)
                part.add_header(
                    "Content-Disposition",
                    "attachment",
                    filename=pdf_file.name,
                )
                msg.attach(part)
                logger.info("PDF 첨부 완료: %s", pdf_file.name)

        host, port = _detect_smtp_provider(smtp_user)
        with smtplib.SMTP_SSL(host, port) as server:
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)

        logger.info("주간 리포트 이메일 전송 완료 → %s (%s)", to_email, host)
        return True
    except Exception as e:
        logger.error("주간 리포트 이메일 전송 실패: %s", e)
        return False
