"""notification outbox: cancelled status

Revision ID: 0006_outbox_cancelled
Revises: 0005_review_workflow
Create Date: 2026-10-19
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_outbox_cancelled"
down_revision: str | None = "0005_review_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_notification_outbox_outbox_status"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), "notification_outbox", type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT),
        "notification_outbox",
        "status IN ('pending', 'sent', 'failed', 'cancelled')",
    )


def downgrade() -> None:
    op.execute("UPDATE notification_outbox SET status = 'failed' WHERE status = 'cancelled'")
    op.drop_constraint(op.f(CONSTRAINT), "notification_outbox", type_="check")
    op.create_check_constraint(
        op.f(CONSTRAINT), "notification_outbox", "status IN ('pending', 'sent', 'failed')"
    )
