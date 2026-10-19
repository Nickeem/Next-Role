"""Background worker: queues postponed-review reminders and publishes queued notifications.

Run with ``python -m next_role.worker`` (``--once`` for a single cycle). It uses the same
image and configuration as the API. With ntfy disabled it stays idle.
"""

import argparse
import logging
import signal
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType

from sqlalchemy.orm import Session, sessionmaker

from next_role.config import Settings, get_settings
from next_role.db import get_engine, get_sessionmaker
from next_role.integrations.ntfy import build_ntfy_publisher
from next_role.logging_config import configure_logging
from next_role.notifications.dispatcher import DispatchPolicy, dispatch_pending
from next_role.notifications.publisher import NotificationPublisher
from next_role.review import service as review_service

logger = logging.getLogger(__name__)

# Touched after every loop iteration; the Compose healthcheck checks its age.
HEARTBEAT = Path(tempfile.gettempdir()) / "next-role-worker.heartbeat"


def _now() -> datetime:
    return datetime.now(UTC)


def policy_from(settings: Settings) -> DispatchPolicy:
    return DispatchPolicy(
        max_attempts=settings.notification_max_attempts,
        max_age=timedelta(hours=settings.notification_max_age_hours),
        public_base_url=settings.public_base_url,
    )


def run_cycle(
    factory: sessionmaker[Session],
    publisher: NotificationPublisher,
    policy: DispatchPolicy,
    now: Callable[[], datetime] = _now,
) -> tuple[int, int]:
    """Queue due reminders, then publish due notifications. Returns (queued, processed)."""
    with factory.begin() as session:
        queued = review_service.queue_due_reminders(session, now(), policy.max_age)
    processed = dispatch_pending(
        factory, publisher, policy, now, is_relevant=review_service.notification_still_relevant
    )
    if queued or processed:
        logger.info("worker cycle", extra={"reminders_queued": queued, "processed": processed})
    return queued, processed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m next_role.worker")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument(
        "--check", action="store_true", help="exit 0 if the running worker loop is alive"
    )
    args = parser.parse_args(argv)
    if args.check:
        # Three missed polls (or 60 s, whichever is longer) means the loop is stuck.
        poll = get_settings().worker_poll_seconds
        return 0 if healthy(max(60.0, 3 * poll)) else 1

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    # httpx logs each request URL, which includes the ntfy topic.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    factory = get_sessionmaker(get_engine(settings.database_url))
    publisher = build_ntfy_publisher(settings)
    policy = policy_from(settings)

    stop = threading.Event()

    def _stop(signum: int, _: FrameType | None) -> None:
        logger.info("worker stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if publisher is None:
        logger.warning("ntfy is disabled (NEXT_ROLE_NTFY_ENABLED=false); worker is idle")
    else:
        logger.info("worker started", extra={"poll_seconds": settings.worker_poll_seconds})

    while not stop.is_set():
        if publisher is not None:
            try:
                run_cycle(factory, publisher, policy)
            except Exception:
                # A failed cycle (e.g. database restarting) is retried on the next poll.
                logger.exception("worker cycle failed")
        HEARTBEAT.touch()
        if args.once:
            break
        stop.wait(settings.worker_poll_seconds)
    return 0


def healthy(max_age_seconds: float) -> bool:
    """Whether the loop has completed an iteration recently (used by --check)."""
    try:
        return time.time() - HEARTBEAT.stat().st_mtime <= max_age_seconds
    except FileNotFoundError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
