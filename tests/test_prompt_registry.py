"""프롬프트 버전 관리 레지스트리 테스트."""

from src.ai.prompt_registry import (
    DEFAULT_VERSION,
    PROMPT_VERSIONS,
    get_prompt_config,
    list_versions,
)


class TestGetPromptConfig:
    def test_get_default_version(self):
        config = get_prompt_config()
        assert config == PROMPT_VERSIONS[DEFAULT_VERSION]
        assert config["chain_of_thought"] is True
        assert config["bull_bear_debate"] is True

    def test_get_specific_version(self):
        config = get_prompt_config("v1_base")
        assert config["chain_of_thought"] is False
        assert config["bull_bear_debate"] is False

    def test_unknown_version_falls_back_to_default(self):
        config = get_prompt_config("v99_nonexistent")
        assert config == PROMPT_VERSIONS[DEFAULT_VERSION]


class TestListVersions:
    def test_list_versions_all_present(self):
        versions = list_versions()
        assert len(versions) == 4
        version_names = [v["version"] for v in versions]
        assert "v1_base" in version_names
        assert "v2_cot" in version_names
        assert "v3_debate" in version_names
        assert "v4_multi_agent" in version_names
        for v in versions:
            assert "description" in v
            assert "chain_of_thought" in v
