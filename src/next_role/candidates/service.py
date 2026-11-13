"""Use cases for maintaining the candidate knowledge base."""

import uuid
from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from next_role.candidates.models import (
    Candidate,
    CandidateFact,
    EvidenceSource,
    FactOrigin,
    FactType,
    VerificationStatus,
)
from next_role.candidates.schemas import (
    ATTRIBUTE_MODELS,
    CandidateContact,
    CandidateOut,
    CandidatePreferences,
    CandidateProfileIn,
    CandidateUpdate,
    EvidenceSourceIn,
    EvidenceSourceOut,
    FactIn,
    FactOut,
    FactSupport,
    FactUpdate,
    check_dates,
)
from next_role.errors import InvalidInputError, NotFoundError
from next_role.skills import normalize_skills


def fact_support(fact: CandidateFact) -> FactSupport:
    if fact.verification_status is VerificationStatus.USER_VERIFIED:
        return FactSupport.VERIFIED
    if fact.evidence_sources:
        return FactSupport.EVIDENCED
    return FactSupport.UNSUPPORTED


def fact_out(fact: CandidateFact) -> FactOut:
    return FactOut.model_validate(
        {
            **{k: getattr(fact, k) for k in FactOut.model_fields if hasattr(fact, k)},
            "support": fact_support(fact),
            "evidence_source_ids": [e.id for e in fact.evidence_sources],
        }
    )


def candidate_out(candidate: Candidate, include_retired: bool = False) -> CandidateOut:
    return CandidateOut(
        id=candidate.id,
        full_name=candidate.full_name,
        headline=candidate.headline,
        about_me=candidate.about_me,
        location=candidate.location,
        contact=CandidateContact.model_validate(candidate.contact or {}),
        preferences=CandidatePreferences.model_validate(candidate.preferences),
        created_at=candidate.created_at,
        updated_at=candidate.updated_at,
        evidence_sources=[EvidenceSourceOut.model_validate(e) for e in candidate.evidence_sources],
        facts=[fact_out(f) for f in candidate.facts if include_retired or f.retired_at is None],
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _new_evidence(session: Session, candidate: Candidate, data: EvidenceSourceIn) -> EvidenceSource:
    evidence = EvidenceSource(
        candidate=candidate,
        kind=data.kind,
        title=data.title,
        reference=data.reference,
        notes=data.notes,
    )
    session.add(evidence)
    return evidence


def _new_fact(
    session: Session, candidate: Candidate, data: FactIn, origin: FactOrigin
) -> CandidateFact:
    record = data.to_record()
    verified = data.verification_status is VerificationStatus.USER_VERIFIED
    fact = CandidateFact(
        candidate=candidate,
        **record.model_dump(),
        verification_status=data.verification_status,
        verified_at=_now() if verified else None,
        origin=origin,
        include_in_documents=data.include_in_documents,
    )
    session.add(fact)
    return fact


def create_candidate_profile(session: Session, payload: CandidateProfileIn) -> Candidate:
    candidate = Candidate(
        full_name=payload.full_name,
        headline=payload.headline,
        about_me=payload.about_me,
        location=payload.location,
        contact=payload.contact.model_dump(mode="json", exclude_none=True),
        preferences=payload.preferences.model_dump(mode="json"),
    )
    session.add(candidate)
    evidence_by_key: dict[str, EvidenceSource] = {}
    for item in payload.evidence_sources:
        evidence = _new_evidence(session, candidate, item)
        if item.key:
            evidence_by_key[item.key] = evidence

    facts_by_key: dict[str, CandidateFact] = {}
    pending_parents: list[tuple[CandidateFact, str]] = []
    for fact_in in payload.facts:
        fact = _new_fact(session, candidate, fact_in, FactOrigin.IMPORT)
        fact.evidence_sources = [evidence_by_key[k] for k in fact_in.evidence_keys]
        if fact_in.key:
            facts_by_key[fact_in.key] = fact
        if fact_in.parent_key:
            pending_parents.append((fact, fact_in.parent_key))

    session.flush()
    for fact, parent_key in pending_parents:
        fact.parent_fact_id = facts_by_key[parent_key].id
    session.flush()
    return get_candidate(session, candidate.id)


def list_candidates(session: Session) -> list[Candidate]:
    return list(session.scalars(select(Candidate).order_by(Candidate.created_at)))


def get_candidate(session: Session, candidate_id: uuid.UUID) -> Candidate:
    candidate = session.scalar(
        select(Candidate)
        .where(Candidate.id == candidate_id)
        .options(
            selectinload(Candidate.evidence_sources),
            selectinload(Candidate.facts).selectinload(CandidateFact.evidence_sources),
        )
        .execution_options(populate_existing=True)
    )
    if candidate is None:
        raise NotFoundError(f"Candidate {candidate_id} not found")
    return candidate


def update_candidate(session: Session, candidate_id: uuid.UUID, data: CandidateUpdate) -> Candidate:
    candidate = get_candidate(session, candidate_id)
    changes = data.model_dump(exclude_unset=True, exclude={"preferences", "contact"})
    for field, value in changes.items():
        if value is None and field == "full_name":
            raise InvalidInputError("full_name cannot be null")
        setattr(candidate, field, value)
    if data.contact is not None:
        candidate.contact = data.contact.model_dump(mode="json", exclude_none=True)
    if data.preferences is not None:
        candidate.preferences = data.preferences.model_dump(mode="json")
    session.flush()
    return get_candidate(session, candidate_id)


def add_evidence_source(
    session: Session, candidate_id: uuid.UUID, data: EvidenceSourceIn
) -> EvidenceSource:
    candidate = get_candidate(session, candidate_id)
    evidence = _new_evidence(session, candidate, data)
    session.flush()
    return evidence


def _owned_evidence(
    session: Session, candidate_id: uuid.UUID, ids: list[uuid.UUID]
) -> list[EvidenceSource]:
    if not ids:
        return []
    found = list(
        session.scalars(
            select(EvidenceSource).where(
                EvidenceSource.id.in_(ids), EvidenceSource.candidate_id == candidate_id
            )
        )
    )
    missing = set(ids) - {e.id for e in found}
    if missing:
        raise InvalidInputError(f"Unknown evidence source ids: {sorted(map(str, missing))}")
    return found


def get_fact(session: Session, candidate_id: uuid.UUID, fact_id: uuid.UUID) -> CandidateFact:
    fact = session.scalar(
        select(CandidateFact)
        .where(CandidateFact.id == fact_id, CandidateFact.candidate_id == candidate_id)
        .options(selectinload(CandidateFact.evidence_sources))
    )
    if fact is None:
        raise NotFoundError(f"Fact {fact_id} not found for candidate {candidate_id}")
    return fact


def _single_fact_input(data: FactIn) -> None:
    if data.key or data.evidence_keys or data.parent_key:
        raise InvalidInputError("Use evidence_source_ids/parent_fact_id for a single fact")


def add_fact(
    session: Session,
    candidate_id: uuid.UUID,
    data: FactIn,
    origin: FactOrigin = FactOrigin.API,
) -> CandidateFact:
    """Add one fact. ``origin`` is set by the caller (the web workspace passes ``manual``)."""
    _single_fact_input(data)
    candidate = get_candidate(session, candidate_id)
    fact = _new_fact(session, candidate, data, origin)
    fact.evidence_sources = _owned_evidence(session, candidate_id, data.evidence_source_ids)
    if data.parent_fact_id:
        fact.parent_fact_id = get_fact(session, candidate_id, data.parent_fact_id).id
    session.flush()
    return fact


def list_facts(
    session: Session, candidate_id: uuid.UUID, include_retired: bool = False
) -> list[CandidateFact]:
    candidate = get_candidate(session, candidate_id)
    return [f for f in candidate.facts if include_retired or f.retired_at is None]


_CONTENT_FIELDS = (
    "title",
    "organization",
    "description",
    "start_date",
    "end_date",
    "is_current",
    "skills",
    "attributes",
)


def update_fact(
    session: Session, candidate_id: uuid.UUID, fact_id: uuid.UUID, data: FactUpdate
) -> CandidateFact:
    fact = get_fact(session, candidate_id, fact_id)
    if fact.retired_at is not None:
        raise InvalidInputError("Retired facts cannot be modified")
    changes = data.model_dump(exclude_unset=True)
    if changes.get("title", "") is None:
        raise InvalidInputError("title cannot be null")

    if "attributes" in changes and changes["attributes"] is not None:
        try:
            model = ATTRIBUTE_MODELS[fact.fact_type].model_validate(changes["attributes"])
        except ValidationError as exc:
            raise InvalidInputError(f"Invalid attributes for {fact.fact_type}: {exc}") from exc
        changes["attributes"] = model.model_dump(mode="json", exclude_none=True)
    if changes.get("skills") is not None:
        changes["skills"] = normalize_skills(changes["skills"])

    content_changed = False
    for field in _CONTENT_FIELDS:
        if field in changes and changes[field] != getattr(fact, field):
            setattr(fact, field, changes[field])
            content_changed = True
    # Validate the updated record as a whole, with the same rules as on creation.
    try:
        check_dates(fact.start_date, fact.end_date, fact.is_current)
    except ValueError as exc:
        raise InvalidInputError(str(exc)) from exc
    if fact.fact_type is FactType.EXPERIENCE and not fact.is_current and fact.end_date is None:
        raise InvalidInputError("end_date is required unless is_current is true")

    if data.evidence_source_ids is not None:
        fact.evidence_sources = _owned_evidence(session, candidate_id, data.evidence_source_ids)
    if data.include_in_documents is not None:
        fact.include_in_documents = data.include_in_documents

    if data.verification_status is not None:
        _set_verification(fact, data.verification_status)
    elif content_changed and fact.verification_status is VerificationStatus.USER_VERIFIED:
        # A confirmation applies to the content that was confirmed, not to edits made later.
        _set_verification(fact, VerificationStatus.SELF_REPORTED)
    session.flush()
    return fact


def replace_fact(
    session: Session, candidate_id: uuid.UUID, fact_id: uuid.UUID, data: FactIn
) -> CandidateFact:
    """Replace a fact's content with a typed body, validated as on creation.

    The type cannot change. Evidence links, the parent and ``include_in_documents`` are
    replaced too. Changed content resets a confirmed fact to self-reported unless
    ``verification_status`` is sent in the same request (the rule ``update_fact`` uses).
    """
    _single_fact_input(data)
    fact = get_fact(session, candidate_id, fact_id)
    if fact.retired_at is not None:
        raise InvalidInputError("Retired facts cannot be modified")
    if data.fact_type != fact.fact_type:
        raise InvalidInputError(
            f"This fact is a {fact.fact_type.value}; its type cannot be changed"
        )
    parent_id = None
    if data.parent_fact_id is not None:
        if data.parent_fact_id == fact.id:
            raise InvalidInputError("A fact cannot be its own parent")
        parent_id = get_fact(session, candidate_id, data.parent_fact_id).id

    content_changed = parent_id != fact.parent_fact_id
    fact.parent_fact_id = parent_id
    for field, value in data.to_record().model_dump(exclude={"fact_type"}).items():
        if value != getattr(fact, field):
            setattr(fact, field, value)
            content_changed = True
    fact.evidence_sources = _owned_evidence(session, candidate_id, data.evidence_source_ids)
    fact.include_in_documents = data.include_in_documents

    if "verification_status" in data.model_fields_set:
        _set_verification(fact, data.verification_status)
    elif content_changed and fact.verification_status is VerificationStatus.USER_VERIFIED:
        _set_verification(fact, VerificationStatus.SELF_REPORTED)
    session.flush()
    return fact


def _set_verification(fact: CandidateFact, status: VerificationStatus) -> None:
    fact.verification_status = status
    fact.verified_at = _now() if status is VerificationStatus.USER_VERIFIED else None


def verify_fact(session: Session, candidate_id: uuid.UUID, fact_id: uuid.UUID) -> CandidateFact:
    fact = get_fact(session, candidate_id, fact_id)
    if fact.retired_at is not None:
        raise InvalidInputError("Retired facts cannot be verified")
    _set_verification(fact, VerificationStatus.USER_VERIFIED)
    session.flush()
    return fact


def retire_fact(session: Session, candidate_id: uuid.UUID, fact_id: uuid.UUID) -> CandidateFact:
    """Soft-delete: retired facts stay for audit but are excluded from matching."""
    fact = get_fact(session, candidate_id, fact_id)
    if fact.retired_at is None:
        fact.retired_at = _now()
        session.flush()
    return fact
