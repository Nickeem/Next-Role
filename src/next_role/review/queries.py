"""Read models for the review workspace: queue, summary, details, and activity."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, and_, func, or_, select
from sqlalchemy.orm import Session, contains_eager

from next_role.jobs.models import JobOpportunity
from next_role.jobs.schemas import JobOut
from next_role.jobs.service import get_job
from next_role.matching import service as matching_service
from next_role.matching.engine import Tier
from next_role.matching.models import SuitabilityAssessment
from next_role.review import service
from next_role.review.models import OpportunityReview, ReviewEvent
from next_role.review.schemas import (
    ActivityItemOut,
    ActivityPageOut,
    ReviewDetailOut,
    ReviewJobOut,
    ReviewListItemOut,
    ReviewPageOut,
    ReviewSort,
    ReviewSummaryOut,
)
from next_role.review.workflow import ReviewEventType, ReviewStatus


def _due(now: datetime) -> ColumnElement[bool]:
    return and_(
        OpportunityReview.status == ReviewStatus.POSTPONED,
        OpportunityReview.postpone_until <= now,
    )


def summary(
    session: Session, candidate_id: uuid.UUID | None = None, now: datetime | None = None
) -> ReviewSummaryOut:
    now = now or datetime.now(UTC)
    scope = [OpportunityReview.candidate_id == candidate_id] if candidate_id else []
    rows = session.execute(
        select(OpportunityReview.status, func.count())
        .where(*scope)
        .group_by(OpportunityReview.status)
    ).all()
    counts = dict.fromkeys(ReviewStatus, 0) | {status: n for status, n in rows}
    due = session.scalar(
        select(func.count()).select_from(OpportunityReview).where(*scope, _due(now))
    )
    return ReviewSummaryOut(counts=counts, follow_ups_due=due or 0, total=sum(counts.values()))


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def list_reviews(
    session: Session,
    *,
    statuses: list[ReviewStatus] | None = None,
    tiers: list[Tier] | None = None,
    candidate_id: uuid.UUID | None = None,
    search: str | None = None,
    due_only: bool = False,
    sort: ReviewSort = ReviewSort.UPDATED,
    limit: int = 50,
    offset: int = 0,
    now: datetime | None = None,
) -> ReviewPageOut:
    """The review queue. Rejected reviews are hidden unless requested by status."""
    now = now or datetime.now(UTC)
    query: Select[OpportunityReview] = (
        select(OpportunityReview)
        .join(OpportunityReview.opportunity)
        .join(OpportunityReview.latest_assessment)
    )
    if statuses:
        query = query.where(OpportunityReview.status.in_(statuses))
    else:
        query = query.where(OpportunityReview.status != ReviewStatus.REJECTED)
    if tiers:
        query = query.where(SuitabilityAssessment.tier.in_(tiers))
    if candidate_id:
        query = query.where(OpportunityReview.candidate_id == candidate_id)
    if search and search.strip():
        pattern = _like(search.strip())
        query = query.where(
            or_(
                JobOpportunity.title.ilike(pattern, escape="\\"),
                JobOpportunity.company.ilike(pattern, escape="\\"),
            )
        )
    if due_only:
        query = query.where(_due(now))

    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    order: dict[ReviewSort, ColumnElement[Any]] = {
        ReviewSort.UPDATED: OpportunityReview.updated_at.desc(),
        ReviewSort.SCORE: SuitabilityAssessment.score.desc().nulls_last(),
        ReviewSort.ASSESSED: SuitabilityAssessment.created_at.desc(),
        ReviewSort.CREATED: OpportunityReview.created_at.desc(),
    }
    reviews = session.scalars(
        query.options(
            contains_eager(OpportunityReview.opportunity),
            contains_eager(OpportunityReview.latest_assessment),
        )
        .order_by(order[sort], OpportunityReview.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return ReviewPageOut(
        items=[_list_item(r, now) for r in reviews], total=total, limit=limit, offset=offset
    )


def _list_item(review: OpportunityReview, now: datetime) -> ReviewListItemOut:
    job = review.opportunity
    due = (
        review.status is ReviewStatus.POSTPONED
        and review.postpone_until is not None
        and review.postpone_until <= now
    )
    return ReviewListItemOut(
        id=review.id,
        candidate_id=review.candidate_id,
        job_id=review.opportunity_id,
        status=review.status,
        postpone_until=review.postpone_until,
        follow_up_due=due,
        version=review.version,
        created_at=review.created_at,
        updated_at=review.updated_at,
        job=ReviewJobOut(
            title=job.title,
            company=job.company,
            location=job.location,
            work_arrangement=job.work_arrangement,
            status=job.status,
        ),
        assessment=matching_service.assessment_summary_out(review.latest_assessment),
    )


def detail(session: Session, review_id: uuid.UUID) -> ReviewDetailOut:
    review = service.get_review(session, review_id)
    decision_assessment_id = session.scalar(
        select(ReviewEvent.assessment_id)
        .where(ReviewEvent.review_id == review_id, ReviewEvent.actor == service.OPERATOR)
        .order_by(ReviewEvent.created_at.desc())
        .limit(1)
    )
    return ReviewDetailOut(
        review=service.review_out(review),
        job=JobOut.model_validate(get_job(session, review.opportunity_id)),
        assessment=matching_service.assessment_out(
            matching_service.get_assessment(session, review.latest_assessment_id)
        ),
        decision_assessment_id=decision_assessment_id,
        newer_assessment_available=(
            decision_assessment_id is not None
            and decision_assessment_id != review.latest_assessment_id
        ),
    )


def activity(
    session: Session,
    *,
    event_types: list[ReviewEventType] | None = None,
    candidate_id: uuid.UUID | None = None,
    actor: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> ActivityPageOut:
    """Review events across all reviews, newest first."""
    query = (
        select(ReviewEvent, OpportunityReview, JobOpportunity)
        .join(OpportunityReview, ReviewEvent.review_id == OpportunityReview.id)
        .join(JobOpportunity, OpportunityReview.opportunity_id == JobOpportunity.id)
    )
    if event_types:
        query = query.where(ReviewEvent.event_type.in_(event_types))
    if candidate_id:
        query = query.where(OpportunityReview.candidate_id == candidate_id)
    if actor:
        query = query.where(ReviewEvent.actor == actor)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.execute(
        query.order_by(ReviewEvent.created_at.desc(), ReviewEvent.id).limit(limit).offset(offset)
    ).all()
    items = [
        ActivityItemOut(
            **service.event_out(event).model_dump(),
            candidate_id=review.candidate_id,
            job_id=review.opportunity_id,
            job_title=job.title,
            company=job.company,
        )
        for event, review, job in rows
    ]
    return ActivityPageOut(items=items, total=total, limit=limit, offset=offset)
