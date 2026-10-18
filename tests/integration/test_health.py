from fastapi.testclient import TestClient


def test_liveness(client: TestClient) -> None:
    assert client.get("/health/live").json() == {"status": "ok"}


def test_readiness_reports_schema_revision(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["schema_revision"]


def test_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "abc123"})
    assert response.headers["X-Request-ID"] == "abc123"
