"""Notification outbox and preferences.

Rows are written to ``notification_outbox`` in the same transaction as the event that causes
them, and published later by the worker. Payloads never contain candidate or job details.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from next_role.db import Base, IdMixin, TimestampMixin


class NotificationEventType(StrEnum):
    REVIEW_READY = "review.ready"
    REVIEW_POSTPONED_DUE = "review.postponed_due"
    INTEGRATION_TEST = "integration.test"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class NotificationOutbox(IdMixin, TimestampMixin, Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        Index("ix_notification_outbox_status_next_attempt_at", "status", "next_attempt_at"),
    )

    # Deterministic per logical event, so the same event is never queued twice.
    event_key: Mapped[str] = mapped_column(String(200), unique=True)
    event_type: Mapped[NotificationEventType] = mapped_column(
        _enum(NotificationEventType, "notification_event_type")
    )
    review_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("opportunity_reviews.id", ondelete="CASCADE"), index=True
    )
    # title, message, priority, tags, and an app-relative click path.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[OutboxStatus] = mapped_column(
        _enum(OutboxStatus, "outbox_status"), default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Redacted: never contains the token or response bodies.
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationPreferences(TimestampMixin, Base):
    """Per-operator switches for each event type. A missing row means all enabled."""

    __tablename__ = "notification_preferences"

    operator_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    review_ready: Mapped[bool] = mapped_column(Boolean, default=True)
    review_postponed_due: Mapped[bool] = mapped_column(Boolean, default=True)
