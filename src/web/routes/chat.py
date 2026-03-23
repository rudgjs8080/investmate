"""Claude 채팅 API 라우트 — 캐싱 + 멀티턴 + 모델 라우팅."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import DimStock, FactDailyRecommendation, FactMacroIndicator
from src.db.repository import MacroRepository, RecommendationRepository
from src.web.deps import get_db

logger = logging.getLogger(__name__)
router = APIRouter()

CHAT_TIMEOUT = 60  # 채팅 응답 타임아웃 (초)

# ---------------------------------------------------------------------------
# 모듈 수준 캐시 + 멀티턴 히스토리
# ---------------------------------------------------------------------------
_chat_cache: dict[str, tuple[str, float]] = {}
_chat_history: dict[str, list[dict]] = {}
CHAT_CACHE_TTL = 3600  # 1시간
MAX_HISTORY_TURNS = 10


@router.post("/api/chat")
async def chat(request: Request, db: Session = Depends(get_db)):
    """Claude에게 질문하고 응답을 반환한다 (캐싱 + 멀티턴 지원)."""
    try:
        body = await request.json()
        message = body.get("message", "").strip()
        session_id = body.get("session_id", "default")
    except Exception:
        return JSONResponse({"error": "잘못된 요청"}, status_code=400)

    if not message:
        return JSONResponse({"error": "메시지를 입력하세요"}, status_code=400)

    if len(message) > 1000:
        return JSONResponse({"error": "메시지가 너무 깁니다 (최대 1000자)"}, status_code=400)

    # DB 컨텍스트 구성
    context = _build_chat_context(db, message)

    # 캐시 확인 (context + message 해시)
    cache_key = hashlib.sha256(f"{context}:{message}".encode()).hexdigest()[:16]
    cached = _chat_cache.get(cache_key)
    if cached and time.time() - cached[1] < CHAT_CACHE_TTL:
        return {"response": cached[0], "session_id": session_id, "cached": True}

    # 멀티턴 히스토리 구축
    history = _chat_history.get(session_id, [])
    history.append({"role": "user", "content": message})

    system_msg = (
        "당신은 S&P 500 AI 투자 분석 어시스턴트입니다.\n\n"
        f"현재 시장 데이터:\n{context}\n\n"
        "위 데이터를 참고하여 한국어로 간결하게 답변해주세요. "
        "투자 참고용임을 명시하세요."
    )

    # Claude 호출 (Anthropic SDK 우선 → CLI 폴백)
    try:
        answer = await asyncio.to_thread(
            _call_claude_with_history, system_msg, history, message,
        )
    except Exception as e:
        logger.warning("Chat Claude 호출 실패: %s", e)
        return JSONResponse({"error": f"AI 응답 실패: {str(e)}"}, status_code=500)

    if answer is None:
        return JSONResponse({"error": "AI 응답 시간 초과"}, status_code=504)

    # 히스토리 + 캐시 저장
    history.append({"role": "assistant", "content": answer})
    _chat_history[session_id] = history[-MAX_HISTORY_TURNS:]
    _chat_cache[cache_key] = (answer, time.time())

    return {"response": answer, "session_id": session_id, "cached": False}


def _call_claude_with_history(
    system_msg: str, history: list[dict], message: str,
) -> str | None:
    """Anthropic SDK (멀티턴) 우선, CLI 폴백."""
    from src.config import get_settings
    settings = get_settings()

    # 1순위: SDK 멀티턴
    try:
        from anthropic import Anthropic
        client = Anthropic()
        resp = client.messages.create(
            model=settings.ai_model_chat,
            max_tokens=1024,
            system=system_msg,
            messages=history[-MAX_HISTORY_TURNS:],
            timeout=CHAT_TIMEOUT,
        )
        return resp.content[0].text
    except ImportError:
        pass
    except Exception as e:
        logger.debug("SDK 채팅 실패, CLI 폴백: %s", e)

    # 2순위: CLI 싱글턴
    claude_path = shutil.which("claude")
    if not claude_path:
        return None

    full_prompt = f"{system_msg}\n\n사용자 질문: {message}"
    return _call_claude_cli(claude_path, full_prompt)


def _call_claude_cli(claude_path: str, prompt: str) -> str | None:
    """Claude CLI를 동기 호출한다."""
    env = os.environ.copy()
    node_path = shutil.which("node")
    if node_path:
        env["PATH"] = str(Path(node_path).parent) + os.pathsep + env.get("PATH", "")

    try:
        result = subprocess.run(
            [claude_path, "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CHAT_TIMEOUT,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        logger.warning("Claude chat 실패 (code %d): %s", result.returncode, result.stderr[:200])
        return None
    except subprocess.TimeoutExpired:
        return None


def _build_chat_context(db: Session, message: str) -> str:
    """사용자 질문에 맞는 DB 컨텍스트를 구성한다."""
    parts = ["# Investmate 투자 분석 시스템 컨텍스트"]

    # 매크로 환경
    macro = MacroRepository.get_latest(db)
    if macro:
        from src.db.helpers import id_to_date
        try:
            d = id_to_date(macro.date_id)
            parts.append(f"\n## 시장 환경 ({d.isoformat()})")
        except Exception:
            parts.append("\n## 시장 환경")
        parts.append(f"- VIX: {float(macro.vix):.1f}" if macro.vix else "- VIX: -")
        parts.append(f"- S&P 500: {float(macro.sp500_close):,.0f}" if macro.sp500_close else "")
        parts.append(f"- 시장 점수: {macro.market_score}/10" if macro.market_score else "")

    # 최신 추천 요약
    if macro:
        recs = RecommendationRepository.get_by_date(db, macro.date_id)
        if recs:
            parts.append(f"\n## 오늘의 추천 ({len(recs)}종목)")
            for rec in recs[:10]:
                stock = db.execute(
                    select(DimStock).where(DimStock.stock_id == rec.stock_id)
                ).scalar_one_or_none()
                if stock:
                    from src.data.kr_names import get_kr_name
                    name = get_kr_name(stock.ticker, stock.name)
                    ai_str = ""
                    if rec.ai_approved is True:
                        ai_str = f" [AI 추천 {rec.ai_confidence or ''}/10]"
                    elif rec.ai_approved is False:
                        ai_str = " [AI 제외]"
                    parts.append(
                        f"- {rec.rank}. {stock.ticker} ({name}) "
                        f"종합 {float(rec.total_score):.1f} "
                        f"${float(rec.price_at_recommendation):,.0f}{ai_str}"
                    )
                    if rec.ai_reason:
                        parts.append(f"  AI: {rec.ai_reason[:100]}")

    # 특정 종목 언급 감지 → 상세 데이터 추가
    ticker_match = re.findall(r'\b([A-Z]{2,5})\b', message.upper())
    _NON_TICKERS = {"AI", "RSI", "MACD", "SMA", "VIX", "PER", "PBR", "ROE", "ETF", "TOP"}
    for t in ticker_match:
        if t in _NON_TICKERS:
            continue
        stock = db.execute(
            select(DimStock).where(DimStock.ticker == t)
        ).scalar_one_or_none()
        if stock:
            from src.data.kr_names import get_kr_name
            parts.append(f"\n## {t} ({get_kr_name(t, stock.name)}) 상세")
            # 최신 추천 데이터
            rec = db.execute(
                select(FactDailyRecommendation)
                .where(FactDailyRecommendation.stock_id == stock.stock_id)
                .order_by(FactDailyRecommendation.run_date_id.desc())
                .limit(1)
            ).scalar_one_or_none()
            if rec:
                parts.append(f"- 종합 {float(rec.total_score):.1f}/10 (기술 {float(rec.technical_score):.1f} / 기본 {float(rec.fundamental_score):.1f} / 수급 {float(rec.smart_money_score):.1f})")
                parts.append(f"- 추천근거: {rec.recommendation_reason}")
                if rec.ai_reason:
                    parts.append(f"- AI 분석: {rec.ai_reason}")
                if rec.ai_entry_strategy:
                    parts.append(f"- 매수전략: {rec.ai_entry_strategy}")
                if rec.ai_exit_strategy:
                    parts.append(f"- 익절/손절: {rec.ai_exit_strategy}")

    return "\n".join(parts)
