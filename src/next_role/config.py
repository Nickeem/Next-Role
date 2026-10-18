"""Typed application configuration loaded from environment variables (and an optional .env)."""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMProviderName(StrEnum):
    NONE = "none"
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEXT_ROLE_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    environment: str = "development"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+psycopg://next_role:next_role@localhost:5432/next_role"
    database_echo: bool = False

    # Shared-secret API key required on all /api routes. Health endpoints stay open.
    api_key: SecretStr | None = None
    # Explicit opt-out for local experimentation only; never enable on an exposed server.
    auth_disabled: bool = False

    # Maximum accepted job description length, in characters.
    max_job_description_chars: int = Field(default=50_000, ge=1_000)

    # LLM integration. "none" keeps all processing local and deterministic.
    llm_provider: LLMProviderName = LLMProviderName.NONE
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    # External (non-self-hosted) providers receive job description text. This flag makes
    # sending data off the server an explicit operator decision.
    llm_allow_external: bool = False

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.api_key is None and not self.auth_disabled:
            raise ValueError(
                "NEXT_ROLE_API_KEY must be set (or NEXT_ROLE_AUTH_DISABLED=true for local use)"
            )
        if self.llm_provider is not LLMProviderName.NONE:
            if not self.llm_base_url or not self.llm_model:
                raise ValueError("NEXT_ROLE_LLM_BASE_URL and NEXT_ROLE_LLM_MODEL are required")
            if (
                self.llm_provider is LLMProviderName.OPENAI_COMPATIBLE
                and not self.llm_allow_external
            ):
                raise ValueError(
                    "The openai_compatible provider may send job text to a third party; "
                    "set NEXT_ROLE_LLM_ALLOW_EXTERNAL=true to confirm"
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
