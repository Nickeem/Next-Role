"""API contracts for notification preferences."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class NotificationPreferencesOut(BaseModel):
    review_ready: bool
    review_postponed_due: bool
    # Null until the preferences are first saved.
    updated_at: datetime | None


class NotificationPreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_ready: bool | None = None
    review_postponed_due: bool | None = None
