import uuid

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from next_role.api.deps import get_app_settings, get_session
from next_role.config import Settings
from next_role.jobs import service
from next_role.jobs.models import OpportunityStatus
from next_role.jobs.schemas import (
    IngestionOutcome,
    IngestionResult,
    JobOut,
    JobSubmission,
    JobSummaryOut,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobStatusUpdate(BaseModel):
    status: OpportunityStatus


def get_extractor(request: Request) -> service.RequirementExtractor:
    extractor: service.RequirementExtractor = request.app.state.requirement_extractor
    return extractor


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Duplicate: the existing opportunity is returned"}},
)
def submit_job(
    submission: JobSubmission,
    response: Response,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
    extractor: service.RequirementExtractor = Depends(get_extractor),
) -> IngestionResult:
    """Manually submit a job description. Requirements are extracted and stored with it."""
    result = service.ingest_job(session, submission, extractor, settings.max_job_description_chars)
    if result.outcome is not IngestionOutcome.CREATED:
        response.status_code = status.HTTP_200_OK
    return IngestionResult(
        outcome=result.outcome,
        posting_id=result.posting.id,
        job=JobOut.model_validate(result.opportunity),
    )


@router.get("")
def list_jobs(
    status_filter: OpportunityStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> list[JobSummaryOut]:
    jobs = service.list_jobs(session, status_filter, limit, offset)
    return [JobSummaryOut.model_validate(j) for j in jobs]


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, session: Session = Depends(get_session)) -> JobOut:
    return JobOut.model_validate(service.get_job(session, job_id))


@router.patch("/{job_id}")
def update_job_status(
    job_id: uuid.UUID, payload: JobStatusUpdate, session: Session = Depends(get_session)
) -> JobOut:
    return JobOut.model_validate(service.set_job_status(session, job_id, payload.status))
