"""Canonical document content and style contracts (specification §5).

The canonical document is the source of truth: the preview, the PDF and the DOCX are all
rendered from it. It is plain data, never markup. Substantive text is held in ``Claim``s that
cite candidate facts by id; organization, title, dates, institution and credential are copied
from the cited fact by code and checked again by verification.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from next_role.candidates.schemas import CandidateContact
from next_role.hashing import canonical_json, sha256_json

__all__ = ["canonical_json", "sha256_json"]

RESUME_SCHEMA: Literal["resume/v1"] = "resume/v1"
COVER_LETTER_SCHEMA: Literal["cover-letter/v1"] = "cover-letter/v1"

Id = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{0,39}$")]
# A candidate fact id, "stmt:<clarification id>" for a letter-only operator statement, or
# "about:me" for the candidate's about-me (a personal statement, ADR 0009).
EvidenceId = Annotated[
    str,
    Field(
        pattern=r"^(?:(stmt:)?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        r"|about:me)$"
    ),
]
YearMonth = Annotated[str, Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
Line = Annotated[str, Field(min_length=1, max_length=300)]
# Shown as a detail of an entry, copied from the fact by code.
Url = Annotated[str, Field(max_length=2000, pattern=r"^https?://\S+$")]

STATEMENT_PREFIX = "stmt:"
ABOUT_ME_ID = "about:me"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Claim(_Strict):
    """One sentence, bullet, or skill name, and the facts that support it."""

    claim_id: Id
    text: Annotated[str, Field(min_length=1, max_length=600)]
    evidence_ids: Annotated[list[EvidenceId], Field(max_length=10)] = []
    # Cover letters only: a sentence that states nothing about the candidate (e.g. a
    # greeting). Verification restricts what such a sentence may contain.
    connective: bool = False

    @property
    def personal(self) -> bool:
        """A personal statement: it cites the about-me, not only facts (ADR 0009)."""
        return ABOUT_ME_ID in self.evidence_ids


class Header(_Strict):
    name: Line
    # From the profile, rendered by code like the name; never written by or sent to an LLM.
    headline: Line | None = None
    location: Line | None = None
    contact: CandidateContact = CandidateContact()


class Target(_Strict):
    """The job being applied for. Display only: it comes from the job, not from claims."""

    job_title: Line | None = None
    company: Line | None = None


class _Item(_Strict):
    id: Id
    source_fact_id: uuid.UUID


class ExperienceItem(_Item):
    organization: Line
    position: Line
    location: Line | None = None
    start_date: YearMonth | None = None
    end_date: YearMonth | None = None
    is_current: bool = False
    bullets: Annotated[list[Claim], Field(max_length=8)] = []


class ProjectItem(_Item):
    name: Line
    role: Line | None = None
    organization: Line | None = None
    start_date: YearMonth | None = None
    end_date: YearMonth | None = None
    is_current: bool = False
    # Display detail copied from the fact; not a claim.
    url: Url | None = None
    bullets: Annotated[list[Claim], Field(max_length=6)] = []


class EducationItem(_Item):
    institution: Line | None = None
    qualification: Line
    field_of_study: Line | None = None
    start_date: YearMonth | None = None
    end_date: YearMonth | None = None


class CertificationItem(_Item):
    name: Line
    issuer: Line | None = None
    issued: YearMonth | None = None
    expires: YearMonth | None = None
    # Display details copied from the fact; not claims.
    credential_id: Line | None = None
    credential_url: Url | None = None


class SectionType(StrEnum):
    SUMMARY = "summary"
    EXPERIENCE = "experience"
    PROJECTS = "projects"
    EDUCATION = "education"
    CERTIFICATIONS = "certifications"
    SKILLS = "skills"


class _Section(_Strict):
    id: Id
    title: Line


class SummarySection(_Section):
    type: Literal[SectionType.SUMMARY] = SectionType.SUMMARY
    claims: Annotated[list[Claim], Field(min_length=1, max_length=4)]


class ExperienceSection(_Section):
    type: Literal[SectionType.EXPERIENCE] = SectionType.EXPERIENCE
    items: Annotated[list[ExperienceItem], Field(min_length=1, max_length=12)]


class ProjectsSection(_Section):
    type: Literal[SectionType.PROJECTS] = SectionType.PROJECTS
    items: Annotated[list[ProjectItem], Field(min_length=1, max_length=8)]


class EducationSection(_Section):
    type: Literal[SectionType.EDUCATION] = SectionType.EDUCATION
    items: Annotated[list[EducationItem], Field(min_length=1, max_length=8)]


class CertificationsSection(_Section):
    type: Literal[SectionType.CERTIFICATIONS] = SectionType.CERTIFICATIONS
    items: Annotated[list[CertificationItem], Field(min_length=1, max_length=12)]


class SkillsSection(_Section):
    type: Literal[SectionType.SKILLS] = SectionType.SKILLS
    # Each claim's text is one skill name.
    skills: Annotated[list[Claim], Field(min_length=1, max_length=40)]


Section = Annotated[
    SummarySection
    | ExperienceSection
    | ProjectsSection
    | EducationSection
    | CertificationsSection
    | SkillsSection,
    Field(discriminator="type"),
]

SECTION_TITLES: dict[SectionType, str] = {
    SectionType.SUMMARY: "Summary",
    SectionType.EXPERIENCE: "Experience",
    SectionType.PROJECTS: "Projects",
    SectionType.EDUCATION: "Education",
    SectionType.CERTIFICATIONS: "Certifications",
    SectionType.SKILLS: "Skills",
}


def _unique_ids(ids: list[str], what: str) -> None:
    if len(set(ids)) != len(ids):
        raise ValueError(f"{what} ids must be unique")


class ResumeDocument(_Strict):
    kind: Literal["resume"] = "resume"
    schema_version: Literal["resume/v1"] = RESUME_SCHEMA
    header: Header
    target: Target = Target()
    sections: Annotated[list[Section], Field(min_length=1, max_length=6)]

    @model_validator(mode="after")
    def _ids(self) -> Self:
        _unique_ids([s.id for s in self.sections], "section")
        _unique_ids([s.type for s in self.sections], "section type")
        _unique_ids([c.claim_id for _, c in iter_claims(self)], "claim")
        if any(c.connective for _, c in iter_claims(self)):
            raise ValueError("connective claims are only allowed in cover letters")
        return self


Salutation = Literal["Dear Hiring Manager,", "Dear Hiring Team,", "To the Hiring Team,"]
Closing = Literal["Sincerely,", "Kind regards,", "Best regards,"]


class Paragraph(_Strict):
    id: Id
    claims: Annotated[list[Claim], Field(min_length=1, max_length=8)]


class CoverLetterDocument(_Strict):
    kind: Literal["cover_letter"] = "cover_letter"
    schema_version: Literal["cover-letter/v1"] = COVER_LETTER_SCHEMA
    header: Header
    target: Target = Target()
    salutation: Salutation = "Dear Hiring Team,"
    paragraphs: Annotated[list[Paragraph], Field(min_length=1, max_length=6)]
    closing: Closing = "Sincerely,"

    @model_validator(mode="after")
    def _ids(self) -> Self:
        _unique_ids([p.id for p in self.paragraphs], "paragraph")
        _unique_ids([c.claim_id for _, c in iter_claims(self)], "claim")
        return self


Document = ResumeDocument | CoverLetterDocument


def parse_document(content: dict[str, Any]) -> Document:
    if content.get("kind") == "cover_letter":
        return CoverLetterDocument.model_validate(content)
    return ResumeDocument.model_validate(content)


@dataclass(frozen=True)
class ClaimRef:
    """Where a claim sits: a JSON-pointer-like path, and the item it belongs to, if any."""

    path: str
    section: str
    item: ExperienceItem | ProjectItem | None = None


def iter_claims(document: Document) -> Iterator[tuple[ClaimRef, Claim]]:
    """Every claim in reading order."""
    if isinstance(document, CoverLetterDocument):
        for p, paragraph in enumerate(document.paragraphs):
            for c, claim in enumerate(paragraph.claims):
                yield ClaimRef(f"/paragraphs/{p}/claims/{c}", paragraph.id), claim
        return
    for s, section in enumerate(document.sections):
        base = f"/sections/{s}"
        match section:
            case SummarySection():
                for c, claim in enumerate(section.claims):
                    yield ClaimRef(f"{base}/claims/{c}", section.id), claim
            case SkillsSection():
                for c, claim in enumerate(section.skills):
                    yield ClaimRef(f"{base}/skills/{c}", section.id), claim
            case ExperienceSection() | ProjectsSection():
                items: list[ExperienceItem | ProjectItem] = list(section.items)
                for i, item in enumerate(items):
                    for c, claim in enumerate(item.bullets):
                        yield ClaimRef(f"{base}/items/{i}/bullets/{c}", section.id, item), claim
            case _:
                pass


def content_hash(document: Document) -> str:
    return sha256_json(document.model_dump(mode="json"))


# --- Style ---------------------------------------------------------------------------------


class PageSize(StrEnum):
    A4 = "a4"
    LETTER = "letter"


class Density(StrEnum):
    NORMAL = "normal"
    COMPACT = "compact"


class StyleSettings(_Strict):
    """How a bundle looks. Only values from the template registry are accepted."""

    template_id: Literal["clean", "modern-deedy", "modern"] = "clean"
    page_size: PageSize = PageSize.LETTER
    density: Density = Density.NORMAL
    accent: Literal["slate", "navy", "teal", "burgundy", "forest"] = "slate"
    font: Literal["sans", "serif"] = "sans"
    # Resume section order; sections not listed keep their default order after these.
    section_order: Annotated[list[SectionType], Field(max_length=6)] = []
    hidden_sections: Annotated[list[SectionType], Field(max_length=6)] = []

    @model_validator(mode="after")
    def _sections(self) -> Self:
        _unique_ids(list(self.section_order), "section_order")
        _unique_ids(list(self.hidden_sections), "hidden_sections")
        return self

    def style_hash(self, template_version: str) -> str:
        return sha256_json({"v": template_version, **self.model_dump(mode="json")})
