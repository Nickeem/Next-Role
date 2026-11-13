"""The immutable input a generation run drafts from, and per-fact content hashes.

A snapshot pins the job and the assessment the approval was based on, and copies the
candidate's active facts as they were when the run started, except facts kept off documents
(``include_in_documents`` false) and the facts under them (ADR 0009). Facts can be edited later; the
content hash recorded here (and on each verification) lets approval detect that.
"""

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from next_role.candidates.models import Candidate, CandidateFact, FactType, VerificationStatus
from next_role.candidates.schemas import CandidateContact
from next_role.candidates.service import fact_content_hash
from next_role.documents.schemas import sha256_json
from next_role.errors import NotFoundError
from next_role.jobs.models import JobOpportunity
from next_role.matching.engine import MatchStatus
from next_role.matching.models import SuitabilityAssessment

SNAPSHOT_SCHEMA = "generation-input/v1"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FactSnapshot(_Frozen):
    id: uuid.UUID
    fact_type: FactType
    title: str
    organization: str | None
    description: str | None
    start_date: date | None
    end_date: date | None
    is_current: bool
    skills: list[str]
    attributes: dict[str, Any]
    parent_fact_id: uuid.UUID | None
    verification_status: VerificationStatus
    evidence_kinds: list[str]
    content_hash: str

    @property
    def confirmed(self) -> bool:
        return self.verification_status is VerificationStatus.USER_VERIFIED


class RequirementSnapshot(_Frozen):
    id: uuid.UUID
    position: int
    text: str
    category: str
    importance: str
    terms: list[str]
    # From the assessment the approval was based on.
    status: MatchStatus | None
    evidence_fact_ids: list[uuid.UUID]
    # The assessment's evidence is only facts kept off documents: drafting neither covers the
    # requirement nor asks about it.
    excluded_only: bool = False


class JobSnapshot(_Frozen):
    id: uuid.UUID
    title: str | None
    company: str | None
    location: str | None
    description: str
    requirements: list[RequirementSnapshot]


class CandidateSnapshot(_Frozen):
    id: uuid.UUID
    full_name: str
    location: str | None
    # Display only. Never sent to an LLM (ADR 0006).
    contact: CandidateContact
    # Display only, rendered in document headers. Never sent to an LLM (ADR 0009).
    headline: str | None = None
    # Self-description that personal statements may cite. Sent only with LLM drafting and
    # the semantic check, under the candidate-data opt-in (ADR 0009).
    about_me: str | None = None
    about_me_hash: str | None = None


class InputSnapshot(_Frozen):
    schema_version: str = SNAPSHOT_SCHEMA
    assessment_id: uuid.UUID
    tier: str
    job: JobSnapshot
    candidate: CandidateSnapshot
    facts: list[FactSnapshot]

    def fact(self, fact_id: uuid.UUID) -> FactSnapshot | None:
        return next((f for f in self.facts if f.id == fact_id), None)


def fact_snapshot(fact: CandidateFact) -> FactSnapshot:
    return FactSnapshot(
        id=fact.id,
        fact_type=fact.fact_type,
        title=fact.title,
        organization=fact.organization,
        description=fact.description,
        start_date=fact.start_date,
        end_date=fact.end_date,
        is_current=fact.is_current,
        skills=list(fact.skills),
        attributes=dict(fact.attributes),
        parent_fact_id=fact.parent_fact_id,
        verification_status=fact.verification_status,
        evidence_kinds=[e.kind.value for e in fact.evidence_sources],
        content_hash=fact_content_hash(fact),
    )


def about_me_hash(about_me: str | None) -> str | None:
    """What approval compares to tell that the about-me changed since a claim cited it."""
    return sha256_json({"about_me": about_me}) if about_me else None


def active_facts(session: Session, candidate_id: uuid.UUID) -> list[CandidateFact]:
    return list(
        session.scalars(
            select(CandidateFact)
            .where(CandidateFact.candidate_id == candidate_id, CandidateFact.retired_at.is_(None))
            .options(selectinload(CandidateFact.evidence_sources))
            .order_by(CandidateFact.created_at, CandidateFact.id)
        )
    )


def document_facts(facts: list[CandidateFact]) -> list[CandidateFact]:
    """Facts documents may use: not excluded, and not under an excluded fact."""
    by_id = {f.id: f for f in facts}

    def included(fact: CandidateFact, seen: frozenset[uuid.UUID] = frozenset()) -> bool:
        if not fact.include_in_documents:
            return False
        parent = by_id.get(fact.parent_fact_id) if fact.parent_fact_id else None
        if parent is None or parent.id in seen:
            return True
        return included(parent, seen | {fact.id})

    return [f for f in facts if included(f)]


def build_snapshot(session: Session, assessment_id: uuid.UUID) -> InputSnapshot:
    assessment = session.scalar(
        select(SuitabilityAssessment)
        .where(SuitabilityAssessment.id == assessment_id)
        .options(selectinload(SuitabilityAssessment.requirement_results))
    )
    if assessment is None:
        raise NotFoundError(f"Assessment {assessment_id} not found")
    job = session.scalar(
        select(JobOpportunity)
        .where(JobOpportunity.id == assessment.opportunity_id)
        .options(selectinload(JobOpportunity.requirements))
    )
    candidate = session.get(Candidate, assessment.candidate_id)
    if job is None or candidate is None:  # pragma: no cover - foreign keys guarantee both
        raise NotFoundError("Job or candidate not found")

    active = active_facts(session, candidate.id)
    usable = document_facts(active)
    excluded = {f.id for f in active} - {f.id for f in usable}
    results = {r.requirement_id: r for r in assessment.requirement_results}
    requirements = []
    for req in job.requirements:
        result = results.get(req.id)
        cited = [] if result is None else [e.get("fact_id") for e in result.evidence]
        evidence = [uuid.UUID(f) for f in cited if f]
        requirements.append(
            RequirementSnapshot(
                id=req.id,
                position=req.position,
                text=req.text,
                category=req.category.value,
                importance=req.importance.value,
                terms=list(req.terms),
                status=result.status if result else None,
                evidence_fact_ids=evidence,
                excluded_only=bool(evidence) and set(evidence) <= excluded,
            )
        )
    return InputSnapshot(
        assessment_id=assessment.id,
        tier=assessment.tier.value,
        job=JobSnapshot(
            id=job.id,
            title=job.title,
            company=job.company,
            location=job.location,
            description=job.description,
            requirements=requirements,
        ),
        candidate=CandidateSnapshot(
            id=candidate.id,
            full_name=candidate.full_name,
            location=candidate.location,
            contact=CandidateContact.model_validate(candidate.contact or {}),
            headline=candidate.headline,
            about_me=candidate.about_me,
            about_me_hash=about_me_hash(candidate.about_me),
        ),
        facts=[fact_snapshot(f) for f in usable],
    )


def snapshot_hash(snapshot: InputSnapshot) -> str:
    return sha256_json(snapshot.model_dump(mode="json"))
