"""Browser sessions: login, cookie flags, CSRF, logout, expiry, throttling, and API keys."""

import hashlib
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from next_role.auth.models import OperatorSession
from next_role.config import Settings
from next_role.main import create_app
from tests.conftest import API_KEY, TEST_DATABASE_URL

PASSWORD = "correct horse battery staple"


@pytest.fixture
def browser(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    """A client with no API key, like the web workspace."""
    settings = Settings(
        database_url=TEST_DATABASE_URL,
        api_key=SecretStr(API_KEY),
        ui_password=SecretStr(PASSWORD),
        log_json=False,
        _env_file=None,
    )
    with TestClient(create_app(settings, session_factory), base_url="https://testserver") as c:
        yield c


def _login(client: TestClient, password: str = PASSWORD) -> str:
    response = client.post("/api/v1/auth/login", json={"password": password})
    assert response.status_code == 200, response.text
    csrf: str = response.json()["csrf_token"]
    return csrf


def test_login_sets_hardened_cookie(browser: TestClient, session: Session) -> None:
    response = browser.post("/api/v1/auth/login", json={"password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is True
    assert body["method"] == "session"
    assert body["csrf_token"]

    cookie = response.headers["set-cookie"]
    assert cookie.startswith("next_role_session=")
    for flag in ("HttpOnly", "Secure", "SameSite=strict", "Path=/", "Max-Age=43200"):
        assert flag.lower() in cookie.lower()

    # Only a hash of the cookie value is stored.
    token = browser.cookies["next_role_session"]
    [stored] = session.scalars(select(OperatorSession)).all()
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in stored.token_hash


def test_wrong_password_and_throttle(browser: TestClient) -> None:
    for _ in range(5):
        response = browser.post("/api/v1/auth/login", json={"password": "wrong password!!"})
        assert response.status_code == 401
        assert "set-cookie" not in response.headers
    assert browser.post("/api/v1/auth/login", json={"password": PASSWORD}).status_code == 429


def test_session_reads_and_csrf_on_writes(browser: TestClient) -> None:
    assert browser.get("/api/v1/reviews").status_code == 401
    csrf = _login(browser)

    assert browser.get("/api/v1/reviews").status_code == 200
    patch = {"review_ready": False}
    assert browser.patch("/api/v1/notification-preferences", json=patch).status_code == 403
    assert (
        browser.patch(
            "/api/v1/notification-preferences", json=patch, headers={"X-CSRF-Token": "nope"}
        ).status_code
        == 403
    )
    response = browser.patch(
        "/api/v1/notification-preferences", json=patch, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 200


def test_session_endpoint_returns_csrf_after_reload(browser: TestClient) -> None:
    assert browser.get("/api/v1/auth/session").json() == {
        "authenticated": False,
        "method": None,
        "csrf_token": None,
        "expires_at": None,
    }
    csrf = _login(browser)
    body = browser.get("/api/v1/auth/session").json()
    assert body["authenticated"] is True
    assert body["csrf_token"] == csrf
    assert body["expires_at"]


def test_session_endpoint_rejects_a_wrong_api_key_even_with_a_cookie(browser: TestClient) -> None:
    _login(browser)
    response = browser.get("/api/v1/auth/session", headers={"X-API-Key": "wrong"})
    assert response.status_code == 401
    assert browser.get("/api/v1/reviews", headers={"X-API-Key": "wrong"}).status_code == 401
    body = browser.get("/api/v1/auth/session", headers={"X-API-Key": API_KEY}).json()
    assert body["method"] == "api_key"


def test_logout_requires_csrf_and_revokes(browser: TestClient, session: Session) -> None:
    csrf = _login(browser)
    token = browser.cookies["next_role_session"]
    assert browser.post("/api/v1/auth/logout").status_code == 403
    response = browser.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 204
    assert 'next_role_session=""' in response.headers["set-cookie"]
    assert session.scalars(select(OperatorSession)).all() == []

    # The old cookie no longer works even if replayed.
    browser.cookies.set("next_role_session", token, domain="testserver")
    assert browser.get("/api/v1/reviews").status_code == 401


def test_expired_session_is_rejected(browser: TestClient, session: Session) -> None:
    _login(browser)
    session.execute(text("UPDATE operator_sessions SET expires_at = now() - interval '1 second'"))
    session.commit()
    assert browser.get("/api/v1/reviews").status_code == 401
    assert browser.get("/api/v1/auth/session").json()["authenticated"] is False


def test_api_key_still_works_without_csrf(browser: TestClient) -> None:
    headers = {"X-API-Key": API_KEY}
    response = browser.patch(
        "/api/v1/notification-preferences", json={"review_ready": False}, headers=headers
    )
    assert response.status_code == 200
    assert browser.get("/api/v1/auth/session", headers=headers).json()["method"] == "api_key"


def test_wrong_api_key_does_not_fall_back_to_cookie(browser: TestClient) -> None:
    _login(browser)
    assert browser.get("/api/v1/reviews", headers={"X-API-Key": "wrong"}).status_code == 401


def test_login_unavailable_without_password(client: TestClient) -> None:
    response = client.post("/api/v1/auth/login", json={"password": "anything at all"})
    assert response.status_code == 409


def test_short_ui_password_rejected() -> None:
    with pytest.raises(ValueError, match="at least 12"):
        Settings(api_key=SecretStr("k"), ui_password=SecretStr("short"), _env_file=None)
