"""Provider-neutral interface for LLM calls that must return JSON matching a schema."""

from typing import Any, Protocol


class LLMError(Exception):
    """The provider failed, timed out, or returned an unusable response."""


class LLMProvider(Protocol):
    name: str
    # True when requests leave this server (a third-party API).
    external: bool

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> str:
        """Return the model's raw JSON text. Callers must validate it."""
        ...
