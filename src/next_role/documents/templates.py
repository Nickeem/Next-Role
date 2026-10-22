"""Template registry: the three resume designs and the design tokens every renderer uses.

This is the single source of truth for sizes, spacing, colours and fonts. The web preview and
the PDF print page receive resolved tokens from the API; the DOCX renderer reads them here.
Changing a template's look requires bumping its ``version``, which is recorded on each style
and therefore on each artifact.

The designs are independent implementations; Resume-Matcher was design inspiration only.
Fonts are the metric-compatible Liberation families in the renderer image, mapped to Arial and
Times New Roman in Word, so no font files are bundled or licensed.
"""

from dataclasses import asdict, dataclass
from typing import Any, Literal

from next_role.documents.schemas import Density, PageSize, StyleSettings

# Body text never gets smaller than this, whatever the template or density.
MIN_BODY_PT = 9.5

ACCENTS: dict[str, str] = {
    "slate": "#334155",
    "navy": "#1e3a8a",
    "teal": "#0f766e",
    "burgundy": "#881337",
    "forest": "#166534",
}

FONTS: dict[str, dict[str, str]] = {
    "sans": {"css": '"Liberation Sans", Arial, Helvetica, sans-serif', "docx": "Arial"},
    "serif": {
        "css": '"Liberation Serif", "Times New Roman", Times, serif',
        "docx": "Times New Roman",
    },
}

PAGES: dict[PageSize, tuple[float, float]] = {
    PageSize.A4: (8.27, 11.69),
    PageSize.LETTER: (8.5, 11.0),
}

HeadingStyle = Literal["rule", "caps-accent", "bar-accent"]


@dataclass(frozen=True)
class TemplateDef:
    id: str
    version: str
    name: str
    description: str
    body_pt: tuple[float, float]  # normal, compact
    name_pt: float
    heading_pt: float
    line_height: tuple[float, float]
    section_gap_pt: tuple[float, float]
    item_gap_pt: tuple[float, float]
    margin_in: tuple[float, float]
    heading_style: HeadingStyle
    # Whether the candidate's name uses the accent colour.
    accent_name: bool
    target_resume_pages: int


TEMPLATES: dict[str, TemplateDef] = {
    "clean": TemplateDef(
        id="clean",
        version="1",
        name="Clean",
        description="Understated and conventional. A safe default for most applications.",
        body_pt=(10.5, 10.0),
        name_pt=20,
        heading_pt=11.5,
        line_height=(1.35, 1.25),
        section_gap_pt=(12, 8),
        item_gap_pt=(7, 4),
        margin_in=(0.8, 0.6),
        heading_style="rule",
        accent_name=False,
        target_resume_pages=2,
    ),
    "modern-deedy": TemplateDef(
        id="modern-deedy",
        version="1",
        name="Modern Deedy",
        description="Dense and compact, oriented to one page for technical roles.",
        body_pt=(10.0, 9.5),
        name_pt=24,
        heading_pt=10.5,
        line_height=(1.25, 1.18),
        section_gap_pt=(8, 6),
        item_gap_pt=(5, 3),
        margin_in=(0.55, 0.45),
        heading_style="caps-accent",
        accent_name=False,
        target_resume_pages=1,
    ),
    "modern": TemplateDef(
        id="modern",
        version="1",
        name="Modern",
        description="Clean single column with a restrained accent colour.",
        body_pt=(10.5, 10.0),
        name_pt=22,
        heading_pt=12,
        line_height=(1.35, 1.25),
        section_gap_pt=(13, 9),
        item_gap_pt=(7, 4),
        margin_in=(0.75, 0.6),
        heading_style="bar-accent",
        accent_name=True,
        target_resume_pages=2,
    ),
}

TARGET_LETTER_PAGES = 1


def template(template_id: str) -> TemplateDef:
    return TEMPLATES[template_id]


def resolve_tokens(settings: StyleSettings) -> dict[str, Any]:
    """Concrete design values for one style, shared by the preview, PDF and DOCX."""
    t = TEMPLATES[settings.template_id]
    i = 1 if settings.density is Density.COMPACT else 0
    width, height = PAGES[settings.page_size]
    margin = t.margin_in[i]
    body = max(t.body_pt[i], MIN_BODY_PT)
    return {
        "template_id": t.id,
        "template_version": t.version,
        "font_css": FONTS[settings.font]["css"],
        "font_docx": FONTS[settings.font]["docx"],
        "body_pt": body,
        "small_pt": max(body - 0.5, MIN_BODY_PT),
        "name_pt": t.name_pt,
        "heading_pt": t.heading_pt,
        "line_height": t.line_height[i],
        "section_gap_pt": t.section_gap_pt[i],
        "item_gap_pt": t.item_gap_pt[i],
        "margin_in": margin,
        "page": {"size": settings.page_size.value, "width_in": width, "height_in": height},
        "accent": ACCENTS[settings.accent],
        "text": "#111827",
        "muted": "#4b5563",
        "heading_style": t.heading_style,
        "accent_name": t.accent_name,
        "target_resume_pages": t.target_resume_pages,
        "target_letter_pages": TARGET_LETTER_PAGES,
    }


def registry() -> list[dict[str, Any]]:
    """Public description of the templates and their controls (``GET /templates``)."""
    return [
        {
            **{k: v for k, v in asdict(t).items() if k in {"id", "version", "name", "description"}},
            "target_resume_pages": t.target_resume_pages,
        }
        for t in TEMPLATES.values()
    ]


CONTROLS: dict[str, list[str]] = {
    "page_size": [p.value for p in PageSize],
    "density": [d.value for d in Density],
    "accent": list(ACCENTS),
    "font": list(FONTS),
}
