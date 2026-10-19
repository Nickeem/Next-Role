import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from next_role.api.deps import (
    SESSION_COOKIE,
    current_operator_session,
    get_app_settings,
    get_session,
    valid_api_key,
)
from next_role.auth import service
from next_role.config import Settings
from next_role.errors import ConflictError

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=1000)


class SessionOut(BaseModel):
    authenticated: bool
    # "session" for the browser, "api_key", or "disabled" when auth is off.
    method: str | None = None
    # Send as X-CSRF-Token on state-changing requests made with the session cookie.
    csrf_token: str | None = None
    expires_at: datetime | None = None


def _throttle(request: Request) -> service.LoginThrottle:
    throttle: service.LoginThrottle = request.app.state.login_throttle
    return throttle


@router.post("/login", responses={401: {}, 409: {}, 429: {}})
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> SessionOut:
    """Exchange the operator password for an HttpOnly session cookie."""
    if settings.ui_password is None:
        raise ConflictError("Browser login is not configured (NEXT_ROLE_UI_PASSWORD)")
    client = request.client.host if request.client else "unknown"
    throttle = _throttle(request)
    throttle.attempt(client)
    ttl = timedelta(hours=settings.session_ttl_hours)
    try:
        new = service.login(
            session,
            payload.password,
            settings.ui_password.get_secret_value(),
            ttl,
            datetime.now(UTC),
        )
    except service.InvalidCredentialsError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid password") from None
    throttle.succeeded(client)
    response.set_cookie(
        SESSION_COOKIE,
        new.token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="strict",
        path="/",
    )
    return SessionOut(
        authenticated=True,
        method="session",
        csrf_token=new.record.csrf_token,
        expires_at=new.record.expires_at,
    )


@router.get("/session", responses={401: {}})
def current_session(
    request: Request,
    x_api_key: str | None = Header(default=None),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> SessionOut:
    """Whether the caller is authenticated, and the CSRF token for a browser session."""
    if settings.auth_disabled:
        return SessionOut(authenticated=True, method="disabled")
    if x_api_key is not None:
        # Same rule as require_auth: a wrong key never falls back to the cookie.
        if valid_api_key(x_api_key, settings):
            return SessionOut(authenticated=True, method="api_key")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid API key")
    operator = current_operator_session(request, session)
    if operator is None:
        return SessionOut(authenticated=False)
    return SessionOut(
        authenticated=True,
        method="session",
        csrf_token=operator.csrf_token,
        expires_at=operator.expires_at,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    x_csrf_token: str | None = Header(default=None),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """End the browser session. Requires the session's CSRF token."""
    operator = current_operator_session(request, session)
    if operator is not None:
        if not (x_csrf_token and secrets.compare_digest(x_csrf_token, operator.csrf_token)):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Missing or invalid CSRF token")
        service.logout(session, operator)
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="strict",
    )
