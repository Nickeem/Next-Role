"""Use cases for running, persisting, and retrieving suitability assessments."""

import hashlib
import json
import logging
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from next_role.candidates.models import Candidate, CandidateFact
from next_role.candidates.schemas import CandidatePreferences
from next_role.candidates.service import fact_support, get_candidate
from next_role.errors import NotFoundError
from next_role.jobs.models import JobOpportunity
from next_role.jobs.service import get_job
from next_role.matching import engine
from next_role.matching.engine import MatchStatus
from next_role.matching.models import (
    AssessmentStatus,
    RequirementAssessment,
    SuitabilityAssessment,
)
from next_role.matching.schemas import (
    AssessmentOut,
    AssessmentSummary,
    AssessmentSummaryOut,
    EvidenceOut,
    PreferenceCheckOut,
    RequirementAssessmentOut,
)
from next_role.review import service as review_service

logger = logging.getLogger(__name__)


def _fact_view(fact: CandidateFact) -> engine.FactView:
    text_parts = [fact.description or "", *fact.attributes.get("responsibilities", [])]
    return engine.FactView(
        id=fact.id,
        fact_type=fact.fact_type.value,
        title=fact.title,
        organization=fact.organization,
        text=" ".join(p for p in text_parts if p),
        skills=tuple(fact.skills),
        start_date=fact.start_date,
        end_date=fact.end_date,
        is_current=fact.is_current,
        support=fact_support(fact),
        attributes=dict(fact.attributes),
    )


def candidate_view(candidate: Candidate) -> engine.CandidateView:
    return engine.CandidateView(
        preferences=CandidatePreferences.model_validate(candidate.preferences),
        facts=tuple(_fact_view(f) for f in candidate.facts if f.retired_at is None),
    )


def job_view(job: JobOpportunity) -> engine.JobView:
    return engine.JobView(
        location=job.location,
        work_arrangement=job.work_arrangement.value,
        employment_type=job.employment_type.value,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        requirements=tuple(
            engine.RequirementView(
                id=r.id,
                text=r.text,
                category=r.category,
                importance=r.importance,
                terms=tuple(r.terms),
                details=dict(r.details),
            )
            for r in job.requirements
        ),
    )


def input_fingerprint(candidate: Candidate, job: JobOpportunity, as_of: date) -> str:
    """Hash of everything that can change the result, so identical runs are idempotent."""
    payload = {
        "method_version": engine.METHOD_VERSION,
        "as_of": as_of.isoformat(),
        "preferences": candidate.preferences,
        "facts": sorted(
            (
                str(f.id),
                f.updated_at.isoformat(),
                f.verification_status.value,
                sorted(str(e.id) for e in f.evidence_sources),
            )
            for f in candidate.facts
            if f.retired_at is None
        ),
        "job": [
            job.work_arrangement.value,
            job.employment_type.value,
            job.location,
            job.salary_min,
            job.salary_max,
            job.salary_currency,
        ],
        "requirements": [
            (str(r.id), r.text, r.category.value, r.importance.value, r.terms, r.details)
            for r in job.requirements
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def run_assessment(
    session: Session, job_id: uuid.UUID, candidate_id: uuid.UUID, as_of: date | None = None
) -> tuple[SuitabilityAssessment, bool]:
    """Assess a candidate against a job. Returns (assessment, created).

    If an assessment with identical inputs exists, it is returned unchanged (created=False).
    A new assessment opens or updates the candidate's review of the job.
    """
    as_of = as_of or datetime.now(UTC).date()
    job = get_job(session, job_id)
    candidate = get_candidate(session, candidate_id)
    fingerprint = input_fingerprint(candidate, job, as_of)

    existing = _find_assessment(session, candidate_id, job_id, fingerprint)
    if existing is not None:
        return get_assessment(session, existing.id), False

    result = engine.assess(candidate_view(candidate), job_view(job), as_of)
    assessment = SuitabilityAssessment(
        candidate_id=candidate_id,
        opportunity_id=job_id,
        status=AssessmentStatus.COMPLETED,
        method=result.method,
        method_version=result.method_version,
        as_of=as_of,
        score=result.score,
        score_excluding_unknowns=result.score_excluding_unknowns,
        coverage=result.coverage,
        tier=result.tier,
        has_blocking_gaps=result.has_blocking_gaps,
        status_counts=result.status_counts,
        preference_checks=[c.as_dict() for c in result.preference_checks],
        input_fingerprint=fingerprint,
    )
    try:
        with session.begin_nested():
            session.add(assessment)
            for position, r in enumerate(result.results):
                session.add(
                    RequirementAssessment(
                        assessment=assessment,
                        requirement_id=r.requirement_id,
                        position=position,
                        status=r.status,
                        weight=r.weight,
                        credit=r.credit,
                        explanation=r.explanation,
                        evidence=[e.as_dict() for e in r.evidence],
                    )
                )
    except IntegrityError:
        # A concurrent request stored the same assessment first; return that one.
        existing = _find_assessment(session, candidate_id, job_id, fingerprint)
        if existing is None:
            raise
        return get_assessment(session, existing.id), False
    # Same transaction: the review (and its notification) exists if and only if this does.
    review_service.record_assessment(session, assessment)
    logger.info(
        "assessment completed",
        extra={
            "assessment_id": str(assessment.id),
            "opportunity_id": str(job_id),
            "score": result.score,
            "tier": result.tier.value,
        },
    )
    return get_assessment(session, assessment.id), True


def _find_assessment(
    session: Session, candidate_id: uuid.UUID, job_id: uuid.UUID, fingerprint: str
) -> SuitabilityAssessment | None:
    return session.scalar(
        select(SuitabilityAssessment).where(
            SuitabilityAssessment.candidate_id == candidate_id,
            SuitabilityAssessment.opportunity_id == job_id,
            SuitabilityAssessment.input_fingerprint == fingerprint,
        )
    )


def get_assessment(session: Session, assessment_id: uuid.UUID) -> SuitabilityAssessment:
    assessment = session.scalar(
        select(SuitabilityAssessment)
        .where(SuitabilityAssessment.id == assessment_id)
        .options(
            selectinload(SuitabilityAssessment.requirement_results).selectinload(
                RequirementAssessment.requirement
            )
        )
        .execution_options(populate_existing=True)
    )
    if assessment is None:
        raise NotFoundError(f"Assessment {assessment_id} not found")
    return assessment


def list_assessments(
    session: Session,
    job_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[SuitabilityAssessment]:
    query = select(SuitabilityAssessment).order_by(SuitabilityAssessment.created_at.desc())
    if job_id:
        query = query.where(SuitabilityAssessment.opportunity_id == job_id)
    if candidate_id:
        query = query.where(SuitabilityAssessment.candidate_id == candidate_id)
    return list(session.scalars(query.limit(limit)))


def assessment_out(assessment: SuitabilityAssessment) -> AssessmentOut:
    requirements = [
        RequirementAssessmentOut(
            requirement_id=r.requirement_id,
            text=r.requirement.text,
            category=r.requirement.category,
            importance=r.requirement.importance,
            status=r.status,
            weight=r.weight,
            credit=r.credit,
            explanation=r.explanation,
            evidence=[EvidenceOut.model_validate(e) for e in r.evidence],
        )
        for r in assessment.requirement_results
    ]

    def texts(*statuses: MatchStatus) -> list[str]:
        return [r.text for r in requirements if r.status in statuses]

    return AssessmentOut(
        id=assessment.id,
        candidate_id=assessment.candidate_id,
        job_id=assessment.opportunity_id,
        status=assessment.status,
        method=assessment.method,
        method_version=assessment.method_version,
        as_of=assessment.as_of,
        score=assessment.score,
        score_excluding_unknowns=assessment.score_excluding_unknowns,
        coverage=assessment.coverage,
        tier=assessment.tier,
        has_blocking_gaps=assessment.has_blocking_gaps,
        status_counts=assessment.status_counts,
        preference_checks=[
            PreferenceCheckOut.model_validate(c) for c in assessment.preference_checks
        ],
        summary=AssessmentSummary(
            matched=texts(MatchStatus.MET),
            partially_matched=texts(MatchStatus.PARTIALLY_MET),
            gaps=texts(MatchStatus.NOT_MET),
            unverified=texts(MatchStatus.UNVERIFIED),
            unknowns=texts(MatchStatus.NO_EVIDENCE, MatchStatus.UNKNOWN),
        ),
        requirements=requirements,
        created_at=assessment.created_at,
    )


def assessment_summary_out(assessment: SuitabilityAssessment) -> AssessmentSummaryOut:
    return AssessmentSummaryOut(
        id=assessment.id,
        candidate_id=assessment.candidate_id,
        job_id=assessment.opportunity_id,
        score=assessment.score,
        tier=assessment.tier,
        has_blocking_gaps=assessment.has_blocking_gaps,
        method_version=assessment.method_version,
        as_of=assessment.as_of,
        created_at=assessment.created_at,
    )
