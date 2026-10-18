import pytest
from pydantic import SecretStr, ValidationError

from next_role.config import LLMProviderName, Settings


def test_api_key_required_unless_auth_disabled() -> None:
    with pytest.raises(ValidationError, match="NEXT_ROLE_API_KEY"):
        Settings(_env_file=None)
    assert Settings(auth_disabled=True, _env_file=None).auth_disabled


def test_external_llm_requires_explicit_opt_in() -> None:
    common = {
        "api_key": SecretStr("k"),
        "llm_provider": LLMProviderName.OPENAI_COMPATIBLE,
        "llm_base_url": "https://llm.example.invalid",
        "llm_model": "m",
        "_env_file": None,
    }
    with pytest.raises(ValidationError, match="ALLOW_EXTERNAL"):
        Settings(**common)
    assert Settings(**common, llm_allow_external=True).llm_allow_external


def test_llm_provider_requires_url_and_model() -> None:
    with pytest.raises(ValidationError, match="LLM_BASE_URL"):
        Settings(api_key=SecretStr("k"), llm_provider=LLMProviderName.OLLAMA, _env_file=None)
