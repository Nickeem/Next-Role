"""Review API: queue, summary, details, decisions, history, activity, and preferences."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((EXAMPLES / name).read_text())
    return data


@pytest.fixture
def candidate_id(client: TestClient) -> str:
    response = client.post("/api/v1/candidates", json=_load("candidate.json"))
    assert response.status_code == 201
    cid: str = response.json()["id"]
    return cid


def _assessed_review(client: TestClient, candidate_id: str, job: str) -> dict[str, Any]:
    job_id = client.post("/api/v1/jobs", json=_load(f"jobs/{job}")).json()["job"]["id"]
    response = client.post(
        f"/api/v1/jobs/{job_id}/assessments",
        json={"candidate_id": candidate_id, "as_of": "2026-10-01"},
    )
    assert response.status_code == 201
    [item] = [
        i
        for i in client.get("/api/v1/reviews", params={"status": list(_ALL)}).json()["items"]
        if i["job_id"] == job_id
    ]
    return item


_ALL = ("pending_review", "shortlisted", "postponed", "rejected", "approved_for_drafting")


@pytest.fixture
def backend(client: TestClient, candidate_id: str) -> dict[str, Any]:
    return _assessed_review(client, candidate_id, "senior-backend-engineer.json")


@pytest.fixture
def data_eng(client: TestClient, candidate_id: str) -> dict[str, Any]:
    return _assessed_review(client, candidate_id, "data-engineer-us.json")


def _decide(client: TestClient, review: dict[str, Any], **body: Any) -> Any:
    return client.post(f"/api/v1/reviews/{review['id']}/decisions", json=body)


def test_queue_lists_assessed_jobs_with_stored_assessment(
    client: TestClient, backend: dict[str, Any], data_eng: dict[str, Any]
) -> None:
    page = client.get("/api/v1/reviews", params={"sort": "score"}).json()
    assert page["total"] == 2
    first, second = page["items"]
    assert first["id"] == backend["id"]
    assert first["status"] == "pending_review"
    assert first["version"] == 1
    assert first["follow_up_due"] is False
    assert first["job"]["title"] == "Senior Backend Engineer"
    assert first["assessment"]["score"] == 69
    assert first["assessment"]["tier"] == "moderate"
    assert first["assessment"]["as_of"] == "2026-10-01"
    assert second["assessment"]["tier"] == "weak"


def test_queue_filters_and_search(
    client: TestClient, backend: dict[str, Any], data_eng: dict[str, Any]
) -> None:
    def ids(**params: Any) -> list[str]:
        return [i["id"] for i in client.get("/api/v1/reviews", params=params).json()["items"]]

    assert ids(tier="weak") == [data_eng["id"]]
    assert ids(tier=["weak", "moderate"], sort="score") == [backend["id"], data_eng["id"]]
    assert ids(q="backend") == [backend["id"]]
    assert ids(q="%") == []
    assert ids(status="shortlisted") == []
    assert ids(limit=1, sort="score") == [backend["id"]]
    assert ids(limit=1, offset=1, sort="score") == [data_eng["id"]]
    assert client.get("/api/v1/reviews", params={"status": "bogus"}).status_code == 422


def test_rejected_hidden_by_default(client: TestClient, backend: dict[str, Any]) -> None:
    response = _decide(client, backend, action="reject", expected_version=1, confirm=True)
    assert response.status_code == 200, response.text
    assert client.get("/api/v1/reviews").json()["total"] == 0
    rejected = client.get("/api/v1/reviews", params={"status": "rejected"}).json()
    assert [i["id"] for i in rejected["items"]] == [backend["id"]]


def test_detail_shows_job_and_full_assessment(client: TestClient, backend: dict[str, Any]) -> None:
    body = client.get(f"/api/v1/reviews/{backend['id']}").json()
    assert body["review"]["status"] == "pending_review"
    assert set(body["review"]["allowed_actions"]) == {
        "shortlist",
        "postpone",
        "reject",
        "approve_for_drafting",
    }
    assert body["job"]["description"]
    assert body["assessment"]["score"] == 69
    assert body["assessment"]["requirements"]
    assert "not a probability" in body["assessment"]["disclaimer"]
    assert body["decision_assessment_id"] is None
    assert body["newer_assessment_available"] is False


def test_decision_flow_and_history(client: TestClient, backend: dict[str, Any]) -> None:
    response = _decide(client, backend, action="shortlist", expected_version=1)
    assert response.status_code == 200
    assert response.json()["status"] == "shortlisted"
    assert response.json()["version"] == 2

    response = _decide(
        client, backend, action="approve_for_drafting", expected_version=2, confirm=True
    )
    assert response.json()["status"] == "approved_for_drafting"
    assert response.json()["allowed_actions"] == ["reopen"]

    events = client.get(f"/api/v1/reviews/{backend['id']}/events").json()
    assert [e["event_type"] for e in events] == ["created", "shortlist", "approve_for_drafting"]
    assert events[-1]["previous_status"] == "shortlisted"
    assert events[-1]["actor"] == "operator"

    detail = client.get(f"/api/v1/reviews/{backend['id']}").json()
    assert detail["decision_assessment_id"] == detail["assessment"]["id"]


def test_decision_errors(client: TestClient, backend: dict[str, Any]) -> None:
    stale = _decide(client, backend, action="shortlist", expected_version=7)
    assert stale.status_code == 409
    assert stale.json()["error"] == "conflict"

    not_allowed = _decide(client, backend, action="reopen", expected_version=1, reason="x")
    assert not_allowed.status_code == 409

    unconfirmed = _decide(client, backend, action="reject", expected_version=1)
    assert unconfirmed.status_code == 422
    assert "confirm" in unconfirmed.json()["detail"]

    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    assert (
        _decide(
            client, backend, action="postpone", expected_version=1, postpone_until=past
        ).status_code
        == 422
    )
    naive = _decide(
        client, backend, action="postpone", expected_version=1, postpone_until="2030-01-01T00:00"
    )
    assert naive.status_code == 422
    unknown = _decide(client, backend, action="approve", expected_version=1)
    assert unknown.status_code == 422
    extra = _decide(client, backend, action="shortlist", expected_version=1, score=100)
    assert extra.status_code == 422

    missing = client.post(
        "/api/v1/reviews/00000000-0000-0000-0000-000000000000/decisions",
        json={"action": "shortlist", "expected_version": 1},
    )
    assert missing.status_code == 404
    assert len(client.get(f"/api/v1/reviews/{backend['id']}/events").json()) == 1


def test_weak_assessment_override_requires_reason(
    client: TestClient, data_eng: dict[str, Any]
) -> None:
    body = {"action": "approve_for_drafting", "expected_version": 1, "confirm": True}
    assert _decide(client, data_eng, **body).status_code == 422
    response = _decide(client, data_eng, **body, reason="Sponsorship confirmed by recruiter")
    assert response.status_code == 200
    events = client.get(f"/api/v1/reviews/{data_eng['id']}/events").json()
    assert events[-1]["reason"] == "Sponsorship confirmed by recruiter"


def test_summary_and_due_follow_ups(
    client: TestClient, session: Session, backend: dict[str, Any], data_eng: dict[str, Any]
) -> None:
    until = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    assert (
        _decide(
            client, backend, action="postpone", expected_version=1, postpone_until=until
        ).status_code
        == 200
    )
    summary = client.get("/api/v1/reviews/summary").json()
    assert summary["counts"]["pending_review"] == 1
    assert summary["counts"]["postponed"] == 1
    assert summary["counts"]["approved_for_drafting"] == 0
    assert summary["follow_ups_due"] == 0
    assert summary["total"] == 2

    # Move the date into the past, as if two days had passed.
    session.execute(
        text(
            "UPDATE opportunity_reviews SET postpone_until = now() - interval '1 minute' "
            "WHERE status = 'postponed'"
        )
    )
    session.commit()
    assert client.get("/api/v1/reviews/summary").json()["follow_ups_due"] == 1
    due = client.get("/api/v1/reviews", params={"due": True}).json()["items"]
    assert [i["id"] for i in due] == [backend["id"]]
    assert due[0]["follow_up_due"] is True


def test_activity_across_reviews(
    client: TestClient, backend: dict[str, Any], data_eng: dict[str, Any]
) -> None:
    _decide(client, backend, action="shortlist", expected_version=1)
    page = client.get("/api/v1/activity").json()
    assert page["total"] == 3
    newest = page["items"][0]
    assert newest["event_type"] == "shortlist"
    assert newest["job_title"] == "Senior Backend Engineer"
    assert newest["job_id"] == backend["job_id"]

    decisions = client.get("/api/v1/activity", params={"actor": "operator"}).json()
    assert [i["event_type"] for i in decisions["items"]] == ["shortlist"]
    created = client.get("/api/v1/activity", params={"event_type": "created"}).json()
    assert created["total"] == 2


def test_notification_preferences(client: TestClient, candidate_id: str) -> None:
    defaults = client.get("/api/v1/notification-preferences").json()
    assert defaults == {"review_ready": True, "review_postponed_due": True, "updated_at": None}

    response = client.patch("/api/v1/notification-preferences", json={"review_ready": False})
    assert response.status_code == 200
    assert response.json()["review_ready"] is False
    assert response.json()["review_postponed_due"] is True
    assert response.json()["updated_at"] is not None
    assert client.get("/api/v1/notification-preferences").json()["review_ready"] is False
    bad = client.patch("/api/v1/notification-preferences", json={"ntfy_token": "x"})
    assert bad.status_code == 422


def test_review_routes_require_api_key(client: TestClient) -> None:
    for path in ("/api/v1/reviews", "/api/v1/reviews/summary", "/api/v1/activity"):
        assert client.get(path, headers={"X-API-Key": "wrong"}).status_code == 401
