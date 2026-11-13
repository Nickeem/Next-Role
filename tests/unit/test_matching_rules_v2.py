"""Matching rules-v2 (ADR 0009): home location, and skill years from dated facts."""

from datetime import date
from typing import Any

from next_role.candidates.models import (
    CandidateFact,
    EvidenceKind,
    EvidenceSource,
    FactType,
    VerificationStatus,
)
from next_role.candidates.schemas import CandidatePreferences, FactSupport
from next_role.candidates.service import fact_support
from next_role.matching.engine import (
    METHOD_VERSION,
    CandidateView,
    FactView,
    JobView,
    MatchStatus,
    PreferenceStatus,
    assess,
    match_requirement,
)
from tests.unit.test_matching_engine import AS_OF, fact, req

U = FactSupport.UNCONFIRMED


def test_the_method_version_is_rules_v2() -> None:
    assert METHOD_VERSION == "rules-v2"


def test_only_confirmation_counts_not_linked_evidence() -> None:
    evidence = EvidenceSource(kind=EvidenceKind.CERTIFICATE, title="Certificate (synthetic)")
    claimed = CandidateFact(
        fact_type=FactType.SKILL, title="Go", verification_status=VerificationStatus.SELF_REPORTED
    )
    claimed.evidence_sources = [evidence]
    assert fact_support(claimed) is FactSupport.UNCONFIRMED
    claimed.verification_status = VerificationStatus.USER_VERIFIED
    assert fact_support(claimed) is FactSupport.CONFIRMED


# --- Location -----------------------------------------------------------------------------


def _location(home: str | None, **prefs: Any) -> tuple[PreferenceStatus, str]:
    job = JobView("Toronto, ON", "onsite", "unknown", None, None, None, ())
    cand = CandidateView(CandidatePreferences(**prefs), (), home_location=home)
    check = next(c for c in assess(cand, job, AS_OF).preference_checks if c.name == "location")
    return check.status, check.explanation


def test_the_home_location_is_used_only_without_preferred_locations() -> None:
    assert _location("Toronto") == (
        PreferenceStatus.COMPATIBLE,
        "Toronto, ON is the candidate's home location.",
    )
    assert _location("Ottawa", willing_to_relocate=False) == (
        PreferenceStatus.CONFLICT,
        "Toronto, ON is not the candidate's home location.",
    )
    # Stated preferences win over the home location.
    status, explanation = _location("Toronto", locations=["Vancouver"])
    assert status is PreferenceStatus.UNKNOWN and "not a preferred location" in explanation
    assert _location(None)[0] is PreferenceStatus.UNKNOWN


# --- Skill years --------------------------------------------------------------------------


def _dated(
    fact_type: str, start: date, end: date, support: FactSupport = FactSupport.CONFIRMED
) -> FactView:
    return fact(fact_type, "Work", support, skills=("python",), start=start, end=end)


def _years(*facts: FactView, text: str = "5+ years of Python") -> Any:
    return match_requirement(req(text), CandidateView(CandidatePreferences(), facts), AS_OF)


def test_skill_years_merge_dated_experience_and_projects() -> None:
    job = _dated("experience", date(2018, 1, 1), date(2020, 12, 31))
    project = _dated("project", date(2020, 1, 1), date(2022, 12, 31))
    result = _years(job, project)
    assert result.status is MatchStatus.MET
    assert "totals 5 years" in result.explanation
    assert [e.reason for e in result.evidence] == ["dated experience", "dated project"]


def test_unconfirmed_dated_facts_do_not_count() -> None:
    result = _years(_dated("project", date(2010, 1, 1), date(2022, 12, 31), U))
    assert result.status is MatchStatus.UNVERIFIED


def test_stated_years_are_used_only_without_a_dated_fact() -> None:
    stated = fact(title="Python", years_experience=8)
    result = _years(stated)
    assert result.status is MatchStatus.MET
    assert result.explanation.startswith("Stated experience involving python is 8 years")
    assert [e.reason for e in result.evidence] == ["states 8 years of 'python' (stated)"]

    # A dated fact wins, even when the stated number is larger.
    short = _dated("experience", date(2024, 1, 1), date(2024, 12, 31))
    result = _years(stated, short)
    assert result.status is MatchStatus.NOT_MET
    assert [e.fact_id for e in result.evidence] == [short.id]


def test_unconfirmed_stated_years_do_not_count() -> None:
    result = _years(fact(title="Python", support=U, years_experience=8))
    assert result.status is MatchStatus.NO_EVIDENCE


def test_general_years_count_experience_only() -> None:
    project = _dated("project", date(2015, 1, 1), date(2022, 12, 31))
    result = _years(project, text="5+ years of professional experience")
    assert result.status is MatchStatus.NO_EVIDENCE
    assert result.evidence == ()
