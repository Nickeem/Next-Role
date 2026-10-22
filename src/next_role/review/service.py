"""Use cases for candidate reviews: creation from assessments, decisions, and history.

Every change locks the review row, bumps its version, and appends a ``review_events`` row in
the caller's transaction.
"""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, selectinload

from next_role.documents import runs as document_runs
from next_role.documents.artifacts import bundle_from_key
from next_role.documents.models import DocumentBundle
from next_role.documents.workflow import BundleStatus
from next_role.errors import ConflictError, NotFoundError
from next_role.matching.models import SuitabilityAssessment
from next_role.notifications import service as notifications
from next_role.notifications.models import (
    DOCUMENT_EVENTS,
    NotificationEventType,
    NotificationOutbox,
)
from next_role.review import workflow
from next_role.review.models import OpportunityReview, ReviewEvent
from next_role.review.schemas import DecisionIn, ReviewEventOut, ReviewOut
from next_role.review.workflow import ReviewEventType, ReviewStatus

logger = logging.getLogger(__name__)

OPERATOR = "operator"
SYSTEM = "system"


def review_path(review_id: uuid.UUID) -> str:
    """App-relative link to a review, used in notifications."""
    return f"/reviews/{review_id}"


def record_assessment(session: Session, assessment: SuitabilityAssessment) -> OpportunityReview:
    """Attach a newly created assessment to its review, creating the review if needed.

    The first assessment of a candidate and opportunity pair opens a review in ``pending_review``
    and queues a ``review.ready`` notification. Later assessments only update
    ``latest_assessment_id``; the candidate's decision stands.
    """
    review_id = session.execute(
        insert(OpportunityReview)
        .values(
            id=uuid.uuid4(),
            candidate_id=assessment.candidate_id,
            opportunity_id=assessment.opportunity_id,
            status=ReviewStatus.PENDING_REVIEW,
            latest_assessment_id=assessment.id,
            version=1,
        )
        .on_conflict_do_nothing(
            index_elements=[OpportunityReview.candidate_id, OpportunityReview.opportunity_id]
        )
        .returning(OpportunityReview.id)
    ).scalar()

    if review_id is not None:
        review = _lock(session, review_id)
        _append(session, review, ReviewEventType.CREATED, None, actor=SYSTEM)
        notifications.enqueue(
            session,
            NotificationEventType.REVIEW_READY,
            event_key=f"review.ready:{review.id}",
            click_path=review_path(review.id),
            review_id=review.id,
        )
        logger.info("review opened", extra={"review_id": str(review.id)})
        return review

    review = _lock_pair(session, assessment.candidate_id, assessment.opportunity_id)
    review.latest_assessment_id = assessment.id
    review.version += 1
    _append(session, review, ReviewEventType.ASSESSMENT_UPDATED, review.status, actor=SYSTEM)
    return review


def reminder_key(review: OpportunityReview) -> str:
    """One reminder per scheduled date: rescheduling produces a new key."""
    if review.postpone_until is None:
        raise ValueError("review is not postponed")
    until = review.postpone_until.astimezone(UTC).isoformat()
    return f"review.postponed_due:{review.id}:{until}"


def queue_due_reminders(session: Session, now: datetime, max_age: timedelta) -> int:
    """Queue a reminder for each postponed review whose date has passed (within max_age).

    Safe to call repeatedly: a reminder already queued for the same date is not queued again.
    Returns the number queued.
    """
    due = session.scalars(
        select(OpportunityReview).where(
            OpportunityReview.status == ReviewStatus.POSTPONED,
            OpportunityReview.postpone_until <= now,
            OpportunityReview.postpone_until > now - max_age,
        )
    ).all()
    queued = 0
    for review in due:
        row = notifications.enqueue(
            session,
            NotificationEventType.REVIEW_POSTPONED_DUE,
            event_key=reminder_key(review),
            click_path=review_path(review.id),
            review_id=review.id,
        )
        queued += row is not None
    return queued


def notification_still_relevant(session: Session, row: NotificationOutbox) -> bool:
    """A postponed-review reminder applies only while the review is still postponed to the
    same date; a document notification only while its bundle is not withdrawn. Other
    notifications always apply."""
    if row.event_type in DOCUMENT_EVENTS:
        bundle_id = bundle_from_key(row.event_key)
        bundle = session.get(DocumentBundle, bundle_id) if bundle_id else None
        return bundle is None or bundle.status is not BundleStatus.WITHDRAWN
    if row.event_type is not NotificationEventType.REVIEW_POSTPONED_DUE:
        return True
    review = session.get(OpportunityReview, row.review_id) if row.review_id else None
    return (
        review is not None
        and review.status is ReviewStatus.POSTPONED
        and review.postpone_until is not None
        and reminder_key(review) == row.event_key
    )


def apply_decision(
    session: Session,
    review_id: uuid.UUID,
    payload: DecisionIn,
    request_id: str | None = None,
    now: datetime | None = None,
) -> OpportunityReview:
    """Apply a candidate decision. Raises ConflictError if the version is stale.

    Approving for drafting queues document generation in the same transaction. Leaving
    ``approved_for_drafting`` revokes that approval: it is refused while drafting is in
    progress, and otherwise cancels queued work and withdraws the documents.
    """
    now = now or datetime.now(UTC)
    review = _lock(session, review_id)
    if review.version != payload.expected_version:
        raise ConflictError(
            f"Review {review_id} has changed (version {review.version}); reload and retry"
        )
    assessment = review.latest_assessment
    outcome = workflow.decide(
        review.status,
        workflow.Decision(
            action=payload.action,
            reason=payload.reason,
            confirm=payload.confirm,
            postpone_until=payload.postpone_until,
        ),
        workflow.AssessmentSignal(
            tier=assessment.tier, has_blocking_gaps=assessment.has_blocking_gaps
        ),
        now,
    )
    previous = review.status
    if previous is ReviewStatus.APPROVED_FOR_DRAFTING:
        document_runs.revoke(session, review, now)
    review.status = outcome.new_status
    review.postpone_until = outcome.postpone_until
    review.version += 1
    event = _append(
        session,
        review,
        ReviewEventType(payload.action.value),
        previous,
        actor=OPERATOR,
        reason=payload.reason or None,
        request_id=request_id,
    )
    session.flush()
    if review.status is ReviewStatus.APPROVED_FOR_DRAFTING:
        document_runs.queue_initial_run(session, review, event)
    logger.info(
        "review decision recorded",
        extra={
            "review_id": str(review.id),
            "action": payload.action.value,
            "previous_status": previous.value,
            "new_status": review.status.value,
            "overrides_assessment": outcome.overrides_assessment,
        },
    )
    return review


def get_review(session: Session, review_id: uuid.UUID) -> OpportunityReview:
    review = session.get(OpportunityReview, review_id)
    if review is None:
        raise NotFoundError(f"Review {review_id} not found")
    return review


def get_review_for(
    session: Session, candidate_id: uuid.UUID, opportunity_id: uuid.UUID
) -> OpportunityReview | None:
    return session.scalar(
        select(OpportunityReview).where(
            OpportunityReview.candidate_id == candidate_id,
            OpportunityReview.opportunity_id == opportunity_id,
        )
    )


def list_events(session: Session, review_id: uuid.UUID) -> list[ReviewEvent]:
    get_review(session, review_id)
    return list(
        session.scalars(
            select(ReviewEvent)
            .where(ReviewEvent.review_id == review_id)
            .order_by(ReviewEvent.created_at, ReviewEvent.id)
        )
    )


def review_out(review: OpportunityReview) -> ReviewOut:
    return ReviewOut(
        id=review.id,
        candidate_id=review.candidate_id,
        job_id=review.opportunity_id,
        status=review.status,
        postpone_until=review.postpone_until,
        latest_assessment_id=review.latest_assessment_id,
        version=review.version,
        allowed_actions=workflow.allowed_actions(review.status),
        created_at=review.created_at,
        updated_at=review.updated_at,
    )


def event_out(event: ReviewEvent) -> ReviewEventOut:
    return ReviewEventOut(
        id=event.id,
        review_id=event.review_id,
        event_type=event.event_type,
        previous_status=event.previous_status,
        new_status=event.new_status,
        assessment_id=event.assessment_id,
        reason=event.reason,
        postpone_until=event.postpone_until,
        actor=event.actor,
        created_at=event.created_at,
    )


def _lock(session: Session, review_id: uuid.UUID) -> OpportunityReview:
    review = session.scalar(
        select(OpportunityReview)
        .where(OpportunityReview.id == review_id)
        .options(selectinload(OpportunityReview.latest_assessment))
        .with_for_update(of=OpportunityReview)
        .execution_options(populate_existing=True)
    )
    if review is None:
        raise NotFoundError(f"Review {review_id} not found")
    return review


def _lock_pair(
    session: Session, candidate_id: uuid.UUID, opportunity_id: uuid.UUID
) -> OpportunityReview:
    review = session.scalar(
        select(OpportunityReview)
        .where(
            OpportunityReview.candidate_id == candidate_id,
            OpportunityReview.opportunity_id == opportunity_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if review is None:  # pragma: no cover - the insert above conflicted, so it exists
        raise NotFoundError("Review not found")
    return review


def _append(
    session: Session,
    review: OpportunityReview,
    event_type: ReviewEventType,
    previous: ReviewStatus | None,
    *,
    actor: str,
    reason: str | None = None,
    request_id: str | None = None,
) -> ReviewEvent:
    event = ReviewEvent(
        review_id=review.id,
        event_type=event_type,
        previous_status=previous,
        new_status=review.status,
        assessment_id=review.latest_assessment_id,
        reason=reason,
        postpone_until=review.postpone_until,
        actor=actor,
        request_id=request_id,
    )
    session.add(event)
    return event
