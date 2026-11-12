"""Which model and reasoning effort a task uses.

Gateways are built once per process and ask their resolver on every call, so a resolver can
change its answer without rebuilding them.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from next_role.integrations.llm.base import ReasoningEffort
from next_role.integrations.llm.models import LLMTask


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


class StaticResolver:
    """Every task uses one model and sends no reasoning effort."""

    def __init__(self, default: str) -> None:
        self.default = default

    def resolve(self, task: LLMTask) -> Resolved:
        return Resolved(self.default, None)
