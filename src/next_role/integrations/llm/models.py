"""Recorded LLM call events, and the model and reasoning effort chosen per task.

Event rows (one per attempt, temperature drop, or fallback) hold metadata only (task, model,
outcome, redacted error, request params, latency), never prompts, messages, schemas or
responses. They back ``GET /integrations/llm/status``.

Adding an ``LLMTask`` value needs a migration: both tables have a check constraint on it.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, Enum, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from next_role.db import Base, IdMixin, TimestampMixin
from next_role.integrations.llm.base import ReasoningEffort


class LLMTask(StrEnum):
    EXTRACTION = "extraction"
    RESUME_DRAFTING = "resume_drafting"
    LETTER_DRAFTING = "letter_drafting"
    SEMANTIC_CHECK = "semantic_check"


class LLMOutcome(StrEnum):
    OK = "ok"
    PROVIDER_ERROR = "provider_error"
    SCHEMA_INVALID = "schema_invalid"
    # The model rejected an explicit temperature; the request was resent without it.
    TEMPERATURE_DROPPED = "temperature_dropped"
    # The caller gave up on the model and used deterministic code instead.
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    # The task's chosen model was unavailable; the attempt was repeated with the default.
    MODEL_FALLBACK = "model_fallback"


ERROR_OUTCOMES = frozenset({LLMOutcome.PROVIDER_ERROR, LLMOutcome.SCHEMA_INVALID})


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class LLMCallEvent(IdMixin, Base):
    __tablename__ = "llm_call_events"
    __table_args__ = (
        Index("ix_llm_call_events_task_created_at", "task", "created_at"),
        Index("ix_llm_call_events_created_at", "created_at"),
    )

    task: Mapped[LLMTask] = mapped_column(_enum(LLMTask, "llm_task"), nullable=False)
    outcome: Mapped[LLMOutcome] = mapped_column(_enum(LLMOutcome, "llm_outcome"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(200))
    # 1-based attempt number; null for fallback rows.
    attempt: Mapped[int | None] = mapped_column(Integer)
    # Redacted: describe_error() output, or an exception type name. Never content.
    error: Mapped[str | None] = mapped_column(String(500))
    params: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LLMTaskModel(TimestampMixin, Base):
    """The model and reasoning effort chosen for one task (ADR 0008).

    Deployment-wide, not per operator: the worker uses it whoever is signed in. A missing row
    or a null column means the default (NEXT_ROLE_LLM_MODEL, or the task's default effort).
    Rows are ignored, not deleted, when per-task selection is unavailable.
    """

    __tablename__ = "llm_task_models"

    task: Mapped[LLMTask] = mapped_column(_enum(LLMTask, "llm_task"), primary_key=True)
    model: Mapped[str | None] = mapped_column(String(200))
    reasoning_effort: Mapped[ReasoningEffort | None] = mapped_column(
        _enum(ReasoningEffort, "reasoning_effort")
    )
