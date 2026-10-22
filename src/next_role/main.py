"""Application factory: wires configuration, logging, database, error handling, and routes."""

import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, sessionmaker

from next_role.api.deps import require_auth
from next_role.api.routes import (
    assessments,
    auth,
    candidates,
    documents,
    health,
    jobs,
    notifications,
    reviews,
)
from next_role.api.spa import mount_spa
from next_role.auth.service import LoginThrottle
from next_role.config import Settings, get_settings
from next_role.db import get_engine, get_sessionmaker
from next_role.errors import (
    ConflictError,
    DomainError,
    InvalidInputError,
    NotFoundError,
    RateLimitedError,
)
from next_role.integrations.llm.factory import build_llm_provider
from next_role.jobs.llm_extraction import LLMRequirementExtractor
from next_role.jobs.service import RequirementExtractor, rule_based_extractor
from next_role.logging_config import configure_logging, request_id_var

logger = logging.getLogger(__name__)

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    ConflictError: status.HTTP_409_CONFLICT,
    InvalidInputError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    RateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
}


def _build_extractor(settings: Settings) -> RequirementExtractor:
    provider = build_llm_provider(settings)
    if provider is None:
        return rule_based_extractor
    logger.info(
        "llm requirement extraction enabled",
        extra={"provider": provider.name, "external": provider.external},
    )
    return LLMRequirementExtractor(provider)


def create_app(
    settings: Settings | None = None, session_factory: sessionmaker[Session] | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    app = FastAPI(
        title="Next-Role",
        version="0.1.0",
        description="Candidate knowledge base, manual job ingestion, evidence-backed "
        "suitability assessments, and candidate review decisions.",
    )
    app.state.settings = settings
    app.state.session_factory = session_factory or get_sessionmaker(
        get_engine(settings.database_url)
    )
    app.state.requirement_extractor = _build_extractor(settings)
    app.state.login_throttle = LoginThrottle()

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        # Leaving the app (e.g. opening a job's source link) must not leak review URLs.
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        logger.info(
            "request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            },
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        code = next(
            (c for t, c in _STATUS_BY_ERROR.items() if isinstance(exc, t)),
            status.HTTP_400_BAD_REQUEST,
        )
        return JSONResponse(status_code=code, content={"error": exc.code, "detail": exc.message})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not echo submitted input back: it may contain candidate data.
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"error": "validation_error", "detail": errors},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "detail": "An unexpected error occurred"},
        )

    app.include_router(health.router)
    app.include_router(auth.router, prefix="/api/v1")
    protected = [Depends(require_auth)]
    app.include_router(candidates.router, prefix="/api/v1", dependencies=protected)
    app.include_router(jobs.router, prefix="/api/v1", dependencies=protected)
    app.include_router(assessments.router, prefix="/api/v1", dependencies=protected)
    app.include_router(reviews.router, prefix="/api/v1", dependencies=protected)
    app.include_router(notifications.router, prefix="/api/v1", dependencies=protected)
    app.include_router(documents.router, prefix="/api/v1", dependencies=protected)
    if settings.static_dir:
        # Registered last: the catch-all must not shadow API, health, or docs routes.
        mount_spa(app, Path(settings.static_dir))
    return app
