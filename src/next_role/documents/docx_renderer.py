"""Native Word rendering of the display model (ADR 0007).

Uses real paragraphs, heading-like styles and Word's list bullets, never images of text, so
the file stays editable. It follows the template's tokens (fonts, sizes, accent, spacing,
margins) for close branding; it does not try to match the PDF pixel for pixel.
"""

import io
from typing import Any

from docx import Document as open_docx  # noqa: N813 - a factory function
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from docx.text.paragraph import Paragraph

from next_role.documents.display import DisplayDocument, DisplaySection, Entry

RENDERER_VERSION = "docx-1"


def _rgb(hex_color: str) -> RGBColor:
    return RGBColor.from_string(hex_color.lstrip("#").upper())


def _border(paragraph: Paragraph, side: str, color: str, size_eighths: int) -> None:
    """Add a paragraph border (e.g. a rule under section headings)."""
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    edge = OxmlElement(f"w:{side}")
    edge.set(qn("w:val"), "single")
    edge.set(qn("w:sz"), str(size_eighths))
    edge.set(qn("w:space"), "4")
    edge.set(qn("w:color"), color.lstrip("#").upper())
    borders.append(edge)


class _Writer:
    def __init__(self, tokens: dict[str, Any]) -> None:
        self.t = tokens
        self.doc = open_docx()
        section = self.doc.sections[0]
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Inches(tokens["page"]["width_in"])
        section.page_height = Inches(tokens["page"]["height_in"])
        margin = Inches(tokens["margin_in"])
        section.left_margin = section.right_margin = margin
        section.top_margin = section.bottom_margin = margin
        self.content_width = Inches(tokens["page"]["width_in"] - 2 * tokens["margin_in"])

        normal = self.doc.styles["Normal"]
        normal.font.name = tokens["font_docx"]
        normal.font.size = Pt(tokens["body_pt"])
        normal.font.color.rgb = _rgb(tokens["text"])
        # East Asian font fallback must be set too, or Word substitutes its own.
        r_fonts = normal.element.get_or_add_rPr().get_or_add_rFonts()
        r_fonts.set(qn("w:eastAsia"), tokens["font_docx"])
        fmt = normal.paragraph_format
        fmt.line_spacing = tokens["line_height"]
        fmt.space_after = Pt(0)
        fmt.space_before = Pt(0)
        bullets = self.doc.styles["List Bullet"]
        bullets.font.name = tokens["font_docx"]
        bullets.font.size = Pt(tokens["body_pt"])
        bullets.paragraph_format.space_after = Pt(1)

    def paragraph(
        self,
        text: str = "",
        *,
        size: float | None = None,
        bold: bool = False,
        color: str | None = None,
        style: str | None = None,
        after: float = 0,
        before: float = 0,
    ) -> Paragraph:
        p = self.doc.add_paragraph(style=style)
        p.paragraph_format.space_after = Pt(after)
        p.paragraph_format.space_before = Pt(before)
        if text:
            run = p.add_run(text)
            run.bold = bold
            if size:
                run.font.size = Pt(size)
            if color:
                run.font.color.rgb = _rgb(color)
        return p

    def header(self, display: DisplayDocument) -> None:
        t = self.t
        name_color = t["accent"] if t["accent_name"] else t["text"]
        self.paragraph(display.header.name, size=t["name_pt"], bold=True, color=name_color, after=2)
        if display.header.headline:
            self.paragraph(display.header.headline, after=2)
        if display.header.contact:
            self.paragraph(
                "  |  ".join(display.header.contact),
                size=t["small_pt"],
                color=t["muted"],
                after=t["section_gap_pt"] * 0.6,
            )

    def heading(self, title: str) -> None:
        t = self.t
        style = t["heading_style"]
        text = title.upper() if style == "caps-accent" else title
        color = t["text"] if style == "rule" else t["accent"]
        p = self.paragraph(
            text, size=t["heading_pt"], bold=True, color=color, before=t["section_gap_pt"], after=3
        )
        if style == "rule":
            _border(p, "bottom", t["muted"], 4)
        elif style == "bar-accent":
            _border(p, "left", t["accent"], 18)

    def entry(self, entry: Entry) -> None:
        t = self.t
        p = self.paragraph(before=t["item_gap_pt"])
        p.paragraph_format.tab_stops.add_tab_stop(self.content_width, WD_TAB_ALIGNMENT.RIGHT)
        lead = p.add_run(entry.heading)
        lead.bold = True
        if entry.subheading:
            p.add_run(f", {entry.subheading}")
        if entry.dates:
            dates = p.add_run(f"\t{entry.dates}")
            dates.font.color.rgb = _rgb(t["muted"])
            dates.font.size = Pt(t["small_pt"])
        if entry.location:
            self.paragraph(entry.location, size=t["small_pt"], color=t["muted"])
        if entry.details:
            self.paragraph("  ·  ".join(entry.details), size=t["small_pt"], color=t["muted"])
        for bullet in entry.bullets:
            self.paragraph(bullet.text, style="List Bullet")

    def section(self, section: DisplaySection) -> None:
        self.heading(section.title)
        if section.inline:
            separator = " " if section.type.value == "summary" else "  ·  "
            self.paragraph(separator.join(t.text for t in section.inline))
        for entry in section.entries:
            self.entry(entry)

    def letter(self, display: DisplayDocument) -> None:
        t = self.t
        gap = t["section_gap_pt"]
        if display.regarding:
            self.paragraph(display.regarding, bold=True, before=gap, after=gap)
        if display.salutation:
            self.paragraph(display.salutation, after=gap * 0.6)
        for paragraph in display.paragraphs:
            self.paragraph(" ".join(s.text for s in paragraph), after=gap * 0.6)
        if display.closing:
            self.paragraph(display.closing, before=gap * 0.4, after=gap * 1.5)
            self.paragraph(display.header.name)

    def bytes(self) -> bytes:
        buffer = io.BytesIO()
        self.doc.core_properties.author = ""
        self.doc.core_properties.comments = ""
        self.doc.save(buffer)
        return buffer.getvalue()


def render(display: DisplayDocument, tokens: dict[str, Any]) -> bytes:
    writer = _Writer(tokens)
    writer.header(display)
    if display.kind == "cover_letter":
        writer.letter(display)
    else:
        for section in display.sections:
            writer.section(section)
    return writer.bytes()


def extract_text(data: bytes) -> list[str]:
    """Paragraph texts of a DOCX, in order (for QA)."""
    document = open_docx(io.BytesIO(data))
    return [p.text for p in document.paragraphs]
