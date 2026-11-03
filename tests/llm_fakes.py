"""Shared helpers for faked LLM providers and recorded call events."""

from next_role.integrations.llm.base import Completion, RequestParams
from next_role.integrations.llm.gateway import CallEvent

PARAMS = RequestParams(
    model="fake-model", temperature=0, response_format="json_schema", timeout_seconds=60.0
)


def completion(text: str) -> Completion:
    return Completion(text, PARAMS)


class MemoryRecorder:
    def __init__(self) -> None:
        self.events: list[CallEvent] = []

    def record(self, event: CallEvent) -> None:
        self.events.append(event)

    def outcomes(self) -> list[str]:
        return [e.outcome.value for e in self.events]
