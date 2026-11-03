"""llm call events: per-task attempts, temperature drops and fallbacks

Revision ID: 0010_llm_call_events
Revises: 0009_document_notifications
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_llm_call_events"
down_revision: str | None = "0009_document_notifications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TASKS = "'extraction', 'resume_drafting', 'letter_drafting', 'semantic_check'"
OUTCOMES = (
    "'ok', 'provider_error', 'schema_invalid', 'temperature_dropped', 'deterministic_fallback'"
)


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=False, length=32)


def upgrade() -> None:
    op.create_table(
        "llm_call_events",
        sa.Column(
            "task",
            _enum("llm_task", "extraction", "resume_drafting", "letter_drafting", "semantic_check"),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            _enum(
                "llm_outcome",
                "ok",
                "provider_error",
                "schema_invalid",
                "temperature_dropped",
                "deterministic_fallback",
            ),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(f"task IN ({TASKS})", name=op.f("ck_llm_call_events_llm_task")),
        sa.CheckConstraint(f"outcome IN ({OUTCOMES})", name=op.f("ck_llm_call_events_llm_outcome")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_call_events")),
    )
    # "Latest per task" and per-task counts.
    op.create_index("ix_llm_call_events_task_created_at", "llm_call_events", ["task", "created_at"])
    # Retention pruning.
    op.create_index("ix_llm_call_events_created_at", "llm_call_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_call_events_created_at", table_name="llm_call_events")
    op.drop_index("ix_llm_call_events_task_created_at", table_name="llm_call_events")
    op.drop_table("llm_call_events")
