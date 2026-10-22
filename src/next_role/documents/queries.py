"""Read models for generated documents."""

import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from next_role.documents import artifacts, display, runs, service, templates
from next_role.documents.api_schemas import (
    ApprovalOut,
    ArtifactOut,
    BundleDetailOut,
    BundleSummaryOut,
    ClarificationOut,
    JobRefOut,
    PreviewOut,
    ReviewDocumentsOut,
    RevisionOut,
    RevisionSummaryOut,
    RunOut,
    StyleOut,
    TemplatesOut,
    VerificationOut,
)
from next_role.documents.models import (
    DocumentBundle,
    DocumentKind,
    DocumentRevision,
    DocumentStyle,
    DocumentVerification,
    GenerationRun,
)
from next_role.documents.schemas import StyleSettings, parse_document
from next_role.documents.workflow import BundleStatus
from next_role.errors import InvalidInputError, NotFoundError
from next_role.jobs.models import JobOpportunity


def bundle_summary(bundle: DocumentBundle) -> BundleSummaryOut:
    return BundleSummaryOut(
        id=bundle.id,
        candidate_id=bundle.candidate_id,
        job_id=bundle.opportunity_id,
        review_id=bundle.review_id,
        authorization_event_id=bundle.authorization_event_id,
        status=bundle.status,
        drafting_mode=bundle.drafting_mode,
        version=bundle.version,
        created_at=bundle.created_at,
        updated_at=bundle.updated_at,
    )


def review_documents(session: Session, review_id: uuid.UUID) -> ReviewDocumentsOut:
    """Runs and bundles for a review, newest first."""
    review_runs = runs.runs_for_review(session, review_id)
    bundles = session.scalars(
        select(DocumentBundle)
        .where(DocumentBundle.review_id == review_id)
        .order_by(DocumentBundle.created_at.desc())
    )
    return ReviewDocumentsOut(
        runs=[RunOut.model_validate(r) for r in review_runs],
        bundles=[bundle_summary(b) for b in bundles],
    )


def verification_out(row: DocumentVerification | None) -> VerificationOut | None:
    return VerificationOut.model_validate(row) if row is not None else None


def revision_summary(session: Session, revision: DocumentRevision) -> RevisionSummaryOut:
    latest = service.latest_verification(session, revision.id)
    return RevisionSummaryOut(
        id=revision.id,
        kind=revision.kind,
        seq=revision.seq,
        created_by=revision.created_by,
        note=revision.note,
        created_at=revision.created_at,
        verification_status=latest.status if latest else None,
    )


def revision_out(session: Session, revision: DocumentRevision) -> RevisionOut:
    latest = service.latest_verification(session, revision.id)
    return RevisionOut(
        **revision_summary(session, revision).model_dump(),
        bundle_id=revision.bundle_id,
        parent_id=revision.parent_id,
        schema_version=revision.schema_version,
        content=revision.content,
        content_hash=revision.content_hash,
        verification=verification_out(latest),
    )


def style_out(style: DocumentStyle) -> StyleOut:
    settings = StyleSettings.model_validate(style.settings)
    return StyleOut(
        id=style.id,
        seq=style.seq,
        template_id=style.template_id,
        template_version=style.template_version,
        settings=settings,
        tokens=templates.resolve_tokens(settings),
        created_at=style.created_at,
    )


def bundle_detail(session: Session, bundle_id: uuid.UUID) -> BundleDetailOut:
    bundle = service.get_bundle(session, bundle_id)
    job = session.get(JobOpportunity, bundle.opportunity_id)
    revisions = service.current_revisions(session, bundle.id)
    style = service.current_style(session, bundle.id)
    approval = service.latest_approval(session, bundle.id)
    recent = session.scalars(
        select(GenerationRun)
        .where(GenerationRun.bundle_id == bundle.id)
        .order_by(GenerationRun.created_at.desc())
        .limit(10)
    )
    return BundleDetailOut(
        **bundle_summary(bundle).model_dump(),
        job=JobRefOut(
            id=bundle.opportunity_id,
            title=job.title if job else None,
            company=job.company if job else None,
        ),
        drafting_notes=list(bundle.drafting_notes),
        resume=(
            revision_out(session, revisions[DocumentKind.RESUME])
            if DocumentKind.RESUME in revisions
            else None
        ),
        cover_letter=(
            revision_out(session, revisions[DocumentKind.COVER_LETTER])
            if DocumentKind.COVER_LETTER in revisions
            else None
        ),
        clarifications=[
            ClarificationOut.model_validate(c) for c in service.clarifications(session, bundle.id)
        ],
        style=style_out(style) if style else None,
        runs=[RunOut.model_validate(r) for r in recent],
        artifacts=[ArtifactOut.model_validate(a) for a in artifacts.current(session, bundle.id)],
        approval=ApprovalOut.model_validate(approval) if approval else None,
        approval_current=bundle.status is BundleStatus.APPROVED,
        preview={
            kind: display.build(
                parse_document(revision.content),
                StyleSettings.model_validate(style.settings) if style else StyleSettings(),
            )
            for kind, revision in revisions.items()
        },
    )


def preview(
    session: Session,
    bundle_id: uuid.UUID,
    kind: DocumentKind,
    content: dict[str, Any] | None,
    settings: StyleSettings | None,
) -> PreviewOut:
    """Lay out unsaved content or style exactly as the files would be. Nothing is stored."""
    bundle = service.get_bundle(session, bundle_id)
    current = service.current_revision(session, bundle.id, kind)
    if current is None:
        raise NotFoundError("No document of that kind")
    style = service.current_style(session, bundle.id)
    settings = settings or (
        StyleSettings.model_validate(style.settings) if style else StyleSettings()
    )
    if content is None:
        document = parse_document(current.content)
    else:
        try:
            document = parse_document({**content, "kind": kind.value})
        except ValidationError as exc:
            raise InvalidInputError(
                f"Invalid {kind.value}: {exc.error_count()} problem(s)"
            ) from exc
        previous = parse_document(current.content)
        document = document.model_copy(
            update={"header": previous.header, "target": previous.target}
        )
    return PreviewOut(
        display=display.build(document, settings), tokens=templates.resolve_tokens(settings)
    )


def templates_out() -> TemplatesOut:
    return TemplatesOut(
        templates=templates.registry(), controls=templates.CONTROLS, defaults=StyleSettings()
    )
