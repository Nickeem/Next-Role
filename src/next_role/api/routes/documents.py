import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from next_role.api.deps import get_session
from next_role.documents import queries, service
from next_role.documents.api_schemas import (
    AnswerIn,
    BundleDetailOut,
    ClarificationOut,
    ConfirmClaimIn,
    DismissIn,
    EditIn,
    RegenerateIn,
    ReviewDocumentsOut,
    RevisionOut,
    RevisionSummaryOut,
    RunOut,
    TemplatesOut,
)
from next_role.documents.models import DocumentKind

router = APIRouter(tags=["documents"])

_STALE: dict[int | str, dict[str, Any]] = {
    409: {"description": "Stale expected_version, or the bundle was withdrawn"}
}


@router.get("/reviews/{review_id}/documents")
def review_documents(
    review_id: uuid.UUID, session: Session = Depends(get_session)
) -> ReviewDocumentsOut:
    """Generation runs and document bundles for a review, newest first."""
    return queries.review_documents(session, review_id)


@router.get("/templates")
def list_templates() -> TemplatesOut:
    """The resume templates and the style controls they accept."""
    return queries.templates_out()


@router.get("/bundles/{bundle_id}")
def get_bundle(bundle_id: uuid.UUID, session: Session = Depends(get_session)) -> BundleDetailOut:
    """The bundle with its current resume and letter, checks, questions, style, and runs."""
    return queries.bundle_detail(session, bundle_id)


@router.get("/bundles/{bundle_id}/revisions")
def list_revisions(
    bundle_id: uuid.UUID, kind: DocumentKind | None = None, session: Session = Depends(get_session)
) -> list[RevisionSummaryOut]:
    """Revision history, newest first per document."""
    return [
        queries.revision_summary(session, r)
        for r in service.list_revisions(session, bundle_id, kind)
    ]


@router.get("/revisions/{revision_id}")
def get_revision(revision_id: uuid.UUID, session: Session = Depends(get_session)) -> RevisionOut:
    return queries.revision_out(session, service.get_revision(session, revision_id))


@router.post(
    "/bundles/{bundle_id}/revisions", status_code=status.HTTP_201_CREATED, responses=_STALE
)
def save_edit(
    bundle_id: uuid.UUID, payload: EditIn, session: Session = Depends(get_session)
) -> RevisionOut:
    """Save an edited document as a new revision. It is checked again before approval."""
    revision = service.save_edit(
        session, bundle_id, payload.kind, payload.content, payload.expected_version, payload.note
    )
    return queries.revision_out(session, revision)


@router.post(
    "/bundles/{bundle_id}/regenerations", status_code=status.HTTP_202_ACCEPTED, responses=_STALE
)
def regenerate(
    bundle_id: uuid.UUID, payload: RegenerateIn, session: Session = Depends(get_session)
) -> RunOut:
    """Redraft one section (or the whole letter) in the background."""
    run = service.request_regeneration(
        session,
        bundle_id,
        payload.kind,
        payload.section,
        payload.expected_version,
        payload.idempotency_key,
    )
    return RunOut.model_validate(run)


@router.post("/bundles/{bundle_id}/verifications", status_code=status.HTTP_202_ACCEPTED)
def verify(bundle_id: uuid.UUID, session: Session = Depends(get_session)) -> RunOut:
    """Check the current documents again, e.g. after confirming facts on the profile."""
    return RunOut.model_validate(service.request_verification(session, bundle_id))


@router.post(
    "/bundles/{bundle_id}/claims/{claim_id}/confirm-as-fact",
    status_code=status.HTTP_201_CREATED,
    responses=_STALE,
)
def confirm_claim(
    bundle_id: uuid.UUID,
    claim_id: str,
    payload: ConfirmClaimIn,
    session: Session = Depends(get_session),
) -> RevisionOut:
    """Attest that a claim is true as written: it becomes a confirmed fact and is cited."""
    revision = service.confirm_claim_as_fact(
        session, bundle_id, payload.kind, claim_id, payload.expected_version
    )
    return queries.revision_out(session, revision)


@router.post("/clarifications/{clarification_id}/answer", responses=_STALE)
def answer(
    clarification_id: uuid.UUID, payload: AnswerIn, session: Session = Depends(get_session)
) -> ClarificationOut:
    row = service.answer_clarification(
        session, clarification_id, payload.answer, payload.expected_version
    )
    return ClarificationOut.model_validate(row)


@router.post("/clarifications/{clarification_id}/dismiss", responses=_STALE)
def dismiss(
    clarification_id: uuid.UUID, payload: DismissIn, session: Session = Depends(get_session)
) -> ClarificationOut:
    row = service.dismiss_clarification(
        session, clarification_id, payload.reason, payload.expected_version
    )
    return ClarificationOut.model_validate(row)
