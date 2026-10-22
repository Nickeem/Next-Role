"""Generation runs over a real database: creation on approval, revocation, leases,
authorization re-checks, input snapshots, append-only tables, and candidate contact."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from next_role.documents import runs
from next_role.documents.models import DocumentBundle, DraftingMode, GenerationInput, GenerationRun
from next_role.documents.snapshot import build_snapshot, snapshot_hash
from next_role.documents.workflow import BundleStatus, RetryPolicy, RunStatus
from next_role.errors import ConflictError
from next_role.review import service as review_service
from next_role.review.schemas import DecisionIn
from tests.integration.test_review_workflow import _assess, _candidate, _job, _review

LEASE = timedelta(minutes=10)


@pytest.fixture
def review_id(client: TestClient, session: Session) -> uuid.UUID:
    candidate_id = _candidate(client)
    job_id = _job(client)
    _assess(client, job_id, candidate_id)
    return _review(session, candidate_id, job_id).id


def _decide(factory: sessionmaker[Session], review_id: uuid.UUID, **fields: Any) -> None:
    with factory.begin() as session:
        review = review_service.get_review(session, review_id)
        review_service.apply_decision(
            session, review_id, DecisionIn(expected_version=review.version, **fields)
        )


def _approve(factory: sessionmaker[Session], review_id: uuid.UUID) -> None:
    _decide(factory, review_id, action="approve_for_drafting", confirm=True, reason="Good fit")


def _runs(factory: sessionmaker[Session]) -> list[GenerationRun]:
    with factory() as session:
        return list(session.scalars(select(GenerationRun).order_by(GenerationRun.created_at)))


def _claim(factory: sessionmaker[Session], now: datetime) -> GenerationRun | None:
    with factory.begin() as session:
        return runs.claim_next(session, now, LEASE, max_attempts=3)


def _bundle(factory: sessionmaker[Session], run: GenerationRun) -> uuid.UUID:
    """A minimal bundle for the run's approval, as the drafting step would create."""
    with factory.begin() as session:
        snapshot = build_snapshot(session, _event(session, run).assessment_id)
        inp = GenerationInput(
            candidate_id=snapshot.candidate.id,
            opportunity_id=snapshot.job.id,
            assessment_id=snapshot.assessment_id,
            snapshot=snapshot.model_dump(mode="json"),
            snapshot_hash=snapshot_hash(snapshot),
            policy_version="test",
        )
        session.add(inp)
        session.flush()
        bundle = DocumentBundle(
            candidate_id=snapshot.candidate.id,
            opportunity_id=snapshot.job.id,
            review_id=run.review_id,
            authorization_event_id=run.authorization_event_id,
            input_id=inp.id,
            status=BundleStatus.READY_FOR_REVIEW,
            drafting_mode=DraftingMode.DETERMINISTIC,
            drafting_notes=[],
        )
        session.add(bundle)
        session.flush()
        return bundle.id


def _event(session: Session, run: GenerationRun) -> Any:
    event = runs.latest_approval(session, run.review_id)
    assert event is not None
    return event


def test_approval_queues_exactly_one_run(
    session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    _approve(session_factory, review_id)
    [run] = _runs(session_factory)
    assert run.status is RunStatus.QUEUED
    assert run.review_id == review_id
    assert run.idempotency_key == runs.initial_key(run.authorization_event_id)

    # A retried request carries the old version and is rejected; no second run appears.
    with pytest.raises(ConflictError), session_factory.begin() as session:
        review_service.apply_decision(
            session,
            review_id,
            DecisionIn(action="approve_for_drafting", confirm=True, reason="x", expected_version=1),
        )
    assert len(_runs(session_factory)) == 1


def test_other_decisions_queue_nothing(
    session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    _decide(session_factory, review_id, action="shortlist", reason="maybe")
    assert _runs(session_factory) == []


def test_reopen_cancels_a_queued_run(
    session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    _approve(session_factory, review_id)
    _decide(session_factory, review_id, action="reopen", reason="Changed my mind")
    [run] = _runs(session_factory)
    assert run.status is RunStatus.CANCELLED
    assert run.last_error == "approval_revoked"
    assert _claim(session_factory, datetime.now(UTC)) is None


def test_reopen_is_refused_while_drafting_and_allowed_after_the_lease_expires(
    session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    _approve(session_factory, review_id)
    now = datetime.now(UTC)
    assert _claim(session_factory, now) is not None
    with pytest.raises(ConflictError, match="being drafted"):
        _decide(session_factory, review_id, action="reopen", reason="Changed my mind")

    # A crashed worker's lease expires; the run can then be cancelled.
    with session_factory.begin() as session:
        session.execute(
            text("UPDATE generation_runs SET lease_expires_at = now() - interval '1 second'")
        )
    _decide(session_factory, review_id, action="reopen", reason="Changed my mind")
    [run] = _runs(session_factory)
    assert run.status is RunStatus.CANCELLED


def test_reopen_after_drafting_withdraws_the_bundle(
    session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    _approve(session_factory, review_id)
    run = _claim(session_factory, datetime.now(UTC))
    assert run is not None
    bundle_id = _bundle(session_factory, run)
    with session_factory.begin() as session:
        held = runs.hold(session, run.id, run.lease_token)  # type: ignore[arg-type]
        runs.complete(held, datetime.now(UTC))

    _decide(session_factory, review_id, action="reopen", reason="Changed my mind")
    with session_factory() as session:
        bundle = session.get(DocumentBundle, bundle_id)
        assert bundle is not None
        assert bundle.status is BundleStatus.WITHDRAWN
        assert bundle.version == 2

    # Approving again starts a new run for the new approval.
    _approve(session_factory, review_id)
    statuses = [r.status for r in _runs(session_factory)]
    assert statuses == [RunStatus.COMPLETED, RunStatus.QUEUED]


def test_leases_prevent_double_claims_and_recover_crashes(
    session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    _approve(session_factory, review_id)
    now = datetime.now(UTC)
    first = _claim(session_factory, now)
    assert first is not None and first.attempts == 1
    assert _claim(session_factory, now) is None

    # After the lease expires another worker takes over; the first loses its lease.
    second = _claim(session_factory, now + LEASE + timedelta(seconds=1))
    assert second is not None and second.id == first.id and second.attempts == 2
    with pytest.raises(runs.LeaseLostError), session_factory.begin() as session:
        runs.hold(session, first.id, first.lease_token)  # type: ignore[arg-type]

    # A third crash exhausts the attempts: the run fails instead of being claimed again.
    later = now + 2 * LEASE + timedelta(seconds=2)
    assert _claim(session_factory, later) is not None
    assert _claim(session_factory, later + LEASE + timedelta(seconds=1)) is None
    [run] = _runs(session_factory)
    assert (run.status, run.last_error) == (RunStatus.FAILED, "lease_expired")


def test_failed_attempts_retry_with_backoff_then_fail(
    session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    _approve(session_factory, review_id)
    now = datetime.now(UTC)
    policy = RetryPolicy(max_attempts=2)
    run = _claim(session_factory, now)
    assert run is not None
    with session_factory.begin() as session:
        held = runs.hold(session, run.id, run.lease_token)  # type: ignore[arg-type]
        assert runs.fail(held, now, "LLMError", policy, retryable=True) is True
    assert _claim(session_factory, now) is None  # not due yet
    run = _claim(session_factory, now + timedelta(minutes=1))
    assert run is not None
    with session_factory.begin() as session:
        held = runs.hold(session, run.id, run.lease_token)  # type: ignore[arg-type]
        assert runs.fail(held, now, "LLMError", policy, retryable=True) is False
    [run] = _runs(session_factory)
    assert (run.status, run.last_error) == (RunStatus.FAILED, "LLMError")


def test_authorization_goes_stale_when_a_newer_assessment_arrives(
    client: TestClient, session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    _approve(session_factory, review_id)
    [run] = _runs(session_factory)
    with session_factory() as session:
        event = runs.check_authorization(session, run)
        assert event.id == run.authorization_event_id

    with session_factory() as session:
        review = review_service.get_review(session, review_id)
        candidate_id, job_id = review.candidate_id, review.opportunity_id
    _assess(client, str(job_id), str(candidate_id), as_of="2026-10-05")
    with pytest.raises(runs.StaleAuthorizationError), session_factory() as session:
        runs.check_authorization(session, run)


def test_snapshot_pins_the_assessment_and_copies_active_facts(
    client: TestClient, session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    with session_factory() as session:
        review = review_service.get_review(session, review_id)
        snapshot = build_snapshot(session, review.latest_assessment_id)
    assert snapshot.assessment_id == review.latest_assessment_id
    assert snapshot.job.requirements
    assert any(r.evidence_fact_ids for r in snapshot.job.requirements)
    assert snapshot.candidate.contact.email == "alex@example.invalid"
    fact = snapshot.facts[0]
    assert len(fact.content_hash) == 64

    # Retiring a fact removes it from later snapshots; editing changes its hash.
    cid = str(snapshot.candidate.id)
    assert client.delete(f"/api/v1/candidates/{cid}/facts/{fact.id}").status_code == 200
    other = snapshot.facts[1]
    response = client.patch(
        f"/api/v1/candidates/{cid}/facts/{other.id}", json={"title": "Changed title"}
    )
    assert response.status_code == 200
    with session_factory() as session:
        later = build_snapshot(session, review.latest_assessment_id)
    assert fact.id not in {f.id for f in later.facts}
    assert later.fact(other.id).content_hash != other.content_hash  # type: ignore[union-attr]
    assert snapshot_hash(snapshot) != snapshot_hash(later)


def test_append_only_tables_reject_updates_and_deletes(
    session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    _approve(session_factory, review_id)
    [run] = _runs(session_factory)
    _bundle(session_factory, run)
    for statement in (
        "UPDATE generation_inputs SET policy_version = 'x'",
        "DELETE FROM generation_inputs",
    ):
        with pytest.raises(DBAPIError, match="append-only"), session_factory.begin() as s:
            s.execute(text(statement))


def test_review_documents_route_lists_runs(
    client: TestClient, session_factory: sessionmaker[Session], review_id: uuid.UUID
) -> None:
    assert client.get(f"/api/v1/reviews/{review_id}/documents").json() == {
        "runs": [],
        "bundles": [],
    }
    _approve(session_factory, review_id)
    body = client.get(f"/api/v1/reviews/{review_id}/documents").json()
    [run] = body["runs"]
    assert (run["intent"], run["status"]) == ("initial", "queued")
    assert client.get(f"/api/v1/reviews/{uuid.uuid4()}/documents").status_code == 404


def test_candidate_contact_round_trips_and_is_validated(client: TestClient) -> None:
    cid = _candidate(client)
    body = client.get(f"/api/v1/candidates/{cid}").json()
    assert body["contact"]["email"] == "alex@example.invalid"
    response = client.patch(
        f"/api/v1/candidates/{cid}",
        json={"contact": {"email": "new@example.invalid", "links": []}},
    )
    assert response.status_code == 200
    assert response.json()["contact"] == {
        "email": "new@example.invalid",
        "phone": None,
        "links": [],
    }
    bad = client.patch(f"/api/v1/candidates/{cid}", json={"contact": {"email": "not-an-email"}})
    assert bad.status_code == 422
    bad = client.patch(
        f"/api/v1/candidates/{cid}",
        json={"contact": {"links": [{"label": "x", "url": "javascript:alert(1)"}]}},
    )
    assert bad.status_code == 422
