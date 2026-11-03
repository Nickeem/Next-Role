"""Background worker: drafts documents, queues postponed-review reminders, and publishes
queued notifications.

Run with ``python -m next_role.worker`` (``--once`` for a single cycle). It uses the same
image and configuration as the API. Document generation always runs; notification work only
when ntfy is enabled.
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

from next_role.config import LLMProviderName, Settings, get_settings
from next_role.db import get_engine, get_sessionmaker
from next_role.documents.generation import GenerationContext, drafting_provider, process_next
from next_role.integrations.llm import status as llm_status
from next_role.integrations.llm.factory import build_llm_provider
from next_role.integrations.llm.gateway import LLMGateway, SessionRecorder
from next_role.integrations.ntfy import build_ntfy_publisher
from next_role.logging_config import configure_logging
from next_role.notifications.dispatcher import DispatchPolicy, dispatch_pending
from next_role.notifications.publisher import NotificationPublisher
from next_role.review import service as review_service

logger = logging.getLogger(__name__)

# Touched after every loop iteration and every dispatched row; the Compose healthcheck
# checks its age.
HEARTBEAT = Path(tempfile.gettempdir()) / "next-role-worker.heartbeat"


def touch_heartbeat() -> None:
    HEARTBEAT.touch()


def health_threshold(settings: Settings) -> float:
    """Seconds without a heartbeat before the worker counts as stuck.

    The longest normal gap is one poll plus one publish that runs to its timeout (the
    heartbeat is touched between rows), so allow three polls or two such gaps, at least 60 s.
    """
    poll = settings.worker_poll_seconds
    threshold = max(60.0, 3 * poll, 2 * (poll + settings.ntfy_timeout_seconds))
    if settings.llm_provider is not LLMProviderName.NONE:
        # Drafting touches the heartbeat between model calls; each call may retry once.
        threshold = max(threshold, poll + 4 * settings.llm_timeout_seconds)
    return threshold


def _now() -> datetime:
    return datetime.now(UTC)


def policy_from(settings: Settings) -> DispatchPolicy:
    return DispatchPolicy(
        max_attempts=settings.notification_max_attempts,
        max_age=timedelta(hours=settings.notification_max_age_hours),
        public_base_url=settings.public_base_url,
    )


# Runs executed per cycle, so notifications are not starved by a backlog of drafting.
MAX_RUNS_PER_CYCLE = 5


def generation_context(
    settings: Settings, factory: sessionmaker[Session], on_progress: Callable[[], None] | None
) -> GenerationContext:
    provider, reason = drafting_provider(settings, build_llm_provider(settings))
    return GenerationContext(
        factory=factory,
        llm=LLMGateway(provider, SessionRecorder(factory)) if provider else None,
        no_llm_reason=reason,
        lease=timedelta(seconds=settings.generation_lease_seconds),
        max_attempts=settings.generation_max_attempts,
        now=_now,
        on_progress=on_progress,
    )


def run_generation(ctx: GenerationContext, limit: int = MAX_RUNS_PER_CYCLE) -> int:
    """Execute up to ``limit`` due generation runs. Returns how many ran."""
    done = 0
    while done < limit and process_next(ctx):
        done += 1
        ctx.progress()
    return done


def prune_llm_events(
    factory: sessionmaker[Session], retention_days: int, now: Callable[[], datetime] = _now
) -> int:
    """Delete LLM call events older than the retention window. Returns how many."""
    with factory.begin() as session:
        deleted = llm_status.prune(session, now() - timedelta(days=retention_days))
    if deleted:
        logger.info("pruned llm call events", extra={"deleted": deleted})
    return deleted


def run_cycle(
    factory: sessionmaker[Session],
    publisher: NotificationPublisher,
    policy: DispatchPolicy,
    now: Callable[[], datetime] = _now,
    on_progress: Callable[[], None] | None = None,
) -> tuple[int, int]:
    """Queue due reminders, then publish due notifications. Returns (queued, processed)."""
    with factory.begin() as session:
        queued = review_service.queue_due_reminders(session, now(), policy.max_age)
    processed = dispatch_pending(
        factory,
        publisher,
        policy,
        now,
        is_relevant=review_service.notification_still_relevant,
        on_progress=on_progress,
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
        return 0 if healthy(health_threshold(get_settings())) else 1

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    # httpx logs each request URL, which includes the ntfy topic.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    factory = get_sessionmaker(get_engine(settings.database_url))
    publisher = build_ntfy_publisher(settings)
    policy = policy_from(settings)
    generation = generation_context(settings, factory, touch_heartbeat)

    stop = threading.Event()

    def _stop(signum: int, _: FrameType | None) -> None:
        logger.info("worker stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    if publisher is None:
        logger.warning("ntfy is disabled (NEXT_ROLE_NTFY_ENABLED=false); notifications are idle")
    logger.info(
        "worker started",
        extra={
            "poll_seconds": settings.worker_poll_seconds,
            "drafting": "llm" if generation.llm else "deterministic",
        },
    )

    while not stop.is_set():
        try:
            run_generation(generation)
            if publisher is not None:
                run_cycle(factory, publisher, policy, on_progress=touch_heartbeat)
            # Last, so a pruning failure never delays drafting or notifications.
            prune_llm_events(factory, settings.llm_event_retention_days)
        except Exception:
            # A failed cycle (e.g. database restarting) is retried on the next poll.
            logger.exception("worker cycle failed")
        touch_heartbeat()
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
