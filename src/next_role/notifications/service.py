"""Queueing notifications in the transactional outbox.

Callers pass the session of the transaction that records the triggering event, so the
outbox row commits (or rolls back) with it. Message texts are fixed and contain no candidate
or job details; the click path leads to a page that requires login.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from next_role.errors import RateLimitedError
from next_role.notifications.models import (
    NotificationEventType,
    NotificationOutbox,
    NotificationPreferences,
    OutboxStatus,
)
from next_role.notifications.schemas import (
    DeliveryStatusOut,
    NotificationPreferencesOut,
    NotificationPreferencesUpdate,
)

logger = logging.getLogger(__name__)

OPERATOR_ID = "operator"
TEST_INTERVAL = timedelta(seconds=60)
# Advisory lock key that serializes test-notification requests (arbitrary, app-unique).
_TEST_LOCK_KEY = 0x6E727465737431

_MESSAGES: dict[NotificationEventType, dict[str, Any]] = {
    NotificationEventType.REVIEW_READY: {
        "title": "Review ready",
        "message": "A role is ready for your review.",
        "priority": 3,
        "tags": ["briefcase"],
    },
    NotificationEventType.REVIEW_POSTPONED_DUE: {
        "title": "Postponed review due",
        "message": "A postponed role is ready for another look.",
        "priority": 3,
        "tags": ["alarm_clock"],
    },
    NotificationEventType.INTEGRATION_TEST: {
        "title": "Test notification",
        "message": "Resume review notifications are connected.",
        "priority": 3,
        "tags": ["white_check_mark"],
    },
}


def get_preferences(session: Session) -> NotificationPreferences:
    """The operator's preferences; defaults (all enabled) if never saved."""
    prefs = session.get(NotificationPreferences, OPERATOR_ID)
    return prefs or NotificationPreferences(
        operator_id=OPERATOR_ID, review_ready=True, review_postponed_due=True
    )


def update_preferences(
    session: Session, update: NotificationPreferencesUpdate
) -> NotificationPreferences:
    prefs = session.get(NotificationPreferences, OPERATOR_ID)
    if prefs is None:
        prefs = get_preferences(session)
        session.add(prefs)
    for field, value in update.model_dump(exclude_none=True).items():
        setattr(prefs, field, value)
    session.flush()
    session.refresh(prefs)
    return prefs


def preferences_out(prefs: NotificationPreferences) -> NotificationPreferencesOut:
    return NotificationPreferencesOut(
        review_ready=prefs.review_ready,
        review_postponed_due=prefs.review_postponed_due,
        updated_at=prefs.updated_at,
    )


def is_enabled(session: Session, event_type: NotificationEventType) -> bool:
    prefs = get_preferences(session)
    match event_type:
        case NotificationEventType.REVIEW_READY:
            return prefs.review_ready
        case NotificationEventType.REVIEW_POSTPONED_DUE:
            return prefs.review_postponed_due
        case NotificationEventType.INTEGRATION_TEST:
            return True


def enqueue(
    session: Session,
    event_type: NotificationEventType,
    event_key: str,
    click_path: str,
    review_id: uuid.UUID | None = None,
) -> NotificationOutbox | None:
    """Queue a notification unless disabled or already queued under ``event_key``.

    Returns the queued row, or None if nothing was queued.
    """
    if not is_enabled(session, event_type):
        return None
    payload = {**_MESSAGES[event_type], "click_path": click_path}
    row_id = session.execute(
        insert(NotificationOutbox)
        .values(
            id=uuid.uuid4(),
            event_key=event_key,
            event_type=event_type,
            review_id=review_id,
            payload=payload,
            status=OutboxStatus.PENDING,
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=[NotificationOutbox.event_key])
        .returning(NotificationOutbox.id)
    ).scalar()
    if row_id is None:
        return None
    logger.info(
        "notification queued", extra={"outbox_id": str(row_id), "event_type": event_type.value}
    )
    return session.scalar(select(NotificationOutbox).where(NotificationOutbox.id == row_id))


def queue_test(session: Session, now: datetime) -> NotificationOutbox:
    """Queue a test notification, at most one per TEST_INTERVAL."""
    # Serialize concurrent requests so two cannot both pass the interval check. The lock is
    # held until the transaction commits, by which time this request's row is visible.
    session.execute(select(func.pg_advisory_xact_lock(_TEST_LOCK_KEY)))
    last = session.scalar(
        select(func.max(NotificationOutbox.created_at)).where(
            NotificationOutbox.event_type == NotificationEventType.INTEGRATION_TEST
        )
    )
    if last is not None and now - last < TEST_INTERVAL:
        raise RateLimitedError("A test notification was sent recently; try again in a minute")
    row = enqueue(
        session,
        NotificationEventType.INTEGRATION_TEST,
        event_key=f"integration.test:{uuid.uuid4()}",
        click_path="/settings",
    )
    if row is None:  # pragma: no cover - tests cannot be disabled and keys are unique
        raise RuntimeError("test notification was not queued")
    return row


def delivery_status(session: Session) -> DeliveryStatusOut:
    counts = dict(
        session.execute(
            select(NotificationOutbox.status, func.count()).group_by(NotificationOutbox.status)
        ).all()
    )
    last_sent_at = session.scalar(select(func.max(NotificationOutbox.sent_at)))
    last_error = session.execute(
        select(NotificationOutbox.last_error, NotificationOutbox.updated_at)
        .where(
            NotificationOutbox.last_error.is_not(None),
            NotificationOutbox.status != OutboxStatus.CANCELLED,
        )
        .order_by(NotificationOutbox.updated_at.desc())
        .limit(1)
    ).first()
    return DeliveryStatusOut(
        counts={status: counts.get(status, 0) for status in OutboxStatus},
        last_accepted_at=last_sent_at,
        last_error=last_error[0] if last_error else None,
        last_error_at=last_error[1] if last_error else None,
    )
