"""Deterministic, rule-based extraction and classification of job requirements.

Job descriptions are untrusted input. This module only parses text; it never follows
instructions found in it. Classification is keyword-based and conservative: anything it
cannot interpret is kept as an ``other`` requirement so matching reports it as unknown,
instead of silently dropping it.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from next_role.skills import detect_skills, skill_family


class RequirementCategory(StrEnum):
    SKILL = "skill"
    EXPERIENCE_YEARS = "experience_years"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    LANGUAGE = "language"
    WORK_AUTHORIZATION = "work_authorization"
    OTHER = "other"


class RequirementImportance(StrEnum):
    REQUIRED = "required"
    PREFERRED = "preferred"
    # The posting does not indicate whether this is required.
    UNSPECIFIED = "unspecified"


class WorkArrangement(StrEnum):
    REMOTE = "remote"
    HYBRID = "hybrid"
    ONSITE = "onsite"
    UNKNOWN = "unknown"


class EmploymentType(StrEnum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    TEMPORARY = "temporary"
    INTERNSHIP = "internship"
    UNKNOWN = "unknown"


# Education levels, ordered. Matches next_role.candidates.schemas.DegreeLevel values.
EDUCATION_LEVELS = ("high_school", "associate", "bachelor", "master", "doctorate")


@dataclass
class ExtractedRequirement:
    text: str
    category: RequirementCategory
    importance: RequirementImportance
    terms: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


# --- Sections -----------------------------------------------------------------------------

_REQUIRED_HEADERS = (
    "requirements",
    "required qualifications",
    "minimum qualifications",
    "basic qualifications",
    "qualifications",
    "required skills",
    "skills",
    "what you'll need",
    "what you will need",
    "what you bring",
    "what we're looking for",
    "what we are looking for",
    "must have",
    "must haves",
    "must-haves",
    "you have",
    "about you",
    "who you are",
)
_PREFERRED_HEADERS = (
    "preferred qualifications",
    "preferred skills",
    "preferred",
    "nice to have",
    "nice to haves",
    "nice-to-have",
    "nice-to-haves",
    "bonus points",
    "bonus",
    "pluses",
    "desired qualifications",
    "desired skills",
    "good to have",
)
_OTHER_HEADERS = (
    "responsibilities",
    "what you'll do",
    "what you will do",
    "the role",
    "about the role",
    "about us",
    "about the company",
    "who we are",
    "benefits",
    "perks",
    "compensation",
    "salary",
    "how to apply",
    "our values",
    "location",
)

_BULLET = re.compile(r"^\s*(?:[-*•·▪◦]|\d+[.)])\s+")
_HEADER_STRIP = re.compile(r"^[#\s*_]+|[\s*_:]+$")


def _header_kind(line: str) -> RequirementImportance | str | None:
    """Return importance for requirement sections, "other" for non-requirement sections."""
    if _BULLET.match(line) or len(line) > 60:
        return None
    text = _HEADER_STRIP.sub("", line.strip()).lower().replace("\u2019", "'")
    if not text:
        return None
    if text in _PREFERRED_HEADERS:
        return RequirementImportance.PREFERRED
    if text in _REQUIRED_HEADERS:
        return RequirementImportance.REQUIRED
    if text in _OTHER_HEADERS:
        return "other"
    return None


# --- Classification -----------------------------------------------------------------------

_YEARS = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-|to|\u2013)?\s*(?:\d{1,2}\s*)?\+?\s*(?:years?|yrs?)\b", re.IGNORECASE
)
_EDUCATION = {
    "doctorate": re.compile(r"\b(?:ph\.?\s?d|doctorate|doctoral)\b", re.IGNORECASE),
    "master": re.compile(r"\b(?:master'?s?|m\.?sc?\.?|mba)\b", re.IGNORECASE),
    "bachelor": re.compile(r"\b(?:bachelor'?s?|b\.?sc?\.?|b\.?a\.?|undergraduate)\b", re.I),
    "associate": re.compile(r"\bassociate'?s? degree\b", re.IGNORECASE),
    "high_school": re.compile(r"\b(?:high school|ged|secondary school)\b", re.IGNORECASE),
}
_DEGREE_WORD = re.compile(r"\bdegree\b", re.IGNORECASE)
_CERTIFICATION = re.compile(r"\b(?:certifi\w*|certified|licen[sc]e[ds]?)\b", re.IGNORECASE)
_WORK_AUTH = re.compile(
    r"\b(?:authori[sz]ed to work|work authori[sz]ation|right to work|eligible to work|"
    r"sponsor\w*|visa|citizenship|citizen|permanent resident)\b",
    re.IGNORECASE,
)
_NO_SPONSORSHIP = re.compile(
    r"\b(?:no|not|unable to|cannot|can't|won't|will not|without)\b[^.]{0,40}\bsponsor",
    re.IGNORECASE,
)
_SPONSORSHIP_OK = re.compile(
    r"\b(?:sponsorship (?:is )?available|will sponsor|visa sponsorship provided|"
    r"(?:we|can) sponsor)\b",
    re.IGNORECASE,
)
_COUNTRIES = {
    # Case-sensitive abbreviations so "join us" is not read as a country.
    "US": re.compile(r"\bU\.S\.(?:A\.)?|\bUSA?\b|(?i:\bunited states\b)"),
    "CA": re.compile(r"\bcanada\b", re.IGNORECASE),
    "GB": re.compile(r"\bUK\b|\bU\.K\.|(?i:\b(?:united kingdom|great britain)\b)"),
    "DE": re.compile(r"\bgermany\b", re.IGNORECASE),
    "IE": re.compile(r"\bireland\b", re.IGNORECASE),
    "AU": re.compile(r"\baustralia\b", re.IGNORECASE),
    "IN": re.compile(r"\bindia\b", re.IGNORECASE),
}
_LANGUAGE_CUE = re.compile(r"\b(?:fluen\w*|native|proficien\w*|speak\w*|written|spoken)\b", re.I)
_PREFERRED_CUE = re.compile(
    r"\b(?:preferred|nice to have|a plus|is a plus|bonus|ideally|desirable|advantageous)\b",
    re.IGNORECASE,
)
_REQUIRED_CUE = re.compile(r"\b(?:required|must|mandatory)\b", re.IGNORECASE)


def _education_level(text: str) -> str | None:
    """Lowest education level mentioned (requirements usually say "X or higher")."""
    for level in EDUCATION_LEVELS:
        if _EDUCATION[level].search(text):
            return level
    return None


def classify_requirement(
    text: str, importance: RequirementImportance = RequirementImportance.UNSPECIFIED
) -> ExtractedRequirement:
    """Classify one requirement statement and extract the details matching needs."""
    clean = _BULLET.sub("", text).strip()
    terms = detect_skills(clean)
    details: dict[str, Any] = {}

    if _PREFERRED_CUE.search(clean):
        importance = RequirementImportance.PREFERRED
    elif importance is RequirementImportance.UNSPECIFIED and _REQUIRED_CUE.search(clean):
        importance = RequirementImportance.REQUIRED

    category: RequirementCategory
    if _WORK_AUTH.search(clean):
        category = RequirementCategory.WORK_AUTHORIZATION
        details["countries"] = [code for code, rx in _COUNTRIES.items() if rx.search(clean)]
        if _NO_SPONSORSHIP.search(clean):
            details["sponsorship_available"] = False
        elif _SPONSORSHIP_OK.search(clean):
            details["sponsorship_available"] = True
        terms = []
    elif years := _YEARS.search(clean):
        category = RequirementCategory.EXPERIENCE_YEARS
        details["min_years"] = int(years.group(1))
    elif (level := _education_level(clean)) or _DEGREE_WORD.search(clean):
        category = RequirementCategory.EDUCATION
        details["education_level"] = level
    elif _CERTIFICATION.search(clean):
        category = RequirementCategory.CERTIFICATION
    elif terms and all(skill_family(t) == "human_language" for t in terms):
        category = RequirementCategory.LANGUAGE
    elif terms:
        category = RequirementCategory.SKILL
    elif _LANGUAGE_CUE.search(clean) and "english" in clean.lower():
        category = RequirementCategory.LANGUAGE
        terms = ["english"]
    else:
        category = RequirementCategory.OTHER
    return ExtractedRequirement(clean[:1000], category, importance, terms, details)


# --- Extraction ---------------------------------------------------------------------------


def _looks_like_requirement(text: str) -> bool:
    return bool(
        detect_skills(text)
        or _YEARS.search(text)
        or _education_level(text)
        or _CERTIFICATION.search(text)
        or _WORK_AUTH.search(text)
    )


def extract_requirements(description: str) -> list[ExtractedRequirement]:
    """Extract requirement statements from a free-text job description.

    1. If the description has requirement-like section headers, the bullets in those
       sections become requirements (every line, if a section has no bullets). Importance
       comes from the section.
    2. Otherwise, bullets anywhere outside known non-requirement sections that mention a
       recognizable requirement (skill, years, degree, ...) are used, importance unspecified.
    3. Otherwise, sentences that mention a recognizable requirement are used.
    """
    lines = [line.rstrip() for line in description.splitlines()]
    sectioned: list[ExtractedRequirement] = []
    loose_bullets: list[str] = []
    current: RequirementImportance | str | None = None
    section_lines: list[str] = []
    saw_requirement_header = False

    def close_section() -> None:
        # In a bulleted section, only bullets are requirements; trailing prose is not.
        if isinstance(current, RequirementImportance):
            bullets = [ln for ln in section_lines if _BULLET.match(ln)]
            sectioned.extend(classify_requirement(ln, current) for ln in bullets or section_lines)
        section_lines.clear()

    for line in lines:
        if not line.strip():
            continue
        kind = _header_kind(line)
        if kind is not None:
            close_section()
            current = kind
            saw_requirement_header |= kind != "other"
            continue
        if isinstance(current, RequirementImportance):
            section_lines.append(line)
        elif current is None and _BULLET.match(line) and _looks_like_requirement(line):
            loose_bullets.append(line)
    close_section()

    if saw_requirement_header:
        return dedupe_requirements(sectioned)
    if loose_bullets:
        return dedupe_requirements([classify_requirement(b) for b in loose_bullets])
    sentences = re.split(r"(?<=[.!?;])\s+|\n+", description)
    return dedupe_requirements(
        [classify_requirement(s) for s in sentences if s.strip() and _looks_like_requirement(s)]
    )


def requirement_key(text: str) -> str:
    """Case-, punctuation- and whitespace-insensitive form used to spot repeated requirements."""
    return re.sub(r"\W+", " ", text.lower()).strip()


def dedupe_requirements(items: list[ExtractedRequirement]) -> list[ExtractedRequirement]:
    """Keep the first of requirements that differ only in case, punctuation or spacing."""
    seen: set[str] = set()
    result: list[ExtractedRequirement] = []
    for item in items:
        key = requirement_key(item.text)
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


# --- Posting-level attributes -------------------------------------------------------------

_ARRANGEMENT_PATTERNS = (
    (WorkArrangement.HYBRID, re.compile(r"\bhybrid\b", re.IGNORECASE)),
    (WorkArrangement.REMOTE, re.compile(r"\b(?:fully remote|remote[- ]first|remote)\b", re.I)),
    (WorkArrangement.ONSITE, re.compile(r"\b(?:on[- ]?site|in[- ]office|in office)\b", re.I)),
)
_EMPLOYMENT_PATTERNS = (
    (EmploymentType.INTERNSHIP, re.compile(r"\b(?:internship|intern)\b", re.IGNORECASE)),
    (
        EmploymentType.CONTRACT,
        re.compile(r"\b(?:contract (?:role|position|basis)|contractor|freelance)\b", re.I),
    ),
    (EmploymentType.PART_TIME, re.compile(r"\bpart[- ]time\b", re.IGNORECASE)),
    (EmploymentType.TEMPORARY, re.compile(r"\b(?:temporary|fixed[- ]term)\b", re.I)),
    (
        EmploymentType.FULL_TIME,
        re.compile(r"\b(?:full[- ]time|permanent (?:role|position))\b", re.IGNORECASE),
    ),
)


def infer_work_arrangement(*texts: str | None) -> WorkArrangement:
    """Texts are checked in order; within a text, hybrid wins over remote ("hybrid remote")."""
    for text in texts:
        if not text:
            continue
        for arrangement, pattern in _ARRANGEMENT_PATTERNS:
            if pattern.search(text):
                return arrangement
    return WorkArrangement.UNKNOWN


def infer_employment_type(*texts: str | None) -> EmploymentType:
    for text in texts:
        if not text:
            continue
        for employment_type, pattern in _EMPLOYMENT_PATTERNS:
            if pattern.search(text):
                return employment_type
    return EmploymentType.UNKNOWN
