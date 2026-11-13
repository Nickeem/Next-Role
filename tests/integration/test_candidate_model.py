"""ADR 0009 model changes: migration 0012, about-me, origin, inclusion, typed PUT."""

import copy
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from next_role.candidates.models import CandidateFact, FactOrigin
from tests.conftest import TEST_DATABASE_URL, alembic_config

EXAMPLE = json.loads(
    (Path(__file__).resolve().parents[2] / "examples" / "candidate.json").read_text()
)


@pytest.fixture
def profile() -> dict[str, Any]:
    return copy.deepcopy(EXAMPLE)


def _create(client: TestClient, profile: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/v1/candidates", json=profile)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def _fact(candidate: dict[str, Any], fact_type: str) -> dict[str, Any]:
    return next(f for f in candidate["facts"] if f["fact_type"] == fact_type)


# --- Migration 0012 -----------------------------------------------------------------------


def _old_candidate(conn: Any, summary: str) -> uuid.UUID:
    cid = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO candidates (id, full_name, summary, contact, preferences) "
            "VALUES (:id, 'Sam Synthetic', :s, '{}', '{}')"
        ),
        {"id": cid, "s": summary},
    )
    return cid


def _old_fact(conn: Any, cid: uuid.UUID, fact_type: str, attributes: dict[str, Any]) -> uuid.UUID:
    fid = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO candidate_facts (id, candidate_id, fact_type, title, is_current, "
            "skills, attributes, verification_status) VALUES (:id, :c, :t, 'Synthetic', false, "
            "'{}', CAST(:a AS jsonb), 'self_reported')"
        ),
        {"id": fid, "c": cid, "t": fact_type, "a": json.dumps(attributes)},
    )
    return fid


def test_migration_0012_round_trip(engine: Engine, session_factory: sessionmaker[Session]) -> None:
    cfg = alembic_config(TEST_DATABASE_URL)
    command.downgrade(cfg, "0011_llm_task_models")
    try:
        with engine.begin() as conn:
            cid = _old_candidate(conn, "I like calm systems.")
            skill = _old_fact(conn, cid, "skill", {"proficiency": "expert", "category": "tool"})
            job = _old_fact(conn, cid, "experience", {"employment_type": "full_time"})
            kept = _old_fact(conn, cid, "achievement", {"metrics": []})
            conn.execute(
                text(
                    "INSERT INTO evidence_sources (id, candidate_id, kind, title) "
                    "VALUES (:id, :c, 'user_statement', 'Answer')"
                ),
                {"id": uuid.uuid4(), "c": cid},
            )
        command.upgrade(cfg, "0012_candidate_model")
        with engine.begin() as conn:
            assert conn.execute(text("SELECT about_me FROM candidates")).scalar_one() == (
                "I like calm systems."
            )
            rows = dict(
                conn.execute(text("SELECT id, attributes FROM candidate_facts")).tuples().all()
            )
            assert rows[skill] == {"category": "tool"}
            assert rows[job] == {}
            assert rows[kept] == {"metrics": []}
            facts = conn.execute(
                text("SELECT DISTINCT origin, include_in_documents FROM candidate_facts")
            ).all()
            assert facts == [("api", True)]
            assert conn.execute(text("SELECT kind FROM evidence_sources")).scalar_one() == "other"
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text("UPDATE evidence_sources SET kind = 'user_statement'"))
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text("UPDATE candidate_facts SET origin = 'scraped'"))
        command.downgrade(cfg, "0011_llm_task_models")
        with engine.begin() as conn:
            assert conn.execute(text("SELECT summary FROM candidates")).scalar_one() == (
                "I like calm systems."
            )
            conn.execute(text("UPDATE evidence_sources SET kind = 'user_statement'"))
    finally:
        command.upgrade(cfg, "head")


def test_migration_0012_stops_on_a_long_summary(
    engine: Engine, session_factory: sessionmaker[Session]
) -> None:
    cfg = alembic_config(TEST_DATABASE_URL)
    command.downgrade(cfg, "0011_llm_task_models")
    try:
        with engine.begin() as conn:
            _old_candidate(conn, "x" * 2001)
        with pytest.raises(RuntimeError, match="1 candidate\\(s\\) have a summary longer"):
            command.upgrade(cfg, "0012_candidate_model")
        with engine.begin() as conn:  # nothing changed
            assert conn.execute(text("SELECT count(summary) FROM candidates")).scalar_one() == 1
            conn.execute(text("UPDATE candidates SET summary = 'short'"))
    finally:
        command.upgrade(cfg, "head")


# --- Profile fields -----------------------------------------------------------------------


def test_about_me_is_limited_and_returned(client: TestClient, profile: dict[str, Any]) -> None:
    created = _create(client, profile)
    assert created["about_me"].startswith("Synthetic candidate")
    assert "summary" not in created
    url = f"/api/v1/candidates/{created['id']}"
    assert client.patch(url, json={"about_me": "x" * 2001}).status_code == 422
    assert client.patch(url, json={"about_me": "x" * 2000}).json()["about_me"] == "x" * 2000


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(summary=p.pop("about_me")),
        lambda p: next(f for f in p["facts"] if f["fact_type"] == "skill").update(
            proficiency="expert"
        ),
        lambda p: next(f for f in p["facts"] if f["fact_type"] == "experience").update(
            employment_type="full_time"
        ),
        lambda p: p["evidence_sources"][0].update(kind="user_statement"),
    ],
    ids=["summary", "proficiency", "employment_type", "user_statement"],
)
def test_removed_fields_are_rejected(
    client: TestClient, profile: dict[str, Any], change: Any
) -> None:
    change(profile)
    assert client.post("/api/v1/candidates", json=profile).status_code == 422


def test_origin_is_set_per_creation_path(
    client: TestClient, profile: dict[str, Any], session_factory: sessionmaker[Session]
) -> None:
    created = _create(client, profile)
    assert {f["origin"] for f in created["facts"]} == {"import"}
    added = client.post(
        f"/api/v1/candidates/{created['id']}/facts", json={"fact_type": "skill", "name": "Go"}
    ).json()
    assert added["origin"] == "api" and added["include_in_documents"] is True
    # Clients cannot choose the origin.
    response = client.post(
        f"/api/v1/candidates/{created['id']}/facts",
        json={"fact_type": "skill", "name": "Rust", "origin": "manual"},
    )
    assert response.status_code == 422
    with session_factory() as session:
        fact = session.get(CandidateFact, uuid.UUID(added["id"]))
        assert fact is not None and fact.origin is FactOrigin.API


def test_inclusion_is_not_content(client: TestClient, profile: dict[str, Any]) -> None:
    created = _create(client, profile)
    fact = next(f for f in created["facts"] if f["verification_status"] == "user_verified")
    url = f"/api/v1/candidates/{created['id']}/facts/{fact['id']}"
    excluded = client.patch(url, json={"include_in_documents": False}).json()
    assert excluded["include_in_documents"] is False
    assert excluded["verification_status"] == "user_verified"


# --- Typed update (PUT) --------------------------------------------------------------------


def _experience(**changes: Any) -> dict[str, Any]:
    body = {
        "fact_type": "experience",
        "title": "Platform Engineer",
        "organization": "Example Corp",
        "start_date": "2020-01-01",
        "end_date": "2022-06-30",
        "skills": ["Python"],
        "verification_status": "user_verified",
    }
    return {**body, **changes}


@pytest.fixture
def experience(client: TestClient, profile: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    created = _create(client, profile)
    url = f"/api/v1/candidates/{created['id']}/facts"
    fact = client.post(url, json=_experience()).json()
    return f"{url}/{fact['id']}", fact


def test_put_replaces_content_and_resets_confirmation(
    client: TestClient, experience: tuple[str, dict[str, Any]]
) -> None:
    url, fact = experience
    assert fact["verification_status"] == "user_verified"
    body = {k: v for k, v in _experience().items() if k != "verification_status"}

    unchanged = client.put(url, json=body).json()
    assert unchanged["verification_status"] == "user_verified"

    edited = client.put(url, json={**body, "title": "Staff Engineer"}).json()
    assert edited["title"] == "Staff Engineer"
    assert edited["verification_status"] == "self_reported"
    assert edited["origin"] == "api"  # not content

    confirmed = client.put(url, json=_experience(title="Principal Engineer")).json()
    assert confirmed["verification_status"] == "user_verified"

    excluded = client.put(
        url, json=_experience(title="Principal Engineer", include_in_documents=False)
    )
    assert excluded.json()["verification_status"] == "user_verified"
    assert excluded.json()["include_in_documents"] is False


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (_experience(end_date=None), "end_date is required"),
        (_experience(end_date="2019-01-01"), "end date must not be before"),
        ({"fact_type": "skill", "name": "Go"}, "type cannot be changed"),
        (_experience(key="k"), "single fact"),
        (_experience(employment_type="full_time"), "extra"),
    ],
)
def test_put_validates_like_creation(
    client: TestClient, experience: tuple[str, dict[str, Any]], body: dict[str, Any], message: str
) -> None:
    url, _ = experience
    response = client.put(url, json=body)
    assert response.status_code == 422
    assert message in response.text


def test_put_rejects_a_fact_as_its_own_parent(
    client: TestClient, experience: tuple[str, dict[str, Any]]
) -> None:
    url, fact = experience
    response = client.put(url, json=_experience(parent_fact_id=fact["id"]))
    assert response.status_code == 422 and "own parent" in response.text


def test_put_on_a_retired_fact_is_rejected(
    client: TestClient, experience: tuple[str, dict[str, Any]]
) -> None:
    url, _ = experience
    client.delete(url)
    assert client.put(url, json=_experience()).status_code == 422
