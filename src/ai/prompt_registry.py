"""프롬프트 버전 관리 레지스트리."""

from __future__ import annotations

PROMPT_VERSIONS: dict[str, dict] = {
    "v1_base": {
        "description": "기본 프롬프트 (CFA 역할 + 5차원 분석)",
        "chain_of_thought": False,
        "bull_bear_debate": False,
    },
    "v2_cot": {
        "description": "Chain-of-Thought 추가 (단계적 사고)",
        "chain_of_thought": True,
        "bull_bear_debate": False,
    },
    "v3_debate": {
        "description": "Bull vs Bear 대립 분석 추가",
        "chain_of_thought": True,
        "bull_bear_debate": True,
    },
    "v4_multi_agent": {
        "description": "멀티 에이전트 토론 (Bull/Bear/Synthesizer 3라운드)",
        "chain_of_thought": True,
        "bull_bear_debate": True,
        "multi_agent": True,
    },
}

DEFAULT_VERSION = "v4_multi_agent"


def get_prompt_config(version: str | None = None) -> dict:
    """프롬프트 버전 설정을 반환한다."""
    version = version or DEFAULT_VERSION
    return PROMPT_VERSIONS.get(version, PROMPT_VERSIONS[DEFAULT_VERSION])


def list_versions() -> list[dict]:
    """등록된 프롬프트 버전 목록."""
    return [
        {"version": k, **v}
        for k, v in PROMPT_VERSIONS.items()
    ]
