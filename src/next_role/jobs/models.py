"""Persistence models for job opportunities, their raw submissions, and normalized requirements.

* ``job_postings`` keeps every distinct submission exactly as received (source retention).
* ``job_opportunities`` is the normalized, de-duplicated domain record.
* ``job_requirements`` are the requirement statements extracted from the opportunity.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ARRAY, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from next_role.db import Base, IdMixin, TimestampMixin
from next_role.jobs.extraction import (
    EmploymentType,
    RequirementCategory,
    RequirementImportance,
    WorkArrangement,
)


class OpportunityStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class ExtractionMethod(StrEnum):
    # Requirements supplied explicitly in the submission.
    MANUAL = "manual"
    RULES = "rules"
    LLM = "llm"


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class JobOpportunity(IdMixin, TimestampMixin, Base):
    __tablename__ = "job_opportunities"

    # Null means the submission did not state it; it is never guessed.
    title: Mapped[str | None] = mapped_column(String(300))
    company: Mapped[str | None] = mapped_column(String(300))
    location: Mapped[str | None] = mapped_column(String(300))
    work_arrangement: Mapped[WorkArrangement] = mapped_column(
        _enum(WorkArrangement, "work_arrangement")
    )
    employment_type: Mapped[EmploymentType] = mapped_column(
        _enum(EmploymentType, "employment_type")
    )
    salary_min: Mapped[int | None] = mapped_column(Integer)
    salary_max: Mapped[int | None] = mapped_column(Integer)
    salary_currency: Mapped[str | None] = mapped_column(String(3))
    description: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(2000))
    # Deduplication keys. See next_role.jobs.service.
    canonical_url: Mapped[str | None] = mapped_column(String(2000), unique=True)
    identity_key: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[OpportunityStatus] = mapped_column(
        _enum(OpportunityStatus, "opportunity_status"), default=OpportunityStatus.ACTIVE
    )
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        _enum(ExtractionMethod, "extraction_method")
    )
    # Free-form notes about extraction, e.g. why an LLM result was rejected.
    extraction_notes: Mapped[str | None] = mapped_column(Text)

    postings: Mapped[list["JobPosting"]] = relationship(
        back_populates="opportunity", order_by="JobPosting.received_at"
    )
    requirements: Mapped[list["JobRequirement"]] = relationship(
        back_populates="opportunity",
        order_by="JobRequirement.position",
        cascade="all, delete-orphan",
    )


class JobPosting(IdMixin, Base):
    """One distinct submission of a job description, preserved as received."""

    __tablename__ = "job_postings"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(100))
    source_url: Mapped[str | None] = mapped_column(String(2000))
    # The submitted payload, verbatim.
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # SHA-256 of the whitespace/case-normalized description; detects exact resubmissions.
    content_hash: Mapped[str] = mapped_column(String(64), unique=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    opportunity: Mapped[JobOpportunity] = relationship(back_populates="postings")


class JobRequirement(IdMixin, Base):
    __tablename__ = "job_requirements"

    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[RequirementCategory] = mapped_column(
        _enum(RequirementCategory, "requirement_category")
    )
    importance: Mapped[RequirementImportance] = mapped_column(
        _enum(RequirementImportance, "requirement_importance")
    )
    # Canonical skill names referenced by the requirement.
    terms: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    # Category-specific parsed values: min_years, education_level, countries, ...
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    extraction_method: Mapped[ExtractionMethod] = mapped_column(
        _enum(ExtractionMethod, "extraction_method")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    opportunity: Mapped[JobOpportunity] = relationship(back_populates="requirements")
