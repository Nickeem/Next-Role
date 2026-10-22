"""PDF rendering through Chromium. Runs in the renderer image, where Chromium and the built
print page exist; skipped elsewhere:

    docker run --rm --user 0 -v "$PWD/tests:/app/tests:ro" next-role-renderer \\
        sh -c "uv pip install --python /opt/venv/bin/python pytest -q && pytest -m renderer"
"""

import io
import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from next_role.documents import display, drafting
from next_role.documents.schemas import StyleSettings
from next_role.documents.templates import TEMPLATES, resolve_tokens
from tests.unit.document_fixtures import snapshot

pytestmark = pytest.mark.renderer

BUNDLE = Path(os.environ.get("NEXT_ROLE_PRINT_BUNDLE_DIR", "/app/print"))


@pytest.fixture(scope="module")
def pdf() -> Iterator[Any]:
    try:
        from next_role.documents.pdf_renderer import PdfRenderer

        renderer = PdfRenderer(30, str(BUNDLE))
        renderer._ensure_browser()
    except Exception as exc:  # Chromium or the print bundle is not available here.
        pytest.skip(f"PDF renderer unavailable: {type(exc).__name__}")
    yield renderer
    renderer.close()


def _views(settings: StyleSettings) -> list[display.DisplayDocument]:
    draft = drafting.draft(snapshot(), {}, date(2026, 10, 22), None)
    return [display.build(draft.resume, settings), display.build(draft.letter, settings)]


@pytest.mark.parametrize("template_id", list(TEMPLATES))
@pytest.mark.parametrize(("page_size", "width_pt"), [("a4", 595), ("letter", 612)])
def test_every_template_renders_a_text_pdf_that_passes_qa(
    pdf: Any, template_id: str, page_size: str, width_pt: int
) -> None:
    from pypdf import PdfReader

    settings = StyleSettings.model_validate({"template_id": template_id, "page_size": page_size})
    tokens = resolve_tokens(settings)
    for view in _views(settings):
        data = pdf.render(view, tokens)
        result = pdf.check(view, data, tokens)
        assert result.passed, (result.missing, result.out_of_order, result.warnings)
        assert result.page_count == 1
        page = PdfReader(io.BytesIO(data)).pages[0]
        assert abs(float(page.mediabox.width) - width_pt) < 2


def test_the_browser_only_loads_bundled_files(pdf: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from next_role.documents import pdf_renderer

    seen: list[str] = []
    original = pdf_renderer.PdfRenderer._route

    def spy(self: Any, route: Any) -> None:
        seen.append(route.request.url)
        original(self, route)

    monkeypatch.setattr(pdf_renderer.PdfRenderer, "_route", spy)
    settings = StyleSettings()
    view = _views(settings)[0]
    # Text that looks like a URL or markup stays text and is never fetched or parsed.
    hostile = view.model_copy(
        update={
            "header": view.header.model_copy(
                update={
                    "contact": ['<img src="http://169.254.169.254/x">', "https://example.invalid/"]
                }
            )
        }
    )
    tokens = resolve_tokens(settings)
    data = pdf.render(hostile, tokens)
    assert pdf.check(hostile, data, tokens).passed
    assert seen and all(url.startswith(pdf_renderer.ORIGIN + "/") for url in seen)


def test_long_content_breaks_across_pages_and_never_overflows(pdf: Any) -> None:
    settings = StyleSettings(template_id="modern-deedy")
    tokens = resolve_tokens(settings)
    view = _views(settings)[0]
    section = view.sections[0]
    bullets = [
        display.Text(claim_id=f"b{i}", text=f"Delivered milestone {i} for the platform team.")
        for i in range(60)
    ] + [display.Text(claim_id="long", text="x" * 400)]
    entry = section.entries[0].model_copy(update={"bullets": bullets})
    long_view = view.model_copy(
        update={"sections": [section.model_copy(update={"entries": [entry]}), *view.sections[1:]]}
    )
    data = pdf.render(long_view, tokens)
    result = pdf.check(long_view, data, tokens)
    assert result.passed, (result.missing[:3], result.warnings)
    assert result.page_count is not None and result.page_count >= 2
    assert any("pages" in w for w in result.warnings)  # over the one-page target: a warning
