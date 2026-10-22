import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from next_role.api.deps import get_session
from next_role.documents import artifacts, queries, service
from next_role.documents.api_schemas import (
    AnswerIn,
    ApprovalIn,
    ApprovalOut,
    ArtifactOut,
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
    StyleIn,
    StyleOut,
    TemplatesOut,
)
from next_role.documents.models import DocumentKind
from next_role.documents.storage import ArtifactStore, StorageError, read_verified
from next_role.documents.workflow import ArtifactStatus
from next_role.errors import ConflictError
from next_role.logging_config import request_id_var

logger = logging.getLogger(__name__)
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


@router.post("/bundles/{bundle_id}/style", status_code=status.HTTP_201_CREATED, responses=_STALE)
def set_style(
    bundle_id: uuid.UUID, payload: StyleIn, session: Session = Depends(get_session)
) -> StyleOut:
    """Change template, page size, density, accent, font or section order. Files are
    rendered again; the content and its checks do not change."""
    style = service.set_style(session, bundle_id, payload.settings, payload.expected_version)
    return queries.style_out(style)


@router.post(
    "/bundles/{bundle_id}/approval",
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Something changed or is unresolved; the detail says what"}},
)
def approve(
    bundle_id: uuid.UUID, payload: ApprovalIn, session: Session = Depends(get_session)
) -> ApprovalOut:
    """Approve these exact revisions, style and files. This does not apply to any job."""
    approval = service.approve(
        session,
        bundle_id,
        expected_version=payload.expected_version,
        resume_revision_id=payload.resume_revision_id,
        cover_letter_revision_id=payload.cover_letter_revision_id,
        style_id=payload.style_id,
        artifact_hashes=payload.artifacts,
        request_id=request_id_var.get(),
    )
    return ApprovalOut.model_validate(approval)


@router.post("/artifacts/{artifact_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_artifact(
    artifact_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> ArtifactOut:
    """Render a failed or damaged file again."""
    store: ArtifactStore = request.app.state.artifact_store
    return ArtifactOut.model_validate(
        artifacts.retry(session, artifact_id, datetime.now(UTC), store)
    )


@router.get(
    "/artifacts/{artifact_id}/download",
    response_class=Response,
    responses={200: {"content": {m: {} for m in artifacts.MEDIA_TYPES.values()}}},
)
def download(
    artifact_id: uuid.UUID, request: Request, session: Session = Depends(get_session)
) -> Response:
    """Download a rendered file. Unapproved files are named ``…-DRAFT``."""
    artifact = artifacts.get(session, artifact_id)
    if artifact.status is not ArtifactStatus.READY or not artifact.storage_key:
        raise ConflictError("This file is not ready")
    if artifact.sha256 is None:  # pragma: no cover - ready rows always have a hash
        raise ConflictError("This file is not ready")
    store: ArtifactStore = request.app.state.artifact_store
    try:
        data = read_verified(store, artifact.storage_key, artifact.sha256)
    except StorageError as exc:
        logger.error(
            "artifact unreadable", extra={"artifact_id": str(artifact.id), "error": str(exc)}
        )
        raise ConflictError("This file is unavailable; render it again") from exc
    name = artifacts.download_name(session, artifact, service.approval_covers(session, artifact))
    return Response(
        content=data,
        media_type=artifacts.MEDIA_TYPES[artifact.format],
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
