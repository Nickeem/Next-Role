"""Read models for generated documents."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from next_role.documents import runs
from next_role.documents.api_schemas import BundleSummaryOut, ReviewDocumentsOut, RunOut
from next_role.documents.models import DocumentBundle


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
