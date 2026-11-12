"""GET /integrations/llm/status, event retention, and the llm_call_events schema."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from next_role.config import LLMProviderName, Settings
from next_role.integrations.llm.gateway import CallEvent, SessionRecorder
from next_role.integrations.llm.models import LLMCallEvent, LLMOutcome, LLMTask
from next_role.main import create_app
from next_role.worker import prune_llm_events
from tests.conftest import API_KEY

NOW = datetime.now(UTC)
PARAMS = {
    "model": "gpt-x",
    "temperature_sent": False,
    "temperature": None,
    "response_format": "json_schema",
    "timeout_seconds": 60.0,
}


@pytest.fixture
def llm_client(settings: Settings, session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    configured = settings.model_copy(
        update={
            "llm_provider": LLMProviderName.OPENAI_COMPATIBLE,
            "llm_base_url": "https://llm.example.invalid:8443/v1/private-path",
            "llm_model": "gpt-x",
            "llm_api_key": SecretStr("sk-very-secret"),
            "llm_allow_external": True,
        }
    )
    with TestClient(create_app(configured, session_factory), headers={"X-API-Key": API_KEY}) as c:
        yield c


def _event(outcome: LLMOutcome, task: LLMTask = LLMTask.EXTRACTION, **fields: Any) -> CallEvent:
    defaults: dict[str, Any] = {
        "provider": "openai_compatible",
        "model": "gpt-x",
        "params": PARAMS,
        "created_at": NOW,
    }
    return CallEvent(task=task, outcome=outcome, **{**defaults, **fields})


def _record(factory: sessionmaker[Session], *events: CallEvent) -> None:
    recorder = SessionRecorder(factory)
    for event in events:
        recorder.record(event)


def _task(body: dict[str, Any], task: LLMTask) -> dict[str, Any]:
    [row] = [t for t in body["tasks"] if t["task"] == task.value]
    return row


def test_status_without_a_provider(client: TestClient) -> None:
    body = client.get("/api/v1/integrations/llm/status").json()
    assert body["provider"] == "none" and body["enabled"] is False and body["host"] is None
    assert body["retention_days"] == 30
    assert [t["task"] for t in body["tasks"]] == [t.value for t in LLMTask]
    for row in body["tasks"]:
        assert row["available"] is False and row["model"] is None
        assert set(row["counts"].values()) == {0} and set(row["counts_24h"]) == {
            o.value for o in LLMOutcome
        }
        assert row["last_error"] is None and row["last_success"] is None
        assert row["last_attempt"] is None and row["reasoning_effort"] is None
        assert row["model_source"] is None and row["effort_source"] is None


def test_status_requires_auth(settings: Settings, session_factory: sessionmaker[Session]) -> None:
    with TestClient(create_app(settings, session_factory)) as anonymous:
        assert anonymous.get("/api/v1/integrations/llm/status").status_code == 401


def test_status_aggregates_per_task(
    llm_client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    old = NOW - timedelta(days=3)
    _record(
        session_factory,
        _event(LLMOutcome.OK, attempt=1, latency_ms=900, created_at=old),
        _event(LLMOutcome.PROVIDER_ERROR, attempt=1, error="old error", created_at=old),
        _event(LLMOutcome.TEMPERATURE_DROPPED, attempt=1, created_at=NOW - timedelta(minutes=9)),
        _event(
            LLMOutcome.SCHEMA_INVALID,
            attempt=1,
            error="ValidationError",
            created_at=NOW - timedelta(minutes=8),
        ),
        _event(LLMOutcome.OK, attempt=2, latency_ms=420, created_at=NOW - timedelta(minutes=7)),
        _event(
            LLMOutcome.DETERMINISTIC_FALLBACK,
            error="LLM returned no grounded requirements",
            params=None,
            created_at=NOW - timedelta(minutes=6),
        ),
        _event(
            LLMOutcome.PROVIDER_ERROR,
            task=LLMTask.SEMANTIC_CHECK,
            attempt=2,
            error="openai_compatible returned HTTP 500",
        ),
    )
    body = llm_client.get("/api/v1/integrations/llm/status").json()
    assert body["provider"] == "openai_compatible" and body["enabled"] is True
    assert body["host"] == "llm.example.invalid"
    assert body["allow_external"] is True and body["allow_candidate_data"] is False

    extraction = _task(body, LLMTask.EXTRACTION)
    assert extraction["available"] is True and extraction["model"] == "gpt-x"
    assert extraction["counts"] == {
        "ok": 2,
        "provider_error": 1,
        "schema_invalid": 1,
        "temperature_dropped": 1,
        "deterministic_fallback": 1,
        "model_fallback": 0,
    }
    assert extraction["counts_24h"] == {**extraction["counts"], "ok": 1, "provider_error": 0}
    # The latest error, not the latest event (the fallback is reported in counts).
    assert extraction["last_error"]["message"] == "ValidationError"
    assert extraction["last_error"]["params"] == PARAMS
    assert extraction["last_error"]["model"] == "gpt-x"
    assert extraction["last_success"]["latency_ms"] == 420
    # Not OpenAI itself: the env model, no effort, and the latest request (not the fallback).
    assert extraction["model_source"] == "default"
    assert extraction["reasoning_effort"] is None and extraction["effort_source"] is None
    assert extraction["last_attempt"] == {
        "at": extraction["last_success"]["at"],
        "outcome": "ok",
        "model": "gpt-x",
        "reasoning_effort": None,
    }

    check = _task(body, LLMTask.SEMANTIC_CHECK)
    # Drafting and checks need the candidate-data opt-in (ADR 0006).
    assert check["available"] is False
    assert check["counts"]["provider_error"] == 1 and check["last_success"] is None
    assert check["last_error"]["message"] == "openai_compatible returned HTTP 500"
    assert _task(body, LLMTask.RESUME_DRAFTING)["last_error"] is None


def test_status_never_discloses_url_path_key_or_content(
    llm_client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    _record(session_factory, _event(LLMOutcome.SCHEMA_INVALID, attempt=1, error="ValidationError"))
    raw = llm_client.get("/api/v1/integrations/llm/status").text
    for secret in ("private-path", "sk-very-secret", "8443", "https://", "messages", "prompt"):
        assert secret not in raw


def test_old_events_are_pruned(session_factory: sessionmaker[Session]) -> None:
    _record(
        session_factory,
        _event(LLMOutcome.OK, created_at=NOW - timedelta(days=31)),
        _event(LLMOutcome.OK, created_at=NOW - timedelta(days=29)),
    )
    assert prune_llm_events(session_factory, 30, now=lambda: NOW) == 1
    with session_factory() as s:
        [kept] = s.scalars(select(LLMCallEvent)).all()
    assert kept.created_at > NOW - timedelta(days=30)


def test_the_table_rejects_unknown_tasks_and_outcomes(session: Session) -> None:
    for column, value in (("task", "nonexistent_task"), ("outcome", "nonexistent_outcome")):
        values = {"task": "extraction", "outcome": "ok", column: value}
        with pytest.raises(IntegrityError):
            session.execute(
                text(
                    "INSERT INTO llm_call_events (id, task, outcome, provider) "
                    "VALUES (gen_random_uuid(), :task, :outcome, 'p')"
                ),
                values,
            )
        session.rollback()
