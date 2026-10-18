"""Persistence for suitability assessments.

Assessments are immutable snapshots. Re-assessing after the candidate's knowledge base or the
job's requirements change creates a new assessment. Identical inputs return the existing one,
keyed by ``input_fingerprint``.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from next_role.db import Base, IdMixin
from next_role.jobs.models import JobRequirement
from next_role.matching.engine import MatchStatus, Tier


class AssessmentStatus(StrEnum):
    COMPLETED = "completed"


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class SuitabilityAssessment(IdMixin, Base):
    __tablename__ = "suitability_assessments"
    __table_args__ = (UniqueConstraint("candidate_id", "opportunity_id", "input_fingerprint"),)

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[AssessmentStatus] = mapped_column(_enum(AssessmentStatus, "assessment_status"))
    method: Mapped[str] = mapped_column(String(32))
    method_version: Mapped[str] = mapped_column(String(32))
    # Date used for time-dependent rules (experience duration, certificate expiry).
    as_of: Mapped[date] = mapped_column(Date)
    score: Mapped[int | None] = mapped_column(Integer)
    score_excluding_unknowns: Mapped[int | None] = mapped_column(Integer)
    coverage: Mapped[float] = mapped_column(Float)
    tier: Mapped[Tier] = mapped_column(_enum(Tier, "assessment_tier"))
    has_blocking_gaps: Mapped[bool] = mapped_column()
    status_counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    preference_checks: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Hash of candidate knowledge base, job requirements, method version, and as_of.
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    requirement_results: Mapped[list["RequirementAssessment"]] = relationship(
        back_populates="assessment",
        cascade="all, delete-orphan",
        order_by="RequirementAssessment.position",
    )


class RequirementAssessment(IdMixin, Base):
    __tablename__ = "requirement_assessments"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suitability_assessments.id", ondelete="CASCADE"), index=True
    )
    requirement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_requirements.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    status: Mapped[MatchStatus] = mapped_column(_enum(MatchStatus, "match_status"))
    weight: Mapped[int] = mapped_column(Integer)
    credit: Mapped[float] = mapped_column(Float)
    explanation: Mapped[str] = mapped_column(Text)
    # Snapshot of the supporting facts at assessment time (ids, titles, support, reasons).
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)

    assessment: Mapped[SuitabilityAssessment] = relationship(back_populates="requirement_results")
    requirement: Mapped[JobRequirement] = relationship()
