"""API contracts for LLM call status. Metadata only: no prompts, responses, keys or paths."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

from next_role.config import LLMProviderName
from next_role.integrations.llm.base import ReasoningEffort
from next_role.integrations.llm.models import LLMOutcome, LLMTask
from next_role.integrations.llm.resolver import Source


class LLMLastErrorOut(BaseModel):
    # Redacted: an HTTP status or error class, never a prompt or response.
    message: str | None
    at: datetime
    model: str | None
    # What was sent: model, temperature, reasoning effort, response format type, timeout.
    params: dict[str, Any] | None


class LLMLastSuccessOut(BaseModel):
    at: datetime
    model: str | None
    latency_ms: int | None


class LLMLastAttemptOut(BaseModel):
    """The most recent request sent for a task, whatever its outcome."""

    at: datetime
    outcome: LLMOutcome
    model: str | None
    # The reasoning effort that request sent; null when none was sent.
    reasoning_effort: str | None


class LLMTaskStatusOut(BaseModel):
    task: LLMTask
    # The model and effort the task uses now; events show what each call actually sent.
    model: str | None
    # Null when no effort is sent (not OpenAI, or a model outside the catalog).
    reasoning_effort: ReasoningEffort | None
    # Whether each comes from a choice saved in Settings or from the default.
    model_source: Source | None
    effort_source: Source | None
    # False when the task does not call the model, e.g. drafting without the candidate-data
    # opt-in (ADR 0006).
    available: bool
    counts_24h: dict[LLMOutcome, int]
    # Since the retention window started (NEXT_ROLE_LLM_EVENT_RETENTION_DAYS).
    counts: dict[LLMOutcome, int]
    last_error: LLMLastErrorOut | None
    last_success: LLMLastSuccessOut | None
    last_attempt: LLMLastAttemptOut | None


class LLMStatusOut(BaseModel):
    provider: LLMProviderName
    enabled: bool
    # Host only; the base URL path and API key are not disclosed.
    host: str | None
    allow_external: bool
    allow_candidate_data: bool
    retention_days: int
    tasks: list[LLMTaskStatusOut]
