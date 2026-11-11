"""Reading and saving the model and reasoning effort chosen per task (ADR 0008).

Follows the notification-preferences service: the route validates the request shape, this
module checks the values and writes rows, and ``get_session`` owns the transaction.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from next_role.errors import InvalidInputError
from next_role.integrations.llm.catalog import TASK_DEFAULT_EFFORT, lookup
from next_role.integrations.llm.discovery import Listing
from next_role.integrations.llm.models import LLMTask, LLMTaskModel
from next_role.integrations.llm.resolver import ModelResolver
from next_role.integrations.llm.schemas import LLMTaskModelOut, LLMTaskModelsUpdate


def get_task_models(session: Session) -> dict[LLMTask, LLMTaskModel]:
    return {row.task: row for row in session.scalars(select(LLMTaskModel))}


def update_task_models(
    session: Session, update: LLMTaskModelsUpdate, listing: Listing, default_model: str
) -> None:
    """Validate every listed task, then save them all (or none).

    A model must be one the key can use that is in the catalog. An effort must be one the
    task's model (the chosen one, else the default) supports.
    """
    for task, choice in update.tasks.items():
        if choice.model is not None and listing.get(choice.model) is None:
            raise InvalidInputError(
                f"{choice.model} is not a supported model this API key can use ({task.value})"
            )
        if choice.reasoning_effort is not None:
            model = choice.model or default_model
            entry = lookup(model)
            if entry is None:
                raise InvalidInputError(
                    f"{model} takes no reasoning effort; choose a supported model ({task.value})"
                )
            if choice.reasoning_effort not in entry.efforts:
                supported = ", ".join(e.value for e in entry.efforts)
                raise InvalidInputError(
                    f"{model} does not support reasoning effort "
                    f"{choice.reasoning_effort.value} ({task.value}); use one of: {supported}"
                )
    rows = get_task_models(session)
    for task, choice in update.tasks.items():
        row = rows.get(task)
        if choice.model is None and choice.reasoning_effort is None:
            if row is not None:
                session.delete(row)
            continue
        if row is None:
            row = LLMTaskModel(task=task)
            session.add(row)
        row.model = choice.model
        row.reasoning_effort = choice.reasoning_effort
    session.flush()


def task_models_out(
    rows: dict[LLMTask, LLMTaskModel], resolver: ModelResolver | None
) -> list[LLMTaskModelOut]:
    """Saved choices and what calls use now. ``resolver`` is None when no model is set up."""
    out = []
    for task in LLMTask:
        row = rows.get(task)
        resolved = resolver.resolve(task) if resolver else None
        out.append(
            LLMTaskModelOut(
                task=task,
                model=row.model if row else None,
                reasoning_effort=row.reasoning_effort if row else None,
                effective_model=resolved.model if resolved else None,
                effective_reasoning_effort=resolved.reasoning_effort if resolved else None,
                model_source=resolved.model_source if resolved else None,
                effort_source=(
                    resolved.effort_source if resolved and resolved.reasoning_effort else None
                ),
                default_reasoning_effort=TASK_DEFAULT_EFFORT[task],
            )
        )
    return out
