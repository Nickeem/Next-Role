"""Use cases for document bundles: revisions, verification records, questions, and edits.

Every change locks the bundle row, checks the client's ``expected_version`` (stale → 409),
writes immutable rows, bumps the version, and recomputes the bundle's status, all in the
caller's transaction. Anything slow (drafting, LLM checks) is queued as a run for the worker.
"""

import logging
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from next_role.candidates.models import (
    CandidateFact,
    EvidenceKind,
    EvidenceSource,
    FactType,
    VerificationStatus,
)
from next_role.documents import templates
from next_role.documents.drafting import Question, connective_templates
from next_role.documents.models import (
    ClarificationRequest,
    DocumentBundle,
    DocumentKind,
    DocumentRevision,
    DocumentStyle,
    DocumentVerification,
    GenerationRun,
    RevisionAuthor,
)
from next_role.documents.schemas import (
    STATEMENT_PREFIX,
    CoverLetterDocument,
    Document,
    ResumeDocument,
    SectionType,
    StyleSettings,
    Target,
    content_hash,
    iter_claims,
    parse_document,
)
from next_role.documents.snapshot import FactSnapshot, active_facts, fact_snapshot
from next_role.documents.verification import (
    CHECK_VERSION,
    Context,
    Verdict,
    VerificationResult,
)
from next_role.documents.workflow import (
    BundleStatus,
    ClarificationStatus,
    RunIntent,
    RunStatus,
    VerificationOutcome,
    bundle_status,
    ensure_open,
)
from next_role.errors import ConflictError, InvalidInputError, NotFoundError
from next_role.skills import normalize_skill

logger = logging.getLogger(__name__)

POLICY_VERSION = "documents-v1"
LETTER_SECTION = "letter"
SECTIONS: dict[DocumentKind, frozenset[str]] = {
    DocumentKind.RESUME: frozenset(t.value for t in SectionType),
    DocumentKind.COVER_LETTER: frozenset({LETTER_SECTION}),
}


# --- Reading -------------------------------------------------------------------------------


def get_bundle(session: Session, bundle_id: uuid.UUID) -> DocumentBundle:
    bundle = session.get(DocumentBundle, bundle_id)
    if bundle is None:
        raise NotFoundError(f"Document bundle {bundle_id} not found")
    return bundle


def lock_bundle(session: Session, bundle_id: uuid.UUID) -> DocumentBundle:
    bundle = session.scalar(
        select(DocumentBundle)
        .where(DocumentBundle.id == bundle_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if bundle is None:
        raise NotFoundError(f"Document bundle {bundle_id} not found")
    return bundle


def check_version(bundle: DocumentBundle, expected_version: int) -> None:
    if bundle.version != expected_version:
        raise ConflictError(
            f"Document bundle {bundle.id} has changed (version {bundle.version}); reload and retry"
        )


def current_revision(
    session: Session, bundle_id: uuid.UUID, kind: DocumentKind
) -> DocumentRevision | None:
    return session.scalar(
        select(DocumentRevision)
        .where(DocumentRevision.bundle_id == bundle_id, DocumentRevision.kind == kind)
        .order_by(DocumentRevision.seq.desc())
        .limit(1)
    )


def current_revisions(
    session: Session, bundle_id: uuid.UUID
) -> dict[DocumentKind, DocumentRevision]:
    found = {k: current_revision(session, bundle_id, k) for k in DocumentKind}
    return {k: r for k, r in found.items() if r is not None}


def current_style(session: Session, bundle_id: uuid.UUID) -> DocumentStyle | None:
    return session.scalar(
        select(DocumentStyle)
        .where(DocumentStyle.bundle_id == bundle_id)
        .order_by(DocumentStyle.seq.desc())
        .limit(1)
    )


def latest_verification(session: Session, revision_id: uuid.UUID) -> DocumentVerification | None:
    return session.scalar(
        select(DocumentVerification)
        .where(DocumentVerification.revision_id == revision_id)
        .order_by(DocumentVerification.created_at.desc(), DocumentVerification.id.desc())
        .limit(1)
    )


def get_revision(session: Session, revision_id: uuid.UUID) -> DocumentRevision:
    revision = session.get(DocumentRevision, revision_id)
    if revision is None:
        raise NotFoundError(f"Revision {revision_id} not found")
    return revision


def list_revisions(
    session: Session, bundle_id: uuid.UUID, kind: DocumentKind | None = None
) -> list[DocumentRevision]:
    get_bundle(session, bundle_id)
    query = select(DocumentRevision).where(DocumentRevision.bundle_id == bundle_id)
    if kind is not None:
        query = query.where(DocumentRevision.kind == kind)
    return list(session.scalars(query.order_by(DocumentRevision.kind, DocumentRevision.seq.desc())))


def clarifications(session: Session, bundle_id: uuid.UUID) -> list[ClarificationRequest]:
    return list(
        session.scalars(
            select(ClarificationRequest)
            .where(ClarificationRequest.bundle_id == bundle_id)
            .order_by(ClarificationRequest.created_at, ClarificationRequest.key)
        )
    )


def statements(session: Session, bundle_id: uuid.UUID) -> dict[str, str]:
    """Letter-only answers, by the evidence id cover-letter claims cite them with."""
    return {
        f"{STATEMENT_PREFIX}{c.id}": c.answer
        for c in clarifications(session, bundle_id)
        if c.status is ClarificationStatus.ANSWERED
        and c.answer_kind == "letter_statement"
        and c.answer
    }


def current_facts(session: Session, candidate_id: uuid.UUID) -> dict[uuid.UUID, FactSnapshot]:
    return {f.id: fact_snapshot(f) for f in active_facts(session, candidate_id)}


def verification_context(
    facts: Mapping[uuid.UUID, FactSnapshot],
    statements_by_id: Mapping[str, str],
    target: Target,
    semantic_available: bool,
    verdicts: Mapping[str, Verdict] | None = None,
) -> Context:
    return Context(
        facts=facts,
        statements=statements_by_id,
        connective_templates=connective_templates(target),
        semantic_available=semantic_available,
        verdicts=verdicts or {},
        target_phrases=tuple(p for p in (target.job_title, target.company) if p),
    )


def known_verdicts(session: Session, bundle_id: uuid.UUID) -> dict[str, Verdict]:
    """LLM verdicts already given for this bundle, so unchanged claims are not re-judged."""
    rows = session.scalars(
        select(DocumentVerification.claims)
        .join(DocumentRevision, DocumentRevision.id == DocumentVerification.revision_id)
        .where(DocumentRevision.bundle_id == bundle_id)
        .order_by(DocumentVerification.created_at)
    )
    verdicts: dict[str, Verdict] = {}
    for claims in rows:
        for claim in claims:
            if claim.get("semantic_key") and claim.get("verdict"):
                verdicts[claim["semantic_key"]] = Verdict(claim["verdict"])
    return verdicts


# --- Writing -------------------------------------------------------------------------------


def add_revision(
    session: Session,
    bundle: DocumentBundle,
    document: Document,
    created_by: RevisionAuthor,
    *,
    run_id: uuid.UUID | None = None,
    note: str | None = None,
) -> DocumentRevision:
    kind = DocumentKind(document.kind)
    parent = current_revision(session, bundle.id, kind)
    revision = DocumentRevision(
        bundle_id=bundle.id,
        kind=kind,
        seq=(parent.seq + 1) if parent else 1,
        parent_id=parent.id if parent else None,
        schema_version=document.schema_version,
        content=document.model_dump(mode="json"),
        content_hash=content_hash(document),
        created_by=created_by,
        run_id=run_id,
        note=note[:300] if note else None,
    )
    session.add(revision)
    session.flush()
    return revision


def add_style(session: Session, bundle: DocumentBundle, settings: StyleSettings) -> DocumentStyle:
    previous = current_style(session, bundle.id)
    version = templates.template(settings.template_id).version
    style = DocumentStyle(
        bundle_id=bundle.id,
        seq=(previous.seq + 1) if previous else 1,
        template_id=settings.template_id,
        template_version=version,
        settings=settings.model_dump(mode="json"),
        style_hash=settings.style_hash(version),
    )
    session.add(style)
    session.flush()
    return style


def record_verification(
    session: Session, revision: DocumentRevision, result: VerificationResult
) -> DocumentVerification:
    row = DocumentVerification(
        revision_id=revision.id,
        check_version=CHECK_VERSION,
        status=result.outcome,
        claims=[c.as_dict() for c in result.claims],
        issues=[i.as_dict() for i in result.issues],
        semantic_checked=result.semantic_checked,
    )
    session.add(row)
    session.flush()
    return row


def add_questions(session: Session, bundle: DocumentBundle, questions: list[Question]) -> None:
    """Add new questions; one already asked under the same key (any status) is kept as is."""
    for q in questions:
        session.execute(
            insert(ClarificationRequest)
            .values(
                id=uuid.uuid4(),
                bundle_id=bundle.id,
                kind=q.kind,
                key=q.key,
                question=q.question,
                material=q.material,
                status=ClarificationStatus.OPEN,
                answer_kind=q.answer_kind,
            )
            .on_conflict_do_nothing(
                index_elements=[ClarificationRequest.bundle_id, ClarificationRequest.key]
            )
        )


def refresh_status(
    session: Session, bundle: DocumentBundle, *, keep_approval: bool = False
) -> None:
    """Recompute an open bundle's status after a change. A change never keeps an approval;
    re-checking unchanged content (``keep_approval``) keeps it if the checks still pass."""
    ensure_open(bundle.status)
    revisions = current_revisions(session, bundle.id)
    passed = len(revisions) == len(DocumentKind) and all(
        (v := latest_verification(session, r.id)) is not None
        and v.status is VerificationOutcome.PASSED
        for r in revisions.values()
    )
    open_material = session.scalar(
        select(func.count())
        .select_from(ClarificationRequest)
        .where(
            ClarificationRequest.bundle_id == bundle.id,
            ClarificationRequest.status == ClarificationStatus.OPEN,
            ClarificationRequest.material.is_(True),
        )
    )
    status = bundle_status(verifications_passed=passed, open_material_questions=open_material or 0)
    if not (
        keep_approval
        and bundle.status is BundleStatus.APPROVED
        and status is BundleStatus.READY_FOR_REVIEW
    ):
        bundle.status = status
    bundle.version += 1


def queue_run(
    session: Session,
    bundle: DocumentBundle,
    intent: RunIntent,
    key: str,
    parameters: dict[str, Any] | None = None,
) -> GenerationRun:
    """Queue work for this bundle. Repeating a key returns the run already queued under it."""
    session.execute(
        insert(GenerationRun)
        .values(
            id=uuid.uuid4(),
            review_id=bundle.review_id,
            authorization_event_id=bundle.authorization_event_id,
            bundle_id=bundle.id,
            intent=intent,
            idempotency_key=key,
            parameters=parameters or {},
            status=RunStatus.QUEUED,
            attempts=0,
        )
        .on_conflict_do_nothing(index_elements=[GenerationRun.idempotency_key])
    )
    run = session.scalar(select(GenerationRun).where(GenerationRun.idempotency_key == key))
    if run is None or run.bundle_id != bundle.id:  # pragma: no cover - keys are per bundle
        raise ConflictError("That request key is already used for other work")
    return run


def queue_verify(session: Session, bundle: DocumentBundle, reason: str) -> GenerationRun:
    return queue_run(
        session, bundle, RunIntent.VERIFY, f"verify:{uuid.uuid4()}", {"reason": reason}
    )


# --- Operator actions ----------------------------------------------------------------------


def _open_bundle(session: Session, bundle_id: uuid.UUID, expected_version: int) -> DocumentBundle:
    bundle = lock_bundle(session, bundle_id)
    ensure_open(bundle.status)
    check_version(bundle, expected_version)
    return bundle


def save_edit(
    session: Session,
    bundle_id: uuid.UUID,
    kind: DocumentKind,
    content: dict[str, Any],
    expected_version: int,
    note: str | None = None,
) -> DocumentRevision:
    """Store the operator's edited document as a new revision and queue its verification.

    The header and target come from the profile and the job, so edits to them are ignored.
    """
    bundle = _open_bundle(session, bundle_id, expected_version)
    current = current_revision(session, bundle.id, kind)
    if current is None:
        raise ConflictError("This bundle has no document of that kind yet")
    try:
        document = parse_document({**content, "kind": kind.value})
    except ValidationError as exc:
        raise InvalidInputError(f"Invalid {kind.value}: {exc.error_count()} problem(s)") from exc
    previous = parse_document(current.content)
    document = document.model_copy(update={"header": previous.header, "target": previous.target})
    if content_hash(document) == current.content_hash:
        raise InvalidInputError("Nothing changed")
    revision = add_revision(
        session, bundle, document, RevisionAuthor.OPERATOR, note=note or "Edited"
    )
    refresh_status(session, bundle)
    queue_verify(session, bundle, "edit")
    logger.info("document edited", extra={"bundle_id": str(bundle.id), "kind": kind.value})
    return revision


def request_regeneration(
    session: Session,
    bundle_id: uuid.UUID,
    kind: DocumentKind,
    section: str,
    expected_version: int,
    idempotency_key: str,
) -> GenerationRun:
    bundle = _open_bundle(session, bundle_id, expected_version)
    if section not in SECTIONS[kind]:
        raise InvalidInputError(f"Unknown section for {kind.value}: {section}")
    active = session.scalar(
        select(func.count())
        .select_from(GenerationRun)
        .where(
            GenerationRun.bundle_id == bundle.id,
            GenerationRun.intent == RunIntent.REGENERATE_SECTION,
            GenerationRun.status.in_([RunStatus.QUEUED, RunStatus.RUNNING]),
            GenerationRun.idempotency_key != f"regenerate:{bundle.id}:{idempotency_key}",
        )
    )
    if active:
        raise ConflictError("A section is already being regenerated; wait for it to finish")
    return queue_run(
        session,
        bundle,
        RunIntent.REGENERATE_SECTION,
        f"regenerate:{bundle.id}:{idempotency_key}",
        {"kind": kind.value, "section": section},
    )


def request_verification(session: Session, bundle_id: uuid.UUID) -> GenerationRun:
    """Check the current revisions again, e.g. after confirming facts on the profile."""
    bundle = lock_bundle(session, bundle_id)
    ensure_open(bundle.status)
    return queue_verify(session, bundle, "requested")


def _clarification(session: Session, clarification_id: uuid.UUID) -> ClarificationRequest:
    row = session.get(ClarificationRequest, clarification_id)
    if row is None:
        raise NotFoundError(f"Question {clarification_id} not found")
    return row


def _user_statement(session: Session, candidate_id: uuid.UUID, title: str) -> EvidenceSource:
    source = EvidenceSource(
        candidate_id=candidate_id, kind=EvidenceKind.USER_STATEMENT, title=title[:300]
    )
    session.add(source)
    return source


def answer_clarification(
    session: Session, clarification_id: uuid.UUID, answer: str, expected_version: int
) -> ClarificationRequest:
    """Record an answer. A career fact becomes an unconfirmed fact on the profile, which the
    candidate must confirm before documents citing it can be approved."""
    row = _clarification(session, clarification_id)
    bundle = _open_bundle(session, row.bundle_id, expected_version)
    if row.status is not ClarificationStatus.OPEN:
        raise ConflictError("This question has already been resolved")
    answer = answer.strip()
    if not answer:
        raise InvalidInputError("An answer is required")
    row.answer = answer
    row.status = ClarificationStatus.ANSWERED
    row.resolved_at = datetime.now(UTC)
    if row.answer_kind == "career_fact":
        # "metric:<experience fact id>" questions ask about one job; the answer belongs to it.
        parent = uuid.UUID(row.key.split(":", 1)[1]) if row.key.startswith("metric:") else None
        if parent is not None and session.get(CandidateFact, parent) is None:
            parent = None
        fact = CandidateFact(
            candidate_id=bundle.candidate_id,
            fact_type=FactType.ACHIEVEMENT,
            title=answer if len(answer) <= 300 else answer[:297] + "…",
            description=answer if len(answer) > 300 else None,
            skills=[],
            attributes={"metrics": []},
            verification_status=VerificationStatus.SELF_REPORTED,
            parent_fact_id=parent,
        )
        fact.evidence_sources = [
            _user_statement(session, bundle.candidate_id, "Answer to a document question")
        ]
        session.add(fact)
        session.flush()
        row.proposed_fact_id = fact.id
    refresh_status(session, bundle)
    return row


def dismiss_clarification(
    session: Session, clarification_id: uuid.UUID, reason: str | None, expected_version: int
) -> ClarificationRequest:
    row = _clarification(session, clarification_id)
    bundle = _open_bundle(session, row.bundle_id, expected_version)
    if row.status is not ClarificationStatus.OPEN:
        raise ConflictError("This question has already been resolved")
    reason = (reason or "").strip() or None
    if row.material and reason is None:
        raise InvalidInputError("A reason is required to dismiss this question")
    row.status = ClarificationStatus.DISMISSED
    row.dismiss_reason = reason
    row.resolved_at = datetime.now(UTC)
    refresh_status(session, bundle)
    return row


def confirm_claim_as_fact(
    session: Session,
    bundle_id: uuid.UUID,
    kind: DocumentKind,
    claim_id: str,
    expected_version: int,
) -> DocumentRevision:
    """The candidate attests that a claim is true as written (specification §6).

    Its text becomes a new confirmed fact, with a user-statement evidence source, and a new
    revision cites that fact. Verification then supports the claim verbatim.
    """
    bundle = _open_bundle(session, bundle_id, expected_version)
    current = current_revision(session, bundle.id, kind)
    if current is None:
        raise NotFoundError("No document of that kind")
    document = parse_document(current.content)
    found = next(((ref, c) for ref, c in iter_claims(document) if c.claim_id == claim_id), None)
    if found is None:
        raise NotFoundError(f"Claim {claim_id} not found")
    ref, claim = found
    if claim.connective:
        raise InvalidInputError("Connective sentences state no fact to confirm")

    is_skill = ref.path.split("/")[-2] == "skills"
    now = datetime.now(UTC)
    fact = CandidateFact(
        candidate_id=bundle.candidate_id,
        fact_type=FactType.SKILL if is_skill else FactType.ACHIEVEMENT,
        title=claim.text if len(claim.text) <= 300 else claim.text[:297] + "…",
        description=None if len(claim.text) <= 300 else claim.text,
        skills=[normalize_skill(claim.text)] if is_skill else [],
        attributes={} if is_skill else {"metrics": []},
        verification_status=VerificationStatus.USER_VERIFIED,
        verified_at=now,
        parent_fact_id=ref.item.source_fact_id if ref.item else None,
    )
    fact.evidence_sources = [
        _user_statement(session, bundle.candidate_id, "Confirmed while reviewing a document")
    ]
    session.add(fact)
    session.flush()

    updated = _with_evidence(document, claim_id, str(fact.id))
    revision = add_revision(
        session, bundle, updated, RevisionAuthor.SYSTEM, note="Claim confirmed as a fact"
    )
    refresh_status(session, bundle)
    queue_verify(session, bundle, "confirmed_fact")
    return revision


def _with_evidence(document: Document, claim_id: str, evidence_id: str) -> Document:
    data = document.model_dump(mode="json")

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("claim_id") == claim_id:
                node["evidence_ids"] = [*node.get("evidence_ids", []), evidence_id][-10:]
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(data)
    return parse_document(data)


def replace_section(current: Document, fresh: Document, section: str) -> Document:
    """``current`` with one section taken from ``fresh`` (the whole letter for letters)."""
    if isinstance(current, CoverLetterDocument) or isinstance(fresh, CoverLetterDocument):
        return fresh
    assert isinstance(fresh, ResumeDocument)  # noqa: S101 - narrowed by the line above
    new = next((s for s in fresh.sections if s.type == section), None)
    sections = [s for s in current.sections if s.type != section]
    if new is not None:
        order = [s.type for s in fresh.sections]
        sections.append(new)
        sections.sort(key=lambda s: order.index(s.type) if s.type in order else len(order))
    return current.model_copy(
        update={"header": fresh.header, "target": fresh.target, "sections": sections}
    )
