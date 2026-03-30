"""설정 관리 모듈."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings


class Environment(str, Enum):
    """실행 환경."""

    DEV = "dev"
    TEST = "test"
    PROD = "prod"


_CONFIG_DIR = Path.home() / ".investmate"
_CONFIG_FILE = _CONFIG_DIR / "config.json"

DEFAULT_DB_PATH = "data/investmate.db"
DEFAULT_HISTORY_PERIOD = "2y"
DEFAULT_TOP_N = 10
DEFAULT_BATCH_SIZE = 50
DEFAULT_NEWS_COUNT = 20


def _load_json_config() -> dict[str, Any]:
    """~/.investmate/config.json 파일에서 설정을 로드한다."""
    if _CONFIG_FILE.exists():
        with open(_CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


class Settings(BaseSettings):
    """애플리케이션 설정."""

    environment: Environment = Field(default=Environment.DEV, alias="INVESTMATE_ENV")
    db_path: str = Field(default=DEFAULT_DB_PATH, alias="INVESTMATE_DB_PATH")
    history_period: str = Field(
        default=DEFAULT_HISTORY_PERIOD, alias="INVESTMATE_HISTORY_PERIOD"
    )
    top_n: int = Field(default=DEFAULT_TOP_N, alias="INVESTMATE_TOP_N")
    batch_size: int = Field(default=DEFAULT_BATCH_SIZE, alias="INVESTMATE_BATCH_SIZE")
    news_count: int = Field(
        default=DEFAULT_NEWS_COUNT, alias="INVESTMATE_NEWS_COUNT"
    )
    notify_channels: str | None = Field(
        default=None, alias="INVESTMATE_NOTIFY_CHANNELS"
    )
    # 스크리너 설정 (환경변수로 조정 가능)
    screener_min_data_days: int = Field(default=60, alias="INVESTMATE_MIN_DATA_DAYS")
    screener_min_volume: int = Field(default=100_000, alias="INVESTMATE_MIN_VOLUME")
    # AI 분석 설정
    ai_enabled: bool = Field(default=True, alias="INVESTMATE_AI_ENABLED")
    ai_timeout: int = Field(default=300, alias="INVESTMATE_AI_TIMEOUT")
    ai_style: str = Field(default="balanced", alias="INVESTMATE_AI_STYLE")
    ai_backend: str = Field(default="auto", alias="INVESTMATE_AI_BACKEND")
    # 모델 라우팅 (용도별 모델 지정)
    ai_model_analysis: str = Field(
        default="claude-sonnet-4-20250514", alias="INVESTMATE_AI_MODEL_ANALYSIS",
    )
    ai_model_chat: str = Field(
        default="claude-haiku-4-5-20251001", alias="INVESTMATE_AI_MODEL_CHAT",
    )
    ai_model_sentiment: str = Field(
        default="claude-haiku-4-5-20251001", alias="INVESTMATE_AI_MODEL_SENTIMENT",
    )
    ai_model_commentary: str = Field(
        default="claude-sonnet-4-20250514", alias="INVESTMATE_AI_MODEL_COMMENTARY",
    )
    # 리스크 제어
    max_sector_pct: float = Field(default=0.4, alias="INVESTMATE_MAX_SECTOR_PCT")
    # 거래 비용 (슬리피지 + 수수료, 왕복 기준 bps)
    transaction_cost_bps: int = Field(default=20, alias="INVESTMATE_TX_COST_BPS")
    # 백테스트 무위험 수익률 (연간 %)
    risk_free_rate_pct: float = Field(default=4.0, alias="INVESTMATE_RISK_FREE_RATE")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


def get_settings() -> Settings:
    """설정 인스턴스를 생성하여 반환한다.

    환경변수 > .env 파일 > config.json > 기본값 순서로 우선순위가 적용된다.
    """
    json_config = _load_json_config()
    return Settings(**json_config)


def ensure_config_dir() -> Path:
    """설정 디렉토리가 존재하는지 확인하고, 없으면 생성한다."""
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    return _CONFIG_DIR


def save_config(config: dict[str, Any]) -> None:
    """설정을 config.json에 저장한다."""
    ensure_config_dir()
    with open(_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def validate_settings(settings: Settings) -> list[str]:
    """필수 설정 누락 검사. 경고 목록을 반환한다."""
    warnings: list[str] = []
    if settings.environment == Environment.PROD:
        db_path = Path(settings.db_path)
        if not db_path.parent.exists():
            warnings.append(f"DB 경로 부모 디렉토리 없음: {settings.db_path}")
    if settings.transaction_cost_bps < 5:
        warnings.append("거래비용이 비현실적으로 낮음 (<5bps)")
    if settings.top_n < 1 or settings.top_n > 50:
        warnings.append(f"top_n 범위 초과: {settings.top_n}")
    return warnings
