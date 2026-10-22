"""Generation runs: creation on approval, revocation, leased claiming, and authorization.

A run is created in the transaction that records the candidate's ``approve_for_drafting``
decision, so an approval and its run commit together. The worker claims runs with
``FOR UPDATE SKIP LOCKED`` in a short transaction and holds a lease (token plus expiry) while
it drafts outside any transaction. Results are persisted only by the holder of the current
lease, and only if the approval still stands.
"""

import logging
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from next_role.documents.models import DocumentBundle, GenerationRun
from next_role.documents.workflow import (
    ACTIVE_RUN_STATUSES,
    BundleStatus,
    RetryPolicy,
    RunIntent,
    RunStatus,
)
from next_role.errors import ConflictError, NotFoundError
from next_role.review.models import OpportunityReview, ReviewEvent
from next_role.review.workflow import ReviewEventType, ReviewStatus

logger = logging.getLogger(__name__)


class StaleAuthorizationError(Exception):
    """The approval to draft was revoked or no longer matches the review's assessment."""


class LeaseLostError(Exception):
    """Another worker took the run over, or it was cancelled, while this one held it."""


def initial_key(event_id: uuid.UUID) -> str:
    return f"initial:{event_id}"


def queue_initial_run(session: Session, review: OpportunityReview, event: ReviewEvent) -> None:
    """Queue drafting for a new approval. Idempotent per approval event."""
    session.execute(
        insert(GenerationRun)
        .values(
            id=uuid.uuid4(),
            review_id=review.id,
            authorization_event_id=event.id,
            intent=RunIntent.INITIAL,
            idempotency_key=initial_key(event.id),
            parameters={},
            status=RunStatus.QUEUED,
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=[GenerationRun.idempotency_key])
    )
    logger.info("generation queued", extra={"review_id": str(review.id)})


def latest_approval(session: Session, review_id: uuid.UUID) -> ReviewEvent | None:
    return session.scalar(
        select(ReviewEvent)
        .where(
            ReviewEvent.review_id == review_id,
            ReviewEvent.event_type == ReviewEventType.APPROVE_FOR_DRAFTING,
        )
        .order_by(ReviewEvent.created_at.desc(), ReviewEvent.id.desc())
        .limit(1)
    )


def revoke(session: Session, review: OpportunityReview, now: datetime) -> None:
    """Called before a review leaves ``approved_for_drafting`` (spec §2).

    Refuses while a run is actively drafting; otherwise cancels pending runs and withdraws the
    bundle, so its document approval no longer counts.
    """
    event = latest_approval(session, review.id)
    if event is None:
        return
    runs = session.scalars(
        select(GenerationRun)
        .where(
            GenerationRun.authorization_event_id == event.id,
            GenerationRun.status.in_(ACTIVE_RUN_STATUSES),
        )
        .with_for_update()
    ).all()
    if any(
        r.status is RunStatus.RUNNING and r.lease_expires_at and r.lease_expires_at > now
        for r in runs
    ):
        raise ConflictError(
            "Documents are being drafted for this approval; try again when drafting finishes"
        )
    for run in runs:
        run.status = RunStatus.CANCELLED
        run.lease_token = None
        run.lease_expires_at = None
        run.finished_at = now
        run.last_error = "approval_revoked"
    session.execute(
        update(DocumentBundle)
        .where(
            DocumentBundle.authorization_event_id == event.id,
            DocumentBundle.status != BundleStatus.WITHDRAWN,
        )
        .values(status=BundleStatus.WITHDRAWN, version=DocumentBundle.version + 1)
    )


def claim_next(
    session: Session, now: datetime, lease: timedelta, max_attempts: int
) -> GenerationRun | None:
    """Claim one due run: queued and due, or running with an expired lease (a crash).

    A crashed run that has used up its attempts is failed instead of claimed.
    """
    while True:
        run = session.scalar(
            select(GenerationRun)
            .where(
                or_(
                    and_(
                        GenerationRun.status == RunStatus.QUEUED,
                        GenerationRun.next_attempt_at <= now,
                    ),
                    and_(
                        GenerationRun.status == RunStatus.RUNNING,
                        GenerationRun.lease_expires_at < now,
                    ),
                )
            )
            .order_by(GenerationRun.next_attempt_at, GenerationRun.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        if run.status is RunStatus.RUNNING and run.attempts >= max_attempts:
            _finish(run, RunStatus.FAILED, now, "lease_expired")
            logger.warning("generation run abandoned", extra={"run_id": str(run.id)})
            continue
        run.status = RunStatus.RUNNING
        run.attempts += 1
        run.lease_token = uuid.uuid4()
        run.lease_expires_at = now + lease
        session.flush()
        return run


def hold(session: Session, run_id: uuid.UUID, token: uuid.UUID) -> GenerationRun:
    """Lock a run for persisting results. Raises LeaseLostError unless this lease holds it."""
    run = session.scalar(
        select(GenerationRun)
        .where(GenerationRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None or run.status is not RunStatus.RUNNING or run.lease_token != token:
        raise LeaseLostError(str(run_id))
    return run


def check_authorization(session: Session, run: GenerationRun) -> ReviewEvent:
    """The approval behind ``run`` must still be the review's current approval, for the
    assessment the review currently points at."""
    review = session.get(OpportunityReview, run.review_id)
    event = latest_approval(session, run.review_id)
    if (
        review is None
        or review.status is not ReviewStatus.APPROVED_FOR_DRAFTING
        or event is None
        or event.id != run.authorization_event_id
        or review.latest_assessment_id != event.assessment_id
    ):
        raise StaleAuthorizationError(str(run.id))
    return event


def complete(run: GenerationRun, now: datetime) -> None:
    _finish(run, RunStatus.COMPLETED, now, None)


def fail(
    run: GenerationRun, now: datetime, error: str, policy: RetryPolicy, retryable: bool
) -> bool:
    """Record a failed attempt. Returns True if the run will be retried."""
    retry_at = policy.next_attempt(run.attempts, now) if retryable else None
    if retry_at is None:
        _finish(run, RunStatus.FAILED, now, error)
        return False
    run.status = RunStatus.QUEUED
    run.next_attempt_at = retry_at
    run.lease_token = None
    run.lease_expires_at = None
    run.last_error = error
    return True


def _finish(run: GenerationRun, status: RunStatus, now: datetime, error: str | None) -> None:
    run.status = status
    run.finished_at = now
    run.lease_token = None
    run.lease_expires_at = None
    run.last_error = error


def runs_for_review(session: Session, review_id: uuid.UUID) -> list[GenerationRun]:
    if session.get(OpportunityReview, review_id) is None:
        raise NotFoundError(f"Review {review_id} not found")
    return list(
        session.scalars(
            select(GenerationRun)
            .where(GenerationRun.review_id == review_id)
            .order_by(GenerationRun.created_at.desc())
        )
    )
