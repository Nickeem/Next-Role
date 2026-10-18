from next_role.config import LLMProviderName, Settings
from next_role.integrations.llm.base import LLMProvider
from next_role.integrations.llm.http_providers import OllamaProvider, OpenAICompatibleProvider


def build_llm_provider(settings: Settings) -> LLMProvider | None:
    """Construct the configured provider, or None when LLM use is disabled (the default)."""
    if settings.llm_provider is LLMProviderName.NONE:
        return None
    if not settings.llm_base_url or not settings.llm_model:  # also enforced by Settings
        raise ValueError("LLM base URL and model must be configured")
    api_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    cls = (
        OllamaProvider
        if settings.llm_provider is LLMProviderName.OLLAMA
        else OpenAICompatibleProvider
    )
    return cls(settings.llm_base_url, settings.llm_model, settings.llm_timeout_seconds, api_key)
