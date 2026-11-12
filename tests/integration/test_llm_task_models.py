"""Saved per-task models and efforts: schema, migration 0011, resolution, and status."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from next_role.config import LLMProviderName, Settings
from next_role.integrations.llm.base import ReasoningEffort
from next_role.integrations.llm.gateway import LLMGateway
from next_role.integrations.llm.http_providers import OpenAICompatibleProvider
from next_role.integrations.llm.models import LLMTask, LLMTaskModel
from next_role.integrations.llm.resolver import (
    DbResolver,
    Source,
    StaticResolver,
    build_resolver,
)
from next_role.main import create_app
from tests.conftest import API_KEY, TEST_DATABASE_URL, alembic_config
from tests.llm_fakes import MemoryRecorder

E = ReasoningEffort
T = LLMTask


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _save(factory: sessionmaker[Session], task: LLMTask, **values: Any) -> None:
    with factory.begin() as session:
        row = session.get(LLMTaskModel, task) or LLMTaskModel(task=task)
        for key, value in values.items():
            setattr(row, key, value)
        session.add(row)


def _openai_settings(settings: Settings, host: str = "https://api.openai.com") -> Settings:
    return settings.model_copy(
        update={
            "llm_provider": LLMProviderName.OPENAI_COMPATIBLE,
            "llm_base_url": host,
            "llm_model": "gpt-6-luna",
            "llm_api_key": SecretStr("sk-very-secret"),
            "llm_allow_external": True,
        }
    )


# --- Schema -------------------------------------------------------------------------------


def test_columns_are_nullable_and_constrained(session_factory: sessionmaker[Session]) -> None:
    _save(session_factory, T.EXTRACTION)  # both null: "use the defaults"
    with session_factory() as s:
        row = s.get(LLMTaskModel, T.EXTRACTION)
        assert row is not None and row.model is None and row.reasoning_effort is None
        assert row.created_at is not None and row.updated_at is not None
    for task, effort in [("unknown_task", None), ("semantic_check", "extreme")]:
        with pytest.raises(IntegrityError), session_factory.begin() as s:
            s.execute(
                text("INSERT INTO llm_task_models (task, reasoning_effort) VALUES (:t, :e)"),
                {"t": task, "e": effort},
            )


def _insert_event(conn: Any, outcome: str) -> None:
    conn.execute(
        text(
            "INSERT INTO llm_call_events (id, task, outcome, provider) "
            "VALUES (gen_random_uuid(), 'extraction', :o, 'openai_compatible')"
        ),
        {"o": outcome},
    )


def test_migration_0011_downgrade_deletes_model_fallback_rows_and_upgrades(
    engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    cfg = alembic_config(TEST_DATABASE_URL)
    with engine.begin() as conn:
        _insert_event(conn, "model_fallback")
        _insert_event(conn, "ok")
    try:
        command.downgrade(cfg, "0010_llm_call_events")
        with engine.begin() as conn:
            outcomes = conn.execute(text("SELECT outcome FROM llm_call_events")).scalars().all()
            assert outcomes == ["ok"]
            exists = conn.execute(text("SELECT to_regclass('llm_task_models')")).scalar()
            assert exists is None
        # The old constraint is back.
        with pytest.raises(IntegrityError), engine.begin() as conn:
            _insert_event(conn, "model_fallback")
    finally:
        command.upgrade(cfg, "head")
    with engine.begin() as conn:
        _insert_event(conn, "model_fallback")


# --- Resolution ---------------------------------------------------------------------------


def test_defaults_without_rows(session_factory: sessionmaker[Session]) -> None:
    resolver = DbResolver(session_factory, "gpt-6-luna")
    resolved = {t: resolver.resolve(t) for t in LLMTask}
    assert {t: (r.model, r.reasoning_effort) for t, r in resolved.items()} == {
        T.EXTRACTION: ("gpt-6-luna", E.NONE),
        T.RESUME_DRAFTING: ("gpt-6-luna", E.MEDIUM),
        T.LETTER_DRAFTING: ("gpt-6-luna", E.MEDIUM),
        T.SEMANTIC_CHECK: ("gpt-6-luna", E.LOW),
    }
    assert all(r.model_source is Source.DEFAULT for r in resolved.values())
    assert all(r.effort_source is Source.DEFAULT for r in resolved.values())


def test_overrides_win_and_null_columns_use_defaults(
    session_factory: sessionmaker[Session],
) -> None:
    _save(session_factory, T.RESUME_DRAFTING, model="gpt-6.1-terra", reasoning_effort=E.HIGH)
    _save(session_factory, T.LETTER_DRAFTING, model="gpt-6.1-terra")  # effort: default
    _save(session_factory, T.EXTRACTION, reasoning_effort=E.LOW)  # model: default
    # An override effort the model does not support falls back to its lowest.
    _save(session_factory, T.SEMANTIC_CHECK, model="gpt-6.1-sol", reasoning_effort=E.NONE)
    resolver = DbResolver(session_factory, "gpt-6-luna")

    def summary(task: LLMTask) -> tuple[str, Any, Source, Source]:
        r = resolver.resolve(task)
        return (r.model, r.reasoning_effort, r.model_source, r.effort_source)

    assert summary(T.RESUME_DRAFTING) == ("gpt-6.1-terra", E.HIGH, Source.OVERRIDE, Source.OVERRIDE)
    assert summary(T.LETTER_DRAFTING) == (
        "gpt-6.1-terra",
        E.MEDIUM,
        Source.OVERRIDE,
        Source.DEFAULT,
    )
    assert summary(T.EXTRACTION) == ("gpt-6-luna", E.LOW, Source.DEFAULT, Source.OVERRIDE)
    assert summary(T.SEMANTIC_CHECK) == ("gpt-6.1-sol", E.LOW, Source.OVERRIDE, Source.OVERRIDE)


def test_for_model_reresolves_the_effort_for_the_fallback_model(
    session_factory: sessionmaker[Session],
) -> None:
    _save(session_factory, T.EXTRACTION, model="gpt-6.1-sol")
    resolver = DbResolver(session_factory, "gpt-6-luna")
    assert resolver.resolve(T.EXTRACTION).reasoning_effort is E.LOW  # Sol has no ``none``
    fallback = resolver.for_model(T.EXTRACTION, "gpt-6-luna")
    assert (fallback.model, fallback.reasoning_effort, fallback.model_source) == (
        "gpt-6-luna",
        E.NONE,
        Source.DEFAULT,
    )


def test_changes_are_picked_up_after_the_cache_expires(
    session_factory: sessionmaker[Session],
) -> None:
    clock = Clock()
    resolver = DbResolver(session_factory, "gpt-6-luna", ttl=5, clock=clock)
    assert resolver.resolve(T.EXTRACTION).model == "gpt-6-luna"
    _save(session_factory, T.EXTRACTION, model="gpt-6.1-terra")
    clock.now += 4.9
    assert resolver.resolve(T.EXTRACTION).model == "gpt-6-luna"  # cached
    clock.now += 0.1
    assert resolver.resolve(T.EXTRACTION).model == "gpt-6.1-terra"
    _save(session_factory, T.EXTRACTION, model=None)
    resolver.invalidate()
    assert resolver.resolve(T.EXTRACTION).model == "gpt-6-luna"


def test_a_failing_database_keeps_the_last_values(
    session_factory: sessionmaker[Session], caplog: pytest.LogCaptureFixture
) -> None:
    _save(session_factory, T.EXTRACTION, model="gpt-6.1-terra")
    clock = Clock()
    resolver = DbResolver(session_factory, "gpt-6-luna", ttl=5, clock=clock)
    assert resolver.resolve(T.EXTRACTION).model == "gpt-6.1-terra"

    def broken() -> Session:
        raise RuntimeError("database is down")

    resolver._factory = broken  # type: ignore[assignment]
    clock.now += 10
    assert resolver.resolve(T.EXTRACTION).model == "gpt-6.1-terra"
    assert "could not load llm task models" in caplog.text


def test_a_gateway_picks_up_a_change_without_being_rebuilt(
    session_factory: sessionmaker[Session],
) -> None:
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"v": 1}'}}]})

    class Out(BaseModel):
        v: int

    clock = Clock()
    provider = OpenAICompatibleProvider(
        "https://api.openai.com", "gpt-6-luna", 5, transport=httpx.MockTransport(handler)
    )
    recorder = MemoryRecorder()
    llm = LLMGateway(provider, recorder, DbResolver(session_factory, "gpt-6-luna", clock=clock))
    llm.complete_model(T.EXTRACTION, "s", "u", Out)
    _save(session_factory, T.EXTRACTION, model="gpt-6.1-terra", reasoning_effort=E.LOW)
    clock.now += 60
    llm.complete_model(T.EXTRACTION, "s", "u", Out)
    assert sent == ["gpt-6-luna", "gpt-6.1-terra"]
    assert [e.model for e in recorder.events] == sent
    assert recorder.events[1].params is not None
    assert recorder.events[1].params["reasoning_effort"] == "low"


@pytest.mark.parametrize("host", ["https://llm.example.invalid", "https://openai.example.invalid"])
def test_saved_choices_are_ignored_elsewhere(
    settings: Settings, session_factory: sessionmaker[Session], host: str
) -> None:
    _save(session_factory, T.EXTRACTION, model="gpt-6.1-terra", reasoning_effort=E.HIGH)
    resolver = build_resolver(_openai_settings(settings, host), session_factory)
    assert isinstance(resolver, StaticResolver)
    resolved = resolver.resolve(T.EXTRACTION)
    assert (resolved.model, resolved.reasoning_effort) == ("gpt-6-luna", None)
    with session_factory() as s:  # ignored, not deleted
        assert s.get(LLMTaskModel, T.EXTRACTION) is not None


# --- Status -------------------------------------------------------------------------------


@pytest.fixture
def openai_client(
    settings: Settings, session_factory: sessionmaker[Session]
) -> Iterator[TestClient]:
    app = create_app(_openai_settings(settings), session_factory)
    with TestClient(app, headers={"X-API-Key": API_KEY}) as c:
        yield c


def test_status_reports_the_resolved_model_effort_and_sources(
    openai_client: TestClient, session_factory: sessionmaker[Session]
) -> None:
    _save(session_factory, T.RESUME_DRAFTING, model="gpt-6.1-terra")
    openai_client.app.state.llm_resolver.invalidate()  # type: ignore[attr-defined]
    body = openai_client.get("/api/v1/integrations/llm/status").json()
    rows = {row["task"]: row for row in body["tasks"]}
    assert {
        t: (r["model"], r["reasoning_effort"], r["model_source"], r["effort_source"])
        for t, r in rows.items()
    } == {
        "extraction": ("gpt-6-luna", "none", "default", "default"),
        "resume_drafting": ("gpt-6.1-terra", "medium", "override", "default"),
        "letter_drafting": ("gpt-6-luna", "medium", "default", "default"),
        "semantic_check": ("gpt-6-luna", "low", "default", "default"),
    }
    assert set(rows["extraction"]["counts"]) >= {"model_fallback"}
    text_body = openai_client.get("/api/v1/integrations/llm/status").text
    assert "sk-very-secret" not in text_body
