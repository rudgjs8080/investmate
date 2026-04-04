"""강화 데이터 수집 — 내부자, 기관, 애널리스트, 실적, 공매도."""

from __future__ import annotations

import logging
import time
from datetime import date

import yfinance as yf
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from sqlalchemy.orm import Session

from src.data.utils import safe_float
from src.db.helpers import date_to_id, ensure_date_ids, id_to_date
from src.db.models import DimStock
from src.db.repository import (
    AnalystConsensusRepository,
    EarningsSurpriseRepository,
    InsiderTradeRepository,
    InstitutionalHoldingRepository,
    ValuationRepository,
)

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(min=1, max=5),
    retry=retry_if_exception_type((ConnectionError, TimeoutError, OSError)),
    reraise=True,
)
def _get_ticker_info(ticker: str):
    """리트라이 로직이 적용된 yfinance Ticker 생성."""
    return yf.Ticker(ticker)


def collect_insider_trades(ticker: str) -> list[dict]:
    """내부자 거래 데이터를 수집한다."""
    try:
        t = _get_ticker_info(ticker)
        df = t.insider_transactions
        if df is None or df.empty:
            return []

        trades = []
        for _, row in df.iterrows():
            trade_date = row.get("Start Date") or row.get("Date")
            if trade_date is None:
                continue
            if hasattr(trade_date, "date"):
                trade_date = trade_date.date()

            trades.append({
                "date_id": date_to_id(trade_date),
                "insider_name": str(row.get("Insider", "Unknown")),
                "insider_title": str(row.get("Position", "")) or None,
                "transaction_type": str(row.get("Transaction", "Unknown")),
                "shares": int(row.get("Shares", 0)),
                "value": safe_float(row.get("Value")),
            })

        return trades
    except Exception as e:
        logger.warning("내부자 거래 수집 실패 [%s]: %s", ticker, e)
        return []


def collect_institutional_holdings(ticker: str) -> list[dict]:
    """기관 보유 데이터를 수집한다."""
    try:
        t = _get_ticker_info(ticker)
        df = t.institutional_holders
        if df is None or df.empty:
            return []

        today_id = date_to_id(date.today())
        holdings = []
        for _, row in df.iterrows():
            report_date = row.get("Date Reported")
            did = date_to_id(report_date.date()) if report_date and hasattr(report_date, "date") else today_id

            holdings.append({
                "date_id": did,
                "institution_name": str(row.get("Holder", "Unknown")),
                "shares": int(row.get("Shares", 0)),
                "value": safe_float(row.get("Value")),
                "pct_of_shares": safe_float(row.get("% Out")),
            })

        return holdings
    except Exception as e:
        logger.warning("기관 보유 수집 실패 [%s]: %s", ticker, e)
        return []


def collect_analyst_consensus(ticker: str) -> dict | None:
    """애널리스트 컨센서스를 수집한다."""
    try:
        t = _get_ticker_info(ticker)
        recs = t.recommendations
        if recs is None or recs.empty:
            return None

        # 최신 행 사용
        latest = recs.iloc[-1] if len(recs) > 0 else None
        if latest is None:
            return None

        result = {
            "strong_buy": int(latest.get("strongBuy", 0)),
            "buy": int(latest.get("buy", 0)),
            "hold": int(latest.get("hold", 0)),
            "sell": int(latest.get("sell", 0)),
            "strong_sell": int(latest.get("strongSell", 0)),
        }

        # 목표가
        try:
            targets = t.analyst_price_targets
            if targets is not None:
                if hasattr(targets, "get"):
                    result["target_mean"] = safe_float(targets.get("mean"))
                    result["target_high"] = safe_float(targets.get("high"))
                    result["target_low"] = safe_float(targets.get("low"))
                    result["target_median"] = safe_float(targets.get("median"))
        except Exception:
            pass

        return result
    except Exception as e:
        logger.warning("애널리스트 수집 실패 [%s]: %s", ticker, e)
        return None


def collect_earnings_surprises(ticker: str) -> list[dict]:
    """실적 서프라이즈 데이터를 수집한다."""
    try:
        t = _get_ticker_info(ticker)
        eh = t.earnings_history
        if eh is None or eh.empty:
            return []

        surprises = []
        for _, row in eh.iterrows():
            report_date = row.get("reportDate") or row.name
            if hasattr(report_date, "date"):
                report_date = report_date.date()
            elif not isinstance(report_date, date):
                continue

            quarter = row.get("quarter")
            period = f"{report_date.year}Q{(report_date.month - 1) // 3 + 1}" if quarter is None else str(quarter)

            surprises.append({
                "date_id": date_to_id(report_date),
                "period": period,
                "eps_estimate": safe_float(row.get("epsEstimate")),
                "eps_actual": safe_float(row.get("epsActual")),
                "surprise_pct": safe_float(row.get("surprisePercent")),
            })

        return surprises
    except Exception as e:
        logger.warning("실적 서프라이즈 수집 실패 [%s]: %s", ticker, e)
        return []


def collect_short_interest(ticker: str) -> dict:
    """공매도 데이터를 수집한다."""
    try:
        info = _get_ticker_info(ticker).info
        return {
            "short_ratio": safe_float(info.get("shortRatio")),
            "short_pct_of_float": safe_float(info.get("shortPercentOfFloat")),
        }
    except Exception as e:
        logger.warning("공매도 수집 실패 [%s]: %s", ticker, e)
        return {}


def collect_all_enhanced(
    session: Session,
    stocks: list[DimStock],
    target_date: date,
    batch_size: int = 50,
) -> dict[str, int]:
    """전체 종목의 강화 데이터를 배치 수집한다.

    Returns:
        {"insider": N, "institutional": N, "analyst": N, "earnings": N}
    """
    counts = {"insider": 0, "institutional": 0, "analyst": 0, "earnings": 0}
    today_id = date_to_id(target_date)

    for i, stock in enumerate(stocks):
        if i % batch_size == 0 and i > 0:
            logger.info("강화 수집 %d/%d...", i, len(stocks))
            time.sleep(1.0)

        ticker = stock.ticker
        sid = stock.stock_id

        try:
            # 내부자 거래
            trades = collect_insider_trades(ticker)
            if trades:
                all_dates = [date.today()]
                for t in trades:
                    try:
                        all_dates.append(id_to_date(t["date_id"]))
                    except Exception:
                        pass
                ensure_date_ids(session, all_dates)
                counts["insider"] += InsiderTradeRepository.upsert_batch(session, sid, trades)

            # 기관 보유
            holdings = collect_institutional_holdings(ticker)
            if holdings:
                counts["institutional"] += InstitutionalHoldingRepository.upsert_batch(
                    session, sid, holdings
                )

            # 애널리스트
            consensus = collect_analyst_consensus(ticker)
            if consensus:
                ensure_date_ids(session, [target_date])
                AnalystConsensusRepository.upsert(session, sid, today_id, consensus)
                counts["analyst"] += 1

            # 실적 서프라이즈
            surprises = collect_earnings_surprises(ticker)
            if surprises:
                all_dates = []
                for s in surprises:
                    try:
                        all_dates.append(id_to_date(s["date_id"]))
                    except Exception:
                        pass
                if all_dates:
                    ensure_date_ids(session, all_dates)
                counts["earnings"] += EarningsSurpriseRepository.upsert(
                    session, sid, surprises
                )

            # 공매도 → FactValuation 업데이트
            short_data = collect_short_interest(ticker)
            if short_data.get("short_ratio") or short_data.get("short_pct_of_float"):
                ensure_date_ids(session, [target_date])
                ValuationRepository.upsert(session, sid, [{
                    "date_id": today_id,
                    **{k: v for k, v in short_data.items() if v is not None},
                }])

        except Exception as e:
            logger.warning("강화 수집 실패 [%s]: %s", ticker, e)

    session.flush()
    logger.info("강화 수집 완료: %s", counts)
    return counts


