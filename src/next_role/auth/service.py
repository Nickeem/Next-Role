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

    In memory, per API process: enough for a single-operator deployment. Each attempt is
    counted before the password is checked and only forgiven on success, so concurrent
    requests cannot all slip past the limit. At most ``max_clients`` addresses are tracked;
    stale ones are dropped first, then the oldest.
    """

    def __init__(
        self, max_failures: int = 5, window: float = 300.0, max_clients: int = 10_000
    ) -> None:
        self.max_failures = max_failures
        self.window = window
        self.max_clients = max_clients
        self._failures: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, failures: deque[float], now: float) -> None:
        while failures and now - failures[0] > self.window:
            failures.popleft()

    def _evict(self, now: float) -> None:
        for client in list(self._failures):
            self._prune(self._failures[client], now)
            if not self._failures[client]:
                del self._failures[client]
        while len(self._failures) >= self.max_clients:
            del self._failures[next(iter(self._failures))]

    def attempt(self, client: str) -> None:
        """Count an attempt, or raise RateLimitedError if the client is over the limit."""
        with self._lock:
            now = time.monotonic()
            failures = self._failures.get(client)
            if failures is not None:
                self._prune(failures, now)
            if failures and len(failures) >= self.max_failures:
                raise RateLimitedError("Too many failed logins; try again in a few minutes")
            if failures is None:
                if len(self._failures) >= self.max_clients:
                    self._evict(now)
                failures = self._failures[client] = deque()
            failures.append(now)

    def succeeded(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)

    def tracked_clients(self) -> int:
        with self._lock:
            return len(self._failures)


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
