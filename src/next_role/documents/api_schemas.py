"""API contracts for generated documents."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from next_role.documents.models import DocumentKind, DraftingMode, RevisionAuthor
from next_role.documents.schemas import StyleSettings
from next_role.documents.workflow import (
    BundleStatus,
    ClarificationStatus,
    RunIntent,
    RunStatus,
    VerificationOutcome,
)


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


class VerificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: VerificationOutcome
    check_version: str
    # Per claim (and per entry, as "item-<id>"): status, issues, cited fact hashes.
    claims: list[dict[str, Any]]
    issues: list[dict[str, Any]]
    semantic_checked: bool
    created_at: datetime


class RevisionSummaryOut(BaseModel):
    id: uuid.UUID
    kind: DocumentKind
    seq: int
    created_by: RevisionAuthor
    note: str | None
    created_at: datetime
    # None while the revision is waiting to be checked.
    verification_status: VerificationOutcome | None


class RevisionOut(RevisionSummaryOut):
    bundle_id: uuid.UUID
    parent_id: uuid.UUID | None
    schema_version: str
    content: dict[str, Any]
    content_hash: str
    verification: VerificationOut | None


class ClarificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: DocumentKind
    key: str
    question: str
    material: bool
    status: ClarificationStatus
    answer: str | None
    answer_kind: str | None
    proposed_fact_id: uuid.UUID | None
    dismiss_reason: str | None
    resolved_at: datetime | None
    created_at: datetime


class StyleOut(BaseModel):
    id: uuid.UUID
    seq: int
    template_id: str
    template_version: str
    settings: StyleSettings
    # Resolved design values for the preview (and the print page).
    tokens: dict[str, Any]
    created_at: datetime


class JobRefOut(BaseModel):
    id: uuid.UUID
    title: str | None
    company: str | None


class BundleDetailOut(BundleSummaryOut):
    job: JobRefOut
    drafting_notes: list[str]
    resume: RevisionOut | None
    cover_letter: RevisionOut | None
    clarifications: list[ClarificationOut]
    style: StyleOut | None
    # Queued or running work, newest first, and the last few finished runs.
    runs: list[RunOut]


class EditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DocumentKind
    content: dict[str, Any]
    expected_version: int
    note: str | None = Field(default=None, max_length=300)


class RegenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DocumentKind
    # A resume section type, or "letter" for the whole cover letter.
    section: str = Field(max_length=32)
    expected_version: int
    # Chosen by the client so a retried request does not queue the work twice.
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=2000)
    expected_version: int


class DismissIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)
    expected_version: int


class ConfirmClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DocumentKind
    expected_version: int


class TemplatesOut(BaseModel):
    templates: list[dict[str, Any]]
    controls: dict[str, list[str]]
    defaults: StyleSettings
