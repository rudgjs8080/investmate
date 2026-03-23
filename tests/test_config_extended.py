"""설정 모듈 확장 테스트."""

from pathlib import Path
from unittest.mock import patch

from src.config import get_settings, ensure_config_dir, save_config


class TestEnsureConfigDir:
    def test_creates_dir(self, tmp_path):
        with patch("src.config._CONFIG_DIR", tmp_path / ".investmate"):
            result = ensure_config_dir()
            assert result.exists()


class TestSaveConfig:
    def test_saves_json(self, tmp_path):
        config_dir = tmp_path / ".investmate"
        config_file = config_dir / "config.json"
        with patch("src.config._CONFIG_DIR", config_dir), \
             patch("src.config._CONFIG_FILE", config_file):
            save_config({"top_n": 5, "history_period": "1y"})
            assert config_file.exists()
            import json
            data = json.loads(config_file.read_text(encoding="utf-8"))
            assert data["top_n"] == 5


class TestGetSettings:
    def test_default_values(self):
        settings = get_settings()
        assert settings.top_n == 10
        assert settings.batch_size == 50
        assert settings.history_period == "2y"
