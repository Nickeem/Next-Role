"""The worker side of document generation: execute one claimed run.

A run is claimed in a short transaction. Drafting and LLM checks then happen outside any
transaction, under the run's lease. The results are written in one final transaction, which
first re-checks the lease and the approval to draft, so a revoked approval, a lost lease, or
a crash never leaves partial or duplicate documents.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from next_role.config import Settings
from next_role.documents import drafting, runs, service
from next_role.documents.models import (
    DocumentBundle,
    DocumentKind,
    DraftingMode,
    GenerationInput,
    GenerationRun,
    RevisionAuthor,
)
from next_role.documents.schemas import Document, StyleSettings, parse_document
from next_role.documents.snapshot import FactSnapshot, InputSnapshot, build_snapshot, snapshot_hash
from next_role.documents.verification import Verdict, VerificationResult, semantic_requests, verify
from next_role.documents.workflow import (
    CLOSED_BUNDLE_STATUSES,
    BundleStatus,
    RetryPolicy,
    RunIntent,
)
from next_role.integrations.llm.base import LLMError, LLMProvider

logger = logging.getLogger(__name__)


class RevisionChangedError(Exception):
    """The document changed while a section was being redrafted; redraft from the new one."""


@dataclass(frozen=True)
class GenerationContext:
    factory: sessionmaker[Session]
    # None when no provider is configured or candidate data may not be sent to it.
    provider: LLMProvider | None
    no_llm_reason: str | None
    lease: timedelta
    max_attempts: int
    now: Callable[[], datetime]
    on_progress: Callable[[], None] | None = None

    @property
    def retry(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=self.max_attempts)

    def progress(self) -> None:
        if self.on_progress:
            self.on_progress()


def drafting_provider(
    settings: Settings, provider: LLMProvider | None
) -> tuple[LLMProvider | None, str | None]:
    """The provider drafting may use, or None with the reason (ADR 0006)."""
    if provider is None:
        return None, "No language model is configured; documents were assembled from your facts"
    if not settings.llm_allow_candidate_data:
        return None, (
            "NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA is not set; documents were assembled from your "
            "facts and nothing about you was sent to a model"
        )
    return provider, None


def process_next(ctx: GenerationContext) -> bool:
    """Claim and execute one due run. Returns False when there was nothing to do."""
    with ctx.factory.begin() as session:
        run = runs.claim_next(session, ctx.now(), ctx.lease, ctx.max_attempts)
        if run is None:
            return False
        run_id, token, intent = run.id, run.lease_token, run.intent
    assert token is not None  # noqa: S101 - claim_next always sets it
    logger.info("generation run started", extra={"run_id": str(run_id), "intent": intent.value})
    try:
        execute(ctx, run_id, token, intent)
    except runs.LeaseLostError:
        logger.warning("generation run lease lost", extra={"run_id": str(run_id)})
    except runs.StaleAuthorizationError:
        _fail(ctx, run_id, token, "stale_authorization", retryable=False)
    except RevisionChangedError:
        _fail(ctx, run_id, token, "revision_changed", retryable=True)
    except (LLMError, ValidationError) as exc:
        _fail(ctx, run_id, token, type(exc).__name__, retryable=True)
    except Exception as exc:
        logger.exception("generation run failed", extra={"run_id": str(run_id)})
        _fail(ctx, run_id, token, type(exc).__name__, retryable=True)
    return True


def _fail(
    ctx: GenerationContext, run_id: uuid.UUID, token: uuid.UUID, error: str, retryable: bool
) -> None:
    try:
        with ctx.factory.begin() as session:
            run = runs.hold(session, run_id, token)
            retrying = runs.fail(run, ctx.now(), error, ctx.retry, retryable)
    except runs.LeaseLostError:
        return
    logger.warning(
        "generation run attempt failed",
        extra={"run_id": str(run_id), "error": error, "retrying": retrying},
    )


def execute(ctx: GenerationContext, run_id: uuid.UUID, token: uuid.UUID, intent: RunIntent) -> None:
    if intent is RunIntent.INITIAL:
        _initial(ctx, run_id, token)
    elif intent is RunIntent.REGENERATE_SECTION:
        _regenerate(ctx, run_id, token)
    else:
        _verify_current(ctx, run_id, token)


# --- Verification with the optional LLM check ---------------------------------------------


@dataclass
class Checked:
    result: VerificationResult
    # Set when the LLM check failed; claims stay "awaiting check" and a verify run is queued.
    semantic_error: str | None = None


def check(
    ctx: GenerationContext,
    document: Document,
    facts: dict[uuid.UUID, FactSnapshot],
    statements: dict[str, str],
    verdicts: dict[str, Verdict],
    *,
    raise_on_llm_error: bool = False,
) -> Checked:
    vctx = service.verification_context(
        facts, statements, document.target, ctx.provider is not None, verdicts
    )
    result = verify(document, vctx)
    requests = semantic_requests(result, document, vctx)
    if not requests or ctx.provider is None:
        return Checked(result)
    try:
        new = drafting.judge(ctx.provider, requests)
    except (LLMError, ValidationError) as exc:
        if raise_on_llm_error:
            raise
        logger.warning("semantic check failed", extra={"error_type": type(exc).__name__})
        return Checked(result, type(exc).__name__)
    ctx.progress()
    vctx = service.verification_context(
        facts, statements, document.target, True, {**verdicts, **new}
    )
    return Checked(verify(document, vctx))


def _facts(snapshot: InputSnapshot) -> dict[uuid.UUID, FactSnapshot]:
    return {f.id: f for f in snapshot.facts}


def _store_input(session: Session, snapshot: InputSnapshot) -> GenerationInput:
    row = GenerationInput(
        candidate_id=snapshot.candidate.id,
        opportunity_id=snapshot.job.id,
        assessment_id=snapshot.assessment_id,
        snapshot=snapshot.model_dump(mode="json"),
        snapshot_hash=snapshot_hash(snapshot),
        policy_version=service.POLICY_VERSION,
    )
    session.add(row)
    session.flush()
    return row


def _author(by_llm: bool) -> RevisionAuthor:
    return RevisionAuthor.LLM if by_llm else RevisionAuthor.SYSTEM


def _today(ctx: GenerationContext) -> date:
    return ctx.now().date()


# --- Intents -------------------------------------------------------------------------------


def _initial(ctx: GenerationContext, run_id: uuid.UUID, token: uuid.UUID) -> None:
    with ctx.factory() as session:
        run = runs.hold(session, run_id, token)
        event = runs.check_authorization(session, run)
        snapshot = build_snapshot(session, event.assessment_id)

    draft = drafting.draft(snapshot, {}, _today(ctx), ctx.provider, ctx.no_llm_reason)
    ctx.progress()
    facts = _facts(snapshot)
    checked = {
        DocumentKind.RESUME: check(ctx, draft.resume, facts, {}, {}),
        DocumentKind.COVER_LETTER: check(ctx, draft.letter, facts, {}, {}),
    }
    documents: dict[DocumentKind, Document] = {
        DocumentKind.RESUME: draft.resume,
        DocumentKind.COVER_LETTER: draft.letter,
    }

    with ctx.factory.begin() as session:
        run = runs.hold(session, run_id, token)
        runs.check_authorization(session, run)
        inp = _store_input(session, snapshot)
        bundle = DocumentBundle(
            candidate_id=snapshot.candidate.id,
            opportunity_id=snapshot.job.id,
            review_id=run.review_id,
            authorization_event_id=run.authorization_event_id,
            input_id=inp.id,
            status=BundleStatus.NEEDS_INPUT,
            drafting_mode=draft.mode,
            drafting_notes=[n for n in draft.notes if n],
            version=1,
        )
        session.add(bundle)
        session.flush()
        service.add_style(session, bundle, StyleSettings())
        semantic_failed = False
        for kind, document in documents.items():
            revision = service.add_revision(
                session,
                bundle,
                document,
                _author(draft.by_llm[kind]),
                run_id=run.id,
                note="Drafted",
            )
            service.record_verification(session, revision, checked[kind].result)
            semantic_failed |= checked[kind].semantic_error is not None
        service.add_questions(session, bundle, draft.questions)
        service.refresh_status(session, bundle)
        if semantic_failed:
            service.queue_verify(session, bundle, "semantic_retry")
        run.bundle_id = bundle.id
        run.input_id = inp.id
        runs.complete(run, ctx.now())
    logger.info(
        "documents drafted",
        extra={"run_id": str(run_id), "mode": draft.mode.value, "questions": len(draft.questions)},
    )


def _open_bundle_for(session: Session, run: GenerationRun) -> DocumentBundle:
    if run.bundle_id is None:  # pragma: no cover - only initial runs have no bundle
        raise runs.StaleAuthorizationError(str(run.id))
    bundle = session.get(DocumentBundle, run.bundle_id)
    if bundle is None or bundle.status in CLOSED_BUNDLE_STATUSES:
        raise runs.StaleAuthorizationError(str(run.id))
    return bundle


def _regenerate(ctx: GenerationContext, run_id: uuid.UUID, token: uuid.UUID) -> None:
    with ctx.factory() as session:
        run = runs.hold(session, run_id, token)
        event = runs.check_authorization(session, run)
        bundle = _open_bundle_for(session, run)
        kind = DocumentKind(run.parameters["kind"])
        section = str(run.parameters["section"])
        snapshot = build_snapshot(session, event.assessment_id)
        statements = service.statements(session, bundle.id)
        current = service.current_revision(session, bundle.id, kind)
        if current is None:  # pragma: no cover - bundles are created with both documents
            raise RevisionChangedError(str(run_id))
        base_seq, base = current.seq, parse_document(current.content)
        verdicts = service.known_verdicts(session, bundle.id)
        bundle_id = bundle.id

    draft = drafting.draft(
        snapshot, statements, _today(ctx), ctx.provider, ctx.no_llm_reason, frozenset({kind})
    )
    ctx.progress()
    fresh: Document = draft.resume if kind is DocumentKind.RESUME else draft.letter
    document = service.replace_section(base, fresh, section)
    checked = check(ctx, document, _facts(snapshot), statements, verdicts)

    with ctx.factory.begin() as session:
        run = runs.hold(session, run_id, token)
        runs.check_authorization(session, run)
        bundle = service.lock_bundle(session, bundle_id)
        if bundle.status in CLOSED_BUNDLE_STATUSES:
            raise runs.StaleAuthorizationError(str(run_id))
        latest = service.current_revision(session, bundle.id, kind)
        if latest is None or latest.seq != base_seq:
            raise RevisionChangedError(str(run_id))
        inp = _store_input(session, snapshot)
        revision = service.add_revision(
            session,
            bundle,
            document,
            _author(draft.by_llm[kind]),
            run_id=run.id,
            note=f"Regenerated {section}",
        )
        service.record_verification(session, revision, checked.result)
        service.add_questions(session, bundle, [q for q in draft.questions if q.kind is kind])
        if draft.mode is DraftingMode.DETERMINISTIC and bundle.drafting_mode is DraftingMode.LLM:
            bundle.drafting_notes = [*bundle.drafting_notes, *[n for n in draft.notes if n]][-10:]
        service.refresh_status(session, bundle)
        if checked.semantic_error:
            service.queue_verify(session, bundle, "semantic_retry")
        run.input_id = inp.id
        runs.complete(run, ctx.now())


def _verify_current(ctx: GenerationContext, run_id: uuid.UUID, token: uuid.UUID) -> None:
    with ctx.factory() as session:
        run = runs.hold(session, run_id, token)
        runs.check_authorization(session, run)
        bundle = _open_bundle_for(session, run)
        facts = service.current_facts(session, bundle.candidate_id)
        statements = service.statements(session, bundle.id)
        verdicts = service.known_verdicts(session, bundle.id)
        targets = {
            kind: (rev.id, parse_document(rev.content))
            for kind, rev in service.current_revisions(session, bundle.id).items()
        }
        bundle_id = bundle.id

    results = {
        kind: (revision_id, check(ctx, doc, facts, statements, verdicts, raise_on_llm_error=True))
        for kind, (revision_id, doc) in targets.items()
    }

    with ctx.factory.begin() as session:
        run = runs.hold(session, run_id, token)
        runs.check_authorization(session, run)
        bundle = service.lock_bundle(session, bundle_id)
        if bundle.status in CLOSED_BUNDLE_STATUSES:
            raise runs.StaleAuthorizationError(str(run_id))
        current = service.current_revisions(session, bundle.id)
        for kind, (revision_id, checked) in results.items():
            # A newer revision has its own verify run queued; skip the superseded one.
            if kind in current and current[kind].id == revision_id:
                service.record_verification(session, current[kind], checked.result)
        service.refresh_status(session, bundle, keep_approval=True)
        runs.complete(run, ctx.now())
