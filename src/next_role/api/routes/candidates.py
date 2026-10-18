import uuid

from fastapi import APIRouter, Body, Depends, status
from sqlalchemy.orm import Session

from next_role.api.deps import get_session
from next_role.candidates import service
from next_role.candidates.schemas import (
    CandidateOut,
    CandidateProfileIn,
    CandidateSummaryOut,
    CandidateUpdate,
    EvidenceSourceIn,
    EvidenceSourceOut,
    FactIn,
    FactOut,
    FactUpdate,
)

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_candidate(
    payload: CandidateProfileIn, session: Session = Depends(get_session)
) -> CandidateOut:
    """Create a candidate from a complete profile: details, preferences, evidence, and facts."""
    return service.candidate_out(service.create_candidate_profile(session, payload))


@router.get("")
def list_candidates(session: Session = Depends(get_session)) -> list[CandidateSummaryOut]:
    return [CandidateSummaryOut.model_validate(c) for c in service.list_candidates(session)]


@router.get("/{candidate_id}")
def get_candidate(
    candidate_id: uuid.UUID, include_retired: bool = False, session: Session = Depends(get_session)
) -> CandidateOut:
    return service.candidate_out(service.get_candidate(session, candidate_id), include_retired)


@router.patch("/{candidate_id}")
def update_candidate(
    candidate_id: uuid.UUID, payload: CandidateUpdate, session: Session = Depends(get_session)
) -> CandidateOut:
    return service.candidate_out(service.update_candidate(session, candidate_id, payload))


@router.post("/{candidate_id}/evidence-sources", status_code=status.HTTP_201_CREATED)
def add_evidence_source(
    candidate_id: uuid.UUID, payload: EvidenceSourceIn, session: Session = Depends(get_session)
) -> EvidenceSourceOut:
    evidence = service.add_evidence_source(session, candidate_id, payload)
    return EvidenceSourceOut.model_validate(evidence)


@router.get("/{candidate_id}/evidence-sources")
def list_evidence_sources(
    candidate_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[EvidenceSourceOut]:
    candidate = service.get_candidate(session, candidate_id)
    return [EvidenceSourceOut.model_validate(e) for e in candidate.evidence_sources]


@router.post("/{candidate_id}/facts", status_code=status.HTTP_201_CREATED)
def add_fact(
    candidate_id: uuid.UUID,
    payload: FactIn = Body(...),
    session: Session = Depends(get_session),
) -> FactOut:
    return service.fact_out(service.add_fact(session, candidate_id, payload))


@router.get("/{candidate_id}/facts")
def list_facts(
    candidate_id: uuid.UUID, include_retired: bool = False, session: Session = Depends(get_session)
) -> list[FactOut]:
    return [service.fact_out(f) for f in service.list_facts(session, candidate_id, include_retired)]


@router.get("/{candidate_id}/facts/{fact_id}")
def get_fact(
    candidate_id: uuid.UUID, fact_id: uuid.UUID, session: Session = Depends(get_session)
) -> FactOut:
    return service.fact_out(service.get_fact(session, candidate_id, fact_id))


@router.patch("/{candidate_id}/facts/{fact_id}")
def update_fact(
    candidate_id: uuid.UUID,
    fact_id: uuid.UUID,
    payload: FactUpdate,
    session: Session = Depends(get_session),
) -> FactOut:
    return service.fact_out(service.update_fact(session, candidate_id, fact_id, payload))


@router.post("/{candidate_id}/facts/{fact_id}/verify")
def verify_fact(
    candidate_id: uuid.UUID, fact_id: uuid.UUID, session: Session = Depends(get_session)
) -> FactOut:
    """Record that the candidate has confirmed this fact as accurate."""
    return service.fact_out(service.verify_fact(session, candidate_id, fact_id))


@router.delete("/{candidate_id}/facts/{fact_id}")
def retire_fact(
    candidate_id: uuid.UUID, fact_id: uuid.UUID, session: Session = Depends(get_session)
) -> FactOut:
    """Retire (soft-delete) a fact. It is kept for audit but excluded from matching."""
    return service.fact_out(service.retire_fact(session, candidate_id, fact_id))
