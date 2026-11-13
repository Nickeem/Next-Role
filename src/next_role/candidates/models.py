"""Persistence models for the candidate knowledge base.

All career claims share one table, ``candidate_facts``, discriminated by ``fact_type``.
Type-specific details live in validated JSONB ``attributes``. Provenance is recorded twice:
``verification_status`` says whether the candidate confirmed the fact, and ``fact_evidence``
links the fact to evidence sources. See docs/decisions/0002-unified-candidate-facts.md.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from next_role.db import Base, IdMixin, TimestampMixin


class FactType(StrEnum):
    EXPERIENCE = "experience"
    SKILL = "skill"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    PROJECT = "project"
    ACHIEVEMENT = "achievement"


class VerificationStatus(StrEnum):
    # Entered by or for the candidate but not yet confirmed.
    SELF_REPORTED = "self_reported"
    # Explicitly confirmed as accurate by the candidate.
    USER_VERIFIED = "user_verified"


class EvidenceKind(StrEnum):
    RESUME = "resume"
    CERTIFICATE = "certificate"
    TRANSCRIPT = "transcript"
    PORTFOLIO = "portfolio"
    PROFILE = "profile"
    REFERENCE = "reference"
    OTHER = "other"


class FactOrigin(StrEnum):
    """How a fact was created. Not part of its content: it never affects matching."""

    # Entered in the web workspace.
    MANUAL = "manual"
    # Added through the single-fact API.
    API = "api"
    # Part of a profile import (POST /candidates).
    IMPORT = "import"
    # An answer to a document question, or a claim confirmed in the document editor.
    DOCUMENT_QUESTION = "document_question"


ABOUT_ME_MAX = 2000


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class Candidate(IdMixin, TimestampMixin, Base):
    __tablename__ = "candidates"

    full_name: Mapped[str] = mapped_column(String(200))
    headline: Mapped[str | None] = mapped_column(String(300))
    # The candidate's self-description (voice, motivation, working style). Self-reported and
    # never confirmed; personal statements in documents may cite it (ADR 0009).
    about_me: Mapped[str | None] = mapped_column(String(ABOUT_ME_MAX))
    location: Mapped[str | None] = mapped_column(String(200))
    # Validated by next_role.candidates.schemas.CandidateContact.
    contact: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    # Validated by next_role.candidates.schemas.CandidatePreferences.
    preferences: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    evidence_sources: Mapped[list["EvidenceSource"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="EvidenceSource.created_at",
    )
    facts: Mapped[list["CandidateFact"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
        order_by="CandidateFact.created_at",
    )


class EvidenceSource(IdMixin, Base):
    """A document or statement that supports one or more career facts.

    ``reference`` is a pointer (URI, file name, or description), not the document content.
    """

    __tablename__ = "evidence_sources"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[EvidenceKind] = mapped_column(_enum(EvidenceKind, "evidence_kind"))
    title: Mapped[str] = mapped_column(String(300))
    reference: Mapped[str | None] = mapped_column(String(2000))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    candidate: Mapped[Candidate] = relationship(back_populates="evidence_sources")


class FactEvidence(Base):
    __tablename__ = "fact_evidence"

    fact_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidate_facts.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_sources.id", ondelete="CASCADE"), primary_key=True
    )


class CandidateFact(IdMixin, TimestampMixin, Base):
    __tablename__ = "candidate_facts"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    fact_type: Mapped[FactType] = mapped_column(_enum(FactType, "fact_type"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    organization: Mapped[str | None] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(default=False)
    # Canonical skill names (next_role.skills.normalize_skill) explicitly attached to the fact.
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(100)), default=list)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    verification_status: Mapped[VerificationStatus] = mapped_column(
        _enum(VerificationStatus, "verification_status"), default=VerificationStatus.SELF_REPORTED
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin: Mapped[FactOrigin] = mapped_column(_enum(FactOrigin, "fact_origin"))
    # False keeps the fact off generated documents; it still counts for matching.
    include_in_documents: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    # E.g. an achievement belongs to an experience or project.
    parent_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_facts.id", ondelete="SET NULL")
    )
    # Retired facts are kept for auditability but excluded from matching.
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    candidate: Mapped[Candidate] = relationship(back_populates="facts")
    evidence_sources: Mapped[list[EvidenceSource]] = relationship(
        secondary="fact_evidence", order_by=EvidenceSource.created_at
    )
