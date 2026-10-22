"""API contracts for generated documents."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from next_role.documents.models import DraftingMode
from next_role.documents.workflow import BundleStatus, RunIntent, RunStatus


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    review_id: uuid.UUID
    bundle_id: uuid.UUID | None
    intent: RunIntent
    parameters: dict[str, Any]
    status: RunStatus
    stage: str | None
    attempts: int
    next_attempt_at: datetime
    last_error: str | None
    created_at: datetime
    finished_at: datetime | None


class BundleSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    review_id: uuid.UUID
    authorization_event_id: uuid.UUID
    status: BundleStatus
    drafting_mode: DraftingMode
    version: int
    created_at: datetime
    updated_at: datetime


class ReviewDocumentsOut(BaseModel):
    runs: list[RunOut]
    bundles: list[BundleSummaryOut]
