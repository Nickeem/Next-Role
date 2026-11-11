"""Shared helpers for faked LLM providers and recorded call events."""

from dataclasses import replace

from next_role.integrations.llm.base import Completion, ReasoningEffort, RequestParams
from next_role.integrations.llm.gateway import CallEvent

PARAMS = RequestParams(
    model="fake-model", temperature=0, response_format="json_schema", timeout_seconds=60.0
)


def completion(
    text: str, model: str = "fake-model", reasoning_effort: ReasoningEffort | None = None
) -> Completion:
    """A completion whose params name the model and effort the fake was called with."""
    effort = reasoning_effort.value if reasoning_effort else None
    return Completion(text, replace(PARAMS, model=model, reasoning_effort=effort))


class MemoryRecorder:
    def __init__(self) -> None:
        self.events: list[CallEvent] = []

    def record(self, event: CallEvent) -> None:
        self.events.append(event)

    def outcomes(self) -> list[str]:
        return [e.outcome.value for e in self.events]
