"""Publishing queued notifications: claim, send, and record the outcome with retries.

Each row is handled in its own transaction and claimed with ``FOR UPDATE SKIP LOCKED``, so
several workers never send the same row concurrently. Delivery is at least once: a crash
after the server accepted a message but before the commit sends it again.
"""

import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from next_role.notifications.models import NotificationOutbox, OutboxStatus
from next_role.notifications.publisher import (
    NotificationPublisher,
    OutgoingNotification,
    PublishError,
)

logger = logging.getLogger(__name__)

# Returns False when a queued notification no longer applies and should be cancelled.
RelevanceCheck = Callable[[Session, NotificationOutbox], bool]


@dataclass(frozen=True)
class DispatchPolicy:
    max_attempts: int = 8
    max_age: timedelta = timedelta(hours=24)
    base_delay: timedelta = timedelta(seconds=30)
    max_delay: timedelta = timedelta(hours=1)
    public_base_url: str | None = None

    def backoff(self, attempts: int, rng: random.Random) -> timedelta:
        """Exponential backoff with jitter: 50-100% of base * 2^(attempts-1), capped."""
        # Cap the exponent: base * 2**n overflows timedelta long before attempts runs out.
        exponent = min(max(attempts - 1, 0), 30)
        delay: timedelta = min(self.max_delay, self.base_delay * 2**exponent)
        return delay * (0.5 + rng.random() / 2)


def _notification(row: NotificationOutbox, public_base_url: str | None) -> OutgoingNotification:
    payload = row.payload
    click_path = payload.get("click_path")
    return OutgoingNotification(
        title=payload["title"],
        message=payload["message"],
        priority=int(payload.get("priority", 3)),
        tags=list(payload.get("tags", [])),
        click_url=(
            f"{public_base_url.rstrip('/')}{click_path}" if public_base_url and click_path else None
        ),
    )


def dispatch_one(
    session: Session,
    publisher: NotificationPublisher,
    policy: DispatchPolicy,
    now: datetime,
    *,
    is_relevant: RelevanceCheck | None = None,
    rng: random.Random | None = None,
) -> NotificationOutbox | None:
    """Claim and process the next due row in the caller's transaction. None if none due."""
    row = session.scalar(
        select(NotificationOutbox)
        .where(
            NotificationOutbox.status == OutboxStatus.PENDING,
            NotificationOutbox.next_attempt_at <= now,
        )
        .order_by(NotificationOutbox.next_attempt_at, NotificationOutbox.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if row is None:
        return None

    if now - row.created_at > policy.max_age:
        _cancel(row, "Expired before delivery")
    elif is_relevant is not None and not is_relevant(session, row):
        _cancel(row, "No longer relevant")
    else:
        row.attempts += 1
        try:
            publisher.publish(_notification(row, policy.public_base_url))
        except PublishError as exc:
            row.last_error = exc.detail
            if exc.retryable and row.attempts < policy.max_attempts:
                row.next_attempt_at = now + policy.backoff(row.attempts, rng or random.Random())  # noqa: S311 - jitter only
            else:
                row.status = OutboxStatus.FAILED
        else:
            row.status = OutboxStatus.SENT
            row.sent_at = now
    session.flush()
    logger.info(
        "notification processed",
        extra={
            "outbox_id": str(row.id),
            "event_type": row.event_type.value,
            "status": row.status.value,
            "attempts": row.attempts,
            "error": row.last_error if row.status is not OutboxStatus.SENT else None,
        },
    )
    return row


def _cancel(row: NotificationOutbox, reason: str) -> None:
    row.status = OutboxStatus.CANCELLED
    row.last_error = reason


def dispatch_pending(
    factory: sessionmaker[Session],
    publisher: NotificationPublisher,
    policy: DispatchPolicy,
    now: Callable[[], datetime],
    *,
    is_relevant: RelevanceCheck | None = None,
    limit: int = 50,
) -> int:
    """Process up to ``limit`` due rows, one transaction each. Returns how many were handled."""
    handled = 0
    while handled < limit:
        with factory.begin() as session:
            if dispatch_one(session, publisher, policy, now(), is_relevant=is_relevant) is None:
                break
        handled += 1
    return handled
