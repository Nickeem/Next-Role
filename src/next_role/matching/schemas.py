"""API contracts for suitability assessments."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel, Field

from next_role.jobs.extraction import RequirementCategory, RequirementImportance
from next_role.matching.engine import MatchStatus, Tier
from next_role.matching.models import AssessmentStatus

SCORE_DISCLAIMER = (
    "The score is a ranking heuristic over the job's stated requirements and the candidate's "
    "supported evidence. It is not a probability of being hired."
)


class AssessmentRequest(BaseModel):
    candidate_id: uuid.UUID
    # Reference date for time-dependent rules; defaults to today (UTC).
    as_of: date | None = None


class EvidenceOut(BaseModel):
    fact_id: uuid.UUID | None
    fact_type: str
    title: str
    support: str
    reason: str


class RequirementAssessmentOut(BaseModel):
    requirement_id: uuid.UUID
    text: str
    category: RequirementCategory
    importance: RequirementImportance
    status: MatchStatus
    weight: int
    credit: float
    explanation: str
    evidence: list[EvidenceOut]


class PreferenceCheckOut(BaseModel):
    name: str
    status: str
    explanation: str


class AssessmentSummary(BaseModel):
    """Requirement texts grouped for review and notification."""

    matched: list[str]
    partially_matched: list[str]
    gaps: list[str] = Field(description="Contradicted by supported candidate information")
    unverified: list[str] = Field(description="Addressed only by unverified candidate claims")
    unknowns: list[str] = Field(description="No relevant evidence, or not interpretable")


class AssessmentOut(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    status: AssessmentStatus
    method: str
    method_version: str
    as_of: date
    score: int | None
    score_excluding_unknowns: int | None
    coverage: float
    tier: Tier
    has_blocking_gaps: bool
    status_counts: dict[str, int]
    preference_checks: list[PreferenceCheckOut]
    summary: AssessmentSummary
    requirements: list[RequirementAssessmentOut]
    disclaimer: str = SCORE_DISCLAIMER
    created_at: datetime


class AssessmentSummaryOut(BaseModel):
    id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    score: int | None
    tier: Tier
    has_blocking_gaps: bool
    method_version: str
    as_of: date
    created_at: datetime


class AssessmentResultOut(BaseModel):
    created: bool
    assessment: AssessmentOut
