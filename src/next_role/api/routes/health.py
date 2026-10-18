import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from next_role.api.deps import get_session

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health/live")
def liveness() -> dict[str, str]:
    """The process is up and serving requests."""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness(response: Response, session: Session = Depends(get_session)) -> dict[str, str]:
    """The application can reach its database and the schema is migrated."""
    try:
        revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError:
        logger.warning("readiness check failed", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "unreachable_or_unmigrated"}
    return {"status": "ok", "database": "ok", "schema_revision": str(revision)}
