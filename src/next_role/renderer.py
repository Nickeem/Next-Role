"""Renderer process: turns pending artifact rows into checked PDF and DOCX files (ADR 0007).

Run with ``python -m next_role.renderer`` (``--once`` for one pass, ``--check`` for the
healthcheck). It claims rows from PostgreSQL with a lease, renders from the display model,
checks the file, writes it atomically to the artifact store, and only then marks the row
ready. It renders DOCX itself; PDF needs the Playwright/Chromium renderer, which only the
``renderer`` image provides, so without it PDF rows are left for a renderer that has it.
"""

import argparse
import importlib
import logging
import signal
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from next_role.config import Settings, get_settings
from next_role.db import get_engine, get_sessionmaker
from next_role.documents import artifacts, display, docx_renderer, qa, templates
from next_role.documents.models import ArtifactFormat, DocumentRevision, DocumentStyle
from next_role.documents.schemas import StyleSettings, parse_document
from next_role.documents.storage import ArtifactStore, LocalArtifactStore
from next_role.documents.workflow import RetryPolicy
from next_role.logging_config import configure_logging

logger = logging.getLogger(__name__)

HEARTBEAT = Path(tempfile.gettempdir()) / "next-role-renderer.heartbeat"
SWEEP_INTERVAL = timedelta(minutes=30)


class Renderer(Protocol):
    version: str

    def render(self, document: display.DisplayDocument, tokens: dict[str, Any]) -> bytes: ...

    def check(
        self, document: display.DisplayDocument, data: bytes, tokens: dict[str, Any]
    ) -> qa.QAResult: ...


class DocxRenderer:
    version = docx_renderer.RENDERER_VERSION

    def render(self, document: display.DisplayDocument, tokens: dict[str, Any]) -> bytes:
        return docx_renderer.render(document, tokens)

    def check(
        self, document: display.DisplayDocument, data: bytes, tokens: dict[str, Any]
    ) -> qa.QAResult:
        return qa.check_docx(document, data, tokens)


@dataclass(frozen=True)
class RenderContext:
    factory: sessionmaker[Session]
    store: ArtifactStore
    renderers: dict[ArtifactFormat, Renderer]
    lease: timedelta
    max_attempts: int
    now: Callable[[], datetime]

    @property
    def retry(self) -> RetryPolicy:
        return RetryPolicy(max_attempts=self.max_attempts, base_delay=timedelta(seconds=15))


def _now() -> datetime:
    return datetime.now(UTC)


def render_next(ctx: RenderContext) -> bool:
    """Claim and render one due file. Returns False when there was nothing to do."""
    with ctx.factory.begin() as session:
        artifact = artifacts.claim_next(
            session, ctx.now(), ctx.lease, ctx.max_attempts, frozenset(ctx.renderers)
        )
        if artifact is None:
            return False
        artifact_id, token, fmt = artifact.id, artifact.lease_token, artifact.format
        key = artifacts.storage_key(artifact)
        revision = session.get(DocumentRevision, artifact.revision_id)
        style = session.get(DocumentStyle, artifact.style_id)
        if revision is None or style is None:  # pragma: no cover - foreign keys
            raise RuntimeError("artifact without revision or style")
        content, settings_data = revision.content, style.settings
    assert token is not None  # noqa: S101 - claim_next always sets it

    renderer = ctx.renderers[fmt]
    started = time.perf_counter()
    try:
        settings = StyleSettings.model_validate(settings_data)
        tokens = templates.resolve_tokens(settings)
        view = display.build(parse_document(content), settings)
        data = renderer.render(view, tokens)
        result = renderer.check(view, data, tokens)
    except Exception as exc:
        logger.exception("render failed", extra={"artifact_id": str(artifact_id)})
        _fail(ctx, artifact_id, token, type(exc).__name__, retryable=True)
        return True
    if not result.passed:
        logger.error(
            "rendered file failed its checks",
            extra={"artifact_id": str(artifact_id), **result.as_dict()},
        )
        _fail(ctx, artifact_id, token, "qa_failed", retryable=False, qa_result=result.as_dict())
        return True

    digest = ctx.store.put(key, data)
    try:
        with ctx.factory.begin() as session:
            row = artifacts.hold(session, artifact_id, token)
            artifacts.complete(
                session,
                row,
                key=key,
                sha256=digest,
                size=len(data),
                page_count=result.page_count,
                renderer_version=renderer.version,
                qa=result.as_dict(),
                now=ctx.now(),
            )
    except artifacts.LeaseLostError:
        # Superseded or taken over meanwhile: the file belongs to no ready row.
        ctx.store.delete(key)
        logger.info("rendered file discarded", extra={"artifact_id": str(artifact_id)})
        return True
    logger.info(
        "file rendered",
        extra={
            "artifact_id": str(artifact_id),
            "format": fmt.value,
            "bytes": len(data),
            "pages": result.page_count,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    )
    return True


def _fail(
    ctx: RenderContext,
    artifact_id: uuid.UUID,
    token: uuid.UUID,
    error: str,
    *,
    retryable: bool,
    qa_result: dict[str, object] | None = None,
) -> None:
    try:
        with ctx.factory.begin() as session:
            row = artifacts.hold(session, artifact_id, token)
            artifacts.fail(session, row, error, ctx.now(), ctx.retry, retryable, qa_result)
    except artifacts.LeaseLostError:
        return


def available_renderers(settings: Settings) -> dict[ArtifactFormat, Renderer]:
    renderers: dict[ArtifactFormat, Renderer] = {ArtifactFormat.DOCX: DocxRenderer()}
    try:
        # Imported dynamically: Playwright is only installed in the renderer image.
        pdf = importlib.import_module("next_role.documents.pdf_renderer")
    except ImportError:
        logger.warning("Playwright is not installed; PDF files are left for another renderer")
    else:
        renderers[ArtifactFormat.PDF] = pdf.PdfRenderer(
            settings.render_timeout_seconds, settings.print_bundle_dir
        )
    return renderers


def touch_heartbeat() -> None:
    HEARTBEAT.touch()


def healthy(max_age_seconds: float) -> bool:
    try:
        return time.time() - HEARTBEAT.stat().st_mtime <= max_age_seconds
    except FileNotFoundError:
        return False


def health_threshold(settings: Settings) -> float:
    return max(60.0, 3 * settings.worker_poll_seconds, 2 * settings.render_timeout_seconds + 30)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m next_role.renderer")
    parser.add_argument("--once", action="store_true", help="render what is due and exit")
    parser.add_argument("--check", action="store_true", help="exit 0 if the loop is alive")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.check:
        return 0 if healthy(health_threshold(settings)) else 1

    configure_logging(settings.log_level, settings.log_json)
    ctx = RenderContext(
        factory=get_sessionmaker(get_engine(settings.database_url)),
        store=LocalArtifactStore(settings.artifact_dir),
        renderers=available_renderers(settings),
        lease=timedelta(seconds=max(60.0, 4 * settings.render_timeout_seconds)),
        max_attempts=settings.generation_max_attempts,
        now=_now,
    )
    stop = threading.Event()

    def _stop(signum: int, _: FrameType | None) -> None:
        logger.info("renderer stopping", extra={"signal": signum})
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    logger.info("renderer started", extra={"formats": sorted(f.value for f in ctx.renderers)})

    last_sweep = datetime.min.replace(tzinfo=UTC)
    try:
        while not stop.is_set():
            try:
                while not stop.is_set() and render_next(ctx):
                    touch_heartbeat()
                if ctx.now() - last_sweep > SWEEP_INTERVAL:
                    with ctx.factory() as session:
                        artifacts.sweep_orphans(session, ctx.store, ctx.now())
                    last_sweep = ctx.now()
            except Exception:
                logger.exception("renderer cycle failed")
            touch_heartbeat()
            if args.once:
                break
            stop.wait(settings.worker_poll_seconds)
    finally:
        for renderer in ctx.renderers.values():
            close = getattr(renderer, "close", None)
            if close:
                close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
