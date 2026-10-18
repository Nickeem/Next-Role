"""Assessment API tests, including the end-to-end Phase 1 workflow on synthetic data."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
AS_OF = "2026-10-01"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return data


@pytest.fixture
def candidate_id(client: TestClient) -> str:
    response = client.post("/api/v1/candidates", json=_load("candidate.json"))
    assert response.status_code == 201
    cid: str = response.json()["id"]
    return cid


def _submit(client: TestClient, name: str) -> str:
    response = client.post("/api/v1/jobs", json=_load(f"jobs/{name}"))
    assert response.status_code == 201
    jid: str = response.json()["job"]["id"]
    return jid


def _assess(client: TestClient, job_id: str, candidate_id: str) -> Any:
    return client.post(
        f"/api/v1/jobs/{job_id}/assessments", json={"candidate_id": candidate_id, "as_of": AS_OF}
    )


def test_end_to_end_backend_role(client: TestClient, candidate_id: str) -> None:
    job_id = _submit(client, "senior-backend-engineer.json")
    response = _assess(client, job_id, candidate_id)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] is True
    a = body["assessment"]

    statuses = {r["text"]: r["status"] for r in a["requirements"]}
    assert statuses == {
        "5+ years of professional software development experience": "met",
        "Strong Python and FastAPI experience": "met",
        "Experience with PostgreSQL and Redis": "met",
        # Kubernetes is only self-reported: never presented as a verified match.
        "Experience running workloads on Kubernetes": "unverified",
        "Bachelor's degree in Computer Science or a related field": "met",
        "Must be legally authorized to work in Canada": "met",
        "Excellent written and verbal communication": "unknown",
        "Experience with Kafka or other event streaming platforms": "met",
        "Go experience is a plus": "unknown",
        # The certification is evidenced but expired on 2026-02-15.
        "AWS certification": "partially_met",
    }
    assert a["score"] == 69
    assert a["tier"] == "moderate"
    assert a["has_blocking_gaps"] is False
    assert a["coverage"] == pytest.approx(17 / 24, abs=0.001)
    assert a["method_version"] == "rules-v1"
    assert "not a probability" in a["disclaimer"]
    assert a["summary"]["unverified"] == ["Experience running workloads on Kubernetes"]
    assert a["summary"]["gaps"] == []

    python = next(r for r in a["requirements"] if r["text"].startswith("Strong Python"))
    assert python["explanation"]
    assert {e["support"] for e in python["evidence"]} <= {"verified", "evidenced"}
    assert all(e["fact_id"] for e in python["evidence"])

    prefs = {c["name"]: c["status"] for c in a["preference_checks"]}
    assert prefs == {
        "work_arrangement": "compatible",
        "location": "compatible",
        "employment_type": "compatible",
        "salary": "compatible",
    }

    # Persisted and retrievable.
    fetched = client.get(f"/api/v1/assessments/{a['id']}").json()
    assert fetched == a
    listed = client.get(f"/api/v1/jobs/{job_id}/assessments").json()
    assert [x["id"] for x in listed] == [a["id"]]
    by_candidate = client.get(f"/api/v1/assessments?candidate_id={candidate_id}").json()
    assert [x["id"] for x in by_candidate] == [a["id"]]


def test_mismatched_role_reports_gaps_separately_from_missing_evidence(
    client: TestClient, candidate_id: str
) -> None:
    job_id = _submit(client, "data-engineer-us.json")
    a = _assess(client, job_id, candidate_id).json()["assessment"]
    statuses = {r["text"]: r["status"] for r in a["requirements"]}
    assert statuses["Master's degree in a quantitative field"] == "not_met"
    assert statuses["Must be authorized to work in the US; we are unable to sponsor visas"] == (
        "not_met"
    )
    assert statuses["3+ years building data pipelines with Spark and Airflow"] == "no_evidence"
    assert statuses["Experience with dbt preferred"] == "no_evidence"
    assert a["has_blocking_gaps"] is True
    assert a["tier"] == "weak"
    assert a["score"] == 0
    assert len(a["summary"]["gaps"]) == 2
    prefs = {c["name"]: c["status"] for c in a["preference_checks"]}
    assert prefs["work_arrangement"] == "conflict"
    assert prefs["location"] == "conflict"


def test_assessment_is_idempotent_and_reflects_knowledge_base_changes(
    client: TestClient, candidate_id: str
) -> None:
    job_id = _submit(client, "senior-backend-engineer.json")
    first = _assess(client, job_id, candidate_id).json()["assessment"]
    again = _assess(client, job_id, candidate_id)
    assert again.status_code == 200
    assert again.json()["created"] is False
    assert again.json()["assessment"]["id"] == first["id"]

    # Verifying the Kubernetes claim changes the inputs, so a new assessment is created.
    facts = client.get(f"/api/v1/candidates/{candidate_id}/facts").json()
    k8s = next(f for f in facts if f["title"] == "Kubernetes")
    client.post(f"/api/v1/candidates/{candidate_id}/facts/{k8s['id']}/verify")
    second = _assess(client, job_id, candidate_id).json()
    assert second["created"] is True
    statuses = {r["text"]: r["status"] for r in second["assessment"]["requirements"]}
    assert statuses["Experience running workloads on Kubernetes"] == "met"
    assert second["assessment"]["score"] > first["score"]

    # The earlier assessment is preserved unchanged.
    old = client.get(f"/api/v1/assessments/{first['id']}").json()
    assert old["score"] == first["score"]
    assert len(client.get(f"/api/v1/jobs/{job_id}/assessments").json()) == 2


def test_retired_facts_are_excluded(client: TestClient, candidate_id: str) -> None:
    job_id = _submit(client, "senior-backend-engineer.json")
    facts = client.get(f"/api/v1/candidates/{candidate_id}/facts").json()
    cert = next(f for f in facts if f["fact_type"] == "certification")
    client.delete(f"/api/v1/candidates/{candidate_id}/facts/{cert['id']}")
    a = _assess(client, job_id, candidate_id).json()["assessment"]
    aws = next(r for r in a["requirements"] if r["text"] == "AWS certification")
    assert aws["status"] == "no_evidence"


def test_job_without_requirements_is_insufficient_data(
    client: TestClient, candidate_id: str
) -> None:
    job = client.post(
        "/api/v1/jobs", json={"description": "Friendly team, great snacks. Apply today please!"}
    ).json()["job"]
    a = _assess(client, job["id"], candidate_id).json()["assessment"]
    assert a["requirements"] == []
    assert a["score"] is None
    assert a["tier"] == "insufficient_data"


def test_not_found(client: TestClient, candidate_id: str) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert _assess(client, missing, candidate_id).status_code == 404
    job_id = _submit(client, "senior-backend-engineer.json")
    assert _assess(client, job_id, missing).status_code == 404
    assert client.get(f"/api/v1/assessments/{missing}").status_code == 404
