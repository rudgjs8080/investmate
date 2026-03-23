"""설정 모듈 테스트."""

from __future__ import annotations

from src.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_DB_PATH,
    DEFAULT_HISTORY_PERIOD,
    DEFAULT_TOP_N,
    Environment,
    Settings,
    get_settings,
    validate_settings,
)


class TestSettings:
    def test_defaults(self):
        settings = Settings()
        assert settings.db_path == DEFAULT_DB_PATH
        assert settings.history_period == DEFAULT_HISTORY_PERIOD
        assert settings.top_n == DEFAULT_TOP_N
        assert settings.batch_size == DEFAULT_BATCH_SIZE
        assert settings.notify_channels is None

    def test_custom_values(self):
        settings = Settings(
            INVESTMATE_DB_PATH="custom.db",
            INVESTMATE_TOP_N="20",
        )
        assert settings.db_path == "custom.db"
        assert settings.top_n == 20

    def test_get_settings_returns_instance(self):
        settings = get_settings()
        assert isinstance(settings, Settings)

    def test_environment_default_dev(self):
        """기본 환경은 DEV이다."""
        settings = Settings()
        assert settings.environment == Environment.DEV

    def test_validate_settings_low_tx_cost(self):
        """거래비용이 5bps 미만이면 경고를 반환한다."""
        settings = Settings(INVESTMATE_TX_COST_BPS="2")
        warnings = validate_settings(settings)
        assert any("거래비용" in w for w in warnings)

    def test_validate_settings_valid(self):
        """유효한 설정에는 경고가 없다."""
        settings = Settings()
        warnings = validate_settings(settings)
        assert warnings == []

    def test_model_routing_defaults(self):
        """모델 라우팅 기본값이 올바르게 설정된다."""
        settings = Settings()
        assert settings.ai_model_analysis == "claude-sonnet-4-20250514"
        assert settings.ai_model_chat == "claude-haiku-4-5-20251001"
        assert settings.ai_model_sentiment == "claude-haiku-4-5-20251001"

    def test_model_routing_custom(self):
        """환경변수로 모델을 변경할 수 있다."""
        settings = Settings(INVESTMATE_AI_MODEL_CHAT="claude-sonnet-4-20250514")
        assert settings.ai_model_chat == "claude-sonnet-4-20250514"
