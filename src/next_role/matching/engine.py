"""Deterministic, evidence-backed requirement matching and suitability scoring.

This module is pure: it takes plain data views of a candidate and a job and returns an
explainable result. Persistence lives in ``next_role.matching.service``. The methodology is
documented in docs/matching.md; keep the two in sync and bump ``METHOD_VERSION`` whenever
rules or weights change.

Core principles:

* Only *confirmed* facts (``user_verified``) can make a requirement ``met`` or
  ``partially_met``. Linked evidence does not change that (ADR 0009). Unconfirmed claims
  produce ``unverified``.
* Absence of evidence (``no_evidence``) is distinct from a confirmed mismatch (``not_met``).
  ``not_met`` is only used when supported candidate information contradicts the requirement.
* Requirements the rules cannot interpret are ``unknown``. They are never assumed satisfied.
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum
from functools import cached_property
from typing import Any

from next_role.candidates.schemas import CandidatePreferences, FactSupport
from next_role.jobs.extraction import (
    EDUCATION_LEVELS,
    RequirementCategory,
    RequirementImportance,
)
from next_role.skills import detect_skills, is_known_skill, skill_family

METHOD = "rules"
METHOD_VERSION = "rules-v2"

IMPORTANCE_WEIGHTS: dict[RequirementImportance, int] = {
    RequirementImportance.REQUIRED: 3,
    RequirementImportance.UNSPECIFIED: 2,
    RequirementImportance.PREFERRED: 1,
}

# Families too broad for one member to partially substitute for another
# (knowing Java says little about Python; English says nothing about Spanish).
NON_SUBSTITUTABLE_FAMILIES = frozenset(
    {"programming_language", "human_language", "query_language", "scripting", "process"}
)

PARTIAL_YEARS_RATIO = 0.7
STRONG_THRESHOLD = 75
MODERATE_THRESHOLD = 50
MIN_COVERAGE_FOR_TIER = 0.4


class MatchStatus(StrEnum):
    MET = "met"
    PARTIALLY_MET = "partially_met"
    # Confirmed candidate information contradicts the requirement.
    NOT_MET = "not_met"
    # Only unconfirmed (self-reported) candidate claims address it.
    UNVERIFIED = "unverified"
    # The knowledge base contains nothing relevant.
    NO_EVIDENCE = "no_evidence"
    # The requirement could not be interpreted or evaluated by the rules.
    UNKNOWN = "unknown"


CREDIT: dict[MatchStatus, float] = {MatchStatus.MET: 1.0, MatchStatus.PARTIALLY_MET: 0.5}
DETERMINED = frozenset({MatchStatus.MET, MatchStatus.PARTIALLY_MET, MatchStatus.NOT_MET})


class Tier(StrEnum):
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    INSUFFICIENT_DATA = "insufficient_data"


class PreferenceStatus(StrEnum):
    COMPATIBLE = "compatible"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


# --- Input views -------------------------------------------------------------------------


@dataclass(frozen=True)
class FactView:
    id: uuid.UUID
    fact_type: str
    title: str
    organization: str | None
    text: str  # description and other free text (e.g. responsibilities)
    skills: tuple[str, ...]
    start_date: date | None
    end_date: date | None
    is_current: bool
    support: FactSupport
    attributes: dict[str, Any] = field(default_factory=dict)

    @cached_property
    def all_skills(self) -> frozenset[str]:
        """Explicit skills plus skills detected in the title and free text (computed once)."""
        return frozenset(self.skills) | frozenset(detect_skills(f"{self.title} {self.text}"))

    @property
    def supported(self) -> bool:
        """Whether the fact can satisfy a requirement: only a confirmed fact can."""
        return self.support is FactSupport.CONFIRMED

    @property
    def label(self) -> str:
        return f"{self.title} ({self.organization})" if self.organization else self.title


@dataclass(frozen=True)
class RequirementView:
    id: uuid.UUID
    text: str
    category: RequirementCategory
    importance: RequirementImportance
    terms: tuple[str, ...]
    details: dict[str, Any]


@dataclass(frozen=True)
class JobView:
    location: str | None
    work_arrangement: str
    employment_type: str
    salary_min: int | None
    salary_max: int | None
    salary_currency: str | None
    requirements: tuple[RequirementView, ...]


@dataclass(frozen=True)
class CandidateView:
    preferences: CandidatePreferences
    facts: tuple[FactView, ...]
    # Candidate.location: the acceptable location when preferences name none.
    home_location: str | None = None


# --- Output ------------------------------------------------------------------------------


@dataclass(frozen=True)
class EvidenceRef:
    fact_id: uuid.UUID | None  # None for candidate preferences
    fact_type: str
    title: str
    support: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "fact_id": str(self.fact_id) if self.fact_id else None,
            "fact_type": self.fact_type,
            "title": self.title,
            "support": self.support,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class RequirementResult:
    requirement_id: uuid.UUID
    status: MatchStatus
    weight: int
    credit: float
    explanation: str
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True)
class PreferenceCheck:
    name: str
    status: PreferenceStatus
    explanation: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "status": self.status.value, "explanation": self.explanation}


@dataclass(frozen=True)
class AssessmentResult:
    score: int | None
    score_excluding_unknowns: int | None
    coverage: float
    tier: Tier
    has_blocking_gaps: bool
    results: tuple[RequirementResult, ...]
    preference_checks: tuple[PreferenceCheck, ...]
    status_counts: dict[str, int]
    method: str = METHOD
    method_version: str = METHOD_VERSION


# --- Skill evidence ----------------------------------------------------------------------


@dataclass(frozen=True)
class _TermEvidence:
    status: MatchStatus
    evidence: tuple[EvidenceRef, ...]
    note: str


def _ref(fact: FactView, reason: str) -> EvidenceRef:
    return EvidenceRef(fact.id, fact.fact_type, fact.label, fact.support.value, reason)


def _direct_mentions(fact: FactView, term: str) -> str | None:
    if term in fact.skills:
        return "lists skill" if fact.fact_type == "skill" else "uses skill"
    if term in fact.all_skills:
        return "mentions skill"
    return None


def _term_evidence(term: str, facts: Sequence[FactView]) -> _TermEvidence:
    direct = [(f, why) for f in facts if (why := _direct_mentions(f, term))]
    supported = [_ref(f, f"{why} '{term}'") for f, why in direct if f.supported]
    if supported:
        return _TermEvidence(MatchStatus.MET, tuple(supported), f"'{term}' is supported")

    family = skill_family(term)
    if family and family not in NON_SUBSTITUTABLE_FAMILIES:
        related = [
            _ref(f, f"related skill '{s}' ({family.replace('_', ' ')})")
            for f in facts
            if f.supported
            for s in sorted(f.all_skills)
            if s != term and skill_family(s) == family
        ]
        if related:
            return _TermEvidence(
                MatchStatus.PARTIALLY_MET,
                tuple(related),
                f"no direct evidence for '{term}', but related skills are supported",
            )

    unsupported = [_ref(f, f"{why} '{term}' (unconfirmed)") for f, why in direct]
    if unsupported:
        return _TermEvidence(
            MatchStatus.UNVERIFIED,
            tuple(unsupported),
            f"'{term}' is claimed but not confirmed",
        )
    return _TermEvidence(MatchStatus.NO_EVIDENCE, (), f"no evidence of '{term}'")


_ANY_OF = re.compile(r"\b(?:or|either|one of|any of)\b", re.IGNORECASE)
# "..., or equivalent experience" / "or similar" do not make the listed skills alternatives.
_OR_QUALIFIER = re.compile(
    r",?\s*\bor\s+(?:an?\s+)?(?:equivalent|similar|comparable|related|other)\b.*$", re.I
)
_RANK = {
    MatchStatus.MET: 0,
    MatchStatus.PARTIALLY_MET: 1,
    MatchStatus.UNVERIFIED: 2,
    MatchStatus.NO_EVIDENCE: 3,
}


def _combine_terms(req: RequirementView, per_term: list[_TermEvidence]) -> tuple[MatchStatus, str]:
    statuses = [t.status for t in per_term]
    if len(per_term) > 1 and _ANY_OF.search(_OR_QUALIFIER.sub("", req.text)):
        best = min(statuses, key=_RANK.__getitem__)
        return best, "any one of the listed skills satisfies this requirement"
    if all(s is MatchStatus.MET for s in statuses):
        return MatchStatus.MET, "all listed skills are supported"
    if any(s in (MatchStatus.MET, MatchStatus.PARTIALLY_MET) for s in statuses):
        return MatchStatus.PARTIALLY_MET, "some listed skills are supported"
    if any(s is MatchStatus.UNVERIFIED for s in statuses):
        return MatchStatus.UNVERIFIED, "relevant claims exist but none are confirmed"
    return MatchStatus.NO_EVIDENCE, "the knowledge base has no evidence for these skills"


def _unlisted_skill_terms(text: str, facts: Sequence[FactView]) -> tuple[str, ...]:
    """Candidate skills outside the vocabulary that the requirement names verbatim.

    Free-text detection only knows the vocabulary, so "COBOL required" has no terms. Skills the
    candidate listed explicitly can still be matched by exact (normalized) name.
    """
    clean = re.sub(r"\s+", " ", text.lower())
    found: dict[str, int] = {}
    for name in {s for f in facts for s in f.skills if not is_known_skill(s)}:
        if m := re.search(rf"(?<![\w+#.]){re.escape(name)}(?![\w+#])", clean):
            found[name] = m.start()
    return tuple(sorted(found, key=found.__getitem__))


def _match_skills(req: RequirementView, facts: Sequence[FactView]) -> RequirementResult:
    per_term = [_term_evidence(t, facts) for t in req.terms]
    status, summary = _combine_terms(req, per_term)
    notes = "; ".join(t.note for t in per_term)
    evidence = tuple(e for t in per_term for e in t.evidence)
    return _result(req, status, f"{summary.capitalize()}: {notes}.", evidence)


# --- Category matchers -------------------------------------------------------------------


def _is_dated(fact: FactView) -> bool:
    return fact.start_date is not None and (fact.end_date is not None or fact.is_current)


def _total_years(facts: Iterable[FactView], as_of: date) -> float:
    """Total experience in years, merging overlapping or adjacent date ranges.

    Ranges are inclusive of both dates, so 2020-01-01..2024-12-31 is 5.0 years.
    """
    intervals = sorted(
        (f.start_date, min(f.end_date or as_of, as_of))
        for f in facts
        if _is_dated(f) and f.start_date is not None and f.start_date <= as_of
    )
    days = 0
    current: tuple[date, date] | None = None
    for start, end in intervals:
        if current is None or start > current[1] + timedelta(days=1):
            if current:
                days += (current[1] - current[0]).days + 1
            current = (start, end)
        else:
            current = (current[0], max(current[1], end))
    if current:
        days += (current[1] - current[0]).days + 1
    return round(max(days, 0) / 365.25, 1)


# Words that describe general experience rather than a specific skill area.
_GENERIC_EXPERIENCE_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "as",
        "at",
        "backend",
        "building",
        "commercial",
        "development",
        "engineer",
        "engineering",
        "experience",
        "full",
        "hands",
        "hands-on",
        "in",
        "industry",
        "it",
        "of",
        "on",
        "or",
        "production",
        "professional",
        "progressive",
        "related",
        "relevant",
        "role",
        "software",
        "stack",
        "technical",
        "the",
        "time",
        "with",
        "work",
        "working",
        "years",
        "year",
        "plus",
        "minimum",
        "least",
        "including",
        "developer",
        "programming",
    }
)
_EXPERIENCE_AREA = re.compile(
    r"(?:years?|yrs?)\+?\s+(?:of\s+|in\s+|with\s+)?(?P<area>[^.;:()]*)", re.IGNORECASE
)


def _unrecognized_experience_area(text: str) -> str | None:
    """The specific area of a years requirement, if it names something we cannot detect.

    "5+ years of professional software development" -> None (general experience).
    "3+ years of Go" -> "Go" ("go" is too ambiguous to detect in free text).
    """
    match = _EXPERIENCE_AREA.search(text)
    if not match:
        return None
    area = match.group("area").strip()
    words = re.findall(r"[a-z][a-z+#.\-]*", area.lower())
    return area if any(w not in _GENERIC_EXPERIENCE_WORDS for w in words) else None


def _match_experience_years(
    req: RequirementView, facts: Sequence[FactView], as_of: date
) -> RequirementResult:
    min_years = req.details.get("min_years")
    if not isinstance(min_years, int | float) or min_years <= 0:
        return _result(req, MatchStatus.UNKNOWN, "The required years could not be determined.")

    experiences = [f for f in facts if f.fact_type == "experience"]
    if not req.terms and (area := _unrecognized_experience_area(req.text)):
        return _result(
            req,
            MatchStatus.UNKNOWN,
            f"The experience area ('{area}') is not recognized by the rules; review manually.",
        )
    if req.terms:
        # Skill years come from dated work and projects that list or mention the skill.
        timed = [f for f in facts if f.fact_type in ("experience", "project")]
        relevant = [f for f in timed if any(_direct_mentions(f, t) for t in req.terms)]
        scope = f"experience involving {', '.join(req.terms)}"
    else:
        relevant = experiences
        scope = "professional experience"

    supported = [f for f in relevant if f.supported]
    dated = [f for f in supported if _is_dated(f)]
    years = _total_years(dated, as_of)
    evidence = [_ref(f, f"dated {f.fact_type}") for f in dated]

    # A stated skill duration ("Python, 8 years") is used only when no dated fact shows it.
    if not dated:
        for term in req.terms:
            for f in facts:
                stated = f.attributes.get("years_experience")
                is_skill = f.fact_type == "skill" and f.supported and term in f.skills
                if is_skill and stated and float(stated) > years:
                    years = float(stated)
                    evidence = [_ref(f, f"states {stated:g} years of '{term}' (stated)")]

    if not evidence:
        if supported:
            return _result(
                req,
                MatchStatus.UNKNOWN,
                f"Confirmed {scope} exists, but its dates are incomplete; duration unknown.",
                tuple(_ref(f, f"undated {f.fact_type}") for f in supported),
            )
        if relevant:
            return _result(
                req,
                MatchStatus.UNVERIFIED,
                f"Relevant {scope} is claimed but not confirmed.",
                tuple(_ref(f, f"unconfirmed {f.fact_type}") for f in relevant),
            )
        return _result(req, MatchStatus.NO_EVIDENCE, f"No {scope} is recorded.")

    stated_only = not dated
    detail = (
        f"Stated {scope} is {years:g} years; {min_years:g}+ required."
        if stated_only
        else f"Confirmed {scope} totals {years:g} years; {min_years:g}+ required."
    )
    if years >= min_years:
        return _result(req, MatchStatus.MET, detail, tuple(evidence))
    if years >= min_years * PARTIAL_YEARS_RATIO:
        return _result(req, MatchStatus.PARTIALLY_MET, detail, tuple(evidence))
    return _result(req, MatchStatus.NOT_MET, detail, tuple(evidence))


def _match_education(req: RequirementView, facts: Sequence[FactView]) -> RequirementResult:
    level = req.details.get("education_level")
    required_rank = EDUCATION_LEVELS.index(level) if level in EDUCATION_LEVELS else None
    # A bare "degree" requirement is satisfied by any post-secondary degree.
    if required_rank is None:
        required_rank = EDUCATION_LEVELS.index("associate")
    needed = EDUCATION_LEVELS[required_rank].replace("_", " ")

    education = [f for f in facts if f.fact_type == "education"]
    completed = [f for f in education if f.supported and not f.is_current]
    supported = [f for f in completed if f.attributes.get("degree_level") in EDUCATION_LEVELS]
    if not supported:
        if completed:
            return _result(
                req,
                MatchStatus.UNKNOWN,
                "Confirmed education is recorded with a degree level ('other') the rules "
                "cannot compare; review manually.",
                tuple(_ref(f, "education of unranked level") for f in completed),
            )
        if education:
            return _result(
                req,
                MatchStatus.UNVERIFIED,
                "Education is recorded but not confirmed or not completed.",
                tuple(_ref(f, "unverified or in-progress education") for f in education),
            )
        return _result(req, MatchStatus.NO_EVIDENCE, "No education is recorded.")

    best = max(supported, key=lambda f: EDUCATION_LEVELS.index(f.attributes["degree_level"]))
    best_level = best.attributes["degree_level"]
    evidence = (_ref(best, f"{best_level.replace('_', ' ')} degree"),)
    if EDUCATION_LEVELS.index(best_level) >= required_rank:
        return _result(
            req,
            MatchStatus.MET,
            f"Highest supported degree ({best_level}) meets the {needed} level. "
            "Field of study is not evaluated by the rules.",
            evidence,
        )
    return _result(
        req,
        MatchStatus.NOT_MET,
        f"Highest supported degree ({best_level}) is below the required {needed} level.",
        evidence,
    )


_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "any",
        "be",
        "certification",
        "certificate",
        "certifications",
        "certified",
        "current",
        "experience",
        "has",
        "have",
        "hold",
        "holding",
        "in",
        "is",
        "must",
        "of",
        "or",
        "plus",
        "possess",
        "preferred",
        "relevant",
        "required",
        "should",
        "the",
        "to",
        "valid",
        "with",
        "active",
    ]
)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]+", text.lower()) if w not in _STOPWORDS}


def _match_certification(
    req: RequirementView, facts: Sequence[FactView], as_of: date
) -> RequirementResult:
    certs = [f for f in facts if f.fact_type == "certification"]
    req_words = _words(req.text)

    def relevant(cert: FactView) -> bool:
        if req.terms and set(req.terms) & cert.all_skills:
            return True
        cert_words = _words(cert.title)
        if not req_words or not cert_words:
            return False
        overlap = len(req_words & cert_words)
        return overlap / min(len(req_words), len(cert_words)) >= 0.5

    matching = [c for c in certs if relevant(c)]
    supported = [c for c in matching if c.supported]
    current = [c for c in supported if not c.end_date or c.end_date >= as_of]
    if current:
        return _result(
            req,
            MatchStatus.MET,
            "A matching certification is supported and current.",
            tuple(_ref(c, "matching certification") for c in current),
        )
    if supported:
        return _result(
            req,
            MatchStatus.PARTIALLY_MET,
            "A matching certification is supported but has expired.",
            tuple(_ref(c, f"expired {c.end_date}") for c in supported),
        )
    if matching:
        return _result(
            req,
            MatchStatus.UNVERIFIED,
            "A matching certification is claimed but not confirmed.",
            tuple(_ref(c, "unverified certification") for c in matching),
        )
    if not req.terms and not req_words:
        return _result(req, MatchStatus.UNKNOWN, "The certification could not be identified.")
    return _result(req, MatchStatus.NO_EVIDENCE, "No matching certification is recorded.")


def _match_work_authorization(
    req: RequirementView, prefs: CandidatePreferences
) -> RequirementResult:
    countries: list[str] = list(req.details.get("countries") or [])
    sponsorship = req.details.get("sponsorship_available")
    authorized = set(prefs.work_authorizations)
    pref_ref = EvidenceRef(
        None,
        "preferences",
        "Candidate work authorization",
        "candidate_declared",
        f"authorized in: {', '.join(sorted(authorized)) or 'not stated'}",
    )
    if not countries:
        return _result(
            req,
            MatchStatus.UNKNOWN,
            "The posting does not name the country; authorization cannot be checked.",
        )
    if not authorized:
        return _result(
            req, MatchStatus.NO_EVIDENCE, "The candidate has not stated work authorization."
        )
    missing = [c for c in countries if c not in authorized]
    if not missing:
        return _result(
            req,
            MatchStatus.MET,
            f"Candidate declares authorization in {', '.join(countries)} "
            "(self-declared; confirm before applying).",
            (pref_ref,),
        )
    where = ", ".join(missing)
    if sponsorship is True:
        return _result(
            req,
            MatchStatus.PARTIALLY_MET,
            f"Candidate is not authorized in {where}; the posting says sponsorship is available.",
            (pref_ref,),
        )
    needs = (
        " The candidate has indicated they require sponsorship."
        if prefs.requires_sponsorship
        else ""
    )
    if sponsorship is False:
        return _result(
            req,
            MatchStatus.NOT_MET,
            f"Candidate is not authorized in {where} and the posting says sponsorship is not "
            f"available.{needs}",
            (pref_ref,),
        )
    return _result(
        req,
        MatchStatus.UNKNOWN,
        f"Candidate is not authorized in {where}; the posting does not say whether sponsorship "
        f"is available.{needs}",
        (pref_ref,),
    )


def _result(
    req: RequirementView,
    status: MatchStatus,
    explanation: str,
    evidence: tuple[EvidenceRef, ...] = (),
) -> RequirementResult:
    weight = IMPORTANCE_WEIGHTS[req.importance]
    return RequirementResult(req.id, status, weight, CREDIT.get(status, 0.0), explanation, evidence)


def match_requirement(
    req: RequirementView, candidate: CandidateView, as_of: date
) -> RequirementResult:
    facts = candidate.facts
    match req.category:
        case RequirementCategory.EXPERIENCE_YEARS:
            return _match_experience_years(req, facts, as_of)
        case RequirementCategory.EDUCATION:
            return _match_education(req, facts)
        case RequirementCategory.CERTIFICATION:
            return _match_certification(req, facts, as_of)
        case RequirementCategory.WORK_AUTHORIZATION:
            return _match_work_authorization(req, candidate.preferences)
        case _ if req.terms:
            return _match_skills(req, facts)
        case RequirementCategory.OTHER if terms := _unlisted_skill_terms(req.text, facts):
            return _match_skills(replace(req, terms=terms), facts)
        case _:
            return _result(
                req,
                MatchStatus.UNKNOWN,
                "Not interpretable by the rule-based matcher; review manually.",
            )


# --- Preferences (reported separately; not part of the score) ----------------------------


def _check_preferences(
    job: JobView, prefs: CandidatePreferences, home_location: str | None = None
) -> tuple[PreferenceCheck, ...]:
    checks: list[PreferenceCheck] = []
    # Preferred locations when stated, otherwise where the candidate lives.
    locations = prefs.locations or ([home_location] if home_location else [])
    preferred = "a preferred location" if prefs.locations else "the candidate's home location"

    arrangements = {a.value for a in prefs.work_arrangements}
    if job.work_arrangement == "unknown" or not arrangements:
        checks.append(
            PreferenceCheck(
                "work_arrangement", PreferenceStatus.UNKNOWN, "Arrangement or preference unknown."
            )
        )
    else:
        ok = job.work_arrangement in arrangements
        checks.append(
            PreferenceCheck(
                "work_arrangement",
                PreferenceStatus.COMPATIBLE if ok else PreferenceStatus.CONFLICT,
                f"Job is {job.work_arrangement}; "
                f"candidate accepts {', '.join(sorted(arrangements))}.",
            )
        )

    if job.work_arrangement == "remote":
        checks.append(
            PreferenceCheck(
                "location",
                PreferenceStatus.COMPATIBLE,
                "Job is remote; regional restrictions, if any, are not evaluated.",
            )
        )
    elif not job.location or not locations:
        checks.append(
            PreferenceCheck("location", PreferenceStatus.UNKNOWN, "Location or preference unknown.")
        )
    elif any(
        re.search(rf"(?<!\w){re.escape(p)}(?!\w)", job.location, re.IGNORECASE) for p in locations
    ):
        checks.append(
            PreferenceCheck(
                "location", PreferenceStatus.COMPATIBLE, f"{job.location} is {preferred}."
            )
        )
    elif prefs.willing_to_relocate:
        checks.append(
            PreferenceCheck(
                "location",
                PreferenceStatus.COMPATIBLE,
                f"{job.location} is not {preferred}, but the candidate will relocate.",
            )
        )
    else:
        status = (
            PreferenceStatus.CONFLICT
            if prefs.willing_to_relocate is False
            else PreferenceStatus.UNKNOWN
        )
        checks.append(PreferenceCheck("location", status, f"{job.location} is not {preferred}."))

    types = {t.value for t in prefs.employment_types}
    if job.employment_type == "unknown" or not types:
        checks.append(
            PreferenceCheck(
                "employment_type",
                PreferenceStatus.UNKNOWN,
                "Employment type or preference unknown.",
            )
        )
    else:
        ok = job.employment_type in types
        checks.append(
            PreferenceCheck(
                "employment_type",
                PreferenceStatus.COMPATIBLE if ok else PreferenceStatus.CONFLICT,
                f"Job is {job.employment_type}; candidate accepts {', '.join(sorted(types))}.",
            )
        )

    top = job.salary_max or job.salary_min
    if top is None or prefs.min_salary is None:
        checks.append(
            PreferenceCheck("salary", PreferenceStatus.UNKNOWN, "Salary or expectation unknown.")
        )
    elif job.salary_currency != prefs.salary_currency:
        checks.append(
            PreferenceCheck(
                "salary",
                PreferenceStatus.UNKNOWN,
                f"Currencies differ ({job.salary_currency} vs {prefs.salary_currency}).",
            )
        )
    else:
        ok = top >= prefs.min_salary
        checks.append(
            PreferenceCheck(
                "salary",
                PreferenceStatus.COMPATIBLE if ok else PreferenceStatus.CONFLICT,
                f"Posted salary up to {top} {job.salary_currency}; "
                f"candidate minimum {prefs.min_salary}.",
            )
        )
    return tuple(checks)


# --- Assessment --------------------------------------------------------------------------


def assess(candidate: CandidateView, job: JobView, as_of: date) -> AssessmentResult:
    """Evaluate every requirement and compute the suitability score (see docs/matching.md)."""
    results = tuple(match_requirement(r, candidate, as_of) for r in job.requirements)
    counts = {s.value: 0 for s in MatchStatus}
    for r in results:
        counts[r.status.value] += 1

    total_weight = sum(r.weight for r in results)
    determined_weight = sum(r.weight for r in results if r.status in DETERMINED)
    earned = sum(r.weight * r.credit for r in results)

    score = round(100 * earned / total_weight) if total_weight else None
    score_known = round(100 * earned / determined_weight) if determined_weight else None
    coverage = round(determined_weight / total_weight, 3) if total_weight else 0.0
    blocking = any(
        r.status is MatchStatus.NOT_MET and req.importance is RequirementImportance.REQUIRED
        for r, req in zip(results, job.requirements, strict=True)
    )

    if score is None or coverage < MIN_COVERAGE_FOR_TIER:
        tier = Tier.INSUFFICIENT_DATA
    elif score >= STRONG_THRESHOLD and not blocking:
        tier = Tier.STRONG
    elif score >= MODERATE_THRESHOLD:
        tier = Tier.MODERATE
    else:
        tier = Tier.WEAK

    return AssessmentResult(
        score=score,
        score_excluding_unknowns=score_known,
        coverage=coverage,
        tier=tier,
        has_blocking_gaps=blocking,
        results=results,
        preference_checks=_check_preferences(job, candidate.preferences, candidate.home_location),
        status_counts=counts,
    )
