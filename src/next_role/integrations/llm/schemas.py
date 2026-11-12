"""API contracts for LLM call status. Metadata only: no prompts, responses, keys or paths."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from next_role.config import LLMProviderName
from next_role.integrations.llm.base import ReasoningEffort
from next_role.integrations.llm.catalog import Tier
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


class LLMModelOut(BaseModel):
    """A model the key can use that is in the catalog."""

    id: str
    tier: Tier
    # Lowest first.
    reasoning_efforts: list[ReasoningEffort]


class LLMModelsOut(BaseModel):
    # False when per-task selection is unavailable or the models could not be listed.
    available: bool
    # Why not, when ``available`` is false.
    reason: str | None
    # True when the latest refresh failed and an older listing is shown.
    stale: bool
    fetched_at: datetime | None
    models: list[LLMModelOut]


class LLMTaskModelOut(BaseModel):
    task: LLMTask
    # The saved choices; null means the default.
    model: str | None
    reasoning_effort: ReasoningEffort | None
    # What calls use now, after the effort rules.
    effective_model: str | None
    effective_reasoning_effort: ReasoningEffort | None
    model_source: Source | None
    effort_source: Source | None
    # The task's default effort, shown as "Default (...)".
    default_reasoning_effort: ReasoningEffort


class LLMTaskModelsOut(BaseModel):
    # False when per-task selection is unavailable: every task then uses ``default_model``.
    available: bool
    reason: str | None
    # NEXT_ROLE_LLM_MODEL.
    default_model: str | None
    tasks: list[LLMTaskModelOut]


class LLMTaskChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Null clears the choice (the default is used).
    model: str | None = Field(default=None, max_length=200)
    reasoning_effort: ReasoningEffort | None = None


class LLMTaskModelsUpdate(BaseModel):
    """The tasks listed are replaced; tasks not listed keep their choices."""

    model_config = ConfigDict(extra="forbid")

    tasks: dict[LLMTask, LLMTaskChoice]
