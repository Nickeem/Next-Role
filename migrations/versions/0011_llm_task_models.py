"""llm task models: a model and reasoning effort per task, and the model_fallback outcome

Revision ID: 0011_llm_task_models
Revises: 0010_llm_call_events
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_llm_task_models"
down_revision: str | None = "0010_llm_call_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASKS = ("extraction", "resume_drafting", "letter_drafting", "semantic_check")
EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
OUTCOMES_BEFORE = (
    "ok",
    "provider_error",
    "schema_invalid",
    "temperature_dropped",
    "deterministic_fallback",
)
OUTCOMES_AFTER = (*OUTCOMES_BEFORE, "model_fallback")
OUTCOME_CONSTRAINT = "ck_llm_call_events_llm_outcome"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _enum(name: str, values: Sequence[str]) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=False, length=32)


def upgrade() -> None:
    op.create_table(
        "llm_task_models",
        sa.Column("task", _enum("llm_task", TASKS), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("reasoning_effort", _enum("reasoning_effort", EFFORTS), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(_in("task", TASKS), name=op.f("ck_llm_task_models_llm_task")),
        sa.CheckConstraint(
            _in("reasoning_effort", EFFORTS), name=op.f("ck_llm_task_models_reasoning_effort")
        ),
        sa.PrimaryKeyConstraint("task", name=op.f("pk_llm_task_models")),
    )
    op.drop_constraint(op.f(OUTCOME_CONSTRAINT), "llm_call_events", type_="check")
    op.create_check_constraint(
        op.f(OUTCOME_CONSTRAINT), "llm_call_events", _in("outcome", OUTCOMES_AFTER)
    )


def downgrade() -> None:
    # model_fallback rows cannot satisfy the old constraint. They are observability data only,
    # so they are deleted (as 0009 does for document notifications) rather than failing.
    op.execute("DELETE FROM llm_call_events WHERE outcome = 'model_fallback'")
    op.drop_constraint(op.f(OUTCOME_CONSTRAINT), "llm_call_events", type_="check")
    op.create_check_constraint(
        op.f(OUTCOME_CONSTRAINT), "llm_call_events", _in("outcome", OUTCOMES_BEFORE)
    )
    op.drop_table("llm_task_models")
