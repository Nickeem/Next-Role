"""API contracts for notification preferences."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from next_role.notifications.models import OutboxStatus


class NotificationPreferencesOut(BaseModel):
    review_ready: bool
    review_postponed_due: bool
    # Null until the preferences are first saved.
    updated_at: datetime | None


class NotificationPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_ready: bool | None = None
    review_postponed_due: bool | None = None


class DeliveryStatusOut(BaseModel):
    counts: dict[OutboxStatus, int]
    # When ntfy last accepted a message. Acceptance is not proof a device displayed it.
    last_accepted_at: datetime | None
    # Redacted: an HTTP status or error class, never a token or response body.
    last_error: str | None
    last_error_at: datetime | None


class NtfyStatusOut(DeliveryStatusOut):
    enabled: bool
    # Host only; the topic and token are not disclosed.
    server: str | None
    click_links: bool


class TestNotificationOut(BaseModel):
    outbox_id: uuid.UUID
    status: OutboxStatus
