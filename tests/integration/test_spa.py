"""Serving the built web workspace: client routes, assets, traversal, and headers."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from next_role.config import Settings
from next_role.main import create_app
from tests.conftest import API_KEY, TEST_DATABASE_URL

INDEX = "<!doctype html><title>Next-Role Review</title><div id=root></div>"


@pytest.fixture
def spa(tmp_path: Path, session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(INDEX)
    (static / "assets" / "app-abc123.js").write_text("console.log(1)")
    (static / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("outside")
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        api_key=SecretStr(API_KEY),
        static_dir=str(static),
        log_json=False,
        _env_file=None,
    )
    with TestClient(create_app(settings, session_factory)) as c:
        yield c


@pytest.mark.parametrize("path", ["/", "/reviews/0bd3eea3-9e2e", "/settings", "/index.html"])
def test_client_routes_serve_index_with_csp(spa: TestClient, path: str) -> None:
    response = spa.get(path)
    assert response.status_code == 200
    assert response.text == INDEX
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_assets_are_served_and_cached(spa: TestClient) -> None:
    response = spa.get("/assets/app-abc123.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"
    assert "immutable" in response.headers["cache-control"]
    assert spa.get("/favicon.svg").headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/..%2Fsecret.txt", "/assets/..%2F..%2Fsecret.txt"])
def test_traversal_never_leaves_static_dir(spa: TestClient, path: str) -> None:
    response = spa.get(path)
    assert "outside" not in response.text


def test_api_and_health_are_not_shadowed(spa: TestClient) -> None:
    assert spa.get("/health/live").json() == {"status": "ok"}
    missing = spa.get("/api/v1/does-not-exist", headers={"X-API-Key": API_KEY})
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/json")
    assert spa.get("/api/v1/reviews").status_code == 401
    assert spa.get("/openapi.json").status_code == 200


def test_missing_index_fails_fast(tmp_path: Path, session_factory: sessionmaker[Session]) -> None:
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        api_key=SecretStr(API_KEY),
        static_dir=str(tmp_path),
        _env_file=None,
    )
    with pytest.raises(ValueError, match=r"index\.html"):
        create_app(settings, session_factory)
