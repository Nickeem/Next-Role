"""document notifications: event types and a preference switch

Revision ID: 0009_document_notifications
Revises: 0008_documents
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_document_notifications"
down_revision: str | None = "0008_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONSTRAINT = "ck_notification_outbox_notification_event_type"
BEFORE = "'review.ready', 'review.postponed_due', 'integration.test'"
AFTER = BEFORE + ", 'documents.ready', 'documents.needs_input', 'documents.failed'"


def upgrade() -> None:
    op.add_column(
        "notification_preferences",
        sa.Column("documents", sa.Boolean(), server_default="true", nullable=False),
    )
    op.drop_constraint(op.f(CONSTRAINT), "notification_outbox", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "notification_outbox", f"event_type IN ({AFTER})")


def downgrade() -> None:
    op.execute("DELETE FROM notification_outbox WHERE event_type LIKE 'documents.%'")
    op.drop_constraint(op.f(CONSTRAINT), "notification_outbox", type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), "notification_outbox", f"event_type IN ({BEFORE})")
    op.drop_column("notification_preferences", "documents")
