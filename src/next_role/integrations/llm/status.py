"""Aggregation and retention of recorded LLM call events."""

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from next_role.integrations.llm.models import ERROR_OUTCOMES, LLMCallEvent, LLMOutcome, LLMTask
from next_role.integrations.llm.schemas import (
    LLMLastErrorOut,
    LLMLastSuccessOut,
    LLMTaskStatusOut,
)


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


def task_status(
    session: Session, now: datetime, models: dict[LLMTask, str | None], available: set[LLMTask]
) -> list[LLMTaskStatusOut]:
    recent = _counts(session, now - timedelta(hours=24))
    total = _counts(session, None)
    out = []
    for task in LLMTask:
        error = _latest(session, task, ERROR_OUTCOMES)
        success = _latest(session, task, frozenset({LLMOutcome.OK}))
        out.append(
            LLMTaskStatusOut(
                task=task,
                model=models.get(task),
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
            )
        )
    return out
