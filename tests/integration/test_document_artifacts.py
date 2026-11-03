"""Rendered files over a real database: queueing, leases, QA, notifications, style changes,
downloads, the orphan sweep, and approval of exact revisions and files."""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from next_role import renderer
from next_role.documents import artifacts, display, qa
from next_role.documents.models import ArtifactFormat, DocumentArtifact, GenerationRun
from next_role.documents.storage import LocalArtifactStore
from next_role.documents.workflow import ArtifactStatus
from next_role.notifications.models import NotificationEventType, NotificationOutbox
from tests.integration.test_document_generation import _ctx, _drain
from tests.integration.test_document_runs import _approve, _decide
from tests.integration.test_review_workflow import _assess, _candidate, _job, _review


class FakePdf:
    """Stands in for Chromium: a PDF-looking file whose 'text' is the display model."""

    version = "fake-pdf"

    def __init__(self, fail: Exception | None = None, passed: bool = True) -> None:
        self.fail = fail
        self.passed = passed

    def render(self, document: display.DisplayDocument, tokens: dict[str, Any]) -> bytes:
        if self.fail:
            raise self.fail
        return b"%PDF-fake\n" + "\n".join(document.texts()).encode()

    def check(
        self, document: display.DisplayDocument, data: bytes, tokens: dict[str, Any]
    ) -> qa.QAResult:
        missing, reordered = qa.check_text(document.texts(), data.decode(errors="ignore"))
        return qa.QAResult(
            passed=self.passed and not missing and not reordered,
            missing=missing,
            page_count=1,
        )


@pytest.fixture
def store(client: TestClient, tmp_path: Path) -> LocalArtifactStore:
    store = LocalArtifactStore(tmp_path / "artifacts")
    client.app.state.artifact_store = store  # type: ignore[attr-defined]
    return store


def _rctx(
    factory: sessionmaker[Session], store: LocalArtifactStore, pdf: Any = None
) -> renderer.RenderContext:
    renderers: dict[ArtifactFormat, Any] = {ArtifactFormat.DOCX: renderer.DocxRenderer()}
    renderers[ArtifactFormat.PDF] = pdf or FakePdf()
    return renderer.RenderContext(
        factory=factory,
        store=store,
        renderers=renderers,
        lease=timedelta(minutes=2),
        max_attempts=2,
        now=lambda: datetime.now(UTC),
    )


def _render_all(ctx: renderer.RenderContext) -> int:
    done = 0
    while renderer.render_next(ctx):
        done += 1
    return done


@pytest.fixture
def review(client: TestClient, session: Session) -> dict[str, Any]:
    candidate_id = _candidate(client)
    job_id = _job(client)
    _assess(client, job_id, candidate_id)
    return {"id": _review(session, candidate_id, job_id).id, "candidate_id": candidate_id}


def _bundle(client: TestClient, review: dict[str, Any]) -> dict[str, Any]:
    [summary] = client.get(f"/api/v1/reviews/{review['id']}/documents").json()["bundles"]
    detail: dict[str, Any] = client.get(f"/api/v1/bundles/{summary['id']}").json()
    return detail


@pytest.fixture
def drafted(
    client: TestClient, session_factory: sessionmaker[Session], review: dict[str, Any]
) -> dict[str, Any]:
    _approve(session_factory, review["id"])
    _drain(_ctx(session_factory))
    return _bundle(client, review)


def _outbox(factory: sessionmaker[Session]) -> list[tuple[str, str]]:
    with factory() as session:
        return [
            (r.event_type.value, r.event_key)
            for r in session.scalars(
                select(NotificationOutbox).order_by(NotificationOutbox.created_at)
            )
            if r.event_type.value.startswith("documents.")
        ]


def test_drafting_queues_four_files_and_the_renderer_completes_them(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
) -> None:
    files = drafted["artifacts"]
    assert sorted((a["format"], a["status"]) for a in files) == [
        ("docx", "pending"),
        ("docx", "pending"),
        ("pdf", "pending"),
        ("pdf", "pending"),
    ]
    # A renderer without PDF support leaves the PDFs for another one; nothing is notified.
    docx_only = _rctx(session_factory, store)
    docx_only.renderers.pop(ArtifactFormat.PDF)
    assert _render_all(docx_only) == 2
    assert _outbox(session_factory) == []

    assert _render_all(_rctx(session_factory, store)) == 2
    detail = _bundle(client, {"id": drafted["review_id"]})
    for a in detail["artifacts"]:
        assert a["status"] == "ready", a
        assert len(a["sha256"]) == 64 and a["qa"]["passed"] is True
        assert a["qa"]["missing"] == 0
    assert len(store.keys()) == 4
    # Every file settled with input still needed: exactly one notification, about input.
    assert _outbox(session_factory) == [
        ("documents.needs_input", f"documents.needs_input:{drafted['id']}")
    ]
    _render_all(_rctx(session_factory, store))
    assert len(_outbox(session_factory)) == 1


def test_downloads_are_hash_checked_and_named_as_drafts(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
) -> None:
    pending = drafted["artifacts"][0]
    assert client.get(f"/api/v1/artifacts/{pending['id']}/download").status_code == 409
    _render_all(_rctx(session_factory, store))
    detail = _bundle(client, {"id": drafted["review_id"]})
    docx = next(a for a in detail["artifacts"] if a["format"] == "docx")
    response = client.get(f"/api/v1/artifacts/{docx['id']}/download")
    assert response.status_code == 200
    assert response.content.startswith(b"PK")
    disposition = response.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="alex-example-')
    assert "-DRAFT.docx" in disposition
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"

    with session_factory() as session:
        key = session.get(DocumentArtifact, uuid.UUID(docx["id"])).storage_key  # type: ignore[union-attr]
    (store.root / key).write_bytes(b"tampered")
    assert client.get(f"/api/v1/artifacts/{docx['id']}/download").status_code == 409
    unauthenticated = client.get(
        f"/api/v1/artifacts/{docx['id']}/download", headers={"X-API-Key": "wrong"}
    )
    assert unauthenticated.status_code == 401


def test_edits_and_style_changes_supersede_old_files(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
) -> None:
    _render_all(_rctx(session_factory, store))
    detail = _bundle(client, {"id": drafted["review_id"]})
    old_ids = {a["id"] for a in detail["artifacts"]}

    response = client.post(
        f"/api/v1/bundles/{drafted['id']}/style",
        json={
            "settings": {"template_id": "modern", "accent": "teal"},
            "expected_version": detail["version"],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["tokens"]["accent"] == "#0f766e"
    same = client.post(
        f"/api/v1/bundles/{drafted['id']}/style",
        json={
            "settings": {"template_id": "modern", "accent": "teal"},
            "expected_version": detail["version"] + 1,
        },
    )
    assert same.status_code == 422

    detail = _bundle(client, {"id": drafted["review_id"]})
    assert {a["id"] for a in detail["artifacts"]}.isdisjoint(old_ids)
    assert detail["resume"]["seq"] == 1  # content unchanged
    with session_factory() as session:
        statuses = {
            a.status for a in session.scalars(select(DocumentArtifact)) if str(a.id) in old_ids
        }
        assert statuses == {ArtifactStatus.SUPERSEDED}
        # A style change never calls the drafting pipeline.
        assert all(
            r.intent.value != "regenerate_section" for r in session.scalars(select(GenerationRun))
        )
    assert _render_all(_rctx(session_factory, store)) == 4
    # Superseded files remain stored for history; nothing is orphaned.
    with session_factory() as session:
        assert artifacts.sweep_orphans(session, store, datetime.now(UTC)) == 0


def test_a_file_superseded_while_rendering_is_discarded(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
) -> None:
    class Interrupting(FakePdf):
        def render(self, document: Any, tokens: dict[str, Any]) -> bytes:
            # The operator changes the style while the file is being rendered.
            detail = _bundle(client, {"id": drafted["review_id"]})
            client.post(
                f"/api/v1/bundles/{drafted['id']}/style",
                json={"settings": {"density": "compact"}, "expected_version": detail["version"]},
            )
            return super().render(document, tokens)

    ctx = _rctx(session_factory, store, Interrupting())
    ctx.renderers.pop(ArtifactFormat.DOCX)
    assert renderer.render_next(ctx)
    assert store.keys() == []


def test_render_failures_retry_then_fail_and_notify(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
) -> None:
    ctx = _rctx(session_factory, store, FakePdf(fail=RuntimeError("chromium crashed")))
    _render_all(ctx)
    with session_factory() as session:
        pdfs = session.scalars(
            select(DocumentArtifact).where(DocumentArtifact.format == ArtifactFormat.PDF)
        ).all()
        assert {(a.status, a.last_error, a.attempts) for a in pdfs} == {
            (ArtifactStatus.PENDING, "RuntimeError", 1)
        }
    later = renderer.RenderContext(
        **{**ctx.__dict__, "now": lambda: datetime.now(UTC) + timedelta(minutes=5)}
    )
    _render_all(later)
    detail = _bundle(client, {"id": drafted["review_id"]})
    failed = [a for a in detail["artifacts"] if a["status"] == "failed"]
    assert len(failed) == 2
    assert _outbox(session_factory) == [("documents.failed", f"documents.failed:{drafted['id']}")]

    retried = client.post(f"/api/v1/artifacts/{failed[0]['id']}/retry")
    assert retried.status_code == 202 and retried.json()["status"] == "pending"
    assert client.post(f"/api/v1/artifacts/{failed[0]['id']}/retry").status_code == 409


def test_files_that_fail_qa_are_never_marked_ready(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
) -> None:
    _render_all(_rctx(session_factory, store, FakePdf(passed=False)))
    detail = _bundle(client, {"id": drafted["review_id"]})
    pdfs = [a for a in detail["artifacts"] if a["format"] == "pdf"]
    assert {(a["status"], a["last_error"]) for a in pdfs} == {("failed", "qa_failed")}
    assert all(a["sha256"] is None for a in pdfs)


def test_orphaned_files_are_swept(
    session_factory: sessionmaker[Session], store: LocalArtifactStore
) -> None:
    key = f"{uuid.uuid4()}/{uuid.uuid4()}-1.pdf"
    store.put(key, b"%PDF orphan")
    with session_factory() as session:
        assert artifacts.sweep_orphans(session, store, datetime.now(UTC)) == 0  # too recent
        old = datetime.now(UTC).timestamp() - 2 * 3600
        import os

        os.utime(store.root / key, (old, old))
        assert artifacts.sweep_orphans(session, store, datetime.now(UTC)) == 1
    assert store.keys() == []


def _make_approvable(
    client: TestClient,
    factory: sessionmaker[Session],
    store: LocalArtifactStore,
    bundle: dict[str, Any],
    candidate_id: str,
) -> dict[str, Any]:
    """Confirm the cited facts, resolve required questions, re-check, and render."""
    for kind in ("resume", "cover_letter"):
        for claim in bundle[kind]["verification"]["claims"]:
            for fact_id in claim["evidence"]:
                client.post(f"/api/v1/candidates/{candidate_id}/facts/{fact_id}/verify")
    version = bundle["version"]
    for q in bundle["clarifications"]:
        if q["material"]:
            client.post(
                f"/api/v1/clarifications/{q['id']}/dismiss",
                json={"reason": "Not applicable", "expected_version": version},
            )
            version += 1
    client.post(f"/api/v1/bundles/{bundle['id']}/verifications")
    _drain(_ctx(factory))
    _render_all(_rctx(factory, store))
    return _bundle(client, {"id": bundle["review_id"]})


def _approval(detail: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    body = {
        "expected_version": detail["version"],
        "resume_revision_id": detail["resume"]["id"],
        "cover_letter_revision_id": detail["cover_letter"]["id"],
        "style_id": detail["style"]["id"],
        "artifacts": {a["id"]: a["sha256"] for a in detail["artifacts"]},
    }
    body.update(overrides)
    return body


def test_approval_binds_exact_revisions_and_files(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
    review: dict[str, Any],
) -> None:
    url = f"/api/v1/bundles/{drafted['id']}/approval"
    # Not yet: claims rest on unconfirmed facts and files are not rendered.
    assert client.post(url, json=_approval(drafted)).status_code in (409, 422)

    detail = _make_approvable(client, session_factory, store, drafted, review["candidate_id"])
    assert detail["status"] == "ready_for_review", detail["resume"]["verification"]

    tampered = _approval(detail)
    first = next(iter(tampered["artifacts"]))
    tampered["artifacts"][first] = "0" * 64
    response = client.post(url, json=tampered)
    assert response.status_code == 409 and "files changed" in response.json()["detail"]
    assert client.post(url, json=_approval(detail, expected_version=0)).status_code == 409

    response = client.post(url, json=_approval(detail))
    assert response.status_code == 201, response.text
    approval = response.json()
    assert len(approval["artifacts"]) == 4
    detail = _bundle(client, review)
    assert (detail["status"], detail["approval_current"]) == ("approved", True)
    assert client.post(url, json=_approval(detail)).status_code == 409  # already approved

    docx = next(a for a in detail["artifacts"] if a["format"] == "docx")
    name = client.get(f"/api/v1/artifacts/{docx['id']}/download").headers["content-disposition"]
    assert "DRAFT" not in name

    # Any change after approval returns the bundle to review; the approval stays in history.
    client.post(
        f"/api/v1/bundles/{drafted['id']}/style",
        json={"settings": {"page_size": "a4"}, "expected_version": detail["version"]},
    )
    detail = _bundle(client, review)
    assert detail["status"] == "ready_for_review"
    assert detail["approval_current"] is False and detail["approval"]["id"] == approval["id"]
    name = client.get(f"/api/v1/artifacts/{docx['id']}/download")
    assert name.status_code == 409  # superseded by the A4 files


def test_approval_is_refused_when_a_cited_fact_was_retired(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
    review: dict[str, Any],
) -> None:
    detail = _make_approvable(client, session_factory, store, drafted, review["candidate_id"])
    cited = next(
        fid
        for c in detail["resume"]["verification"]["claims"]
        if not c["claim_id"].startswith("item-")
        for fid in c["evidence"]
    )
    client.delete(f"/api/v1/candidates/{review['candidate_id']}/facts/{cited}")
    response = client.post(f"/api/v1/bundles/{drafted['id']}/approval", json=_approval(detail))
    assert response.status_code == 409
    assert "retired" in response.json()["detail"]


def test_approval_is_refused_after_the_review_is_reopened(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
    review: dict[str, Any],
) -> None:
    detail = _make_approvable(client, session_factory, store, drafted, review["candidate_id"])
    _decide(session_factory, review["id"], action="reopen", reason="Not now")
    response = client.post(f"/api/v1/bundles/{drafted['id']}/approval", json=_approval(detail))
    assert response.status_code == 409


def test_failed_initial_runs_notify(
    client: TestClient, session_factory: sessionmaker[Session], review: dict[str, Any]
) -> None:
    _approve(session_factory, review["id"])
    with session_factory() as session:
        from next_role.review.models import OpportunityReview

        job_id = str(session.get(OpportunityReview, review["id"]).opportunity_id)  # type: ignore[union-attr]
    _assess(client, job_id, review["candidate_id"], as_of="2026-10-09")
    _drain(_ctx(session_factory))
    [(event, key)] = _outbox(session_factory)
    assert event == NotificationEventType.DOCUMENTS_FAILED.value
    assert key.startswith("documents.failed:approval:")


def test_preview_lays_out_current_and_unsaved_content(
    client: TestClient, drafted: dict[str, Any]
) -> None:
    assert drafted["preview"]["resume"]["header"]["name"] == "Alex Example"
    assert drafted["preview"]["cover_letter"]["salutation"] == "Dear Hiring Team,"

    url = f"/api/v1/bundles/{drafted['id']}/preview"
    content = drafted["resume"]["content"]
    content["header"]["name"] = "Someone Else"  # ignored: the header comes from the profile
    skills = next(s for s in content["sections"] if s["type"] == "skills")
    skills["title"] = "Toolbox"
    response = client.post(
        url,
        json={
            "kind": "resume",
            "content": content,
            "settings": {"template_id": "modern-deedy", "hidden_sections": ["education"]},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["display"]["header"]["name"] == "Alex Example"
    titles = [s["title"] for s in body["display"]["sections"]]
    assert "Toolbox" in titles and "Education" not in titles
    assert body["tokens"]["template_id"] == "modern-deedy"
    bad = client.post(url, json={"kind": "resume", "content": {"sections": "nope"}})
    assert bad.status_code == 422


def test_a_late_attempt_never_deletes_the_file_a_newer_attempt_published(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
) -> None:
    ctx = _rctx(session_factory, store)
    ctx.renderers.pop(ArtifactFormat.PDF)
    later = renderer.RenderContext(
        **{**ctx.__dict__, "now": lambda: datetime.now(UTC) + timedelta(minutes=10)}
    )

    class Slow(renderer.DocxRenderer):
        def render(self, document: Any, tokens: dict[str, Any]) -> bytes:
            # While this attempt is still rendering, its lease expires and another renderer
            # takes the file over and publishes it.
            self_ctx = renderer.RenderContext(
                **{**later.__dict__, "renderers": {ArtifactFormat.DOCX: renderer.DocxRenderer()}}
            )
            assert renderer.render_next(self_ctx)
            return super().render(document, tokens)

    # Only one DOCX is due, so the takeover is of the very file being rendered.
    with session_factory.begin() as s:
        rows = s.scalars(
            select(DocumentArtifact).where(DocumentArtifact.format == ArtifactFormat.DOCX)
        ).all()
        rows[1].next_attempt_at = datetime.now(UTC) + timedelta(days=1)
    slow = renderer.RenderContext(**{**ctx.__dict__, "renderers": {ArtifactFormat.DOCX: Slow()}})
    assert renderer.render_next(slow)
    detail = _bundle(client, {"id": drafted["review_id"]})
    ready = [a for a in detail["artifacts"] if a["status"] == "ready"]
    assert len(ready) == 1
    response = client.get(f"/api/v1/artifacts/{ready[0]['id']}/download")
    assert response.status_code == 200  # the newer file survived the late attempt


def test_a_damaged_ready_file_can_be_rendered_again(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
) -> None:
    _render_all(_rctx(session_factory, store))
    docx = next(
        a
        for a in _bundle(client, {"id": drafted["review_id"]})["artifacts"]
        if a["format"] == "docx"
    )
    assert client.post(f"/api/v1/artifacts/{docx['id']}/retry").status_code == 409  # intact
    for key in list(store.keys()):
        if docx["id"] in key:
            store.delete(key)
    assert client.post(f"/api/v1/artifacts/{docx['id']}/retry").status_code == 202
    assert _render_all(_rctx(session_factory, store)) == 1
    assert client.get(f"/api/v1/artifacts/{docx['id']}/download").status_code == 200


def test_rendering_an_approved_file_again_returns_the_bundle_to_review(
    client: TestClient,
    session_factory: sessionmaker[Session],
    store: LocalArtifactStore,
    drafted: dict[str, Any],
    review: dict[str, Any],
) -> None:
    url = f"/api/v1/bundles/{drafted['id']}/approval"
    detail = _make_approvable(client, session_factory, store, drafted, review["candidate_id"])
    assert client.post(url, json=_approval(detail)).status_code == 201
    docx = next(a for a in _bundle(client, review)["artifacts"] if a["format"] == "docx")
    for key in list(store.keys()):
        if docx["id"] in key:
            store.delete(key)
    assert client.post(f"/api/v1/artifacts/{docx['id']}/retry").status_code == 202

    detail = _bundle(client, review)
    assert (detail["status"], detail["approval_current"]) == ("ready_for_review", False)
    assert _render_all(_rctx(session_factory, store)) == 1
    name = client.get(f"/api/v1/artifacts/{docx['id']}/download").headers["content-disposition"]
    assert "DRAFT" in name
    detail = _bundle(client, review)
    assert client.post(url, json=_approval(detail)).status_code == 201  # approvable again


def test_generation_failures_notify_once_per_bundle(
    client: TestClient,
    session_factory: sessionmaker[Session],
    drafted: dict[str, Any],
) -> None:
    from next_role.documents import generation

    class Broken:
        name = "broken"
        model = "broken-model"
        external = False

        def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> Any:
            raise RuntimeError("boom")

    for n in range(2):
        detail = _bundle(client, {"id": drafted["review_id"]})
        client.post(
            f"/api/v1/bundles/{drafted['id']}/regenerations",
            json={
                "kind": "resume",
                "section": "skills",
                "expected_version": detail["version"],
                "idempotency_key": f"key-0000000{n}",
            },
        )
        ctx = _ctx(session_factory, Broken())  # type: ignore[arg-type]
        for attempt in range(3):
            later = generation.GenerationContext(
                **{**ctx.__dict__, "now": lambda a=attempt: datetime.now(UTC) + timedelta(hours=a)}
            )
            _drain(later)
    assert _outbox(session_factory) == [("documents.failed", f"documents.failed:{drafted['id']}")]
