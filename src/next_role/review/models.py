"""Persistence for candidate reviews of assessed opportunities.

``opportunity_reviews`` holds the current state for each candidate and opportunity pair.
``review_events`` is its append-only history: a database trigger rejects updates
and deletes, and the foreign key to the review restricts deletes so cascades cannot remove it.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from next_role.db import Base, IdMixin, TimestampMixin
from next_role.jobs.models import JobOpportunity
from next_role.matching.models import SuitabilityAssessment
from next_role.review.workflow import ReviewEventType, ReviewStatus


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [m.value for m in e],
    )


class OpportunityReview(IdMixin, TimestampMixin, Base):
    __tablename__ = "opportunity_reviews"
    __table_args__ = (
        UniqueConstraint("candidate_id", "opportunity_id"),
        Index("ix_opportunity_reviews_status_postpone_until", "status", "postpone_until"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"))
    opportunity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("job_opportunities.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[ReviewStatus] = mapped_column(_enum(ReviewStatus, "review_status"))
    # Set only while postponed.
    postpone_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The most recent assessment for this candidate and opportunity pair.
    latest_assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("suitability_assessments.id")
    )
    # Incremented on every change; clients send it back to detect stale decisions.
    version: Mapped[int] = mapped_column(Integer)

    latest_assessment: Mapped[SuitabilityAssessment] = relationship()
    opportunity: Mapped[JobOpportunity] = relationship()


class ReviewEvent(IdMixin, Base):
    __tablename__ = "review_events"

    review_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("opportunity_reviews.id", ondelete="RESTRICT"), index=True
    )
    event_type: Mapped[ReviewEventType] = mapped_column(_enum(ReviewEventType, "review_event_type"))
    previous_status: Mapped[ReviewStatus | None] = mapped_column(
        _enum(ReviewStatus, "previous_status")
    )
    new_status: Mapped[ReviewStatus] = mapped_column(_enum(ReviewStatus, "new_status"))
    # The assessment the candidate was looking at, or the one that triggered the event.
    assessment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suitability_assessments.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    postpone_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "operator" for candidate decisions, "system" for assessment-driven events.
    actor: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(64))
    # clock_timestamp() rather than now(), so events in one transaction stay ordered.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.clock_timestamp(), index=True
    )
