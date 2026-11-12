"""The LLM gateway: retries as the old loops did, and records every attempt and fallback."""

import json
import logging
from datetime import date
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from next_role.documents import drafting
from next_role.integrations.llm.base import LLMError
from next_role.integrations.llm.gateway import CallEvent, LLMGateway, SessionRecorder
from next_role.integrations.llm.http_providers import OllamaProvider, OpenAICompatibleProvider
from next_role.integrations.llm.models import LLMOutcome, LLMTask
from next_role.jobs.models import ExtractionMethod
from next_role.logging_config import JsonFormatter
from tests.llm_fakes import MemoryRecorder
from tests.unit.document_fixtures import snapshot
from tests.unit.test_document_drafting import LETTER_OUTPUT, _resume_output
from tests.unit.test_document_drafting import FakeProvider as DraftingProvider
from tests.unit.test_llm_extraction import DESCRIPTION, FakeProvider, _json, extractor

TASK = LLMTask.EXTRACTION
SECRET = "PRIVATE-CANDIDATE-TEXT"


class Out(BaseModel):
    value: int


def _gateway(*responses: Any) -> tuple[LLMGateway, FakeProvider, MemoryRecorder]:
    provider, recorder = FakeProvider(*responses), MemoryRecorder()
    return LLMGateway(provider, recorder), provider, recorder


def _content_free(events: list[CallEvent], *needles: str) -> None:
    blob = json.dumps([e.__dict__ for e in events], default=str)
    for needle in needles:
        assert needle not in blob


# --- Retry behaviour (matches the loops it replaced) ---------------------------------------


def test_first_success_records_one_ok_attempt() -> None:
    llm, provider, recorder = _gateway('{"value": 1}')
    assert llm.complete_model(TASK, "s", "u", Out).value == Out(value=1)
    assert len(provider.calls) == 1
    [event] = recorder.events
    assert (event.outcome, event.attempt, event.task) == (LLMOutcome.OK, 1, TASK)
    assert event.model == "fake-model" and event.latency_ms is not None
    assert event.params == {
        "model": "fake-model",
        "temperature_sent": True,
        "temperature": 0,
        "reasoning_effort": None,
        "response_format": "json_schema",
        "timeout_seconds": 60.0,
    }


def test_provider_error_is_retried_once_then_succeeds() -> None:
    llm, provider, recorder = _gateway(LLMError("fake returned HTTP 503"), '{"value": 2}')
    assert llm.complete_model(TASK, "s", "u", Out).value.value == 2
    assert len(provider.calls) == 2
    assert recorder.outcomes() == ["provider_error", "ok"]
    assert recorder.events[0].error == "fake returned HTTP 503"
    assert [e.attempt for e in recorder.events] == [1, 2]


@pytest.mark.parametrize(
    ("responses", "raised"),
    [
        ((LLMError("a"), LLMError("b")), LLMError),
        (("not json", '{"value": "x"}'), ValidationError),
        (("not json", LLMError("b")), LLMError),
        ((LLMError("a"), "not json"), ValidationError),
    ],
)
def test_last_error_is_raised_after_two_attempts(
    responses: tuple[Any, ...], raised: type[Exception]
) -> None:
    llm, provider, recorder = _gateway(*responses)
    with pytest.raises(raised):
        llm.complete_model(TASK, "s", "u", Out)
    assert len(provider.calls) == 2 and len(recorder.events) == 2


def test_other_exceptions_are_not_retried() -> None:
    llm, provider, _ = _gateway(RuntimeError("boom"), '{"value": 1}')
    with pytest.raises(RuntimeError):
        llm.complete_model(TASK, "s", "u", Out)
    assert len(provider.calls) == 1


def test_attempt_count_is_configurable_and_must_be_positive() -> None:
    llm, provider, _ = _gateway(LLMError("a"), LLMError("b"), '{"value": 3}')
    assert llm.complete_model(TASK, "s", "u", Out, attempts=3).value.value == 3
    assert len(provider.calls) == 3
    with pytest.raises(LLMError, match="at least 1"):
        llm.complete_model(TASK, "s", "u", Out, attempts=0)


def test_extractor_keeps_its_max_attempts() -> None:
    provider = FakeProvider(LLMError("a"), LLMError("b"), _json(("5+ years of Python", "required")))
    result = extractor(provider, max_attempts=3)(DESCRIPTION)
    assert result.method is ExtractionMethod.LLM and len(provider.calls) == 3


# --- Schema failures --------------------------------------------------------------------


def test_schema_invalid_records_the_type_name_and_never_content() -> None:
    bad = json.dumps({"value": SECRET})
    llm, _, recorder = _gateway(bad, bad)
    with pytest.raises(ValidationError):
        llm.complete_model(TASK, f"system {SECRET}", f"user {SECRET}", Out)
    assert recorder.outcomes() == ["schema_invalid", "schema_invalid"]
    assert {e.error for e in recorder.events} == {"ValidationError"}
    _content_free(recorder.events, SECRET)


# --- Request params, including the temperature retry -------------------------------------


def _rejects_temperature(captured: list[dict[str, Any]], then: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        captured.append(body)
        if "temperature" in body:
            return httpx.Response(400, json={"error": {"param": "temperature"}})
        if then != 200:
            return httpx.Response(then, json={"error": {"message": "down"}})
        content = json.dumps({"value": 7})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def _openai(transport: httpx.MockTransport) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        "https://llm.example.invalid/v1/private", "gpt-x", 12, api_key="sk-x", transport=transport
    )


def test_rejected_temperature_is_recorded_with_the_params_actually_sent() -> None:
    captured: list[dict[str, Any]] = []
    recorder = MemoryRecorder()
    llm = LLMGateway(_openai(_rejects_temperature(captured)), recorder)
    assert llm.complete_model(TASK, f"s {SECRET}", f"u {SECRET}", Out).value.value == 7
    assert ["temperature" in body for body in captured] == [True, False]

    dropped, ok = recorder.events
    assert (dropped.outcome, ok.outcome) == (LLMOutcome.TEMPERATURE_DROPPED, LLMOutcome.OK)
    assert dropped.params == {
        "model": "gpt-x",
        "temperature_sent": True,
        "temperature": 0,
        "reasoning_effort": None,
        "response_format": "json_schema",
        "timeout_seconds": 12,
    }
    assert ok.params is not None
    assert ok.params["temperature_sent"] is False and ok.params["temperature"] is None
    _content_free(recorder.events, SECRET, "sk-x", "/v1/private", "messages")

    # The provider remembers; the next call sends no temperature and records no drop.
    llm.complete_model(TASK, "s", "u", Out)
    assert recorder.outcomes()[2:] == ["ok"]
    assert recorder.events[2].params is not None
    assert recorder.events[2].params["temperature_sent"] is False


def test_failed_retry_after_temperature_drop_records_what_was_sent() -> None:
    captured: list[dict[str, Any]] = []
    recorder = MemoryRecorder()
    llm = LLMGateway(_openai(_rejects_temperature(captured, then=500)), recorder)
    with pytest.raises(LLMError, match="HTTP 500"):
        llm.complete_model(TASK, "s", "u", Out, attempts=1)
    assert recorder.outcomes() == ["temperature_dropped", "provider_error"]
    error = recorder.events[1]
    assert error.error == "openai_compatible returned HTTP 500"
    assert error.params is not None and error.params["temperature_sent"] is False


def test_ollama_params_report_options_temperature() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": '{"value": 1}'}})

    recorder = MemoryRecorder()
    provider = OllamaProvider(
        "http://ollama:11434", "llama3", 30, transport=httpx.MockTransport(handler)
    )
    LLMGateway(provider, recorder).complete_model(TASK, "s", "u", Out)
    assert recorder.events[0].params == {
        "model": "llama3",
        "temperature_sent": True,
        "temperature": 0,
        "reasoning_effort": None,
        "response_format": "json_schema",
        "timeout_seconds": 30,
    }


# --- Recording never breaks the call ----------------------------------------------------


class BrokenRecorder:
    def record(self, event: CallEvent) -> None:
        raise RuntimeError("database is down")


def test_a_failing_recorder_does_not_break_the_call(caplog: pytest.LogCaptureFixture) -> None:
    llm = LLMGateway(FakeProvider(LLMError("x"), '{"value": 5}'), BrokenRecorder())
    with caplog.at_level(logging.WARNING):
        assert llm.complete_model(TASK, "s", "u", Out).value.value == 5
        llm.record_fallback(TASK, "reason")
    failures = [r for r in caplog.records if r.message == "failed to record llm call event"]
    assert len(failures) == 3
    assert {r.__dict__["error_type"] for r in failures} == {"RuntimeError"}


def test_recorder_failures_during_invalid_output_log_no_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # The recorder fails while the ValidationError, which quotes the output, is being handled.
    bad = json.dumps({"value": SECRET})
    llm = LLMGateway(FakeProvider(bad, bad), BrokenRecorder())
    with caplog.at_level(logging.DEBUG), pytest.raises(ValidationError):
        llm.complete_model(TASK, "s", "u", Out)
    failures = [r for r in caplog.records if r.message == "failed to record llm call event"]
    assert len(failures) == 2
    formatter = JsonFormatter()
    for record in caplog.records:
        assert record.exc_info is None
        assert SECRET not in formatter.format(record)
    assert SECRET not in caplog.text


def test_an_unreachable_database_does_not_break_the_call() -> None:
    # Nothing listens on port 1: every write fails with a connection error.
    engine = create_engine("postgresql+psycopg://u:p@127.0.0.1:1/none?connect_timeout=1")
    llm = LLMGateway(FakeProvider('{"value": 6}'), SessionRecorder(sessionmaker(bind=engine)))
    assert llm.complete_model(TASK, "s", "u", Out).value.value == 6


def test_errors_are_truncated() -> None:
    llm, _, recorder = _gateway()
    llm.record_fallback(TASK, "x" * 2000)
    assert recorder.events[0].error is not None and len(recorder.events[0].error) == 500


# --- Fallback sites ---------------------------------------------------------------------


def test_extraction_failure_records_a_fallback() -> None:
    recorder = MemoryRecorder()
    result = extractor(FakeProvider(LLMError("x"), LLMError("y")), recorder)(DESCRIPTION)
    assert result.method is ExtractionMethod.RULES
    assert recorder.outcomes() == ["provider_error", "provider_error", "deterministic_fallback"]
    assert recorder.events[-1].error == "LLM extraction failed (y)"
    assert recorder.events[-1].attempt is None


def test_no_grounded_requirements_records_a_fallback() -> None:
    recorder = MemoryRecorder()
    result = extractor(FakeProvider(_json(("Rust", "required"))), recorder)(DESCRIPTION)
    assert result.method is ExtractionMethod.RULES
    assert recorder.outcomes() == ["ok", "deterministic_fallback"]
    assert recorder.events[-1].error == "LLM returned no grounded requirements"


def test_extraction_notes_name_the_model() -> None:
    provider = FakeProvider(_json(("5+ years of Python experience", "required")))
    assert extractor(provider)(DESCRIPTION).notes == "fake/fake-model (requirements-v1)"


@pytest.mark.parametrize("failing", ["resume", "letter"])
def test_drafting_failures_record_a_fallback_per_document(failing: str) -> None:
    responses = (
        [LLMError("x"), LLMError("x"), LETTER_OUTPUT]
        if failing == "resume"
        else [_resume_output(), LLMError("x"), LLMError("x")]
    )
    recorder = MemoryRecorder()
    llm = LLMGateway(DraftingProvider(*responses), recorder)
    draft = drafting.draft(snapshot(), {}, date(2026, 10, 22), llm)
    task = LLMTask.RESUME_DRAFTING if failing == "resume" else LLMTask.LETTER_DRAFTING
    fallbacks = [e for e in recorder.events if e.outcome is LLMOutcome.DETERMINISTIC_FALLBACK]
    assert [(e.task, e.error) for e in fallbacks] == [(task, f"{failing} assembled from facts (x)")]
    # The model-written document's note names its model; the other says why it fell back.
    if failing == "resume":
        assert draft.notes == [
            "Resume assembled from facts: model failed (x)",
            "Cover letter: fake/fake-model (drafting-v2)",
        ]
    else:
        assert draft.notes == [
            "Resume: fake/fake-model (drafting-v2)",
            "Letter assembled from facts: model failed (x)",
        ]
    assert {e.task for e in recorder.events} == {LLMTask.RESUME_DRAFTING, LLMTask.LETTER_DRAFTING}
