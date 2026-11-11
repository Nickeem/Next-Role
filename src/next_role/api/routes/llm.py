from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from next_role.api.deps import get_app_settings, get_session
from next_role.config import LLMProviderName, Settings
from next_role.integrations.llm import status
from next_role.integrations.llm.models import LLMTask
from next_role.integrations.llm.resolver import ModelResolver
from next_role.integrations.llm.schemas import LLMStatusOut

router = APIRouter(tags=["integrations"])

# Tasks that send candidate data and so also need NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA.
CANDIDATE_DATA_TASKS = frozenset(
    {LLMTask.RESUME_DRAFTING, LLMTask.LETTER_DRAFTING, LLMTask.SEMANTIC_CHECK}
)


def get_resolver(request: Request) -> ModelResolver:
    resolver: ModelResolver = request.app.state.llm_resolver
    return resolver


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
