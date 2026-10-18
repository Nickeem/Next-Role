"""Validated contracts for manual job ingestion and job retrieval."""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

from next_role.jobs.extraction import (
    EmploymentType,
    RequirementCategory,
    RequirementImportance,
    WorkArrangement,
)
from next_role.jobs.models import ExtractionMethod, OpportunityStatus

# Metadata fields are whitespace-stripped; the description is stored exactly as submitted.
StrippedShortStr = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)
]
OptionalShortStr = StrippedShortStr | None


class RequirementIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: Annotated[str, Field(min_length=2, max_length=1000)]
    importance: RequirementImportance = RequirementImportance.UNSPECIFIED


class JobSubmission(BaseModel):
    """A manually submitted job description. Only ``description`` is required; anything not
    supplied is stored as unknown rather than guessed."""

    model_config = ConfigDict(extra="forbid")

    description: Annotated[str, Field(min_length=20)]
    title: OptionalShortStr = None
    company: OptionalShortStr = None
    location: OptionalShortStr = None
    work_arrangement: WorkArrangement | None = None
    employment_type: EmploymentType | None = None
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    salary_currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    source: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
    ] = "manual"
    source_url: HttpUrl | None = None
    # Optional explicit requirements. When given, they are used instead of extraction.
    requirements: list[RequirementIn] | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _validate(self) -> Self:
        if len(self.description.strip()) < 20:
            raise ValueError("description must contain at least 20 non-whitespace characters")
        if self.salary_min and self.salary_max and self.salary_max < self.salary_min:
            raise ValueError("salary_max must not be below salary_min")
        if (self.salary_min or self.salary_max) and not self.salary_currency:
            raise ValueError("salary_currency is required when a salary is given")
        return self


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    text: str
    category: RequirementCategory
    importance: RequirementImportance
    terms: list[str]
    details: dict[str, Any]
    extraction_method: ExtractionMethod


class PostingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    source_url: str | None
    content_hash: str
    received_at: datetime


class JobSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str | None
    company: str | None
    location: str | None
    work_arrangement: WorkArrangement
    status: OpportunityStatus
    created_at: datetime


class JobOut(JobSummaryOut):
    employment_type: EmploymentType
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    description: str
    source: str
    source_url: str | None
    extraction_method: ExtractionMethod
    extraction_notes: str | None
    updated_at: datetime
    requirements: list[RequirementOut]
    postings: list[PostingOut]


class IngestionOutcome(StrEnum):
    CREATED = "created"
    # Identical description already submitted; nothing new was stored.
    DUPLICATE_SUBMISSION = "duplicate_submission"
    # A new submission of an existing opportunity (same URL, or same company, title, and
    # location); stored as an additional posting for the existing opportunity.
    LINKED_TO_EXISTING = "linked_to_existing"


class IngestionResult(BaseModel):
    outcome: IngestionOutcome
    posting_id: uuid.UUID
    job: JobOut
