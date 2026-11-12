"""Model discovery: listing, catalog filtering, caching, and degrading to stale or an error."""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from next_role.integrations.llm.base import LLMError, ReasoningEffort
from next_role.integrations.llm.catalog import CatalogModel, Tier
from next_role.integrations.llm.discovery import ModelDiscovery, catalogued
from next_role.integrations.llm.http_providers import OpenAICompatibleProvider

E = ReasoningEffort
CATALOG = (
    CatalogModel("test-fast", Tier.FAST, tuple(E), strict_json_schema=True),
    CatalogModel("test-strong", Tier.STRONG, tuple(E)[1:], strict_json_schema=True),
    CatalogModel("test-loose", Tier.FAST, tuple(E), strict_json_schema=False),
)
LISTED = ["whisper-1", "test-strong", "test-fast-2026-01-02", "test-fast", "test-loose", "x"]


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Lister:
    def __init__(self, *results: list[str] | Exception) -> None:
        self.results = list(results)
        self.calls = 0

    def __call__(self) -> list[str]:
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_only_catalogued_strict_models_are_kept_fast_first() -> None:
    models = catalogued(LISTED, CATALOG)
    assert [m.id for m in models] == ["test-fast", "test-fast-2026-01-02", "test-strong"]
    assert models[2].entry.efforts == tuple(E)[1:]


def _discovery(lister: Lister, clock: Clock) -> ModelDiscovery:
    return ModelDiscovery(
        lister,
        CATALOG,
        ttl=3600,
        retry=60,
        clock=clock,
        now=lambda: datetime(2026, 11, 11, tzinfo=UTC),
    )


def test_listings_are_cached_for_the_ttl() -> None:
    lister, clock = Lister(["test-fast"], ["test-strong"]), Clock()
    discovery = _discovery(lister, clock)
    first = discovery.listing()
    assert [m.id for m in first.models] == ["test-fast"] and first.available
    assert first.fetched_at == datetime(2026, 11, 11, tzinfo=UTC) and not first.stale
    clock.now = 3599
    assert discovery.listing() is first and lister.calls == 1
    clock.now = 3600
    assert [m.id for m in discovery.listing().models] == ["test-strong"]
    assert lister.calls == 2


def test_a_failed_refresh_serves_the_old_listing_as_stale() -> None:
    lister, clock = Lister(["test-fast"], LLMError("openai_compatible returned HTTP 503")), Clock()
    discovery = _discovery(lister, clock)
    discovery.listing()
    clock.now = 3600
    stale = discovery.listing()
    assert stale.stale and stale.available and [m.id for m in stale.models] == ["test-fast"]
    # Not asked again until the retry delay passes.
    clock.now = 3650
    assert discovery.listing() is stale and lister.calls == 2


def test_a_failure_without_an_earlier_listing_says_why() -> None:
    lister, clock = Lister(LLMError("openai_compatible returned HTTP 401"), ["test-fast"]), Clock()
    discovery = _discovery(lister, clock)
    failed = discovery.listing()
    assert not failed.available and failed.models == []
    assert failed.error == "openai_compatible returned HTTP 401"
    clock.now = 60
    assert discovery.listing().available


# --- The provider's GET /v1/models ----------------------------------------------------------


def _provider(status: int, body: Any) -> tuple[OpenAICompatibleProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    provider = OpenAICompatibleProvider(
        "https://api.openai.com/",
        "m",
        5,
        api_key="sk-secret",
        transport=httpx.MockTransport(handler),
    )
    return provider, seen


def test_list_models_reads_ids_with_the_key() -> None:
    body = {
        "object": "list",
        "data": [{"id": "a", "owned_by": "x"}, {"id": 3}, "junk", {"id": "b"}],
    }
    provider, seen = _provider(200, body)
    assert provider.list_models() == ["a", "b"]
    assert seen[0].url.path == "/v1/models"
    assert seen[0].headers["Authorization"] == "Bearer sk-secret"


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"error": {"message": "Incorrect API key sk-secret"}}, "returned HTTP 401"),
        (200, {"unexpected": True}, "model listing failed: KeyError"),
        (200, {"data": "nope"}, "model listing failed: TypeError"),
    ],
)
def test_list_models_errors_are_redacted(status: int, body: Any, message: str) -> None:
    provider, _ = _provider(status, body)
    with pytest.raises(LLMError, match=message) as raised:
        provider.list_models()
    assert "sk-secret" not in str(raised.value) and "api.openai.com" not in str(raised.value)
