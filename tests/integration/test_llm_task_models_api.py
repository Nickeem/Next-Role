"""GET /integrations/llm/models and GET/PUT /integrations/llm/task-models."""

from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from next_role.config import LLMProviderName, Settings
from next_role.integrations.llm.base import LLMError
from next_role.integrations.llm.discovery import ModelDiscovery
from next_role.integrations.llm.models import LLMTask, LLMTaskModel
from next_role.main import create_app
from tests.conftest import API_KEY

KEY = "sk-very-secret"
LISTED = ["gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-astra", "whisper-1"]
MODELS = "/api/v1/integrations/llm/models"
TASK_MODELS = "/api/v1/integrations/llm/task-models"


def _settings(settings: Settings, **update: Any) -> Settings:
    return settings.model_copy(
        update={
            "llm_provider": LLMProviderName.OPENAI_COMPATIBLE,
            "llm_base_url": "https://api.openai.com/private-path",
            "llm_model": "gpt-6-luna",
            "llm_api_key": SecretStr(KEY),
            "llm_allow_external": True,
            **update,
        }
    )


ClientFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(
    settings: Settings, session_factory: sessionmaker[Session]
) -> Iterator[ClientFactory]:
    clients: list[TestClient] = []

    def make(lister: Callable[[], list[str]] | None = None, **update: Any) -> TestClient:
        app = create_app(_settings(settings, **update), session_factory)
        if lister is not None:
            app.state.llm_discovery = ModelDiscovery(lister)
        client = TestClient(app, headers={"X-API-Key": API_KEY})
        client.__enter__()
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.__exit__(None, None, None)


def _no_secrets(*texts: str) -> None:
    for text in texts:
        assert KEY not in text and "private-path" not in text


def _rows(client: TestClient) -> dict[str, dict[str, Any]]:
    return {t["task"]: t for t in client.get(TASK_MODELS).json()["tasks"]}


# --- Discovery endpoint -------------------------------------------------------------------


def test_models_are_the_catalogued_ones_the_key_can_use(make_client: ClientFactory) -> None:
    client = make_client(lambda: LISTED)
    response = client.get(MODELS)
    body = response.json()
    assert body["available"] is True and body["reason"] is None and body["stale"] is False
    assert body["fetched_at"] is not None
    assert body["models"] == [
        {
            "id": "gpt-6-luna",
            "tier": "fast",
            "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        },
        {
            "id": "gpt-6.1-sol",
            "tier": "strong",
            "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"],
        },
        {
            "id": "gpt-6.1-terra",
            "tier": "strong",
            "reasoning_efforts": ["none", "low", "medium", "high", "xhigh", "max"],
        },
    ]
    _no_secrets(response.text)


def test_a_listing_failure_is_explained(make_client: ClientFactory) -> None:
    def failing() -> list[str]:
        raise LLMError("openai_compatible returned HTTP 401")

    client = make_client(failing)
    body = client.get(MODELS).json()
    assert body["available"] is False and body["models"] == []
    assert "HTTP 401" in body["reason"]
    # The rest of the LLM endpoints keep working.
    assert client.get("/api/v1/integrations/llm/status").status_code == 200
    assert client.get(TASK_MODELS).status_code == 200


@pytest.mark.parametrize(
    "update",
    [
        {"llm_base_url": "https://llm.example.invalid/private-path"},
        {"llm_provider": LLMProviderName.OLLAMA, "llm_base_url": "http://ollama:11434"},
    ],
)
def test_selection_is_unavailable_away_from_openai(
    make_client: ClientFactory, update: dict[str, Any]
) -> None:
    client = make_client(**update)
    assert client.app.state.llm_discovery is None  # type: ignore[attr-defined]
    models = client.get(MODELS).json()
    assert models["available"] is False and "only with OpenAI" in models["reason"]
    task_models = client.get(TASK_MODELS).json()
    assert task_models["available"] is False and "only with OpenAI" in task_models["reason"]
    put = client.put(TASK_MODELS, json={"tasks": {"extraction": {"model": "gpt-6-luna"}}})
    assert put.status_code == 409


def test_without_a_provider(client: TestClient) -> None:
    body = client.get(TASK_MODELS).json()
    assert body["available"] is False and "No language model is configured" in body["reason"]
    assert body["default_model"] is None
    assert {t["effective_model"] for t in body["tasks"]} == {None}


def test_endpoints_require_auth(settings: Settings, session_factory: sessionmaker[Session]) -> None:
    with TestClient(create_app(_settings(settings), session_factory)) as anonymous:
        assert anonymous.get(MODELS).status_code == 401
        assert anonymous.get(TASK_MODELS).status_code == 401
        assert anonymous.put(TASK_MODELS, json={"tasks": {}}).status_code == 401


# --- Reading and saving choices -----------------------------------------------------------


def test_defaults_before_anything_is_saved(make_client: ClientFactory) -> None:
    client = make_client(lambda: LISTED)
    body = client.get(TASK_MODELS).json()
    assert body["available"] is True and body["default_model"] == "gpt-6-luna"
    assert body["tasks"][0] == {
        "task": "extraction",
        "model": None,
        "reasoning_effort": None,
        "effective_model": "gpt-6-luna",
        "effective_reasoning_effort": "none",
        "model_source": "default",
        "effort_source": "default",
        "default_reasoning_effort": "none",
    }


def test_saving_choices_takes_effect_immediately(
    make_client: ClientFactory, session_factory: sessionmaker[Session]
) -> None:
    client = make_client(lambda: LISTED)
    client.get("/api/v1/integrations/llm/status")  # warm the resolver's cache
    response = client.put(
        TASK_MODELS,
        json={
            "tasks": {
                "resume_drafting": {"model": "gpt-6.1-terra", "reasoning_effort": "high"},
                "semantic_check": {"model": "gpt-6.1-sol"},
                "extraction": {"reasoning_effort": "low"},
            }
        },
    )
    assert response.status_code == 200, response.text
    rows = {t["task"]: t for t in response.json()["tasks"]}
    assert rows["resume_drafting"]["effective_model"] == "gpt-6.1-terra"
    assert rows["resume_drafting"]["effective_reasoning_effort"] == "high"
    assert rows["semantic_check"]["effective_reasoning_effort"] == "low"  # task default
    assert rows["semantic_check"]["effort_source"] == "default"
    assert rows["extraction"]["effective_model"] == "gpt-6-luna"
    assert rows["extraction"]["model_source"] == "default"
    assert rows["extraction"]["effort_source"] == "override"
    assert rows["letter_drafting"]["model"] is None
    # The status card agrees without waiting for the cache.
    status = {t["task"]: t for t in client.get("/api/v1/integrations/llm/status").json()["tasks"]}
    assert status["resume_drafting"]["model"] == "gpt-6.1-terra"
    assert status["resume_drafting"]["model_source"] == "override"
    _no_secrets(response.text)


def test_null_resets_and_unlisted_tasks_are_kept(
    make_client: ClientFactory, session_factory: sessionmaker[Session]
) -> None:
    client = make_client(lambda: LISTED)
    client.put(
        TASK_MODELS,
        json={
            "tasks": {
                "resume_drafting": {"model": "gpt-6.1-terra", "reasoning_effort": "high"},
                "letter_drafting": {"model": "gpt-6.1-terra"},
            }
        },
    )
    response = client.put(
        TASK_MODELS,
        json={"tasks": {"resume_drafting": {"model": None, "reasoning_effort": None}}},
    )
    assert response.status_code == 200
    rows = _rows(client)
    assert rows["resume_drafting"]["model"] is None
    assert rows["resume_drafting"]["effective_model"] == "gpt-6-luna"
    assert rows["letter_drafting"]["model"] == "gpt-6.1-terra"
    with session_factory() as s:
        assert s.get(LLMTaskModel, LLMTask.RESUME_DRAFTING) is None


@pytest.mark.parametrize(
    ("tasks", "status", "message"),
    [
        # Listed by the key but not in the catalog.
        ({"extraction": {"model": "gpt-6-astra"}}, 422, "not a supported model"),
        ({"extraction": {"model": "whisper-1"}}, 422, "not a supported model"),
        # In the catalog but not available to this key.
        ({"extraction": {"model": "gpt-6.1-terra-2026-01-01"}}, 422, "not a supported model"),
        # An effort the chosen model does not support.
        (
            {"extraction": {"model": "gpt-6.1-sol", "reasoning_effort": "none"}},
            422,
            "does not support reasoning effort none",
        ),
        ({"extraction": {"reasoning_effort": "extreme"}}, 422, None),
        ({"unknown_task": {"model": "gpt-6-luna"}}, 422, None),
        ({"extraction": {"model": "gpt-6-luna", "temperature": 1}}, 422, None),
    ],
)
def test_invalid_choices_are_rejected_and_nothing_is_saved(
    make_client: ClientFactory,
    session_factory: sessionmaker[Session],
    tasks: dict[str, Any],
    status: int,
    message: str | None,
) -> None:
    client = make_client(lambda: LISTED)
    payload = {"tasks": {"resume_drafting": {"model": "gpt-6.1-terra"}, **tasks}}
    response = client.put(TASK_MODELS, json=payload)
    assert response.status_code == status, response.text
    if message:
        assert message in response.text
    with session_factory() as s:
        assert s.query(LLMTaskModel).count() == 0


def test_an_effort_needs_a_catalogued_model(make_client: ClientFactory) -> None:
    client = make_client(lambda: LISTED, llm_model="custom-finetune")
    response = client.put(TASK_MODELS, json={"tasks": {"extraction": {"reasoning_effort": "low"}}})
    assert response.status_code == 422 and "takes no reasoning effort" in response.text
    # Choosing a catalogued model with it is fine.
    response = client.put(
        TASK_MODELS,
        json={"tasks": {"extraction": {"model": "gpt-6-luna", "reasoning_effort": "low"}}},
    )
    assert response.status_code == 200


def test_saving_needs_a_model_listing(make_client: ClientFactory) -> None:
    def failing() -> list[str]:
        raise LLMError("openai_compatible request failed: ConnectError")

    client = make_client(failing)
    response = client.put(TASK_MODELS, json={"tasks": {"extraction": {"model": "gpt-6-luna"}}})
    assert response.status_code == 409 and "ConnectError" in response.text
