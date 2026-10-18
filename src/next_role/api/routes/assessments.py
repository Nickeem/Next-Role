import uuid

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from next_role.api.deps import get_session
from next_role.matching import service
from next_role.matching.schemas import (
    AssessmentOut,
    AssessmentRequest,
    AssessmentResultOut,
    AssessmentSummaryOut,
)

router = APIRouter(tags=["assessments"])


@router.post(
    "/jobs/{job_id}/assessments",
    status_code=status.HTTP_201_CREATED,
    responses={200: {"description": "Identical inputs: the existing assessment is returned"}},
)
def assess_job(
    job_id: uuid.UUID,
    payload: AssessmentRequest,
    response: Response,
    session: Session = Depends(get_session),
) -> AssessmentResultOut:
    """Assess a candidate's suitability for a job from their supported evidence."""
    assessment, created = service.run_assessment(
        session, job_id, payload.candidate_id, payload.as_of
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return AssessmentResultOut(created=created, assessment=service.assessment_out(assessment))


@router.get("/jobs/{job_id}/assessments")
def list_job_assessments(
    job_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[AssessmentSummaryOut]:
    return [
        service.assessment_summary_out(a) for a in service.list_assessments(session, job_id=job_id)
    ]


@router.get("/assessments")
def list_assessments(
    job_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[AssessmentSummaryOut]:
    found = service.list_assessments(session, job_id, candidate_id, limit)
    return [service.assessment_summary_out(a) for a in found]


@router.get("/assessments/{assessment_id}")
def get_assessment(
    assessment_id: uuid.UUID, session: Session = Depends(get_session)
) -> AssessmentOut:
    return service.assessment_out(service.get_assessment(session, assessment_id))
