from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from next_role.api.deps import get_app_settings, get_session
from next_role.config import LLMProviderName, Settings
from next_role.errors import ConflictError
from next_role.integrations.llm import status, task_models
from next_role.integrations.llm.discovery import Listing, ModelDiscovery
from next_role.integrations.llm.models import LLMTask
from next_role.integrations.llm.resolver import (
    DbResolver,
    ModelResolver,
    selection_unavailable_reason,
)
from next_role.integrations.llm.schemas import (
    LLMModelOut,
    LLMModelsOut,
    LLMStatusOut,
    LLMTaskModelsOut,
    LLMTaskModelsUpdate,
)

router = APIRouter(tags=["integrations"])

# Tasks that send candidate data and so also need NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA.
CANDIDATE_DATA_TASKS = frozenset(
    {LLMTask.RESUME_DRAFTING, LLMTask.LETTER_DRAFTING, LLMTask.SEMANTIC_CHECK}
)


def get_resolver(request: Request) -> ModelResolver:
    resolver: ModelResolver = request.app.state.llm_resolver
    return resolver


def get_discovery(request: Request) -> ModelDiscovery | None:
    discovery: ModelDiscovery | None = request.app.state.llm_discovery
    return discovery


@router.get("/integrations/llm/status")
def llm_status(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    resolver: ModelResolver = Depends(get_resolver),
) -> LLMStatusOut:
    """Per-task LLM call outcomes. Never discloses the base URL path, API key or content."""
    enabled = settings.llm_provider is not LLMProviderName.NONE
    available = {
        task
        for task in LLMTask
        if enabled and (task not in CANDIDATE_DATA_TASKS or settings.llm_allow_candidate_data)
    }
    resolved = {task: resolver.resolve(task) if enabled else None for task in LLMTask}
    return LLMStatusOut(
        provider=settings.llm_provider,
        enabled=enabled,
        host=urlsplit(settings.llm_base_url).hostname
        if enabled and settings.llm_base_url
        else None,
        allow_external=settings.llm_allow_external,
        allow_candidate_data=settings.llm_allow_candidate_data,
        retention_days=settings.llm_event_retention_days,
        tasks=status.task_status(session, datetime.now(UTC), resolved, available),
    )


def _listing(settings: Settings, discovery: ModelDiscovery | None) -> tuple[Listing, str | None]:
    """The current listing and, when it cannot be used, why."""
    reason = selection_unavailable_reason(settings)
    if reason is not None or discovery is None:
        return Listing(), reason or "Model discovery is not configured."
    listing = discovery.listing()
    if not listing.available:
        return listing, f"Could not list the models this API key can use ({listing.error})."
    return listing, None


@router.get("/integrations/llm/models")
def llm_models(
    settings: Settings = Depends(get_app_settings),
    discovery: ModelDiscovery | None = Depends(get_discovery),
) -> LLMModelsOut:
    """Catalogued models the configured OpenAI key can use. Never discloses the key or URL."""
    listing, reason = _listing(settings, discovery)
    return LLMModelsOut(
        available=reason is None,
        reason=reason,
        stale=listing.stale,
        fetched_at=listing.fetched_at,
        models=[
            LLMModelOut(id=m.id, tier=m.entry.tier, reasoning_efforts=list(m.entry.efforts))
            for m in listing.models
        ],
    )


def _task_models_out(
    session: Session, settings: Settings, resolver: ModelResolver
) -> LLMTaskModelsOut:
    enabled = settings.llm_provider is not LLMProviderName.NONE
    return LLMTaskModelsOut(
        available=selection_unavailable_reason(settings) is None,
        reason=selection_unavailable_reason(settings),
        default_model=settings.llm_model if enabled else None,
        tasks=task_models.task_models_out(
            task_models.get_task_models(session), resolver if enabled else None
        ),
    )


@router.get("/integrations/llm/task-models")
def get_task_models(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    resolver: ModelResolver = Depends(get_resolver),
) -> LLMTaskModelsOut:
    """The model and effort saved per task, and what calls use now."""
    return _task_models_out(session, settings, resolver)


@router.put("/integrations/llm/task-models")
def update_task_models(
    payload: LLMTaskModelsUpdate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    resolver: ModelResolver = Depends(get_resolver),
    discovery: ModelDiscovery | None = Depends(get_discovery),
) -> LLMTaskModelsOut:
    """Choose a model and effort per task (null resets to the default).

    Takes effect within seconds in this process and the worker, without a restart.
    """
    listing, reason = _listing(settings, discovery)
    if reason is not None:
        raise ConflictError(reason)
    task_models.update_task_models(session, payload, listing, settings.llm_model or "")
    # Commit before the reload, so this process does not cache the old values.
    session.commit()
    if isinstance(resolver, DbResolver):
        resolver.invalidate()
    return _task_models_out(session, settings, resolver)
