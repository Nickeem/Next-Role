"""Typed application configuration loaded from environment variables (and an optional .env)."""

from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlsplit

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
    # Password for browser login (cookie sessions). Unset disables browser login.
    ui_password: SecretStr | None = None
    session_ttl_hours: float = Field(default=12.0, gt=0, le=24 * 30)
    # Keep true in production. Browsers accept Secure cookies on http://localhost.
    session_cookie_secure: bool = True

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

    # Built web workspace (frontend/dist). Unset: the API runs without the UI.
    static_dir: str | None = None

    # Public URL of this app, used for click-through links in notifications.
    public_base_url: str | None = None

    # ntfy notifications, published by the worker. The token stays server-side.
    ntfy_enabled: bool = False
    ntfy_base_url: str | None = None
    ntfy_topic: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    ntfy_token: SecretStr | None = None
    ntfy_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    # Plain HTTP is refused unless the operator opts in (e.g. a private Docker network).
    ntfy_allow_http: bool = False
    # Attempts before a notification is marked failed, and how long it stays worth sending.
    notification_max_attempts: int = Field(default=8, ge=1, le=50)
    notification_max_age_hours: float = Field(default=24.0, gt=0)
    worker_poll_seconds: float = Field(default=10.0, ge=1)

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.api_key is None and not self.auth_disabled:
            raise ValueError(
                "NEXT_ROLE_API_KEY must be set (or NEXT_ROLE_AUTH_DISABLED=true for local use)"
            )
        if self.ui_password is not None and len(self.ui_password.get_secret_value()) < 12:
            raise ValueError("NEXT_ROLE_UI_PASSWORD must be at least 12 characters")
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
        if self.ntfy_enabled:
            if not self.ntfy_base_url or not self.ntfy_topic or self.ntfy_token is None:
                raise ValueError(
                    "NEXT_ROLE_NTFY_BASE_URL, NEXT_ROLE_NTFY_TOPIC, and NEXT_ROLE_NTFY_TOKEN "
                    "are required when NEXT_ROLE_NTFY_ENABLED=true"
                )
            scheme = urlsplit(self.ntfy_base_url).scheme
            if scheme not in {"https", "http"} or (scheme == "http" and not self.ntfy_allow_http):
                raise ValueError(
                    "NEXT_ROLE_NTFY_BASE_URL must use https "
                    "(or set NEXT_ROLE_NTFY_ALLOW_HTTP=true for a private network)"
                )
        if self.public_base_url and urlsplit(self.public_base_url).scheme not in {"https", "http"}:
            raise ValueError("NEXT_ROLE_PUBLIC_BASE_URL must be an http(s) URL")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
