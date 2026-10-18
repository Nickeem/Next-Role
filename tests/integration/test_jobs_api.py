import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "jobs"


@pytest.fixture
def job() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / "senior-backend-engineer.json").read_text())
    return copy.deepcopy(data)


def test_ingest_normalizes_and_preserves_source(client: TestClient, job: dict[str, Any]) -> None:
    response = client.post("/api/v1/jobs", json=job)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["outcome"] == "created"
    stored = body["job"]

    assert stored["title"] == "Senior Backend Engineer"
    assert stored["work_arrangement"] == "hybrid"
    assert stored["employment_type"] == "full_time"
    assert stored["extraction_method"] == "rules"
    assert len(stored["requirements"]) == 10
    assert [r["position"] for r in stored["requirements"]] == list(range(10))
    assert len(stored["postings"]) == 1

    fetched = client.get(f"/api/v1/jobs/{stored['id']}").json()
    assert fetched["description"] == job["description"]
    assert fetched["requirements"] == stored["requirements"]


def test_missing_details_are_stored_as_unknown(client: TestClient) -> None:
    body = client.post(
        "/api/v1/jobs", json={"description": "Looking for someone with Python and SQL skills."}
    ).json()["job"]
    assert body["title"] is None
    assert body["company"] is None
    assert body["location"] is None
    assert body["work_arrangement"] == "unknown"
    assert body["employment_type"] == "unknown"
    assert body["salary_min"] is None


def test_explicit_requirements_override_extraction(client: TestClient) -> None:
    body = client.post(
        "/api/v1/jobs",
        json={
            "description": "A role on our platform team. Details on request.",
            "requirements": [
                {"text": "Kubernetes in production", "importance": "required"},
                {"text": "Terraform"},
            ],
        },
    ).json()["job"]
    assert body["extraction_method"] == "manual"
    assert [(r["terms"], r["importance"]) for r in body["requirements"]] == [
        (["kubernetes"], "required"),
        (["terraform"], "unspecified"),
    ]


def test_exact_resubmission_is_not_stored_twice(client: TestClient, job: dict[str, Any]) -> None:
    first = client.post("/api/v1/jobs", json=job).json()
    job["description"] = job["description"].upper() + "   "
    second = client.post("/api/v1/jobs", json=job)
    assert second.status_code == 200
    assert second.json()["outcome"] == "duplicate_submission"
    assert second.json()["posting_id"] == first["posting_id"]
    assert len(client.get("/api/v1/jobs").json()) == 1


def test_same_url_links_new_posting(client: TestClient, job: dict[str, Any]) -> None:
    first = client.post("/api/v1/jobs", json=job).json()
    job["description"] += "\nUpdated: salary band confirmed."
    job["source_url"] = "http://www.jobs.example.invalid/fabrikam/senior-backend-engineer/"
    job["title"] = "Sr. Backend Engineer"
    second = client.post("/api/v1/jobs", json=job).json()
    assert second["outcome"] == "linked_to_existing"
    assert second["job"]["id"] == first["job"]["id"]
    assert second["job"]["title"] == "Senior Backend Engineer"  # opportunity unchanged
    assert len(second["job"]["postings"]) == 2


def test_same_company_title_location_links(client: TestClient, job: dict[str, Any]) -> None:
    del job["source_url"]
    first = client.post("/api/v1/jobs", json=job).json()
    job["description"] = "Reposted. " + job["description"]
    job["company"] = "  fabrikam logistics (FICTIONAL) "
    second = client.post("/api/v1/jobs", json=job).json()
    assert second["outcome"] == "linked_to_existing"
    assert second["job"]["id"] == first["job"]["id"]

    job["description"] = "Another team. " + job["description"]
    job["location"] = "Vancouver, BC"
    third = client.post("/api/v1/jobs", json=job).json()
    assert third["outcome"] == "created"


def test_validation(client: TestClient, settings: Any) -> None:
    assert client.post("/api/v1/jobs", json={"description": "short"}).status_code == 422
    bad_salary = {
        "description": "x" * 40,
        "salary_min": 10,
        "salary_max": 5,
        "salary_currency": "USD",
    }
    assert client.post("/api/v1/jobs", json=bad_salary).status_code == 422
    unknown_field = {"description": "x" * 40, "apply_automatically": True}
    assert client.post("/api/v1/jobs", json=unknown_field).status_code == 422
    too_long = {"description": "word " * (settings.max_job_description_chars // 4)}
    response = client.post("/api/v1/jobs", json=too_long)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_input"


def test_list_filter_and_archive(client: TestClient, job: dict[str, Any]) -> None:
    created = client.post("/api/v1/jobs", json=job).json()["job"]
    archived = client.patch(f"/api/v1/jobs/{created['id']}", json={"status": "archived"}).json()
    assert archived["status"] == "archived"
    assert client.get("/api/v1/jobs?status=active").json() == []
    assert len(client.get("/api/v1/jobs?status=archived").json()) == 1
    assert client.get(f"/api/v1/jobs/{'0' * 8}-0000-0000-0000-{'0' * 12}").status_code == 404


def test_configured_llm_extractor_is_used_and_recorded(
    client: TestClient, job: dict[str, Any]
) -> None:
    from next_role.jobs.llm_extraction import LLMRequirementExtractor
    from tests.unit.test_llm_extraction import FakeProvider, _json

    provider = FakeProvider(_json(("Strong Python and FastAPI experience", "required")))
    client.app.state.requirement_extractor = LLMRequirementExtractor(provider)  # type: ignore[attr-defined]
    stored = client.post("/api/v1/jobs", json=job).json()["job"]
    assert stored["extraction_method"] == "llm"
    assert stored["extraction_notes"].startswith("fake")
    assert [(r["terms"], r["extraction_method"]) for r in stored["requirements"]] == [
        (["python", "fastapi"], "llm")
    ]
