"""Claude Code CLI를 통한 AI 분석 자동 호출."""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

AI_TIMEOUT = 300  # 5분

# ---------------------------------------------------------------------------
# Tool Use 스키마: 구조화된 분석 결과를 보장한다
# ---------------------------------------------------------------------------
STOCK_ANALYSIS_TOOL = {
    "name": "submit_stock_analysis",
    "description": "S&P 500 종목 분석 결과를 제출합니다",
    "input_schema": {
        "type": "object",
        "required": ["approved", "excluded", "analysis"],
        "properties": {
            "approved": {"type": "array", "items": {"type": "string"}},
            "excluded": {"type": "array", "items": {"type": "string"}},
            "analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": [
                        "ticker", "reason", "confidence",
                        "risk_level", "target_price", "stop_loss",
                    ],
                    "properties": {
                        "ticker": {"type": "string"},
                        "reason": {"type": "string"},
                        "confidence": {
                            "type": "integer", "minimum": 1, "maximum": 10,
                        },
                        "risk_level": {
                            "type": "string",
                            "enum": ["LOW", "MEDIUM", "HIGH"],
                        },
                        "target_price": {"type": "number"},
                        "stop_loss": {"type": "number"},
                        "entry_strategy": {"type": "string"},
                        "exit_strategy": {"type": "string"},
                    },
                },
            },
            "portfolio_summary": {
                "type": "object",
                "properties": {
                    "market_outlook": {"type": "string"},
                    "overall_risk": {
                        "type": "string",
                        "enum": ["LOW", "MEDIUM", "HIGH"],
                    },
                },
            },
            "deep_dive": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "entry_plan": {"type": "string"},
                        "scenario_best": {"type": "string"},
                        "scenario_base": {"type": "string"},
                        "scenario_worst": {"type": "string"},
                        "catalysts": {
                            "type": "array", "items": {"type": "string"},
                        },
                        "allocation_pct": {"type": "number"},
                    },
                },
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Token / cost 유틸리티
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    """간이 토큰 추정."""
    korean = sum(1 for c in text if '\uac00' <= c <= '\ud7a3')
    other = len(text) - korean
    return korean // 2 + other // 4


def _log_usage(message: object) -> None:
    """SDK 응답의 usage 정보를 로깅한다."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return
    inp = getattr(usage, "input_tokens", 0)
    out = getattr(usage, "output_tokens", 0)
    cost = inp * 3 / 1e6 + out * 15 / 1e6
    logger.info("AI 비용: $%.4f (in=%d, out=%d)", cost, inp, out)


def is_claude_available() -> bool:
    """Claude Code CLI가 사용 가능한지 확인한다."""
    return shutil.which("claude") is not None


# ---------------------------------------------------------------------------
# Tool Use (1순위: 구조화 보장)
# ---------------------------------------------------------------------------

def run_claude_analysis_with_tools(
    prompt: str, timeout: int = AI_TIMEOUT, model: str | None = None,
) -> dict | None:
    """Tool Use로 구조화된 분석 결과를 받는다."""
    try:
        from anthropic import Anthropic

        client = Anthropic()
        message = client.messages.create(
            model=model or "claude-sonnet-4-20250514",
            max_tokens=8192,
            tools=[STOCK_ANALYSIS_TOOL],
            tool_choice={"type": "tool", "name": "submit_stock_analysis"},
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
        )
        _log_usage(message)
        for block in message.content:
            if block.type == "tool_use":
                logger.info("Tool Use 완료")
                return block.input
        return None
    except ImportError:
        logger.debug("anthropic 패키지 미설치")
        return None
    except Exception as e:
        logger.warning("Tool Use 분석 실패: %s", e)
        return None


# ---------------------------------------------------------------------------
# 스트리밍 (2순위)
# ---------------------------------------------------------------------------

def run_claude_analysis_streaming(
    prompt: str, timeout: int = AI_TIMEOUT, model: str | None = None,
) -> str | None:
    """스트리밍으로 응답을 수신한다."""
    try:
        from anthropic import Anthropic

        client = Anthropic()
        collected: list[str] = []
        with client.messages.stream(
            model=model or "claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                collected.append(text)
        return "".join(collected)
    except ImportError:
        return None
    except Exception as e:
        logger.warning("스트리밍 분석 실패: %s", e)
        return None


# ---------------------------------------------------------------------------
# SDK 일반 (3순위)
# ---------------------------------------------------------------------------

def run_claude_analysis_sdk(
    prompt: str, timeout: int = AI_TIMEOUT, model: str | None = None,
) -> str | None:
    """Anthropic SDK로 Claude 분석을 실행한다. API키 없으면 None."""
    try:
        from anthropic import Anthropic
        client = Anthropic()
        message = client.messages.create(
            model=model or "claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
        )
        _log_usage(message)
        return message.content[0].text
    except ImportError:
        logger.debug("anthropic 패키지 미설치, CLI 폴백")
        return None
    except Exception as e:
        logger.warning("SDK 분석 실패: %s", e)
        return None


# ---------------------------------------------------------------------------
# 통합 진입점
# ---------------------------------------------------------------------------

def run_analysis(
    prompt: str,
    timeout: int = AI_TIMEOUT,
    backend: str = "auto",
    model: str | None = None,
) -> dict | str | None:
    """AI 분석을 실행한다. Tool Use -> SDK 스트리밍 -> SDK 일반 -> CLI 순으로 시도.

    Returns:
        dict -- Tool Use 성공 (파싱 불필요)
        str  -- 텍스트 응답 (파싱 필요)
        None -- 모든 방법 실패
    """
    if backend in ("auto", "sdk"):
        # 1순위: Tool Use (구조화 보장)
        result = run_claude_analysis_with_tools(prompt, timeout, model)
        if result:
            return result

        # 2순위: SDK 스트리밍
        text = run_claude_analysis_streaming(prompt, timeout, model)
        if text:
            return text

        # 3순위: SDK 일반
        text = run_claude_analysis_sdk(prompt, timeout, model)
        if text:
            return text

        if backend == "sdk":
            return None  # SDK only mode, no fallback

    if backend in ("auto", "cli"):
        return run_claude_analysis(prompt, timeout)

    return None


def run_claude_analysis(prompt: str, timeout: int = AI_TIMEOUT) -> str | None:
    """Claude Code CLI를 비대화형 모드로 호출한다.

    Returns:
        AI 응답 문자열, 실패 시 None.
    """
    if not is_claude_available():
        logger.warning("Claude Code CLI가 설치되지 않았거나 PATH에 없습니다")
        return None

    try:
        # Windows 호환: claude.CMD + node PATH 보장
        import os
        claude_path = shutil.which("claude")
        if not claude_path:
            logger.warning("Claude Code CLI를 찾을 수 없습니다")
            return None

        env = os.environ.copy()
        # node가 PATH에 없을 수 있으므로 보장
        node_path = shutil.which("node")
        if node_path:
            node_dir = str(Path(node_path).parent)
            env["PATH"] = node_dir + os.pathsep + env.get("PATH", "")

        result = subprocess.run(
            [claude_path, "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            encoding="utf-8",
            errors="replace",
        )

        if result.returncode == 0 and result.stdout.strip():
            logger.info("Claude AI 분석 완료 (%d자)", len(result.stdout))
            return result.stdout.strip()

        logger.warning("Claude AI 분석 실패 (코드 %d): %s", result.returncode, result.stderr[:200])
        return None

    except subprocess.TimeoutExpired:
        logger.warning("Claude AI 분석 타임아웃 (%d초)", timeout)
        return None
    except FileNotFoundError:
        logger.warning("Claude Code CLI를 찾을 수 없습니다")
        return None
    except Exception as e:
        logger.warning("Claude AI 분석 예외: %s", e)
        return None


def parse_ai_response(response: str) -> list[dict]:
    """AI 응답에서 종목별 추천/제외 판단을 파싱한다.

    JSON 블록 우선 → 실패 시 regex fallback.

    Returns:
        [{"ticker": "AAPL", "ai_approved": True, "ai_reason": "...",
          "ai_target_price": 200.0, "ai_stop_loss": 160.0}, ...]
    """
    # 1차: JSON 블록 추출 시도
    json_result = _try_parse_json(response)
    if json_result is not None:
        logger.info("AI 응답 JSON 파싱 성공: %d종목", len(json_result))
        return json_result

    # 2차: regex fallback
    logger.info("AI 응답 JSON 없음, regex fallback 사용")
    results = []
    lines = response.split("\n")

    current_ticker = None
    current_data: dict = {}

    for line in lines:
        line = line.strip()

        # 티커 감지 (대문자 2-5글자, 일반 영단어 제외)
        ticker_match = re.search(r'\b([A-Z]{2,5})\b.*(?:추천|매수|선정|승인)', line)
        # "TOP", "BUY", "USD", "RSI" 등 비티커 제외
        _NON_TICKERS = {"TOP", "BUY", "USD", "RSI", "VIX", "ETF", "IPO", "CEO", "CFO", "THE", "FOR", "AND", "NOT"}
        if ticker_match and ticker_match.group(1) in _NON_TICKERS:
            ticker_match = None
        if ticker_match:
            if current_ticker and current_data:
                current_data["ai_approved"] = True
                results.append(current_data)

            current_ticker = ticker_match.group(1)
            current_data = {
                "ticker": current_ticker,
                "ai_approved": True,
                "ai_reason": line,
            }
            continue

        # 제외 종목 감지
        exclude_match = re.search(r'\b([A-Z]{2,5})\b.*(?:제외|비추|매수 추천하지)', line)
        if exclude_match and exclude_match.group(1) in _NON_TICKERS:
            exclude_match = None
        if exclude_match:
            results.append({
                "ticker": exclude_match.group(1),
                "ai_approved": False,
                "ai_reason": line,
            })
            continue

        # 목표가 감지
        target_match = re.search(r'목표.*?\$?([\d,.]+)', line)
        if target_match and current_data:
            try:
                current_data["ai_target_price"] = float(target_match.group(1).replace(",", ""))
            except ValueError:
                pass

        # 손절 감지
        stop_match = re.search(r'손절.*?\$?([\d,.]+)', line)
        if stop_match and current_data:
            try:
                current_data["ai_stop_loss"] = float(stop_match.group(1).replace(",", ""))
            except ValueError:
                pass

    # 마지막 종목
    if current_ticker and current_data:
        current_data["ai_approved"] = True
        results.append(current_data)

    return results


def _extract_json_robust(text: str) -> dict | None:
    """텍스트에서 JSON 객체를 점진적으로 추출한다."""
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char == '{':
            try:
                obj, _ = decoder.raw_decode(text, i)
                if isinstance(obj, dict) and ("approved" in obj or "analysis" in obj):
                    return obj
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def _try_parse_json(response: str) -> list[dict] | None:
    """AI 응답에서 JSON 블록을 추출하여 파싱한다."""
    # ```json ... ``` 블록 우선 시도
    json_match = re.search(r'```json\s*\n?(.*?)\n?```', response, re.DOTALL)

    data = None
    if json_match:
        try:
            data = json.loads(json_match.group(1))
        except (json.JSONDecodeError, IndexError):
            pass

    # 코드블록 파싱 실패 시 점진적 JSON 추출
    if data is None:
        data = _extract_json_robust(response)

    if data is None:
        return None

    results = []

    # analysis 배열을 ticker로 인덱싱
    analysis_map = {item["ticker"]: item for item in data.get("analysis", []) if "ticker" in item}

    def _build_entry(ticker: str, approved: bool) -> dict:
        entry: dict = {"ticker": ticker, "ai_approved": approved}
        item = analysis_map.get(ticker, {})
        entry["ai_reason"] = item.get("reason", "")
        if item.get("target_price"):
            try:
                entry["ai_target_price"] = float(item["target_price"])
            except (ValueError, TypeError):
                pass
        if item.get("stop_loss"):
            try:
                entry["ai_stop_loss"] = float(item["stop_loss"])
            except (ValueError, TypeError):
                pass
        if item.get("confidence"):
            try:
                entry["ai_confidence"] = max(1, min(10, int(item["confidence"])))
            except (ValueError, TypeError):
                pass
        if item.get("risk_level"):
            rl = str(item["risk_level"]).upper()
            if rl in ("LOW", "MEDIUM", "HIGH"):
                entry["ai_risk_level"] = rl
        if item.get("entry_strategy"):
            entry["entry_strategy"] = str(item["entry_strategy"])
        if item.get("exit_strategy"):
            entry["exit_strategy"] = str(item["exit_strategy"])
        return entry

    # approved 종목
    for ticker in data.get("approved", []):
        results.append(_build_entry(ticker, True))

    # excluded 종목
    for ticker in data.get("excluded", []):
        results.append(_build_entry(ticker, False))

    return results if results else None


def save_analysis(response: str, run_date: date) -> Path:
    """AI 분석 결과를 파일로 저장한다."""
    reports_dir = Path("reports/ai_analysis")
    reports_dir.mkdir(parents=True, exist_ok=True)

    path = reports_dir / f"{run_date.isoformat()}_ai_analysis.md"
    path.write_text(response, encoding="utf-8")

    logger.info("AI 분석 저장: %s", path)
    return path
