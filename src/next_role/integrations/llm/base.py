"""Provider-neutral interface for LLM calls that must return JSON matching a schema."""

from typing import Any, Protocol


class LLMError(Exception):
    """The provider failed, timed out, or returned an unusable response.

    Messages are redacted by construction (provider name, HTTP status, or error type), never
    prompt or response content, so they are safe to log and show in notes.
    """


def describe_error(exc: Exception) -> str:
    """A short, content-free description: the LLMError message, else the exception type."""
    if isinstance(exc, LLMError) and str(exc):
        return str(exc)
    return type(exc).__name__


class LLMProvider(Protocol):
    name: str
    # True when requests leave this server (a third-party API).
    external: bool

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        """Return the model's raw JSON text. Callers must validate it."""
        ...
