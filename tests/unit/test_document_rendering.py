"""The display model, DOCX rendering for every template, and file QA."""

from datetime import date

import pytest

from next_role.documents import display, docx_renderer, drafting, qa
from next_role.documents.schemas import StyleSettings
from next_role.documents.templates import TEMPLATES, resolve_tokens
from tests.unit.document_fixtures import snapshot

TODAY = date(2026, 10, 22)


def _documents() -> tuple[display.DisplayDocument, display.DisplayDocument]:
    draft = drafting.draft(snapshot(), {}, TODAY, None)
    settings = StyleSettings()
    return display.build(draft.resume, settings), display.build(draft.letter, settings)


def test_dates_and_sentences() -> None:
    assert display.date_range("2019-03", None, current=True) == f"Mar 2019 {display.DASH} Present"
    assert display.date_range("2017-01", "2019-02") == f"Jan 2017 {display.DASH} Feb 2019"
    assert display.date_range(None, "2018-05") == "May 2018"
    assert display.date_range(None, None) is None
    assert display.sentence("Built things") == "Built things."
    assert display.sentence("Done?") == "Done?"


def test_sections_follow_the_chosen_order_and_visibility() -> None:
    draft = drafting.draft(snapshot(), {}, TODAY, None)
    default = display.build(draft.resume, StyleSettings())
    assert [s.type for s in default.sections] == ["experience", "skills", "education"]
    custom = display.build(
        draft.resume,
        StyleSettings(section_order=["education", "skills"], hidden_sections=["skills"]),
    )
    assert [s.type for s in custom.sections] == ["education", "experience"]


def test_letter_display() -> None:
    _, letter = _documents()
    assert letter.regarding == (
        "Application for Senior Backend Engineer, Acme Logistics (fictional)"
    )
    assert letter.salutation == "Dear Hiring Team,"
    assert all(t.text.endswith(".") for p in letter.paragraphs for t in p)
    assert letter.texts()[-2:] == ["Sincerely,", "Alex Example"]


@pytest.mark.parametrize("template_id", list(TEMPLATES))
@pytest.mark.parametrize("page_size", ["a4", "letter"])
@pytest.mark.parametrize("density", ["normal", "compact"])
def test_docx_renders_every_template_and_passes_qa(
    template_id: str, page_size: str, density: str
) -> None:
    settings = StyleSettings.model_validate(
        {"template_id": template_id, "page_size": page_size, "density": density}
    )
    tokens = resolve_tokens(settings)
    draft = drafting.draft(snapshot(), {}, TODAY, None)
    for document in (draft.resume, draft.letter):
        view = display.build(document, settings)
        data = docx_renderer.render(view, tokens)
        assert data.startswith(b"PK")  # an OOXML zip
        result = qa.check_docx(view, data, tokens)
        assert result.passed, (result.missing, result.out_of_order)
        assert result.page_count is None


def test_docx_uses_native_bullets_and_the_template_font() -> None:
    import io

    from docx import Document

    resume, _ = _documents()
    data = docx_renderer.render(resume, resolve_tokens(StyleSettings(font="serif")))
    doc = Document(io.BytesIO(data))
    styles = {p.style.name for p in doc.paragraphs if p.style is not None}
    assert "List Bullet" in styles
    assert doc.styles["Normal"].font.name == "Times New Roman"
    assert doc.core_properties.author == ""


def test_qa_reports_missing_and_reordered_text() -> None:
    resume, _ = _documents()
    tokens = resolve_tokens(StyleSettings())
    data = docx_renderer.render(resume, tokens)
    extended = resume.model_copy(
        update={"header": resume.header.model_copy(update={"contact": ["not in the file"]})}
    )
    result = qa.check_docx(extended, data, tokens)
    assert not result.passed and result.missing == ["not in the file"]
    assert result.as_dict()["missing"] == 1  # counts only, never the text

    missing, reordered = qa.check_text(["b", "a"], "a b")
    assert (missing, reordered) == ([], ["a"])
    assert qa.check_text(["Skills"], "SKILLS") == ([], [])
    assert qa.check_text([f"Jan 2017 {display.DASH} Feb 2019"], "Jan 2017 - Feb 2019") == ([], [])


def test_qa_rejects_empty_or_non_pdf_files() -> None:
    resume, _ = _documents()
    tokens = resolve_tokens(StyleSettings())
    assert not qa.check_docx(resume, b"", tokens).passed
    assert not qa.check_pdf(resume, b"<html>", tokens).passed


def test_long_unbroken_text_is_kept_whole_in_docx() -> None:
    resume, _ = _documents()
    long_word = "x" * 500
    section = resume.sections[0]
    entry = section.entries[0].model_copy(
        update={"bullets": [display.Text(claim_id="c", text=long_word)]}
    )
    view = resume.model_copy(
        update={"sections": [section.model_copy(update={"entries": [entry]}), *resume.sections[1:]]}
    )
    tokens = resolve_tokens(StyleSettings())
    assert qa.check_docx(view, docx_renderer.render(view, tokens), tokens).passed
