"""Which model and reasoning effort a task uses.

Gateways are built once per process and ask their resolver on every call, so a resolver can
change its answer without rebuilding them. ``DbResolver`` reads the choices saved in Settings
(``llm_task_models``) at most once per ``ttl`` seconds, so a change reaches the API and the
worker within a few seconds without a query per attempt.

Effort rules (ADR 0008): a reasoning effort is sent only when per-task selection is available
(OpenAI itself) and the model is in the catalog. The task's effort must be one the model
supports; otherwise the model's lowest supported effort is used.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from next_role.config import LLMProviderName, Settings
from next_role.integrations.llm.base import ReasoningEffort
from next_role.integrations.llm.catalog import CATALOG, TASK_DEFAULT_EFFORT, CatalogModel
from next_role.integrations.llm.catalog import effective_effort as _effective_effort
from next_role.integrations.llm.models import LLMTask, LLMTaskModel

logger = logging.getLogger(__name__)

OPENAI_HOST = "api.openai.com"
CACHE_SECONDS = 5.0


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
    def resolve(self, task: LLMTask) -> Resolved:
        """The model and effort ``task`` uses now."""
        ...

    def for_model(self, task: LLMTask, model: str) -> Resolved:
        """``task`` on ``model`` instead (a model fallback), with the effort re-resolved."""
        ...


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
        return self.for_model(task, self.default)

    def for_model(self, task: LLMTask, model: str) -> Resolved:
        if not self.efforts:
            return Resolved(model, None)
        effort, source = resolve_effort(task, model, catalog=self._catalog)
        return Resolved(model, effort, effort_source=source)


@dataclass(frozen=True)
class Override:
    model: str | None = None
    reasoning_effort: ReasoningEffort | None = None


class DbResolver:
    """Choices saved in ``llm_task_models``, cached for ``ttl`` seconds. Thread-safe.

    An override model wins over the default; a null column or a missing row means the
    default. The effort then follows the effort rules for whichever model is used.
    """

    def __init__(
        self,
        factory: sessionmaker[Session],
        default: str,
        ttl: float = CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        catalog: tuple[CatalogModel, ...] = CATALOG,
    ) -> None:
        self.default = default
        self._factory = factory
        self._ttl = ttl
        self._clock = clock
        self._catalog = catalog
        self._lock = threading.Lock()
        self._overrides: dict[LLMTask, Override] = {}
        self._loaded_at: float | None = None

    def resolve(self, task: LLMTask) -> Resolved:
        override = self._current().get(task, Override())
        if override.model:
            return self._resolved(task, override.model, Source.OVERRIDE, override)
        return self._resolved(task, self.default, Source.DEFAULT, override)

    def for_model(self, task: LLMTask, model: str) -> Resolved:
        override = self._current().get(task, Override())
        source = Source.OVERRIDE if model == override.model else Source.DEFAULT
        return self._resolved(task, model, source, override)

    def invalidate(self) -> None:
        """Reload on the next call (after this process saved a change)."""
        with self._lock:
            self._loaded_at = None

    def _resolved(
        self, task: LLMTask, model: str, model_source: Source, override: Override
    ) -> Resolved:
        effort, effort_source = resolve_effort(
            task, model, override.reasoning_effort, self._catalog
        )
        return Resolved(model, effort, model_source, effort_source)

    def _current(self) -> dict[LLMTask, Override]:
        with self._lock:
            now = self._clock()
            if self._loaded_at is None or now - self._loaded_at >= self._ttl:
                # Set first, so a failing database is retried once per ttl, not per call.
                self._loaded_at = now
                try:
                    self._overrides = load_overrides(self._factory)
                except Exception as exc:
                    # Keep the last known choices: a model call must not fail for this.
                    logger.warning(
                        "could not load llm task models; using cached values",
                        extra={"error_type": type(exc).__name__},
                    )
            return self._overrides


def load_overrides(factory: sessionmaker[Session]) -> dict[LLMTask, Override]:
    with factory() as session:
        return overrides(session)


def overrides(session: Session) -> dict[LLMTask, Override]:
    rows = session.execute(
        select(LLMTaskModel.task, LLMTaskModel.model, LLMTaskModel.reasoning_effort)
    )
    return {task: Override(model, effort) for task, model, effort in rows}


def build_resolver(settings: Settings, factory: sessionmaker[Session]) -> ModelResolver:
    """``DbResolver`` when per-task selection is available. Otherwise every task uses
    NEXT_ROLE_LLM_MODEL with no effort, and saved choices are ignored (not deleted)."""
    default = settings.llm_model or ""
    if selection_available(settings):
        return DbResolver(factory, default)
    return StaticResolver(default)
