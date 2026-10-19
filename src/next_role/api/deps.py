"""FastAPI dependencies: database sessions and authentication.

A request is authenticated by a valid ``X-API-Key`` header (scripts) or a valid session
cookie (the browser, ADR 0005). Cookie-authenticated requests that change state must also
send the session's ``X-CSRF-Token``.
"""

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from next_role.auth import service as auth_service
from next_role.auth.models import OperatorSession
from next_role.config import Settings

SESSION_COOKIE = "next_role_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_session(request: Request) -> Iterator[Session]:
    factory: sessionmaker[Session] = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def valid_api_key(x_api_key: str | None, settings: Settings) -> bool:
    expected = settings.api_key.get_secret_value() if settings.api_key else ""
    return bool(x_api_key and expected and secrets.compare_digest(x_api_key, expected))


def current_operator_session(request: Request, session: Session) -> OperatorSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return auth_service.find_session(session, token, datetime.now(UTC))


def require_auth(
    request: Request,
    x_api_key: str | None = Header(default=None),
    x_csrf_token: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
    session: Session = Depends(get_session),
) -> None:
    if settings.auth_disabled:
        return
    if x_api_key is not None:
        # An API key that is present but wrong never falls back to the cookie.
        if valid_api_key(x_api_key, settings):
            return
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")
    operator = current_operator_session(request, session)
    if operator is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if request.method not in SAFE_METHODS and not (
        x_csrf_token and secrets.compare_digest(x_csrf_token, operator.csrf_token)
    ):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Missing or invalid CSRF token")
