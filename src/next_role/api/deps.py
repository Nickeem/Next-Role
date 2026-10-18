"""FastAPI dependencies: database sessions and API-key authentication."""

import secrets
from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from next_role.config import Settings


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


def require_api_key(
    x_api_key: str | None = Header(default=None),
    settings: Settings = Depends(get_app_settings),
) -> None:
    if settings.auth_disabled:
        return
    expected = settings.api_key.get_secret_value() if settings.api_key else ""
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key"
        )
