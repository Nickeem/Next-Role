"""Provider-neutral interface for LLM calls that must return JSON matching a schema."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ReasoningEffort(StrEnum):
    """OpenAI's ``reasoning_effort`` values, lowest first (the order is used for fallbacks)."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


@dataclass(frozen=True)
class RequestParams:
    """Metadata about a request as actually sent. Never messages, prompts, schemas or output."""

    model: str
    # None when no temperature was sent (the model's default applies).
    temperature: float | None
    response_format: str
    timeout_seconds: float
    # None when no reasoning effort was sent (the model's default applies).
    reasoning_effort: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "temperature_sent": self.temperature is not None,
            "temperature": self.temperature,
            "reasoning_effort": self.reasoning_effort,
            "response_format": self.response_format,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass(frozen=True)
class Completion:
    """A provider's raw JSON text and what was sent to obtain it."""

    text: str
    params: RequestParams
    # The parameters of a first request that the model rejected for its temperature; the
    # request that produced ``text`` was then sent without one.
    temperature_rejected: RequestParams | None = None


class LLMError(Exception):
    """The provider failed, timed out, or returned an unusable response.

    Messages are redacted by construction (provider name, HTTP status, or error type), never
    prompt or response content, so they are safe to log and show in notes. ``params`` and
    ``temperature_rejected`` describe the last request sent, when one was.
    """

    def __init__(
        self,
        message: str = "",
        params: RequestParams | None = None,
        temperature_rejected: RequestParams | None = None,
    ) -> None:
        super().__init__(message)
        self.params = params
        self.temperature_rejected = temperature_rejected


def describe_error(exc: Exception) -> str:
    """A short, content-free description: the LLMError message, else the exception type."""
    if isinstance(exc, LLMError) and str(exc):
        return str(exc)
    return type(exc).__name__


class LLMProvider(Protocol):
    name: str
    # The default model (NEXT_ROLE_LLM_MODEL). Each call names the model it uses.
    model: str
    # True when requests leave this server (a third-party API).
    external: bool

    def complete_json(
        self,
        system: str,
        user: str,
        schema: dict[str, Any],
        *,
        model: str,
        reasoning_effort: ReasoningEffort | None = None,
    ) -> Completion:
        """Return the model's raw JSON text and the request params. Callers must validate it.

        ``reasoning_effort`` is sent only when given; providers without the parameter ignore it.
        """
        ...
