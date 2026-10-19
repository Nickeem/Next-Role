"""Review workflow over a real database: creation from assessments, decisions, history,
concurrency, the outbox, and the backfill of assessments made before Phase 2."""

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from next_role.errors import ConflictError, InvalidInputError
from next_role.notifications.models import (
    NotificationEventType,
    NotificationOutbox,
    NotificationPreferences,
)
from next_role.review import service
from next_role.review.models import OpportunityReview, ReviewEvent
from next_role.review.schemas import DecisionIn
from next_role.review.workflow import ReviewAction, ReviewEventType, ReviewStatus
from tests.conftest import TEST_DATABASE_URL, alembic_config

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return data


def _candidate(client: TestClient, name: str = "Alex Example") -> str:
    profile = _load("candidate.json")
    profile["full_name"] = name
    response = client.post("/api/v1/candidates", json=profile)
    assert response.status_code == 201
    cid: str = response.json()["id"]
    return cid


def _job(client: TestClient, name: str = "senior-backend-engineer.json") -> str:
    response = client.post("/api/v1/jobs", json=_load(f"jobs/{name}"))
    assert response.status_code == 201
    jid: str = response.json()["job"]["id"]
    return jid


def _assess(client: TestClient, job_id: str, candidate_id: str, as_of: str = "2026-10-01") -> Any:
    response = client.post(
        f"/api/v1/jobs/{job_id}/assessments", json={"candidate_id": candidate_id, "as_of": as_of}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


def _review(session: Session, candidate_id: str, job_id: str) -> OpportunityReview:
    review = service.get_review_for(session, uuid.UUID(candidate_id), uuid.UUID(job_id))
    assert review is not None
    return review


def _outbox(session: Session) -> list[NotificationOutbox]:
    return list(session.scalars(select(NotificationOutbox)))


def _decide(
    factory: sessionmaker[Session], review_id: uuid.UUID, **fields: Any
) -> OpportunityReview:
    with factory.begin() as session:
        return service.apply_decision(session, review_id, DecisionIn(**fields), request_id="req-1")


@pytest.fixture
def pair(client: TestClient) -> tuple[str, str]:
    candidate_id = _candidate(client)
    job_id = _job(client)
    _assess(client, job_id, candidate_id)
    return candidate_id, job_id


def test_first_assessment_opens_review_and_queues_one_notification(
    session: Session, pair: tuple[str, str]
) -> None:
    review = _review(session, *pair)
    assert review.status is ReviewStatus.PENDING_REVIEW
    assert review.version == 1

    events = service.list_events(session, review.id)
    assert [(e.event_type, e.actor) for e in events] == [(ReviewEventType.CREATED, "system")]
    assert events[0].assessment_id == review.latest_assessment_id

    [row] = _outbox(session)
    assert row.event_type is NotificationEventType.REVIEW_READY
    assert row.event_key == f"review.ready:{review.id}"
    assert row.payload["click_path"] == f"/reviews/{review.id}"
    # No job, employer, score, or candidate details leave the server.
    serialized = json.dumps({k: v for k, v in row.payload.items() if k != "click_path"}).lower()
    for private in ("backend", "alex", "python", "69", "moderate"):
        assert private not in serialized


def test_identical_reassessment_changes_nothing(
    client: TestClient, session: Session, pair: tuple[str, str]
) -> None:
    _assess(client, pair[1], pair[0])
    review = _review(session, *pair)
    assert review.version == 1
    assert len(service.list_events(session, review.id)) == 1
    assert len(_outbox(session)) == 1


def test_new_assessment_updates_review_without_changing_decision(
    client: TestClient,
    session: Session,
    session_factory: sessionmaker[Session],
    pair: tuple[str, str],
) -> None:
    review = _review(session, *pair)
    first_assessment = review.latest_assessment_id
    _decide(session_factory, review.id, action="shortlist", expected_version=1)

    body = _assess(client, pair[1], pair[0], as_of="2026-10-02")
    assert body["created"] is True

    session.expire_all()
    review = _review(session, *pair)
    assert review.status is ReviewStatus.SHORTLISTED
    assert review.version == 3
    assert str(review.latest_assessment_id) == body["assessment"]["id"]
    events = service.list_events(session, review.id)
    assert [e.event_type for e in events] == [
        ReviewEventType.CREATED,
        ReviewEventType.SHORTLIST,
        ReviewEventType.ASSESSMENT_UPDATED,
    ]
    # The decision records the assessment the candidate was looking at.
    assert events[1].assessment_id == first_assessment
    assert len(_outbox(session)) == 1


def test_decision_is_recorded_with_reason_and_request_id(
    session: Session, session_factory: sessionmaker[Session], pair: tuple[str, str]
) -> None:
    review_id = _review(session, *pair).id
    until = datetime.now(UTC) + timedelta(days=7)
    review = _decide(
        session_factory,
        review_id,
        action="postpone",
        expected_version=1,
        reason="After the interview loop elsewhere",
        postpone_until=until,
    )
    assert review.status is ReviewStatus.POSTPONED
    assert review.postpone_until == until
    assert review.version == 2

    event = service.list_events(session, review_id)[-1]
    assert event.event_type is ReviewEventType.POSTPONE
    assert event.previous_status is ReviewStatus.PENDING_REVIEW
    assert event.new_status is ReviewStatus.POSTPONED
    assert event.postpone_until == until
    assert event.reason == "After the interview loop elsewhere"
    assert event.actor == "operator"
    assert event.request_id == "req-1"

    # Routine decisions are not notified.
    assert len(_outbox(session)) == 1


def test_stale_version_is_rejected(
    session: Session, session_factory: sessionmaker[Session], pair: tuple[str, str]
) -> None:
    review_id = _review(session, *pair).id
    _decide(session_factory, review_id, action="shortlist", expected_version=1)
    with pytest.raises(ConflictError, match="changed"):
        _decide(
            session_factory,
            review_id,
            action="reject",
            expected_version=1,
            confirm=True,
        )
    session.expire_all()
    assert _review(session, *pair).status is ReviewStatus.SHORTLISTED
    assert len(service.list_events(session, review_id)) == 2


def test_invalid_input_leaves_no_trace(
    session: Session, session_factory: sessionmaker[Session], pair: tuple[str, str]
) -> None:
    review_id = _review(session, *pair).id
    with pytest.raises(InvalidInputError):
        _decide(session_factory, review_id, action="approve_for_drafting", expected_version=1)
    with pytest.raises(ConflictError):
        _decide(session_factory, review_id, action="reopen", expected_version=1, reason="x")
    session.expire_all()
    assert _review(session, *pair).version == 1
    assert len(service.list_events(session, review_id)) == 1


def test_override_of_weak_assessment_requires_reason(
    client: TestClient, session: Session, session_factory: sessionmaker[Session]
) -> None:
    candidate_id = _candidate(client)
    job_id = _job(client, "data-engineer-us.json")
    assert _assess(client, job_id, candidate_id)["assessment"]["tier"] == "weak"
    review_id = _review(session, candidate_id, job_id).id

    with pytest.raises(InvalidInputError, match="reason"):
        _decide(
            session_factory,
            review_id,
            action="approve_for_drafting",
            expected_version=1,
            confirm=True,
        )
    review = _decide(
        session_factory,
        review_id,
        action="approve_for_drafting",
        expected_version=1,
        confirm=True,
        reason="Recruiter confirmed sponsorship",
    )
    assert review.status is ReviewStatus.APPROVED_FOR_DRAFTING


def test_concurrent_decisions_conflict(
    session: Session, session_factory: sessionmaker[Session], pair: tuple[str, str]
) -> None:
    review_id = _review(session, *pair).id
    first = session_factory()
    first.begin()
    service.apply_decision(
        first, review_id, DecisionIn(action=ReviewAction.SHORTLIST, expected_version=1)
    )

    errors: list[Exception] = []

    def second() -> None:
        try:
            _decide(session_factory, review_id, action="reject", expected_version=1, confirm=True)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=second)
    thread.start()
    # The second decision waits on the row lock until the first commits.
    thread.join(timeout=0.5)
    assert thread.is_alive()
    first.commit()
    first.close()
    thread.join(timeout=5)

    assert len(errors) == 1
    assert isinstance(errors[0], ConflictError)
    session.expire_all()
    assert _review(session, *pair).status is ReviewStatus.SHORTLISTED


def test_reviews_are_per_candidate(client: TestClient, session: Session) -> None:
    job_id = _job(client)
    first, second = _candidate(client, "Alex Example"), _candidate(client, "Sam Example")
    _assess(client, job_id, first)
    _assess(client, job_id, second)
    assert _review(session, first, job_id).id != _review(session, second, job_id).id
    assert len(_outbox(session)) == 2


def test_disabled_preference_queues_nothing(client: TestClient, session: Session) -> None:
    session.add(
        NotificationPreferences(
            operator_id="operator", review_ready=False, review_postponed_due=True
        )
    )
    session.commit()
    candidate_id, job_id = _candidate(client), _job(client)
    _assess(client, job_id, candidate_id)
    assert _review(session, candidate_id, job_id).status is ReviewStatus.PENDING_REVIEW
    assert _outbox(session) == []


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE review_events SET reason = 'edited'",
        "DELETE FROM review_events",
        # A cascade from the parent rows must not erase history either.
        "DELETE FROM opportunity_reviews",
        "DELETE FROM candidates",
    ],
)
def test_review_events_are_append_only(
    session: Session, pair: tuple[str, str], statement: str
) -> None:
    with pytest.raises(DBAPIError, match=r"append-only|foreign key"):
        session.execute(text(statement))
        session.flush()
    session.rollback()
    assert session.scalar(text("SELECT count(*) FROM review_events")) > 0


def test_migration_backfills_existing_assessments_without_notifications(
    client: TestClient, engine: Engine, session: Session, pair: tuple[str, str]
) -> None:
    cfg = alembic_config(TEST_DATABASE_URL)
    session.close()
    try:
        command.downgrade(cfg, "0004_assessments")
        command.upgrade(cfg, "head")
        with Session(engine) as fresh:
            review = _review(fresh, *pair)
            assert review.status is ReviewStatus.PENDING_REVIEW
            [event] = service.list_events(fresh, review.id)
            assert event.event_type is ReviewEventType.CREATED
            assert event.reason is not None and "before" in event.reason
            assert fresh.scalar(select(func.count()).select_from(NotificationOutbox)) == 0
            assert fresh.scalar(select(func.count()).select_from(ReviewEvent)) == 1
    finally:
        command.upgrade(cfg, "head")
