import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from next_role.api.deps import get_session
from next_role.logging_config import request_id_var
from next_role.matching.engine import Tier
from next_role.review import queries, service
from next_role.review.schemas import (
    ActivityPageOut,
    DecisionIn,
    ReviewDetailOut,
    ReviewEventOut,
    ReviewOut,
    ReviewPageOut,
    ReviewSort,
    ReviewSummaryOut,
)
from next_role.review.workflow import ReviewEventType, ReviewStatus

router = APIRouter(tags=["reviews"])


@router.get("/reviews/summary")
def review_summary(
    candidate_id: uuid.UUID | None = None, session: Session = Depends(get_session)
) -> ReviewSummaryOut:
    """Counts by review status and the number of postponed reviews that are due."""
    return queries.summary(session, candidate_id)


@router.get("/reviews")
def list_reviews(
    status: list[ReviewStatus] | None = Query(default=None),
    tier: list[Tier] | None = Query(default=None),
    candidate_id: uuid.UUID | None = None,
    q: str | None = Query(default=None, max_length=200, description="Title or company"),
    due: bool = Query(default=False, description="Only postponed reviews that are due"),
    sort: ReviewSort = ReviewSort.UPDATED,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ReviewPageOut:
    """The review queue. Rejected reviews are hidden unless requested with `status`."""
    return queries.list_reviews(
        session,
        statuses=status,
        tiers=tier,
        candidate_id=candidate_id,
        search=q,
        due_only=due,
        sort=sort,
        limit=limit,
        offset=offset,
    )


@router.get("/reviews/{review_id}")
def get_review(review_id: uuid.UUID, session: Session = Depends(get_session)) -> ReviewDetailOut:
    """The review with its job and the stored assessment it currently points at."""
    return queries.detail(session, review_id)


@router.post(
    "/reviews/{review_id}/decisions",
    responses={
        409: {"description": "Stale expected_version, or action not allowed in this status"},
        422: {"description": "Missing confirmation, reason, or postpone date"},
    },
)
def decide(
    review_id: uuid.UUID, payload: DecisionIn, session: Session = Depends(get_session)
) -> ReviewOut:
    """Record a candidate decision. Approval authorizes future drafting only."""
    review = service.apply_decision(session, review_id, payload, request_id_var.get())
    return service.review_out(review)


@router.get("/reviews/{review_id}/events")
def list_review_events(
    review_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[ReviewEventOut]:
    """The review's append-only history, oldest first."""
    return [service.event_out(e) for e in service.list_events(session, review_id)]


@router.get("/activity")
def activity(
    event_type: list[ReviewEventType] | None = Query(default=None),
    candidate_id: uuid.UUID | None = None,
    actor: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ActivityPageOut:
    """Review events across all reviews, newest first."""
    return queries.activity(
        session,
        event_types=event_type,
        candidate_id=candidate_id,
        actor=actor,
        limit=limit,
        offset=offset,
    )
