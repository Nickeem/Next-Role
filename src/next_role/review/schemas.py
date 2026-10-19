"""API contracts for candidate reviews and decisions."""

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from next_role.jobs.extraction import WorkArrangement
from next_role.jobs.models import OpportunityStatus
from next_role.jobs.schemas import JobOut
from next_role.matching.schemas import AssessmentOut, AssessmentSummaryOut
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


class ReviewSort(StrEnum):
    UPDATED = "updated"
    SCORE = "score"
    ASSESSED = "assessed"
    CREATED = "created"


class ReviewJobOut(BaseModel):
    title: str | None
    company: str | None
    location: str | None
    work_arrangement: WorkArrangement
    status: OpportunityStatus


class ReviewListItemOut(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    status: ReviewStatus
    postpone_until: datetime | None
    # Postponed and the chosen date has passed.
    follow_up_due: bool
    version: int
    created_at: datetime
    updated_at: datetime
    job: ReviewJobOut
    assessment: AssessmentSummaryOut


class ReviewPageOut(BaseModel):
    items: list[ReviewListItemOut]
    total: int
    limit: int
    offset: int


class ReviewSummaryOut(BaseModel):
    counts: dict[ReviewStatus, int]
    follow_ups_due: int
    total: int


class ReviewDetailOut(BaseModel):
    review: ReviewOut
    job: JobOut
    # The stored Phase 1 assessment; never recomputed for display.
    assessment: AssessmentOut
    # The assessment the most recent candidate decision was based on, if any.
    decision_assessment_id: uuid.UUID | None
    newer_assessment_available: bool


class ActivityItemOut(ReviewEventOut):
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    job_title: str | None
    company: str | None


class ActivityPageOut(BaseModel):
    items: list[ActivityItemOut]
    total: int
    limit: int
    offset: int
