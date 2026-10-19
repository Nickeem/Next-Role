"""Queueing notifications in the transactional outbox.

Callers pass the session of the transaction that records the triggering event, so the
outbox row commits (or rolls back) with it. Message texts are fixed and contain no candidate
or job details; the click path leads to a page that requires login.
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from next_role.notifications.models import (
    NotificationEventType,
    NotificationOutbox,
    NotificationPreferences,
    OutboxStatus,
)

logger = logging.getLogger(__name__)

OPERATOR_ID = "operator"

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
