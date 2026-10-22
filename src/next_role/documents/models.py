"""Persistence for generated documents (Phase 3, ADR 0006 and ADR 0007).

A bundle holds the resume and cover letter drafted for one approval to draft. Its content
lives in immutable, numbered revisions; its look in immutable, numbered styles. The current
revision of each kind, and the current style, are the ones with the highest ``seq``.
``generation_inputs``, ``document_revisions``, ``document_styles``, and
``document_approvals`` are append-only: a database trigger rejects updates and deletes.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from next_role.db import Base, IdMixin, TimestampMixin
from next_role.documents.workflow import (
    ArtifactStatus,
    BundleStatus,
    ClarificationStatus,
    RunIntent,
    RunStatus,
    VerificationOutcome,
)


class DocumentKind(StrEnum):
    RESUME = "resume"
    COVER_LETTER = "cover_letter"


class ArtifactFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"


class RevisionAuthor(StrEnum):
    # Assembled by code from facts, verbatim.
    SYSTEM = "system"
    LLM = "llm"
    OPERATOR = "operator"


class DraftingMode(StrEnum):
    LLM = "llm"
    DETERMINISTIC = "deterministic"


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.clock_timestamp())


class GenerationInput(IdMixin, Base):
    """Immutable snapshot of everything a run drafted from."""

    __tablename__ = "generation_inputs"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT"), index=True
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="RESTRICT")
    )
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suitability_assessments.id"))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    snapshot_hash: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created_at()


class DocumentBundle(IdMixin, TimestampMixin, Base):
    __tablename__ = "document_bundles"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="RESTRICT"), index=True
    )
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="RESTRICT")
    )
    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunity_reviews.id", ondelete="RESTRICT"), index=True
    )
    # One bundle per approval to draft.
    authorization_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_events.id", ondelete="RESTRICT"), unique=True
    )
    input_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("generation_inputs.id"))
    status: Mapped[BundleStatus] = mapped_column(_enum(BundleStatus, "bundle_status"))
    drafting_mode: Mapped[DraftingMode] = mapped_column(_enum(DraftingMode, "drafting_mode"))
    # Why the mode was chosen (e.g. no provider configured), or why a section fell back.
    drafting_notes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    # Incremented on every change; clients send it back to detect stale edits and approvals.
    version: Mapped[int] = mapped_column(Integer, default=1)


class GenerationRun(IdMixin, TimestampMixin, Base):
    """A unit of generation work, claimed by the worker with a lease."""

    __tablename__ = "generation_runs"
    __table_args__ = (
        Index("ix_generation_runs_status_next_attempt_at", "status", "next_attempt_at"),
    )

    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunity_reviews.id", ondelete="RESTRICT"), index=True
    )
    authorization_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_events.id", ondelete="RESTRICT"), index=True
    )
    # Null for the initial run until it creates the bundle.
    bundle_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("document_bundles.id", ondelete="RESTRICT"), index=True
    )
    intent: Mapped[RunIntent] = mapped_column(_enum(RunIntent, "run_intent"))
    # "initial:<event id>" for the run an approval starts; a client-supplied key otherwise.
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    # Intent-specific: document kind, section id, revision ids.
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    input_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation_inputs.id"))
    status: Mapped[RunStatus] = mapped_column(_enum(RunStatus, "run_status"))
    stage: Mapped[str | None] = mapped_column(String(32))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set while claimed. Results are only persisted by the holder of the current token.
    lease_token: Mapped[uuid.UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A short, redacted code (e.g. "stale_authorization", "LLMError"), never content.
    last_error: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentRevision(IdMixin, Base):
    __tablename__ = "document_revisions"
    __table_args__ = (UniqueConstraint("bundle_id", "kind", "seq"),)

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_bundles.id", ondelete="RESTRICT")
    )
    kind: Mapped[DocumentKind] = mapped_column(_enum(DocumentKind, "document_kind"))
    # 1, 2, 3… per bundle and kind; the highest is current.
    seq: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_revisions.id"))
    schema_version: Mapped[str] = mapped_column(String(32))
    # A next_role.documents.schemas.ResumeDocument or CoverLetterDocument.
    content: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[RevisionAuthor] = mapped_column(_enum(RevisionAuthor, "revision_author"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("generation_runs.id"))
    # Short operator- or system-facing description of the change.
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created_at()


class DocumentVerification(IdMixin, Base):
    """The outcome of checking one revision against the candidate's facts."""

    __tablename__ = "document_verifications"

    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="RESTRICT"), index=True
    )
    check_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[VerificationOutcome] = mapped_column(
        _enum(VerificationOutcome, "verification_outcome")
    )
    # Per claim: id, path, status, issues, cited fact ids and their content hashes.
    claims: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Document-level issues (schema, cross-document).
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Whether the LLM entailment check ran for this verification.
    semantic_checked: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _created_at()


class ClarificationRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "clarification_requests"
    __table_args__ = (UniqueConstraint("bundle_id", "key"),)

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_bundles.id", ondelete="RESTRICT")
    )
    kind: Mapped[DocumentKind] = mapped_column(_enum(DocumentKind, "document_kind"))
    # Deterministic per topic (e.g. "requirement:<id>"), so regenerating does not duplicate.
    key: Mapped[str] = mapped_column(String(200))
    question: Mapped[str] = mapped_column(Text)
    # Material questions must be answered, or dismissed with a reason, before approval.
    material: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[ClarificationStatus] = mapped_column(
        _enum(ClarificationStatus, "clarification_status")
    )
    answer: Mapped[str | None] = mapped_column(Text)
    # "career_fact" answers become a candidate fact; "letter_statement" answers stay here.
    answer_kind: Mapped[str | None] = mapped_column(String(32))
    proposed_fact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("candidate_facts.id", ondelete="SET NULL")
    )
    dismiss_reason: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentStyle(IdMixin, Base):
    __tablename__ = "document_styles"
    __table_args__ = (UniqueConstraint("bundle_id", "seq"),)

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_bundles.id", ondelete="RESTRICT")
    )
    seq: Mapped[int] = mapped_column(Integer)
    template_id: Mapped[str] = mapped_column(String(32))
    template_version: Mapped[str] = mapped_column(String(16))
    # A next_role.documents.schemas.StyleSettings.
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB)
    style_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()


class DocumentArtifact(IdMixin, TimestampMixin, Base):
    """A rendered file, claimed by the renderer with a lease while it renders."""

    __tablename__ = "document_artifacts"
    __table_args__ = (
        UniqueConstraint("revision_id", "style_id", "format"),
        Index("ix_document_artifacts_status_next_attempt_at", "status", "next_attempt_at"),
    )

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_bundles.id", ondelete="RESTRICT"), index=True
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_revisions.id", ondelete="RESTRICT")
    )
    style_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_styles.id", ondelete="RESTRICT")
    )
    format: Mapped[ArtifactFormat] = mapped_column(_enum(ArtifactFormat, "artifact_format"))
    status: Mapped[ArtifactStatus] = mapped_column(_enum(ArtifactStatus, "artifact_status"))
    # Generated by the server: "<bundle_id>/<artifact_id>.<ext>".
    storage_key: Mapped[str | None] = mapped_column(String(200))
    sha256: Mapped[str | None] = mapped_column(String(64))
    bytes: Mapped[int | None] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer)
    renderer_version: Mapped[str | None] = mapped_column(String(64))
    # Checks run on the file, with warnings (e.g. longer than the target page count).
    qa: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    lease_token: Mapped[uuid.UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    rendered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentApproval(IdMixin, Base):
    """The candidate's approval of exact revisions, style, and files. Append-only."""

    __tablename__ = "document_approvals"

    bundle_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_bundles.id", ondelete="RESTRICT"), index=True
    )
    authorization_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("review_events.id", ondelete="RESTRICT")
    )
    resume_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_revisions.id"))
    cover_letter_revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_revisions.id"))
    style_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_styles.id"))
    # [{id, kind, format, sha256}] for each approved file.
    artifacts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    verification_ids: Mapped[list[str]] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()
