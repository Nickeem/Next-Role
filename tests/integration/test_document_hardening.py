"""Restart safety and access control for documents (Phase 3 hardening)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from next_role import renderer
from next_role.documents import artifacts, generation, runs
from next_role.documents.models import DocumentArtifact, DocumentBundle, GenerationRun
from next_role.documents.storage import LocalArtifactStore
from next_role.documents.workflow import ArtifactStatus, RunStatus
from tests.integration.test_document_artifacts import _rctx, _render_all
from tests.integration.test_document_generation import _ctx, _drain
from tests.integration.test_document_runs import _approve
from tests.integration.test_review_workflow import _assess, _candidate, _job, _review


def _setup(client: TestClient, session: Session) -> Any:
    candidate_id = _candidate(client)
    job_id = _job(client)
    _assess(client, job_id, candidate_id)
    return _review(session, candidate_id, job_id).id


def test_a_worker_that_dies_mid_draft_is_taken_over_once(
    client: TestClient, session: Session, session_factory: sessionmaker[Session]
) -> None:
    review_id = _setup(client, session)
    _approve(session_factory, review_id)
    ctx = _ctx(session_factory)
    # The first worker claims the run and dies without writing anything.
    with session_factory.begin() as s:
        assert runs.claim_next(s, datetime.now(UTC), ctx.lease, ctx.max_attempts) is not None
    assert _drain(ctx) == 0  # its lease is still live

    later = generation.GenerationContext(
        **{**ctx.__dict__, "now": lambda: datetime.now(UTC) + ctx.lease + timedelta(seconds=5)}
    )
    assert _drain(later) == 1
    with session_factory() as s:
        [run] = s.scalars(select(GenerationRun)).all()
        assert (run.status, run.attempts) == (RunStatus.COMPLETED, 2)
        assert len(s.scalars(select(DocumentBundle)).all()) == 1


def test_a_renderer_that_dies_mid_render_leaves_no_ready_row_and_recovers(
    client: TestClient,
    session: Session,
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    review_id = _setup(client, session)
    _approve(session_factory, review_id)
    _drain(_ctx(session_factory))
    store = LocalArtifactStore(tmp_path)
    ctx = _rctx(session_factory, store)
    with session_factory.begin() as s:
        claimed = artifacts.claim_next(s, datetime.now(UTC), ctx.lease, ctx.max_attempts)
        assert claimed is not None
        claimed_id = claimed.id
    # A partial file without a ready row is what a crash after writing would leave.
    store.put(f"{claimed.bundle_id}/{claimed_id}.{claimed.format.value}", b"partial")
    assert _render_all(ctx) == 3  # the other files render; the claimed one waits
    with session_factory() as s:
        row = s.get(DocumentArtifact, claimed_id)
        assert row is not None and row.status is ArtifactStatus.RENDERING

    later = renderer.RenderContext(
        **{**ctx.__dict__, "now": lambda: datetime.now(UTC) + ctx.lease + timedelta(seconds=5)}
    )
    assert _render_all(later) == 1
    with session_factory() as s:
        row = s.get(DocumentArtifact, claimed_id)
        assert row is not None and row.status is ArtifactStatus.READY and row.attempts == 2
        data = store.get(row.storage_key)  # type: ignore[arg-type]
        assert data != b"partial"


def test_every_document_route_requires_authentication(
    client: TestClient, session: Session, session_factory: sessionmaker[Session]
) -> None:
    review_id = _setup(client, session)
    _approve(session_factory, review_id)
    _drain(_ctx(session_factory))
    [bundle] = client.get(f"/api/v1/reviews/{review_id}/documents").json()["bundles"]
    detail = client.get(f"/api/v1/bundles/{bundle['id']}").json()
    some = "00000000-0000-4000-8000-000000000000"
    requests = [
        ("GET", f"/api/v1/reviews/{review_id}/documents"),
        ("GET", "/api/v1/templates"),
        ("GET", f"/api/v1/bundles/{bundle['id']}"),
        ("GET", f"/api/v1/bundles/{bundle['id']}/revisions"),
        ("GET", f"/api/v1/revisions/{detail['resume']['id']}"),
        ("POST", f"/api/v1/bundles/{bundle['id']}/revisions"),
        ("POST", f"/api/v1/bundles/{bundle['id']}/regenerations"),
        ("POST", f"/api/v1/bundles/{bundle['id']}/verifications"),
        ("POST", f"/api/v1/bundles/{bundle['id']}/preview"),
        ("POST", f"/api/v1/bundles/{bundle['id']}/style"),
        ("POST", f"/api/v1/bundles/{bundle['id']}/approval"),
        ("POST", f"/api/v1/bundles/{bundle['id']}/claims/x/confirm-as-fact"),
        ("POST", f"/api/v1/clarifications/{some}/answer"),
        ("POST", f"/api/v1/clarifications/{some}/dismiss"),
        ("POST", f"/api/v1/artifacts/{detail['artifacts'][0]['id']}/retry"),
        ("GET", f"/api/v1/artifacts/{detail['artifacts'][0]['id']}/download"),
    ]
    anonymous = TestClient(client.app)
    for method, path in requests:
        assert anonymous.request(method, path, json={}).status_code == 401, (method, path)
    assert client.get(f"/api/v1/bundles/{some}").status_code == 404
    assert client.get(f"/api/v1/artifacts/{some}/download").status_code == 404
