"""Which model and reasoning effort a task uses.

Gateways are built once per process and ask their resolver on every call, so a resolver can
change its answer without rebuilding them.

Effort rules (ADR 0008): a reasoning effort is sent only when per-task selection is available
(OpenAI itself) and the model is in the catalog. The task's effort must be one the model
supports; otherwise the model's lowest supported effort is used.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from next_role.config import LLMProviderName, Settings
from next_role.integrations.llm.base import ReasoningEffort
from next_role.integrations.llm.catalog import CATALOG, TASK_DEFAULT_EFFORT, CatalogModel
from next_role.integrations.llm.catalog import effective_effort as _effective_effort
from next_role.integrations.llm.models import LLMTask

OPENAI_HOST = "api.openai.com"


class Source(StrEnum):
    # Chosen for this task in Settings.
    OVERRIDE = "override"
    # NEXT_ROLE_LLM_MODEL, or the task's default effort.
    DEFAULT = "default"


@dataclass(frozen=True)
class Resolved:
    model: str
    # None: no reasoning effort is sent, and the provider keeps its usual request shape.
    reasoning_effort: ReasoningEffort | None
    model_source: Source = Source.DEFAULT
    effort_source: Source = Source.DEFAULT


class ModelResolver(Protocol):
    def resolve(self, task: LLMTask) -> Resolved: ...


def selection_available(settings: Settings) -> bool:
    """Per-task models and efforts apply only to OpenAI itself: the catalog describes its
    models, and other OpenAI-compatible servers may not accept ``reasoning_effort``."""
    if settings.llm_provider is not LLMProviderName.OPENAI_COMPATIBLE or not settings.llm_base_url:
        return False
    return urlsplit(settings.llm_base_url).hostname == OPENAI_HOST


def resolve_effort(
    task: LLMTask,
    model: str,
    override: ReasoningEffort | None = None,
    catalog: tuple[CatalogModel, ...] = CATALOG,
) -> tuple[ReasoningEffort | None, Source]:
    """The effort to send for ``task`` on ``model`` and where it came from."""
    requested = override or TASK_DEFAULT_EFFORT[task]
    source = Source.OVERRIDE if override else Source.DEFAULT
    return _effective_effort(model, requested, catalog), source


class StaticResolver:
    """Every task uses one model. With ``efforts`` on, a catalogued model gets each task's
    default effort; with it off (the default), no effort is sent."""

    def __init__(
        self, default: str, efforts: bool = False, catalog: tuple[CatalogModel, ...] = CATALOG
    ) -> None:
        self.default = default
        self.efforts = efforts
        self._catalog = catalog

    def resolve(self, task: LLMTask) -> Resolved:
        if not self.efforts:
            return Resolved(self.default, None)
        effort, source = resolve_effort(task, self.default, catalog=self._catalog)
        return Resolved(self.default, effort, effort_source=source)


def build_resolver(settings: Settings) -> ModelResolver:
    """The resolver for this deployment: efforts only when selection is available."""
    return StaticResolver(settings.llm_model or "", efforts=selection_available(settings))
