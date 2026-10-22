"""Validated contracts for candidate career information.

Each fact type has its own input model so required details are enforced per type, but every
fact is persisted in the same shape (see ``FactRecord``).
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from next_role.candidates.models import EvidenceKind, FactType, VerificationStatus
from next_role.skills import normalize_skill, normalize_skills

SCHEMA_VERSION = "candidate-profile/v1"

ShortStr = Annotated[str, Field(min_length=1, max_length=300)]
LongStr = Annotated[str, Field(max_length=10_000)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Preferences -------------------------------------------------------------------------


class WorkArrangement(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    INTERNSHIP = "internship"


class CandidatePreferences(_Strict):
    """Job-matching preferences. Empty lists and nulls mean "no stated preference"."""

    desired_titles: list[ShortStr] = []
    # Cities/regions/countries the candidate can work from or in.
    locations: list[ShortStr] = []
    work_arrangements: list[WorkArrangement] = []
    employment_types: list[EmploymentType] = []
    willing_to_relocate: bool | None = None
    # ISO 3166-1 alpha-2 country codes where the candidate is authorized to work.
    work_authorizations: list[Annotated[str, Field(pattern=r"^[A-Z]{2}$")]] = []
    requires_sponsorship: bool | None = None
    min_salary: int | None = Field(default=None, ge=0)
    salary_currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None


# --- Contact details --------------------------------------------------------------------


class ContactLink(_Strict):
    label: Annotated[str, Field(min_length=1, max_length=60)]
    url: HttpUrl


class CandidateContact(_Strict):
    """Contact details for document headers. Filled in by code; never sent to an LLM."""

    email: Annotated[str, Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")] | None = (
        None
    )
    phone: Annotated[str, Field(max_length=40, pattern=r"^[0-9+()./ -]{3,40}$")] | None = None
    links: Annotated[list[ContactLink], Field(max_length=5)] = []


# --- Evidence ----------------------------------------------------------------------------


class EvidenceSourceIn(_Strict):
    # Import-local identifier so facts in the same payload can reference this source.
    key: ShortStr | None = None
    kind: EvidenceKind
    title: ShortStr
    reference: Annotated[str, Field(max_length=2000)] | None = None
    notes: LongStr | None = None


class EvidenceSourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: EvidenceKind
    title: str
    reference: str | None
    notes: str | None
    created_at: datetime


# --- Fact attributes (type-specific JSONB) -----------------------------------------------


class SkillCategory(StrEnum):
    TECHNICAL = "technical"
    TOOL = "tool"
    DOMAIN = "domain"
    LANGUAGE = "language"
    INTERPERSONAL = "interpersonal"


class Proficiency(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


class DegreeLevel(StrEnum):
    HIGH_SCHOOL = "high_school"
    ASSOCIATE = "associate"
    BACHELOR = "bachelor"
    MASTER = "master"
    DOCTORATE = "doctorate"
    OTHER = "other"


class ExperienceAttributes(_Strict):
    location: ShortStr | None = None
    employment_type: EmploymentType | None = None
    responsibilities: list[LongStr] = []


class SkillAttributes(_Strict):
    category: SkillCategory | None = None
    proficiency: Proficiency | None = None
    years_experience: float | None = Field(default=None, ge=0, le=60)


class EducationAttributes(_Strict):
    degree_level: DegreeLevel
    field_of_study: ShortStr | None = None


class CertificationAttributes(_Strict):
    credential_id: ShortStr | None = None
    credential_url: HttpUrl | None = None


class ProjectAttributes(_Strict):
    role: ShortStr | None = None
    url: HttpUrl | None = None


class AchievementAttributes(_Strict):
    # Quantified outcomes exactly as the candidate states them, e.g. "reduced p95 latency 40%".
    metrics: list[ShortStr] = []


ATTRIBUTE_MODELS: dict[FactType, type[_Strict]] = {
    FactType.EXPERIENCE: ExperienceAttributes,
    FactType.SKILL: SkillAttributes,
    FactType.EDUCATION: EducationAttributes,
    FactType.CERTIFICATION: CertificationAttributes,
    FactType.PROJECT: ProjectAttributes,
    FactType.ACHIEVEMENT: AchievementAttributes,
}


# --- Fact inputs -------------------------------------------------------------------------


class FactRecord(BaseModel):
    """The uniform persisted shape of any fact."""

    fact_type: FactType
    title: str
    organization: str | None = None
    description: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    skills: list[str] = []
    attributes: dict[str, Any] = {}


class _FactInBase(_Strict):
    verification_status: VerificationStatus = VerificationStatus.SELF_REPORTED
    # Profile import: reference evidence/parents declared in the same payload by key.
    key: ShortStr | None = None
    evidence_keys: list[ShortStr] = []
    parent_key: ShortStr | None = None
    # Single-fact API: reference already-stored records by id.
    evidence_source_ids: list[uuid.UUID] = []
    parent_fact_id: uuid.UUID | None = None

    def to_record(self) -> FactRecord:
        raise NotImplementedError


def check_dates(start: date | None, end: date | None, is_current: bool = False) -> None:
    if start and end and end < start:
        raise ValueError("end date must not be before start date")
    if is_current and end:
        raise ValueError("a current entry must not have an end date")


class ExperienceIn(_FactInBase):
    fact_type: Literal[FactType.EXPERIENCE]
    title: ShortStr
    organization: ShortStr
    description: LongStr | None = None
    location: ShortStr | None = None
    employment_type: EmploymentType | None = None
    responsibilities: list[LongStr] = []
    start_date: date
    end_date: date | None = None
    is_current: bool = False
    skills: list[ShortStr] = []

    @model_validator(mode="after")
    def _dates(self) -> Self:
        check_dates(self.start_date, self.end_date, self.is_current)
        if not self.is_current and self.end_date is None:
            raise ValueError("end_date is required unless is_current is true")
        return self

    def to_record(self) -> FactRecord:
        attrs = ExperienceAttributes(
            location=self.location,
            employment_type=self.employment_type,
            responsibilities=self.responsibilities,
        )
        return FactRecord(
            fact_type=self.fact_type,
            title=self.title,
            organization=self.organization,
            description=self.description,
            start_date=self.start_date,
            end_date=self.end_date,
            is_current=self.is_current,
            skills=normalize_skills(self.skills),
            attributes=attrs.model_dump(mode="json", exclude_none=True),
        )


class SkillIn(_FactInBase):
    fact_type: Literal[FactType.SKILL]
    name: ShortStr
    category: SkillCategory | None = None
    proficiency: Proficiency | None = None
    years_experience: float | None = Field(default=None, ge=0, le=60)
    description: LongStr | None = None

    def to_record(self) -> FactRecord:
        attrs = SkillAttributes(
            category=self.category,
            proficiency=self.proficiency,
            years_experience=self.years_experience,
        )
        return FactRecord(
            fact_type=self.fact_type,
            title=self.name,
            description=self.description,
            skills=[normalize_skill(self.name)],
            attributes=attrs.model_dump(mode="json", exclude_none=True),
        )


class EducationIn(_FactInBase):
    fact_type: Literal[FactType.EDUCATION]
    institution: ShortStr
    degree_level: DegreeLevel
    degree_name: ShortStr | None = None
    field_of_study: ShortStr | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    description: LongStr | None = None

    @model_validator(mode="after")
    def _dates(self) -> Self:
        check_dates(self.start_date, self.end_date, self.is_current)
        return self

    def to_record(self) -> FactRecord:
        title = self.degree_name or (
            f"{self.degree_level.value.replace('_', ' ').title()}"
            + (f" in {self.field_of_study}" if self.field_of_study else "")
        )
        attrs = EducationAttributes(
            degree_level=self.degree_level, field_of_study=self.field_of_study
        )
        return FactRecord(
            fact_type=self.fact_type,
            title=title,
            organization=self.institution,
            description=self.description,
            start_date=self.start_date,
            end_date=self.end_date,
            is_current=self.is_current,
            attributes=attrs.model_dump(mode="json", exclude_none=True),
        )


class CertificationIn(_FactInBase):
    fact_type: Literal[FactType.CERTIFICATION]
    name: ShortStr
    issuer: ShortStr | None = None
    issued_date: date | None = None
    expires_date: date | None = None
    credential_id: ShortStr | None = None
    credential_url: HttpUrl | None = None
    skills: list[ShortStr] = []

    @model_validator(mode="after")
    def _dates(self) -> Self:
        check_dates(self.issued_date, self.expires_date)
        return self

    def to_record(self) -> FactRecord:
        attrs = CertificationAttributes(
            credential_id=self.credential_id, credential_url=self.credential_url
        )
        return FactRecord(
            fact_type=self.fact_type,
            title=self.name,
            organization=self.issuer,
            start_date=self.issued_date,
            end_date=self.expires_date,
            skills=normalize_skills(self.skills),
            attributes=attrs.model_dump(mode="json", exclude_none=True),
        )


class ProjectIn(_FactInBase):
    fact_type: Literal[FactType.PROJECT]
    name: ShortStr
    description: LongStr | None = None
    role: ShortStr | None = None
    url: HttpUrl | None = None
    organization: ShortStr | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool = False
    skills: list[ShortStr] = []

    @model_validator(mode="after")
    def _dates(self) -> Self:
        check_dates(self.start_date, self.end_date, self.is_current)
        return self

    def to_record(self) -> FactRecord:
        attrs = ProjectAttributes(role=self.role, url=self.url)
        return FactRecord(
            fact_type=self.fact_type,
            title=self.name,
            organization=self.organization,
            description=self.description,
            start_date=self.start_date,
            end_date=self.end_date,
            is_current=self.is_current,
            skills=normalize_skills(self.skills),
            attributes=attrs.model_dump(mode="json", exclude_none=True),
        )


class AchievementIn(_FactInBase):
    fact_type: Literal[FactType.ACHIEVEMENT]
    title: ShortStr
    description: LongStr | None = None
    metrics: list[ShortStr] = []
    achieved_date: date | None = None
    skills: list[ShortStr] = []

    def to_record(self) -> FactRecord:
        return FactRecord(
            fact_type=self.fact_type,
            title=self.title,
            description=self.description,
            start_date=self.achieved_date,
            skills=normalize_skills(self.skills),
            attributes=AchievementAttributes(metrics=self.metrics).model_dump(mode="json"),
        )


FactIn = Annotated[
    ExperienceIn | SkillIn | EducationIn | CertificationIn | ProjectIn | AchievementIn,
    Field(discriminator="fact_type"),
]


class FactUpdate(_Strict):
    """Partial update. Changing content of a verified fact resets it to self-reported unless
    ``verification_status`` is supplied in the same request."""

    title: ShortStr | None = None
    organization: ShortStr | None = None
    description: LongStr | None = None
    start_date: date | None = None
    end_date: date | None = None
    is_current: bool | None = None
    skills: list[ShortStr] | None = None
    # Replaces the type-specific attributes; validated against the fact's type.
    attributes: dict[str, Any] | None = None
    verification_status: VerificationStatus | None = None
    # Replaces the set of linked evidence sources.
    evidence_source_ids: list[uuid.UUID] | None = None


class FactSupport(StrEnum):
    """How well a fact is substantiated. Only verified/evidenced facts count as match evidence."""

    VERIFIED = "verified"
    EVIDENCED = "evidenced"
    UNSUPPORTED = "unsupported"


class FactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fact_type: FactType
    title: str
    organization: str | None
    description: str | None
    start_date: date | None
    end_date: date | None
    is_current: bool
    skills: list[str]
    attributes: dict[str, Any]
    verification_status: VerificationStatus
    verified_at: datetime | None
    support: FactSupport
    evidence_source_ids: list[uuid.UUID]
    parent_fact_id: uuid.UUID | None
    retired_at: datetime | None
    created_at: datetime
    updated_at: datetime


# --- Candidate ---------------------------------------------------------------------------


class CandidateBase(_Strict):
    full_name: ShortStr
    headline: ShortStr | None = None
    summary: LongStr | None = None
    location: ShortStr | None = None
    contact: CandidateContact = CandidateContact()
    preferences: CandidatePreferences = CandidatePreferences()


class CandidateProfileIn(CandidateBase):
    """A complete knowledge-base import: profile, evidence sources, and facts."""

    schema_version: Literal["candidate-profile/v1"] = "candidate-profile/v1"
    evidence_sources: list[EvidenceSourceIn] = []
    facts: list[FactIn] = []

    @model_validator(mode="after")
    def _keys(self) -> Self:
        evidence_keys = [e.key for e in self.evidence_sources if e.key]
        fact_keys = [f.key for f in self.facts if f.key]
        if len(set(evidence_keys)) != len(evidence_keys):
            raise ValueError("evidence source keys must be unique")
        if len(set(fact_keys)) != len(fact_keys):
            raise ValueError("fact keys must be unique")
        for fact in self.facts:
            if fact.evidence_source_ids or fact.parent_fact_id:
                raise ValueError("use evidence_keys/parent_key (not ids) in a profile import")
            missing = set(fact.evidence_keys) - set(evidence_keys)
            if missing:
                raise ValueError(f"unknown evidence keys: {sorted(missing)}")
            if fact.parent_key and fact.parent_key not in fact_keys:
                raise ValueError(f"unknown parent key: {fact.parent_key}")
        return self


class CandidateUpdate(_Strict):
    full_name: ShortStr | None = None
    headline: ShortStr | None = None
    summary: LongStr | None = None
    location: ShortStr | None = None
    contact: CandidateContact | None = None
    preferences: CandidatePreferences | None = None


class CandidateSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    headline: str | None
    created_at: datetime
    updated_at: datetime


class CandidateOut(CandidateSummaryOut):
    summary: str | None
    location: str | None
    contact: CandidateContact
    preferences: CandidatePreferences
    evidence_sources: list[EvidenceSourceOut]
    facts: list[FactOut]
