"""Operator login, session lookup, and logout."""

import hashlib
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from next_role.auth.models import OperatorSession
from next_role.errors import RateLimitedError

logger = logging.getLogger(__name__)


class InvalidCredentialsError(Exception):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class NewSession:
    token: str
    record: OperatorSession


class LoginThrottle:
    """At most ``max_failures`` failed logins per client within ``window`` seconds.

    In memory, per API process: enough for a single-operator deployment.
    """

    def __init__(self, max_failures: int = 5, window: float = 300.0) -> None:
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, client: str, now: float) -> deque[float]:
        failures = self._failures.setdefault(client, deque())
        while failures and now - failures[0] > self.window:
            failures.popleft()
        return failures

    def check(self, client: str) -> None:
        with self._lock:
            if len(self._recent(client, time.monotonic())) >= self.max_failures:
                raise RateLimitedError("Too many failed logins; try again in a few minutes")

    def failed(self, client: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._recent(client, now).append(now)

    def succeeded(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)


def login(
    session: Session, password: str, expected: str, ttl: timedelta, now: datetime
) -> NewSession:
    """Create a session if ``password`` matches. Raises InvalidCredentialsError otherwise."""
    if not secrets.compare_digest(password.encode(), expected.encode()):
        raise InvalidCredentialsError
    session.execute(delete(OperatorSession).where(OperatorSession.expires_at <= now))
    token = secrets.token_urlsafe(32)
    record = OperatorSession(
        token_hash=_hash(token), csrf_token=secrets.token_urlsafe(32), expires_at=now + ttl
    )
    session.add(record)
    session.flush()
    logger.info("operator logged in", extra={"session_id": str(record.id)})
    return NewSession(token=token, record=record)


def find_session(session: Session, token: str, now: datetime) -> OperatorSession | None:
    """The live session for a cookie value, or None if unknown or expired."""
    record = session.scalar(
        select(OperatorSession).where(OperatorSession.token_hash == _hash(token))
    )
    if record is None or record.expires_at <= now:
        return None
    return record


def logout(session: Session, record: OperatorSession) -> None:
    session.delete(record)
    session.flush()
    logger.info("operator logged out", extra={"session_id": str(record.id)})
