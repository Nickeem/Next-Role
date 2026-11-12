"""The model catalog, the reasoning-effort rules, and the request shape they produce."""

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel, SecretStr
from sqlalchemy.orm import sessionmaker

from next_role.config import LLMProviderName, Settings
from next_role.integrations.llm import catalog
from next_role.integrations.llm.base import ReasoningEffort
from next_role.integrations.llm.catalog import CatalogModel, Tier, effective_effort, lookup
from next_role.integrations.llm.gateway import LLMGateway
from next_role.integrations.llm.http_providers import OpenAICompatibleProvider
from next_role.integrations.llm.models import LLMOutcome, LLMTask
from next_role.integrations.llm.resolver import (
    Source,
    StaticResolver,
    build_resolver,
    resolve_effort,
    selection_available,
)
from tests.llm_fakes import MemoryRecorder

E = ReasoningEffort
ALL = tuple(E)
# Synthetic models: one with every effort, one without ``none`` (like gpt-6.1-sol).
TEST_CATALOG = (
    CatalogModel("test-fast", Tier.FAST, ALL, strict_json_schema=True),
    CatalogModel("test-strong", Tier.STRONG, ALL[1:], strict_json_schema=True),
)


class Out(BaseModel):
    value: int


# --- Catalog lookup -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model_id", "family"),
    [
        ("test-fast", "test-fast"),
        ("test-fast-2026-05-18", "test-fast"),
        ("test-strong-2027-01-02", "test-strong"),
        ("test", None),
        ("test-fast-mini", None),
        ("test-fast-latest", None),
        ("test-fast-2026-05", None),
        ("other-2026-05-18", None),
        ("", None),
    ],
)
def test_lookup_matches_families_and_dated_snapshots_only(
    model_id: str, family: str | None
) -> None:
    entry = lookup(model_id, TEST_CATALOG)
    assert (entry.family if entry else None) == family


def test_the_shipped_catalog_matches_the_documented_models() -> None:
    by_family = {m.family: m for m in catalog.CATALOG}
    assert set(by_family) == {"gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol"}
    assert by_family["gpt-6-luna"].tier is Tier.FAST
    assert by_family["gpt-6.1-terra"].tier is Tier.STRONG
    assert by_family["gpt-6.1-sol"].tier is Tier.STRONG
    assert by_family["gpt-6-luna"].efforts == ALL
    assert by_family["gpt-6.1-terra"].efforts == ALL
    assert by_family["gpt-6.1-sol"].efforts == ALL[1:]
    assert all(m.strict_json_schema for m in catalog.CATALOG)
    # Efforts are listed lowest first, so the fallback picks the lowest.
    for m in catalog.CATALOG:
        assert list(m.efforts) == sorted(m.efforts, key=ALL.index)


def test_every_task_has_a_default_effort() -> None:
    assert catalog.TASK_DEFAULT_EFFORT == {
        LLMTask.EXTRACTION: E.NONE,
        LLMTask.RESUME_DRAFTING: E.MEDIUM,
        LLMTask.LETTER_DRAFTING: E.MEDIUM,
        LLMTask.SEMANTIC_CHECK: E.LOW,
    }


# --- Effort rules -------------------------------------------------------------------------


def test_a_supported_effort_is_kept() -> None:
    assert effective_effort("test-fast", E.HIGH, TEST_CATALOG) is E.HIGH
    assert effective_effort("test-strong", E.MAX, TEST_CATALOG) is E.MAX


def test_an_unsupported_effort_falls_back_to_the_lowest_supported() -> None:
    assert effective_effort("test-strong", E.NONE, TEST_CATALOG) is E.LOW


def test_an_uncatalogued_model_gets_no_effort() -> None:
    assert effective_effort("someone-elses-model", E.LOW, TEST_CATALOG) is None


@pytest.mark.parametrize(
    ("task", "model", "expected"),
    [
        (LLMTask.EXTRACTION, "test-fast", E.NONE),
        (LLMTask.RESUME_DRAFTING, "test-fast", E.MEDIUM),
        (LLMTask.LETTER_DRAFTING, "test-fast", E.MEDIUM),
        (LLMTask.SEMANTIC_CHECK, "test-fast", E.LOW),
        # Extraction's default ``none`` is not supported: the lowest supported is used.
        (LLMTask.EXTRACTION, "test-strong", E.LOW),
        (LLMTask.EXTRACTION, "uncatalogued", None),
    ],
)
def test_static_resolver_applies_task_default_efforts(
    task: LLMTask, model: str, expected: ReasoningEffort | None
) -> None:
    resolved = StaticResolver(model, efforts=True, catalog=TEST_CATALOG).resolve(task)
    assert (resolved.model, resolved.reasoning_effort) == (model, expected)
    assert resolved.model_source is Source.DEFAULT and resolved.effort_source is Source.DEFAULT


def test_static_resolver_without_efforts_sends_none() -> None:
    resolved = StaticResolver("test-fast", catalog=TEST_CATALOG).resolve(LLMTask.EXTRACTION)
    assert resolved.reasoning_effort is None


def test_an_override_effort_is_marked_as_such_and_still_checked() -> None:
    assert resolve_effort(LLMTask.EXTRACTION, "test-fast", E.HIGH, TEST_CATALOG) == (
        E.HIGH,
        Source.OVERRIDE,
    )
    assert resolve_effort(LLMTask.EXTRACTION, "test-strong", E.NONE, TEST_CATALOG) == (
        E.LOW,
        Source.OVERRIDE,
    )


# --- Where efforts apply ------------------------------------------------------------------


def _settings(**llm: Any) -> Settings:
    return Settings(api_key=SecretStr("k"), _env_file=None, llm_allow_external=True, **llm)


OPENAI = {
    "llm_provider": LLMProviderName.OPENAI_COMPATIBLE,
    "llm_base_url": "https://api.openai.com",
    "llm_model": "gpt-6-luna",
}


@pytest.mark.parametrize(
    ("llm", "available"),
    [
        (OPENAI, True),
        ({**OPENAI, "llm_base_url": "https://api.openai.com/"}, True),
        ({**OPENAI, "llm_base_url": "https://llm.example.invalid"}, False),
        ({**OPENAI, "llm_base_url": "https://api.openai.com.example.invalid"}, False),
        ({**OPENAI, "llm_provider": LLMProviderName.OLLAMA}, False),
        ({}, False),
    ],
)
def test_selection_is_available_only_for_openai_itself(
    llm: dict[str, Any], available: bool
) -> None:
    settings = _settings(**llm)
    assert selection_available(settings) is available
    # An unbound session factory: no saved choices can load, so defaults apply.
    resolved = build_resolver(settings, sessionmaker()).resolve(LLMTask.EXTRACTION)
    # Elsewhere every task keeps today's request shape, even for a catalogued model name.
    assert (resolved.reasoning_effort is not None) is available


# --- Request shape end to end -------------------------------------------------------------


def _gateway(
    model: str, bodies: list[dict[str, Any]], reject_temperature: bool = False
) -> tuple[LLMGateway, MemoryRecorder]:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if reject_temperature and "temperature" in body:
            return httpx.Response(400, json={"error": {"param": "temperature"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"value": 1}'}}]})

    provider = OpenAICompatibleProvider(
        "https://api.openai.com", model, 5, transport=httpx.MockTransport(handler)
    )
    recorder = MemoryRecorder()
    resolver = StaticResolver(model, efforts=True, catalog=TEST_CATALOG)
    return LLMGateway(provider, recorder, resolver), recorder


def test_catalogued_model_at_none_sends_temperature_and_the_effort() -> None:
    bodies: list[dict[str, Any]] = []
    llm, recorder = _gateway("test-fast", bodies)
    done = llm.complete_model(LLMTask.EXTRACTION, "s", "u", Out)
    assert bodies[0]["reasoning_effort"] == "none" and bodies[0]["temperature"] == 0
    assert done.label == "openai_compatible/test-fast, effort none"
    params = recorder.events[0].params
    assert params is not None
    assert params["reasoning_effort"] == "none" and params["temperature_sent"] is True


def test_catalogued_model_at_another_effort_sends_no_temperature() -> None:
    bodies: list[dict[str, Any]] = []
    llm, recorder = _gateway("test-fast", bodies)
    llm.complete_model(LLMTask.RESUME_DRAFTING, "s", "u", Out)
    assert bodies[0]["reasoning_effort"] == "medium" and "temperature" not in bodies[0]
    params = recorder.events[0].params
    assert params is not None
    assert params["reasoning_effort"] == "medium" and params["temperature_sent"] is False
    assert recorder.outcomes() == ["ok"]


def test_the_fallback_effort_is_what_is_sent_and_recorded() -> None:
    bodies: list[dict[str, Any]] = []
    llm, recorder = _gateway("test-strong", bodies)
    llm.complete_model(LLMTask.EXTRACTION, "s", "u", Out)
    assert bodies[0]["reasoning_effort"] == "low" and "temperature" not in bodies[0]
    params = recorder.events[0].params
    assert params is not None and params["reasoning_effort"] == "low"


def test_uncatalogued_model_keeps_todays_shape_and_the_retry() -> None:
    bodies: list[dict[str, Any]] = []
    llm, recorder = _gateway("uncatalogued", bodies, reject_temperature=True)
    llm.complete_model(LLMTask.RESUME_DRAFTING, "s", "u", Out)
    assert [("temperature" in b, "reasoning_effort" in b) for b in bodies] == [
        (True, False),
        (False, False),
    ]
    assert [e.outcome for e in recorder.events] == [
        LLMOutcome.TEMPERATURE_DROPPED,
        LLMOutcome.OK,
    ]
