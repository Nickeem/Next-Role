"""Outbox dispatch, postponed-review reminders, the worker cycle, and the ntfy routes."""

import json
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from next_role.config import Settings
from next_role.errors import RateLimitedError
from next_role.main import create_app
from next_role.notifications import service as notifications
from next_role.notifications.dispatcher import DispatchPolicy, dispatch_one, dispatch_pending
from next_role.notifications.models import (
    NotificationEventType,
    NotificationOutbox,
    NotificationPreferences,
    OutboxStatus,
)
from next_role.notifications.publisher import OutgoingNotification, PublishError
from next_role.review import service as review_service
from next_role.review.schemas import DecisionIn
from next_role.worker import run_cycle
from tests.conftest import API_KEY, TEST_DATABASE_URL

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
POLICY = DispatchPolicy(max_attempts=3, public_base_url="https://resume.example.internal")


class FakePublisher:
    name = "fake"

    def __init__(self, *outcomes: PublishError | None) -> None:
        self.outcomes = list(outcomes)
        self.sent: list[OutgoingNotification] = []

    def publish(self, notification: OutgoingNotification) -> None:
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if outcome is not None:
            raise outcome
        self.sent.append(notification)


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return data


@pytest.fixture
def review_id(client: TestClient) -> uuid.UUID:
    cid = client.post("/api/v1/candidates", json=_load("candidate.json")).json()["id"]
    jid = client.post("/api/v1/jobs", json=_load("jobs/senior-backend-engineer.json")).json()
    response = client.post(
        f"/api/v1/jobs/{jid['job']['id']}/assessments",
        json={"candidate_id": cid, "as_of": "2026-10-01"},
    )
    assert response.status_code == 201
    [item] = client.get("/api/v1/reviews").json()["items"]
    return uuid.UUID(item["id"])


def _rows(session: Session) -> list[NotificationOutbox]:
    session.expire_all()
    return list(session.scalars(select(NotificationOutbox).order_by(NotificationOutbox.created_at)))


def _now() -> datetime:
    return datetime.now(UTC)


def test_sends_queued_notification_once(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    publisher = FakePublisher()
    beats: list[None] = []
    progress = lambda: beats.append(None)  # noqa: E731
    assert dispatch_pending(session_factory, publisher, POLICY, _now, on_progress=progress) == 1
    assert beats == [None]  # the worker's heartbeat is touched after each row
    assert dispatch_pending(session_factory, publisher, POLICY, _now) == 0

    [note] = publisher.sent
    assert note.title == "Review ready"
    assert note.click_url == f"https://resume.example.internal/reviews/{review_id}"
    [row] = _rows(session)
    assert row.status is OutboxStatus.SENT
    assert row.attempts == 1
    assert row.sent_at is not None


def test_retryable_failure_backs_off_then_succeeds(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    publisher = FakePublisher(PublishError("ntfy returned HTTP 503", retryable=True))
    now = _now()
    with session_factory.begin() as s:
        dispatch_one(s, publisher, POLICY, now)
    [row] = _rows(session)
    assert row.status is OutboxStatus.PENDING
    assert row.attempts == 1
    assert row.last_error == "ntfy returned HTTP 503"
    assert now + timedelta(seconds=15) <= row.next_attempt_at <= now + timedelta(seconds=30)

    # Not due yet.
    with session_factory.begin() as s:
        assert dispatch_one(s, publisher, POLICY, now) is None
    with session_factory.begin() as s:
        dispatch_one(s, publisher, POLICY, now + timedelta(minutes=1))
    [row] = _rows(session)
    assert row.status is OutboxStatus.SENT
    assert row.attempts == 2


def test_attempt_budget_marks_failed(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    error = PublishError("ntfy request timed out", retryable=True)
    publisher = FakePublisher(error, error, error)
    now = _now()
    for _ in range(3):
        with session_factory.begin() as s:
            dispatch_one(s, publisher, POLICY, now)
        now += timedelta(hours=2)
    [row] = _rows(session)
    assert row.status is OutboxStatus.FAILED
    assert row.attempts == 3
    assert publisher.sent == []


def test_permanent_failure_is_not_retried(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    publisher = FakePublisher(PublishError("ntfy returned HTTP 401", retryable=False))
    dispatch_pending(session_factory, publisher, POLICY, _now)
    [row] = _rows(session)
    assert row.status is OutboxStatus.FAILED
    assert row.attempts == 1
    assert row.last_error == "ntfy returned HTTP 401"


def test_stale_notifications_expire(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    publisher = FakePublisher()
    dispatch_pending(session_factory, publisher, POLICY, lambda: _now() + timedelta(days=2))
    [row] = _rows(session)
    assert row.status is OutboxStatus.CANCELLED
    assert row.last_error == "Expired before delivery"
    assert publisher.sent == []


def test_locked_row_is_skipped(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    holder = session_factory()
    holder.begin()
    holder.execute(text("SELECT id FROM notification_outbox FOR UPDATE"))
    publisher = FakePublisher()
    try:
        assert dispatch_pending(session_factory, publisher, POLICY, _now) == 0
    finally:
        holder.rollback()
        holder.close()
    assert dispatch_pending(session_factory, publisher, POLICY, _now) == 1


def _postpone(
    factory: sessionmaker[Session], review_id: uuid.UUID, version: int, until: datetime
) -> None:
    with factory.begin() as s:
        review_service.apply_decision(
            s,
            review_id,
            DecisionIn(action="postpone", expected_version=version, postpone_until=until),
        )


def test_due_reminder_is_queued_once_per_date(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    publisher = FakePublisher()
    run_cycle(session_factory, publisher, POLICY)  # sends review.ready
    until = _now() + timedelta(hours=1)
    _postpone(session_factory, review_id, 1, until)

    assert run_cycle(session_factory, publisher, POLICY) == (0, 0)
    later = until + timedelta(minutes=1)
    assert run_cycle(session_factory, publisher, POLICY, lambda: later) == (1, 1)
    assert run_cycle(session_factory, publisher, POLICY, lambda: later) == (0, 0)

    assert [n.title for n in publisher.sent] == ["Review ready", "Postponed review due"]
    reminder = _rows(session)[-1]
    assert reminder.event_type is NotificationEventType.REVIEW_POSTPONED_DUE
    assert reminder.status is OutboxStatus.SENT
    # Postponing is not changed by the reminder; the review stays postponed and due.
    assert review_service.get_review(session, review_id).status.value == "postponed"


def test_rescheduled_reminder_cancels_old_one(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    publisher = FakePublisher()
    run_cycle(session_factory, publisher, POLICY)
    first = _now() + timedelta(hours=1)
    _postpone(session_factory, review_id, 1, first)
    with session_factory.begin() as s:
        review_service.queue_due_reminders(s, first + timedelta(minutes=1), POLICY.max_age)
    second = first + timedelta(hours=2)
    _postpone(session_factory, review_id, 2, second)

    # The reminder for the first date was queued but is no longer relevant.
    run_cycle(session_factory, publisher, POLICY, lambda: first + timedelta(minutes=2))
    stale = _rows(session)[-1]
    assert stale.status is OutboxStatus.CANCELLED
    assert stale.last_error == "No longer relevant"
    assert [n.title for n in publisher.sent] == ["Review ready"]

    run_cycle(session_factory, publisher, POLICY, lambda: second + timedelta(minutes=1))
    assert [n.title for n in publisher.sent] == ["Review ready", "Postponed review due"]


def test_no_reminder_after_review_leaves_postponed(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    publisher = FakePublisher()
    run_cycle(session_factory, publisher, POLICY)
    until = _now() + timedelta(hours=1)
    _postpone(session_factory, review_id, 1, until)
    with session_factory.begin() as s:
        review_service.apply_decision(
            s, review_id, DecisionIn(action="shortlist", expected_version=2)
        )
    assert run_cycle(session_factory, publisher, POLICY, lambda: until + timedelta(hours=1)) == (
        0,
        0,
    )
    assert len(publisher.sent) == 1


def test_disabled_reminders_are_not_queued(
    session_factory: sessionmaker[Session], session: Session, review_id: uuid.UUID
) -> None:
    publisher = FakePublisher()
    run_cycle(session_factory, publisher, POLICY)
    session.add(
        NotificationPreferences(
            operator_id="operator", review_ready=True, review_postponed_due=False
        )
    )
    session.commit()
    until = _now() + timedelta(hours=1)
    _postpone(session_factory, review_id, 1, until)
    assert run_cycle(session_factory, publisher, POLICY, lambda: until + timedelta(minutes=1)) == (
        0,
        0,
    )


@pytest.fixture
def ntfy_client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        api_key=SecretStr(API_KEY),
        log_json=False,
        ntfy_enabled=True,
        ntfy_base_url="https://ntfy.example.internal",
        ntfy_topic="resume-review",
        ntfy_token=SecretStr("tk_do_not_leak"),
        _env_file=None,
    )
    with TestClient(create_app(settings, session_factory), headers={"X-API-Key": API_KEY}) as c:
        yield c


def test_status_and_test_notification(
    ntfy_client: TestClient, session_factory: sessionmaker[Session], session: Session
) -> None:
    status = ntfy_client.get("/api/v1/integrations/ntfy/status")
    assert status.status_code == 200
    body = status.json()
    assert body["enabled"] is True
    assert body["server"] == "ntfy.example.internal"
    assert body["click_links"] is False
    assert body["counts"] == {"pending": 0, "sent": 0, "failed": 0, "cancelled": 0}
    assert "tk_do_not_leak" not in status.text
    assert "resume-review" not in status.text

    queued = ntfy_client.post("/api/v1/integrations/ntfy/test")
    assert queued.status_code == 202
    assert queued.json()["status"] == "pending"
    assert ntfy_client.post("/api/v1/integrations/ntfy/test").status_code == 429

    publisher = FakePublisher(PublishError("ntfy returned HTTP 403", retryable=False))
    dispatch_pending(session_factory, publisher, POLICY, _now)
    body = ntfy_client.get("/api/v1/integrations/ntfy/status").json()
    assert body["counts"]["failed"] == 1
    assert body["last_error"] == "ntfy returned HTTP 403"
    assert body["last_error_at"] is not None


def test_test_notification_requires_ntfy(client: TestClient) -> None:
    response = client.post("/api/v1/integrations/ntfy/test")
    assert response.status_code == 409
    assert client.get("/api/v1/integrations/ntfy/status").json()["enabled"] is False


def test_test_rate_limit_window(session: Session) -> None:
    now = _now()
    notifications.queue_test(session, now)
    session.flush()
    with pytest.raises(Exception, match="recently"):
        notifications.queue_test(session, now + timedelta(seconds=30))
    session.rollback()


def test_concurrent_test_requests_queue_one(session_factory: sessionmaker[Session]) -> None:
    now = _now()
    outcomes: list[str] = []

    def second() -> None:
        with session_factory() as other:
            try:
                notifications.queue_test(other, now)
                other.commit()
                outcomes.append("queued")
            except RateLimitedError:
                outcomes.append("limited")

    with session_factory() as first:
        notifications.queue_test(first, now)
        # The second request starts while the first is uncommitted and must wait for it.
        thread = threading.Thread(target=second)
        thread.start()
        thread.join(timeout=0.5)
        assert thread.is_alive()
        first.commit()
    thread.join(timeout=5)
    assert outcomes == ["limited"]
    with session_factory() as check:
        count = check.scalar(
            select(func.count()).where(
                NotificationOutbox.event_type == NotificationEventType.INTEGRATION_TEST
            )
        )
    assert count == 1
