"""Rendered files: queueing, leased rendering, completion, notifications, and cleanup.

There is one artifact row per (revision, style, format). Whenever a bundle changes, ``sync``
makes sure a PDF and a DOCX row exist for each current revision in the current style, and
marks every other open row ``superseded``, so an older revision's file is never presented as
current.
The renderer claims ``pending`` rows with a lease, renders and checks the file outside any
transaction, writes it atomically, and only then marks the row ``ready`` with its hash in a
transaction that re-checks the lease. A file without a ready row is an orphan and is swept.
"""

import logging
import re
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from next_role.documents.models import (
    ArtifactFormat,
    DocumentArtifact,
    DocumentBundle,
    DocumentKind,
    DocumentRevision,
)
from next_role.documents.storage import ArtifactStore, StorageError, read_verified
from next_role.documents.workflow import (
    SETTLED_ARTIFACT_STATUSES,
    ArtifactStatus,
    BundleStatus,
    RetryPolicy,
)
from next_role.errors import ConflictError, NotFoundError
from next_role.notifications import service as notifications
from next_role.notifications.models import NotificationEventType

logger = logging.getLogger(__name__)

FORMATS = (ArtifactFormat.PDF, ArtifactFormat.DOCX)
MEDIA_TYPES = {
    ArtifactFormat.PDF: "application/pdf",
    ArtifactFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
# Files older than this without a ready row are removed by the sweep.
ORPHAN_AGE = timedelta(hours=1)


class LeaseLostError(Exception):
    """The artifact was superseded or taken over while it was rendering."""


def bundle_path(bundle_id: uuid.UUID) -> str:
    return f"/documents/{bundle_id}"


def storage_key(artifact: DocumentArtifact) -> str:
    """Unique per attempt, so an attempt that lost its lease can only ever delete its own
    file, never the one a later attempt published."""
    return f"{artifact.bundle_id}/{artifact.id}-{artifact.attempts}.{artifact.format.value}"


def current_targets(
    session: Session, bundle_id: uuid.UUID
) -> tuple[list[uuid.UUID], uuid.UUID | None]:
    """Current revision ids (one per kind) and the current style id."""
    from next_role.documents import service  # local: service imports this module

    revisions = [r.id for r in service.current_revisions(session, bundle_id).values()]
    style = service.current_style(session, bundle_id)
    return revisions, style.id if style else None


def sync(session: Session, bundle: DocumentBundle) -> None:
    revisions, style_id = current_targets(session, bundle.id)
    if style_id is None:
        return
    for revision_id in revisions:
        for fmt in FORMATS:
            session.execute(
                insert(DocumentArtifact)
                .values(
                    id=uuid.uuid4(),
                    bundle_id=bundle.id,
                    revision_id=revision_id,
                    style_id=style_id,
                    format=fmt,
                    status=ArtifactStatus.PENDING,
                    attempts=0,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        DocumentArtifact.revision_id,
                        DocumentArtifact.style_id,
                        DocumentArtifact.format,
                    ]
                )
            )
    session.execute(
        update(DocumentArtifact)
        .where(
            DocumentArtifact.bundle_id == bundle.id,
            DocumentArtifact.status != ArtifactStatus.SUPERSEDED,
            or_(
                DocumentArtifact.revision_id.not_in(revisions),
                DocumentArtifact.style_id != style_id,
            ),
        )
        .values(status=ArtifactStatus.SUPERSEDED, lease_token=None, lease_expires_at=None)
    )


def current(session: Session, bundle_id: uuid.UUID) -> list[DocumentArtifact]:
    revisions, style_id = current_targets(session, bundle_id)
    if style_id is None:
        return []
    return list(
        session.scalars(
            select(DocumentArtifact)
            .where(
                DocumentArtifact.bundle_id == bundle_id,
                DocumentArtifact.revision_id.in_(revisions),
                DocumentArtifact.style_id == style_id,
            )
            .order_by(DocumentArtifact.format)
        )
    )


def claim_next(
    session: Session,
    now: datetime,
    lease: timedelta,
    max_attempts: int,
    formats: frozenset[ArtifactFormat] = frozenset(FORMATS),
) -> DocumentArtifact | None:
    """Claim one due file of a format this renderer can produce."""
    while True:
        artifact = session.scalar(
            select(DocumentArtifact)
            .where(
                DocumentArtifact.format.in_(formats),
                or_(
                    and_(
                        DocumentArtifact.status == ArtifactStatus.PENDING,
                        DocumentArtifact.next_attempt_at <= now,
                    ),
                    and_(
                        DocumentArtifact.status == ArtifactStatus.RENDERING,
                        DocumentArtifact.lease_expires_at < now,
                    ),
                ),
            )
            .order_by(DocumentArtifact.next_attempt_at, DocumentArtifact.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if artifact is None:
            return None
        if artifact.status is ArtifactStatus.RENDERING and artifact.attempts >= max_attempts:
            _fail(session, artifact, "lease_expired", now)
            continue
        artifact.status = ArtifactStatus.RENDERING
        artifact.attempts += 1
        artifact.lease_token = uuid.uuid4()
        artifact.lease_expires_at = now + lease
        session.flush()
        return artifact


def hold(session: Session, artifact_id: uuid.UUID, token: uuid.UUID) -> DocumentArtifact:
    artifact = session.scalar(
        select(DocumentArtifact)
        .where(DocumentArtifact.id == artifact_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        artifact is None
        or artifact.status is not ArtifactStatus.RENDERING
        or artifact.lease_token != token
    ):
        raise LeaseLostError(str(artifact_id))
    return artifact


def complete(
    session: Session,
    artifact: DocumentArtifact,
    *,
    key: str,
    sha256: str,
    size: int,
    page_count: int | None,
    renderer_version: str,
    qa: dict[str, object],
    now: datetime,
) -> None:
    artifact.status = ArtifactStatus.READY
    artifact.storage_key = key
    artifact.sha256 = sha256
    artifact.bytes = size
    artifact.page_count = page_count
    artifact.renderer_version = renderer_version
    artifact.qa = qa
    artifact.rendered_at = now
    artifact.last_error = None
    artifact.lease_token = None
    artifact.lease_expires_at = None
    after_settle(session, artifact.bundle_id)


def fail(
    session: Session,
    artifact: DocumentArtifact,
    error: str,
    now: datetime,
    policy: RetryPolicy,
    retryable: bool,
    qa: dict[str, object] | None = None,
) -> bool:
    """Record a failed attempt. Returns True if it will be retried."""
    artifact.qa = qa
    retry_at = policy.next_attempt(artifact.attempts, now) if retryable else None
    if retry_at is None:
        _fail(session, artifact, error, now)
        return False
    artifact.status = ArtifactStatus.PENDING
    artifact.next_attempt_at = retry_at
    artifact.lease_token = None
    artifact.lease_expires_at = None
    artifact.last_error = error
    return True


def _fail(session: Session, artifact: DocumentArtifact, error: str, now: datetime) -> None:
    artifact.status = ArtifactStatus.FAILED
    artifact.last_error = error
    artifact.lease_token = None
    artifact.lease_expires_at = None
    after_settle(session, artifact.bundle_id)


def retry(
    session: Session, artifact_id: uuid.UUID, now: datetime, store: ArtifactStore
) -> DocumentArtifact:
    """Render a file again (the operator asked): one that failed, or a ready one whose stored
    file is missing or no longer matches its hash (e.g. after restoring only the database)."""
    bundle_id = session.scalar(
        select(DocumentArtifact.bundle_id).where(DocumentArtifact.id == artifact_id)
    )
    if bundle_id is None:
        raise NotFoundError(f"Artifact {artifact_id} not found")
    # Bundle before artifact, the order bundle-level changes lock in.
    bundle = session.get(DocumentBundle, bundle_id, with_for_update=True)
    artifact = session.scalar(
        select(DocumentArtifact).where(DocumentArtifact.id == artifact_id).with_for_update()
    )
    if artifact is None or bundle is None:
        raise NotFoundError(f"Artifact {artifact_id} not found")
    if artifact.status is ArtifactStatus.READY:
        try:
            read_verified(store, artifact.storage_key or "", artifact.sha256 or "")
        except StorageError:
            pass
        else:
            raise ConflictError("This file is ready and intact")
        artifact.storage_key = None
        artifact.sha256 = None
    elif artifact.status is not ArtifactStatus.FAILED:
        raise ConflictError("Only failed or damaged files can be rendered again")
    if bundle.status is BundleStatus.APPROVED:
        # The new file may differ from the approved bytes: the approval no longer covers the
        # set, so the operator reviews and approves again (the approval stays in history).
        bundle.status = BundleStatus.READY_FOR_REVIEW
        bundle.version += 1
    artifact.status = ArtifactStatus.PENDING
    artifact.attempts = 0
    artifact.next_attempt_at = now
    return artifact


def after_settle(session: Session, bundle_id: uuid.UUID) -> None:
    """Once every current file has settled, notify once per bundle and outcome (spec §3)."""
    session.flush()
    bundle = session.get(DocumentBundle, bundle_id)
    files = current(session, bundle_id)
    if bundle is None or not files or bundle.status is BundleStatus.WITHDRAWN:
        return
    if any(f.status not in SETTLED_ARTIFACT_STATUSES for f in files):
        return
    if any(f.status is ArtifactStatus.FAILED for f in files):
        event = NotificationEventType.DOCUMENTS_FAILED
    elif bundle.status is BundleStatus.NEEDS_INPUT:
        event = NotificationEventType.DOCUMENTS_NEEDS_INPUT
    else:
        event = NotificationEventType.DOCUMENTS_READY
    notifications.enqueue(
        session,
        event,
        event_key=f"{event.value}:{bundle.id}",
        click_path=bundle_path(bundle.id),
        review_id=bundle.review_id,
    )


def bundle_from_key(event_key: str) -> uuid.UUID | None:
    match = re.fullmatch(r"documents\.[a-z_]+:([0-9a-f-]{36})", event_key)
    return uuid.UUID(match.group(1)) if match else None


def approval_from_key(event_key: str) -> uuid.UUID | None:
    """The approval event of a ``documents.failed:approval:<id>`` notification."""
    match = re.fullmatch(r"documents\.failed:approval:([0-9a-f-]{36})", event_key)
    return uuid.UUID(match.group(1)) if match else None


def sweep_orphans(session: Session, store: ArtifactStore, now: datetime) -> int:
    """Delete stored files that no ready artifact points at. Returns how many were removed."""
    referenced = set(
        session.scalars(
            select(DocumentArtifact.storage_key).where(
                DocumentArtifact.storage_key.is_not(None),
                DocumentArtifact.status.in_([ArtifactStatus.READY, ArtifactStatus.SUPERSEDED]),
            )
        )
    )
    in_flight = set(
        str(a)
        for a in session.scalars(
            select(DocumentArtifact.id).where(DocumentArtifact.status == ArtifactStatus.RENDERING)
        )
    )
    removed = 0
    for key in list(store.keys()):
        artifact_id = key.split("/")[1][:36]
        if key in referenced or artifact_id in in_flight:
            continue
        if store.age(key) < ORPHAN_AGE:
            continue
        store.delete(key)
        removed += 1
    if removed:
        logger.info("orphaned files removed", extra={"count": removed})
    return removed


def get(session: Session, artifact_id: uuid.UUID) -> DocumentArtifact:
    artifact = session.get(DocumentArtifact, artifact_id)
    if artifact is None:
        raise NotFoundError(f"Artifact {artifact_id} not found")
    return artifact


def download_name(session: Session, artifact: DocumentArtifact, approved: bool) -> str:
    """E.g. ``alex-example-resume-fabrikam-r3.pdf``, with ``-DRAFT`` unless approved."""
    from next_role.candidates.models import Candidate
    from next_role.jobs.models import JobOpportunity

    revision = session.get(DocumentRevision, artifact.revision_id)
    bundle = session.get(DocumentBundle, artifact.bundle_id)
    candidate = session.get(Candidate, bundle.candidate_id) if bundle else None
    job = session.get(JobOpportunity, bundle.opportunity_id) if bundle else None
    kind = "resume" if revision and revision.kind is DocumentKind.RESUME else "cover-letter"
    parts = [
        _slug(candidate.full_name if candidate else ""),
        kind,
        _slug(job.company or "" if job else ""),
        f"r{revision.seq if revision else 0}",
    ]
    name = "-".join(p for p in parts if p)
    return f"{name}{'' if approved else '-DRAFT'}.{artifact.format.value}"


def _slug(text: str) -> str:
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
