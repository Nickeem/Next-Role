"""Use cases for manual job ingestion, normalization, deduplication, and retrieval.

Deduplication, in order:

1. **Exact resubmission.** The normalized description hash matches a stored posting. Nothing
   is stored and the existing opportunity is returned.
2. **Same canonical URL.** The source URL, ignoring scheme, ``www.``, query, fragment, and
   trailing slash, matches an opportunity.
3. **Same identity.** Company, title, and location are all present and match an opportunity
   after normalization.

In cases 2 and 3 the new submission is kept as an extra ``JobPosting`` linked to the existing
opportunity. The opportunity and its requirements are left unchanged.
"""

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from next_role.errors import InvalidInputError, NotFoundError
from next_role.jobs.extraction import (
    ExtractedRequirement,
    classify_requirement,
    extract_requirements,
    infer_employment_type,
    infer_work_arrangement,
)
from next_role.jobs.models import (
    ExtractionMethod,
    JobOpportunity,
    JobPosting,
    JobRequirement,
    OpportunityStatus,
)
from next_role.jobs.schemas import IngestionOutcome, JobSubmission

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    requirements: list[ExtractedRequirement]
    method: ExtractionMethod
    notes: str | None = None
    warnings: list[str] = field(default_factory=list)


class RequirementExtractor(Protocol):
    """Turns an (untrusted) job description into requirement statements."""

    def __call__(self, description: str) -> ExtractionResult: ...


def rule_based_extractor(description: str) -> ExtractionResult:
    return ExtractionResult(extract_requirements(description), ExtractionMethod.RULES)


def _norm(text: str | None) -> str:
    return re.sub(r"[^\w]+", " ", (text or "").lower()).strip()


def content_hash(description: str) -> str:
    return hashlib.sha256(_norm(description).encode()).hexdigest()


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    return f"{host}{path}" if host else None


def identity_key(company: str | None, title: str | None, location: str | None) -> str | None:
    if not (company and title and location):
        return None
    joined = "|".join(_norm(v) for v in (company, title, location))
    return hashlib.sha256(joined.encode()).hexdigest()


@dataclass
class IngestionResultData:
    outcome: IngestionOutcome
    posting: JobPosting
    opportunity: JobOpportunity


def ingest_job(
    session: Session,
    submission: JobSubmission,
    extractor: RequirementExtractor = rule_based_extractor,
    max_description_chars: int = 50_000,
) -> IngestionResultData:
    if len(submission.description) > max_description_chars:
        raise InvalidInputError(f"description exceeds {max_description_chars} characters")

    raw = submission.model_dump(mode="json", exclude_none=True)
    digest = content_hash(submission.description)
    existing_posting = session.scalar(select(JobPosting).where(JobPosting.content_hash == digest))
    if existing_posting is not None:
        logger.info(
            "duplicate job submission",
            extra={"opportunity_id": str(existing_posting.opportunity_id)},
        )
        return IngestionResultData(
            IngestionOutcome.DUPLICATE_SUBMISSION,
            existing_posting,
            get_job(session, existing_posting.opportunity_id),
        )

    source_url = str(submission.source_url) if submission.source_url else None
    url_key = canonical_url(source_url)
    id_key = identity_key(submission.company, submission.title, submission.location)
    match = _find_existing(session, url_key, id_key)

    if match is not None:
        posting = _new_posting(session, match, submission, source_url, raw, digest)
        session.flush()
        logger.info("job linked to existing opportunity", extra={"opportunity_id": str(match.id)})
        return IngestionResultData(
            IngestionOutcome.LINKED_TO_EXISTING, posting, get_job(session, match.id)
        )

    if submission.requirements:
        result = ExtractionResult(
            [classify_requirement(r.text, r.importance) for r in submission.requirements],
            ExtractionMethod.MANUAL,
        )
    else:
        result = extractor(submission.description)

    opportunity = JobOpportunity(
        title=submission.title,
        company=submission.company,
        location=submission.location,
        work_arrangement=submission.work_arrangement
        or infer_work_arrangement(submission.location, submission.title, submission.description),
        employment_type=submission.employment_type
        or infer_employment_type(submission.title, submission.description),
        salary_min=submission.salary_min,
        salary_max=submission.salary_max,
        salary_currency=submission.salary_currency,
        description=submission.description,
        source=submission.source,
        source_url=source_url,
        canonical_url=url_key,
        identity_key=id_key,
        status=OpportunityStatus.ACTIVE,
        extraction_method=result.method,
        extraction_notes=result.notes,
    )
    session.add(opportunity)
    for position, req in enumerate(result.requirements):
        session.add(
            JobRequirement(
                opportunity=opportunity,
                position=position,
                text=req.text,
                category=req.category,
                importance=req.importance,
                terms=req.terms,
                details=req.details,
                extraction_method=result.method,
            )
        )
    posting = _new_posting(session, opportunity, submission, source_url, raw, digest)
    try:
        session.flush()
    except IntegrityError as exc:
        # A concurrent request stored the same URL or content first.
        session.rollback()
        raise InvalidInputError("This job was submitted concurrently; retry the request") from exc
    logger.info(
        "job ingested",
        extra={
            "opportunity_id": str(opportunity.id),
            "requirements": len(result.requirements),
            "extraction_method": result.method.value,
        },
    )
    return IngestionResultData(IngestionOutcome.CREATED, posting, get_job(session, opportunity.id))


def _find_existing(
    session: Session, url_key: str | None, id_key: str | None
) -> JobOpportunity | None:
    if url_key:
        found = session.scalar(
            select(JobOpportunity).where(JobOpportunity.canonical_url == url_key)
        )
        if found:
            return found
    if id_key:
        return session.scalar(
            select(JobOpportunity)
            .where(JobOpportunity.identity_key == id_key)
            .order_by(JobOpportunity.created_at)
            .limit(1)
        )
    return None


def _new_posting(
    session: Session,
    opportunity: JobOpportunity,
    submission: JobSubmission,
    source_url: str | None,
    raw: dict[str, object],
    digest: str,
) -> JobPosting:
    posting = JobPosting(
        opportunity=opportunity,
        source=submission.source,
        source_url=source_url,
        raw_payload=raw,
        content_hash=digest,
    )
    session.add(posting)
    return posting


def get_job(session: Session, job_id: uuid.UUID) -> JobOpportunity:
    job = session.scalar(
        select(JobOpportunity)
        .where(JobOpportunity.id == job_id)
        .options(selectinload(JobOpportunity.requirements), selectinload(JobOpportunity.postings))
        .execution_options(populate_existing=True)
    )
    if job is None:
        raise NotFoundError(f"Job {job_id} not found")
    return job


def list_jobs(
    session: Session, status: OpportunityStatus | None = None, limit: int = 100, offset: int = 0
) -> list[JobOpportunity]:
    query = select(JobOpportunity).order_by(JobOpportunity.created_at.desc())
    if status is not None:
        query = query.where(JobOpportunity.status == status)
    return list(session.scalars(query.limit(limit).offset(offset)))


def set_job_status(
    session: Session, job_id: uuid.UUID, status: OpportunityStatus
) -> JobOpportunity:
    job = get_job(session, job_id)
    job.status = status
    session.flush()
    return get_job(session, job_id)
