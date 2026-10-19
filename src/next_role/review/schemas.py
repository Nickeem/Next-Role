"""API contracts for candidate reviews and decisions."""

import uuid
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from next_role.review.workflow import ReviewAction, ReviewEventType, ReviewStatus


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: ReviewAction
    # The version the client last saw. A mismatch returns 409 instead of overwriting.
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=2000)
    # Required for reject and approve_for_drafting.
    confirm: bool = False
    # Required for postpone; must be in the future.
    postpone_until: AwareDatetime | None = None


class ReviewOut(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    status: ReviewStatus
    postpone_until: datetime | None
    latest_assessment_id: uuid.UUID
    version: int
    allowed_actions: list[ReviewAction]
    created_at: datetime
    updated_at: datetime


class ReviewEventOut(BaseModel):
    id: uuid.UUID
    review_id: uuid.UUID
    event_type: ReviewEventType
    previous_status: ReviewStatus | None
    new_status: ReviewStatus
    assessment_id: uuid.UUID
    reason: str | None
    postpone_until: datetime | None
    actor: str
    created_at: datetime
