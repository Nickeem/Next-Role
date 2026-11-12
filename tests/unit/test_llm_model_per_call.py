"""The model and reasoning effort travel with each call, from the resolver to events and notes."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from next_role.documents import drafting
from next_role.integrations.llm.base import LLMError, ReasoningEffort
from next_role.integrations.llm.gateway import LLMGateway
from next_role.integrations.llm.http_providers import OllamaProvider, OpenAICompatibleProvider
from next_role.integrations.llm.models import LLMTask
from next_role.integrations.llm.resolver import Resolved, StaticResolver
from next_role.jobs.llm_extraction import LLMRequirementExtractor
from tests.llm_fakes import MemoryRecorder
from tests.unit.document_fixtures import snapshot
from tests.unit.test_document_drafting import LETTER_OUTPUT, _resume_output
from tests.unit.test_document_drafting import FakeProvider as DraftingProvider
from tests.unit.test_llm_extraction import DESCRIPTION, FakeProvider, _json

TASK = LLMTask.EXTRACTION
OK_BODY = {"choices": [{"message": {"content": '{"value": 1}'}}]}


class Out(BaseModel):
    value: int


class PerTask:
    """A resolver with a fixed answer per task."""

    def __init__(self, answers: dict[LLMTask, Resolved]) -> None:
        self.answers = answers
        self.asked: list[LLMTask] = []

    def resolve(self, task: LLMTask) -> Resolved:
        self.asked.append(task)
        return self.answers[task]


def _openai(handler: Any) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        "https://llm.example.invalid", "default-model", 5, transport=httpx.MockTransport(handler)
    )


def _recording(bodies: list[dict[str, Any]], reject_temperature_for: str = "") -> Any:
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        with lock:
            bodies.append(body)
        if body["model"] == reject_temperature_for and "temperature" in body:
            return httpx.Response(400, json={"error": {"param": "temperature"}})
        return httpx.Response(200, json=OK_BODY)

    return handler


# --- Provider request shape -------------------------------------------------------------


def test_no_effort_keeps_todays_request_shape() -> None:
    bodies: list[dict[str, Any]] = []
    completion = _openai(_recording(bodies)).complete_json("s", "u", {}, model="model-a")
    [body] = bodies
    assert body["model"] == "model-a" and body["temperature"] == 0
    assert "reasoning_effort" not in body
    assert completion.params.model == "model-a"
    assert completion.params.reasoning_effort is None
    assert completion.params.as_dict()["reasoning_effort"] is None


def test_effort_none_sends_temperature_and_the_effort() -> None:
    bodies: list[dict[str, Any]] = []
    completion = _openai(_recording(bodies)).complete_json(
        "s", "u", {}, model="model-a", reasoning_effort=ReasoningEffort.NONE
    )
    assert bodies[0]["reasoning_effort"] == "none" and bodies[0]["temperature"] == 0
    assert completion.params.as_dict()["reasoning_effort"] == "none"
    assert completion.params.temperature == 0


@pytest.mark.parametrize("effort", [e for e in ReasoningEffort if e is not ReasoningEffort.NONE])
def test_any_other_effort_sends_no_temperature(effort: ReasoningEffort) -> None:
    bodies: list[dict[str, Any]] = []
    completion = _openai(_recording(bodies)).complete_json(
        "s", "u", {}, model="model-a", reasoning_effort=effort
    )
    assert bodies[0]["reasoning_effort"] == effort.value and "temperature" not in bodies[0]
    assert completion.params.temperature is None
    assert completion.params.reasoning_effort == effort.value


def test_ollama_uses_the_call_model_and_ignores_the_effort() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "{}"}})

    provider = OllamaProvider(
        "http://o:11434", "default", 5, transport=httpx.MockTransport(handler)
    )
    completion = provider.complete_json(
        "s", "u", {}, model="other", reasoning_effort=ReasoningEffort.HIGH
    )
    assert bodies[0]["model"] == "other" and "reasoning_effort" not in bodies[0]
    assert completion.params.model == "other" and completion.params.reasoning_effort is None


# --- Per-model temperature state ----------------------------------------------------------


def test_a_rejected_temperature_is_remembered_per_model() -> None:
    bodies: list[dict[str, Any]] = []
    provider = _openai(_recording(bodies, reject_temperature_for="model-a"))
    first = provider.complete_json("s", "u", {}, model="model-a")
    assert first.temperature_rejected is not None
    assert first.temperature_rejected.model == "model-a"
    assert first.params.temperature is None

    provider.complete_json("s", "u", {}, model="model-a")
    provider.complete_json("s", "u", {}, model="model-b")
    sent = [(b["model"], "temperature" in b) for b in bodies]
    # model-a: rejected, resent without, then skipped; model-b still gets a temperature.
    assert sent == [("model-a", True), ("model-a", False), ("model-a", False), ("model-b", True)]


def test_temperature_state_is_safe_across_threads() -> None:
    bodies: list[dict[str, Any]] = []
    provider = _openai(_recording(bodies, reject_temperature_for="model-a"))

    def call(i: int) -> float | None:
        model = "model-a" if i % 2 else "model-b"
        return provider.complete_json("s", "u", {}, model=model).params.temperature

    with ThreadPoolExecutor(max_workers=8) as pool:
        temperatures = list(pool.map(call, range(40)))

    # Every model-a call ended without a temperature and every model-b call kept it.
    assert temperatures == [None if i % 2 else 0 for i in range(40)]
    # A race costs at most one extra rejected request per thread, never a failure.
    rejected = [b for b in bodies if b["model"] == "model-a" and "temperature" in b]
    assert 1 <= len(rejected) <= 8
    assert provider.complete_json("s", "u", {}, model="model-a").temperature_rejected is None


# --- Gateway ----------------------------------------------------------------------------


def test_the_gateway_sends_and_records_the_resolved_model_and_effort() -> None:
    provider, recorder = FakeProvider('{"value": 1}'), MemoryRecorder()
    resolver = PerTask({TASK: Resolved("task-model", ReasoningEffort.LOW)})
    done = LLMGateway(provider, recorder, resolver).complete_model(TASK, "s", "u", Out)

    assert provider.sent == [("task-model", ReasoningEffort.LOW)]
    assert (done.value, done.model, done.reasoning_effort) == (
        Out(value=1),
        "task-model",
        ReasoningEffort.LOW,
    )
    assert done.label == "fake/task-model, effort low"
    [event] = recorder.events
    assert event.model == "task-model"
    assert event.params is not None and event.params["reasoning_effort"] == "low"


def test_the_model_is_resolved_once_per_call_not_per_attempt() -> None:
    provider = FakeProvider("not json", '{"value": 2}')
    resolver = PerTask({TASK: Resolved("task-model", None)})
    LLMGateway(provider, MemoryRecorder(), resolver).complete_model(TASK, "s", "u", Out)
    assert resolver.asked == [TASK] and len(provider.calls) == 2


def test_failures_and_fallbacks_record_the_resolved_model() -> None:
    recorder = MemoryRecorder()
    resolver = PerTask({TASK: Resolved("task-model", None)})
    llm = LLMGateway(FakeProvider(LLMError("a"), "not json"), recorder, resolver)
    with pytest.raises(ValidationError):
        llm.complete_model(TASK, "s", "u", Out)
    llm.record_fallback(TASK, "rules")
    assert recorder.outcomes() == ["provider_error", "schema_invalid", "deterministic_fallback"]
    assert {e.model for e in recorder.events} == {"task-model"}


def test_without_a_resolver_every_task_uses_the_provider_default() -> None:
    provider = FakeProvider('{"value": 1}')
    llm = LLMGateway(provider)
    assert isinstance(llm.resolver, StaticResolver)
    done = llm.complete_model(LLMTask.SEMANTIC_CHECK, "s", "u", Out)
    assert provider.sent == [("fake-model", None)] and done.label == "fake/fake-model"


# --- Notes --------------------------------------------------------------------------------


def test_extraction_notes_name_the_model_and_effort() -> None:
    provider = FakeProvider(_json(("5+ years of Python experience", "required")))
    resolver = PerTask({TASK: Resolved("fast-model", ReasoningEffort.NONE)})
    result = LLMRequirementExtractor(LLMGateway(provider, None, resolver))(DESCRIPTION)
    assert result.notes == "fake/fast-model, effort none (requirements-v1)"


def test_each_document_note_names_the_model_that_drafted_it() -> None:
    provider = DraftingProvider(_resume_output(), LETTER_OUTPUT)
    resolver = PerTask(
        {
            LLMTask.RESUME_DRAFTING: Resolved("writer-a", ReasoningEffort.MEDIUM),
            LLMTask.LETTER_DRAFTING: Resolved("writer-b", ReasoningEffort.HIGH),
        }
    )
    llm = LLMGateway(provider, MemoryRecorder(), resolver)
    draft = drafting.draft(snapshot(), {}, date(2026, 10, 22), llm)
    assert provider.sent == [
        ("writer-a", ReasoningEffort.MEDIUM),
        ("writer-b", ReasoningEffort.HIGH),
    ]
    assert draft.notes == [
        "Resume: fake/writer-a, effort medium (drafting-v2)",
        "Cover letter: fake/writer-b, effort high (drafting-v2)",
    ]
    assert all(draft.by_llm.values())
