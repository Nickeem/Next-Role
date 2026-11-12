"""An unavailable model: classification, one retry with the default, and what is recorded."""

import json
import logging
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from next_role.integrations.llm.base import LLMError, ReasoningEffort
from next_role.integrations.llm.catalog import CatalogModel, Tier
from next_role.integrations.llm.gateway import CallEvent, LLMGateway
from next_role.integrations.llm.http_providers import OllamaProvider, OpenAICompatibleProvider
from next_role.integrations.llm.models import ERROR_OUTCOMES, LLMOutcome, LLMTask
from next_role.integrations.llm.resolver import Resolved, Source, StaticResolver
from tests.llm_fakes import MemoryRecorder

E = ReasoningEffort
TASK = LLMTask.EXTRACTION
SECRET = "BODY-SECRET-sk-proj-abc"
NOT_FOUND = {
    "error": {
        "message": f"The model `chosen` does not exist or you do not have access to it. {SECRET}",
        "type": "invalid_request_error",
        "param": None,
        "code": "model_not_found",
    }
}
CATALOG = (
    CatalogModel("chosen", Tier.STRONG, (E.LOW, E.MEDIUM), strict_json_schema=True),
    CatalogModel("default", Tier.FAST, tuple(E), strict_json_schema=True),
)


class Out(BaseModel):
    value: int


class Chosen:
    """Resolves ``TASK`` to the model ``chosen``; falls back through the catalog rules."""

    def __init__(self, model: str = "chosen") -> None:
        self.model = model
        self.static = StaticResolver("default", efforts=True, catalog=CATALOG)

    def resolve(self, task: LLMTask) -> Resolved:
        return Resolved(self.model, E.MEDIUM, Source.OVERRIDE, Source.OVERRIDE)

    def for_model(self, task: LLMTask, model: str) -> Resolved:
        return self.static.for_model(task, model)


def _provider(
    unavailable: set[str], status: int = 404, body: Any = None
) -> tuple[OpenAICompatibleProvider, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        sent.append(payload)
        if payload["model"] in unavailable:
            return httpx.Response(status, json=NOT_FOUND if body is None else body)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"value": 1}'}}]})

    provider = OpenAICompatibleProvider(
        "https://api.openai.com", "default", 5, transport=httpx.MockTransport(handler)
    )
    return provider, sent


# --- Classification -----------------------------------------------------------------------


def _error(provider: Any) -> LLMError:
    with pytest.raises(LLMError) as raised:
        provider.complete_json("s", "u", {}, model="chosen")
    return raised.value


def test_404_model_not_found_is_classified_without_keeping_the_body() -> None:
    error = _error(_provider({"chosen"})[0])
    assert error.model_unavailable is True
    assert str(error) == "openai_compatible returned HTTP 404"
    assert SECRET not in repr(error.__dict__)


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, {"error": {"code": "not_found"}}),
        (404, {"error": {"message": "model_not_found"}}),
        (404, "not json at all"),
        (404, {"detail": "Not Found"}),
        (400, NOT_FOUND),
        (403, NOT_FOUND),
    ],
)
def test_anything_else_is_an_ordinary_provider_error(status: int, body: Any) -> None:
    assert _error(_provider({"chosen"}, status, body)[0]).model_unavailable is False


def test_ollama_never_classifies_a_model_as_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json=NOT_FOUND)

    provider = OllamaProvider("http://o:11434", "m", 5, transport=httpx.MockTransport(handler))
    assert _error(provider).model_unavailable is False


# --- Gateway fallback ---------------------------------------------------------------------


def test_an_unavailable_model_is_retried_once_with_the_default_and_its_effort() -> None:
    provider, sent = _provider({"chosen"})
    recorder = MemoryRecorder()
    done = LLMGateway(provider, recorder, Chosen()).complete_model(TASK, "s", "u", Out)

    assert [(b["model"], b.get("reasoning_effort")) for b in sent] == [
        ("chosen", "medium"),
        # Extraction's default effort on the default model, not the chosen model's effort.
        ("default", "none"),
    ]
    assert (done.model, done.reasoning_effort) == ("default", E.NONE)
    assert done.label == "openai_compatible/default, effort none"

    error, fallback, ok = recorder.events
    assert (error.outcome, error.model, error.attempt) == (LLMOutcome.PROVIDER_ERROR, "chosen", 1)
    assert error.error == "openai_compatible returned HTTP 404"
    assert (fallback.outcome, fallback.model, fallback.attempt) == (
        LLMOutcome.MODEL_FALLBACK,
        "chosen",
        1,
    )
    assert fallback.error == "chosen unavailable; used default"
    # The fallback repeats the same attempt; it does not use up the retry.
    assert (ok.outcome, ok.model, ok.attempt) == (LLMOutcome.OK, "default", 1)
    assert ok.params is not None and ok.params["reasoning_effort"] == "none"


def test_no_fallback_when_the_unavailable_model_is_the_default() -> None:
    provider, sent = _provider({"default"})
    recorder = MemoryRecorder()
    llm = LLMGateway(provider, recorder, Chosen("default"))
    with pytest.raises(LLMError):
        llm.complete_model(TASK, "s", "u", Out)
    assert [b["model"] for b in sent] == ["default", "default"]
    assert recorder.outcomes() == ["provider_error", "provider_error"]


def test_the_fallback_happens_once_even_if_the_default_fails_too() -> None:
    provider, sent = _provider({"chosen", "default"})
    recorder = MemoryRecorder()
    with pytest.raises(LLMError):
        LLMGateway(provider, recorder, Chosen()).complete_model(TASK, "s", "u", Out)
    assert [b["model"] for b in sent] == ["chosen", "default", "default"]
    assert recorder.outcomes() == [
        "provider_error",
        "model_fallback",
        "provider_error",
        "provider_error",
    ]
    assert [e.attempt for e in recorder.events] == [1, 1, 1, 2]


def test_other_errors_do_not_fall_back() -> None:
    provider, sent = _provider({"chosen"}, 404, {"error": {"code": "other"}})
    recorder = MemoryRecorder()
    with pytest.raises(LLMError):
        LLMGateway(provider, recorder, Chosen()).complete_model(TASK, "s", "u", Out)
    assert [b["model"] for b in sent] == ["chosen", "chosen"]
    assert "model_fallback" not in recorder.outcomes()


def test_model_fallback_is_not_an_error_outcome() -> None:
    assert LLMOutcome.MODEL_FALLBACK not in ERROR_OUTCOMES


def test_the_error_body_is_never_recorded_or_logged(caplog: pytest.LogCaptureFixture) -> None:
    provider, _ = _provider({"chosen"})
    recorder = MemoryRecorder()
    with caplog.at_level(logging.DEBUG):
        LLMGateway(provider, recorder, Chosen()).complete_model(TASK, "s", "u", Out)
    blob = json.dumps([_dump(e) for e in recorder.events], default=str)
    assert SECRET not in blob and "does not exist" not in blob
    assert SECRET not in caplog.text


def _dump(event: CallEvent) -> dict[str, Any]:
    return dict(event.__dict__)
