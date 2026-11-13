import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

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


def test_requires_api_key(client: TestClient, profile: dict[str, Any]) -> None:
    response = client.post("/api/v1/candidates", json=profile, headers={"X-API-Key": "wrong"})
    assert response.status_code == 401


def test_create_and_retrieve_profile(client: TestClient, profile: dict[str, Any]) -> None:
    created = _create(client, profile)
    fetched = client.get(f"/api/v1/candidates/{created['id']}").json()

    assert fetched["full_name"] == "Alex Example"
    assert fetched["preferences"]["work_authorizations"] == ["CA"]
    assert len(fetched["evidence_sources"]) == 4
    assert len(fetched["facts"]) == len(profile["facts"])
    assert [c["id"] for c in client.get("/api/v1/candidates").json()] == [created["id"]]


def test_provenance_is_preserved(client: TestClient, profile: dict[str, Any]) -> None:
    created = _create(client, profile)
    evidence = {e["title"]: e["id"] for e in created["evidence_sources"]}
    facts = {f["title"]: f for f in created["facts"]}

    northwind = facts["Backend Engineer"]
    assert northwind["verification_status"] == "user_verified"
    assert northwind["verified_at"] is not None
    assert northwind["support"] == "confirmed"
    assert northwind["evidence_source_ids"] == [evidence["Resume (synthetic)"]]
    # Aliases are normalized to canonical skill names.
    assert northwind["skills"] == ["python", "django", "postgresql", "docker", "rest apis"]

    cert = facts["AWS Certified Solutions Architect - Associate"]
    assert cert["support"] == "confirmed" and cert["evidence_source_ids"]

    assert facts["Kubernetes"]["support"] == "unconfirmed"
    assert (
        facts["Reduced API p95 latency"]["parent_fact_id"] == facts["Senior Backend Engineer"]["id"]
    )


def test_profile_rejects_unknown_evidence_key(client: TestClient, profile: dict[str, Any]) -> None:
    profile["facts"][0]["evidence_keys"] = ["does-not-exist"]
    response = client.post("/api/v1/candidates", json=profile)
    assert response.status_code == 422
    # Validation errors must not echo submitted candidate data back.
    assert "input" not in json.dumps(response.json()["detail"][0])


def test_experience_requires_end_date_unless_current(
    client: TestClient, profile: dict[str, Any]
) -> None:
    del profile["facts"][0]["end_date"]
    assert client.post("/api/v1/candidates", json=profile).status_code == 422


def test_add_fact_verify_edit_and_retire(client: TestClient, profile: dict[str, Any]) -> None:
    created = _create(client, {**profile, "facts": [], "evidence_sources": []})
    cid = created["id"]
    evidence = client.post(
        f"/api/v1/candidates/{cid}/evidence-sources",
        json={"kind": "portfolio", "title": "Talk recording (synthetic)"},
    ).json()

    fact = client.post(
        f"/api/v1/candidates/{cid}/facts",
        json={"fact_type": "skill", "name": "k8s", "evidence_source_ids": [evidence["id"]]},
    ).json()
    assert fact["skills"] == ["kubernetes"]
    # Linked evidence documents a fact; only confirmation makes it count (ADR 0009).
    assert fact["support"] == "unconfirmed"

    verified = client.post(f"/api/v1/candidates/{cid}/facts/{fact['id']}/verify").json()
    assert verified["support"] == "confirmed"

    # Editing content invalidates a previous confirmation.
    edited = client.patch(
        f"/api/v1/candidates/{cid}/facts/{fact['id']}",
        json={"attributes": {"years_experience": 3}},
    ).json()
    assert edited["verification_status"] == "self_reported"
    assert edited["support"] == "unconfirmed"
    assert edited["attributes"] == {"years_experience": 3}

    bad = client.patch(
        f"/api/v1/candidates/{cid}/facts/{fact['id']}", json={"attributes": {"degree": "x"}}
    )
    assert bad.status_code == 422

    retired = client.delete(f"/api/v1/candidates/{cid}/facts/{fact['id']}").json()
    assert retired["retired_at"] is not None
    assert client.get(f"/api/v1/candidates/{cid}/facts").json() == []
    assert len(client.get(f"/api/v1/candidates/{cid}/facts?include_retired=true").json()) == 1


def test_evidence_from_another_candidate_is_rejected(
    client: TestClient, profile: dict[str, Any]
) -> None:
    first = _create(client, profile)
    second = _create(client, {**profile, "facts": [], "evidence_sources": []})
    foreign_evidence = first["evidence_sources"][0]["id"]
    response = client.post(
        f"/api/v1/candidates/{second['id']}/facts",
        json={"fact_type": "skill", "name": "Rust", "evidence_source_ids": [foreign_evidence]},
    )
    assert response.status_code == 422


def test_update_preferences_and_missing_candidate(
    client: TestClient, profile: dict[str, Any]
) -> None:
    created = _create(client, profile)
    updated = client.patch(
        f"/api/v1/candidates/{created['id']}",
        json={"headline": "Platform engineer", "preferences": {"work_arrangements": ["remote"]}},
    ).json()
    assert updated["headline"] == "Platform engineer"
    assert updated["preferences"]["work_arrangements"] == ["remote"]
    assert updated["preferences"]["work_authorizations"] == []

    missing = client.get("/api/v1/candidates/00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    assert missing.json()["error"] == "not_found"
