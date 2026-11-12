"""Aggregation and retention of recorded LLM call events."""

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from next_role.integrations.llm.models import ERROR_OUTCOMES, LLMCallEvent, LLMOutcome, LLMTask
from next_role.integrations.llm.resolver import Resolved
from next_role.integrations.llm.schemas import (
    LLMLastAttemptOut,
    LLMLastErrorOut,
    LLMLastSuccessOut,
    LLMTaskStatusOut,
)

# Outcomes recorded for a request actually sent (not drops or fallbacks).
ATTEMPT_OUTCOMES = frozenset({LLMOutcome.OK, *ERROR_OUTCOMES})


def prune(session: Session, before: datetime) -> int:
    """Delete events created before ``before``. Returns how many were deleted."""
    result = session.execute(delete(LLMCallEvent).where(LLMCallEvent.created_at < before))
    return int(getattr(result, "rowcount", 0) or 0)


def _counts(session: Session, since: datetime | None) -> dict[tuple[LLMTask, LLMOutcome], int]:
    query = select(LLMCallEvent.task, LLMCallEvent.outcome, func.count()).group_by(
        LLMCallEvent.task, LLMCallEvent.outcome
    )
    if since is not None:
        query = query.where(LLMCallEvent.created_at >= since)
    return {(task, outcome): n for task, outcome, n in session.execute(query).all()}


def _latest(
    session: Session, task: LLMTask, outcomes: frozenset[LLMOutcome]
) -> LLMCallEvent | None:
    return session.scalar(
        select(LLMCallEvent)
        .where(LLMCallEvent.task == task, LLMCallEvent.outcome.in_(outcomes))
        .order_by(LLMCallEvent.created_at.desc())
        .limit(1)
    )


def _sent_effort(event: LLMCallEvent) -> str | None:
    effort = (event.params or {}).get("reasoning_effort")
    return effort if isinstance(effort, str) else None


def task_status(
    session: Session,
    now: datetime,
    resolved: dict[LLMTask, Resolved | None],
    available: set[LLMTask],
) -> list[LLMTaskStatusOut]:
    recent = _counts(session, now - timedelta(hours=24))
    total = _counts(session, None)
    out = []
    for task in LLMTask:
        error = _latest(session, task, ERROR_OUTCOMES)
        success = _latest(session, task, frozenset({LLMOutcome.OK}))
        attempt = _latest(session, task, ATTEMPT_OUTCOMES)
        now_uses = resolved.get(task)
        out.append(
            LLMTaskStatusOut(
                task=task,
                model=now_uses.model if now_uses else None,
                reasoning_effort=now_uses.reasoning_effort if now_uses else None,
                model_source=now_uses.model_source if now_uses else None,
                effort_source=(
                    now_uses.effort_source if now_uses and now_uses.reasoning_effort else None
                ),
                available=task in available,
                counts_24h={o: recent.get((task, o), 0) for o in LLMOutcome},
                counts={o: total.get((task, o), 0) for o in LLMOutcome},
                last_error=LLMLastErrorOut(
                    message=error.error, at=error.created_at, model=error.model, params=error.params
                )
                if error
                else None,
                last_success=LLMLastSuccessOut(
                    at=success.created_at, model=success.model, latency_ms=success.latency_ms
                )
                if success
                else None,
                last_attempt=LLMLastAttemptOut(
                    at=attempt.created_at,
                    outcome=attempt.outcome,
                    model=attempt.model,
                    reasoning_effort=_sent_effort(attempt),
                )
                if attempt
                else None,
            )
        )
    return out
