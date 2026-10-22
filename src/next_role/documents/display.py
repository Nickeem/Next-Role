"""The display model: what a reader sees, as plain strings, built once for every renderer.

The web preview, the PDF print page, the DOCX renderer, and file QA all consume this model,
so dates, ordering, hidden sections and punctuation are decided in one place. Text is never
markup; renderers must escape it.
"""

from datetime import date

from pydantic import BaseModel

from next_role.documents.schemas import (
    CertificationsSection,
    CoverLetterDocument,
    Document,
    EducationSection,
    ExperienceSection,
    Header,
    ProjectsSection,
    ResumeDocument,
    Section,
    SectionType,
    SkillsSection,
    StyleSettings,
    SummarySection,
)

DEFAULT_ORDER = [
    SectionType.SUMMARY,
    SectionType.EXPERIENCE,
    SectionType.PROJECTS,
    SectionType.SKILLS,
    SectionType.EDUCATION,
    SectionType.CERTIFICATIONS,
]
_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
DASH = "\u2013"


class Text(BaseModel):
    claim_id: str | None = None
    text: str


class Entry(BaseModel):
    heading: str
    subheading: str | None = None
    dates: str | None = None
    location: str | None = None
    bullets: list[Text] = []


class DisplaySection(BaseModel):
    id: str
    type: SectionType
    title: str
    entries: list[Entry] = []
    # Summary sentences, or skills, shown inline.
    inline: list[Text] = []


class DisplayHeader(BaseModel):
    name: str
    contact: list[str]


class DisplayDocument(BaseModel):
    kind: str
    header: DisplayHeader
    # Cover letters: "Application for <title>, <company>".
    regarding: str | None = None
    sections: list[DisplaySection] = []
    salutation: str | None = None
    paragraphs: list[list[Text]] = []
    closing: str | None = None

    def texts(self) -> list[str]:
        """Every visible string in reading order, for checking rendered files."""
        out = [self.header.name, *self.header.contact]
        if self.regarding:
            out.append(self.regarding)
        for section in self.sections:
            out.append(section.title)
            for entry in section.entries:
                out += [
                    x for x in (entry.heading, entry.subheading, entry.dates, entry.location) if x
                ]
                out += [b.text for b in entry.bullets]
            out += [t.text for t in section.inline]
        if self.salutation:
            out.append(self.salutation)
        for paragraph in self.paragraphs:
            out += [t.text for t in paragraph]
        if self.closing:
            out += [self.closing, self.header.name]
        return out


def month(value: str | None) -> str | None:
    if not value:
        return None
    year, mon = value.split("-")
    return f"{_MONTHS[int(mon) - 1]} {year}"


def date_range(start: str | None, end: str | None, current: bool = False) -> str | None:
    first, last = month(start), "Present" if current else month(end)
    if first and last:
        return f"{first} {DASH} {last}"
    return first or last


def sentence(text: str) -> str:
    text = text.strip()
    return text if text[-1:] in {".", "!", "?", ":"} else f"{text}."


def _header(header: Header) -> DisplayHeader:
    contact = [header.location] if header.location else []
    if header.contact.email:
        contact.append(header.contact.email)
    if header.contact.phone:
        contact.append(header.contact.phone)
    contact += [str(link.url).rstrip("/") for link in header.contact.links]
    return DisplayHeader(name=header.name, contact=[c for c in contact if c])


def _section(section: Section) -> DisplaySection:
    base = {"id": section.id, "type": section.type, "title": section.title}
    match section:
        case SummarySection():
            return DisplaySection(
                **base,
                inline=[Text(claim_id=c.claim_id, text=sentence(c.text)) for c in section.claims],
            )
        case SkillsSection():
            return DisplaySection(
                **base, inline=[Text(claim_id=c.claim_id, text=c.text) for c in section.skills]
            )
        case ExperienceSection():
            return DisplaySection(
                **base,
                entries=[
                    Entry(
                        heading=i.position,
                        subheading=i.organization,
                        dates=date_range(i.start_date, i.end_date, i.is_current),
                        location=i.location,
                        bullets=[Text(claim_id=b.claim_id, text=b.text) for b in i.bullets],
                    )
                    for i in section.items
                ],
            )
        case ProjectsSection():
            return DisplaySection(
                **base,
                entries=[
                    Entry(
                        heading=i.name,
                        subheading=", ".join(x for x in (i.role, i.organization) if x) or None,
                        dates=date_range(i.start_date, i.end_date, i.is_current),
                        bullets=[Text(claim_id=b.claim_id, text=b.text) for b in i.bullets],
                    )
                    for i in section.items
                ],
            )
        case EducationSection():
            return DisplaySection(
                **base,
                entries=[
                    Entry(
                        heading=i.qualification
                        + (
                            f", {i.field_of_study}"
                            if i.field_of_study and i.field_of_study not in i.qualification
                            else ""
                        ),
                        subheading=i.institution,
                        dates=date_range(i.start_date, i.end_date),
                    )
                    for i in section.items
                ],
            )
        case CertificationsSection():
            return DisplaySection(
                **base,
                entries=[
                    Entry(
                        heading=i.name,
                        subheading=i.issuer,
                        dates=month(i.issued),
                    )
                    for i in section.items
                ],
            )
    raise ValueError(f"unknown section {section.type}")  # pragma: no cover


def arrange(resume: ResumeDocument, settings: StyleSettings) -> list[Section]:
    """Sections in the chosen order, without hidden ones."""
    order = [
        *settings.section_order,
        *[t for t in DEFAULT_ORDER if t not in settings.section_order],
    ]
    visible = [s for s in resume.sections if s.type not in settings.hidden_sections]
    return sorted(visible, key=lambda s: order.index(s.type))


def build(
    document: Document, settings: StyleSettings, today: date | None = None
) -> DisplayDocument:
    header = _header(document.header)
    if isinstance(document, ResumeDocument):
        return DisplayDocument(
            kind="resume",
            header=header,
            sections=[_section(s) for s in arrange(document, settings)],
        )
    assert isinstance(document, CoverLetterDocument)  # noqa: S101 - Document is a union of two
    target = document.target
    regarding = None
    if target.job_title or target.company:
        regarding = "Application for " + ", ".join(
            x for x in (target.job_title, target.company) if x
        )
    return DisplayDocument(
        kind="cover_letter",
        header=header,
        regarding=regarding,
        salutation=document.salutation,
        paragraphs=[
            [Text(claim_id=c.claim_id, text=sentence(c.text)) for c in p.claims]
            for p in document.paragraphs
        ],
        closing=document.closing,
    )
