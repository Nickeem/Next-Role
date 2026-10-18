import json
from pathlib import Path

from next_role.jobs.extraction import (
    EmploymentType,
    RequirementCategory,
    RequirementImportance,
    WorkArrangement,
    classify_requirement,
    extract_requirements,
    infer_employment_type,
    infer_work_arrangement,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "jobs"
Cat = RequirementCategory
Imp = RequirementImportance


def _example(name: str) -> str:
    description: str = json.loads((EXAMPLES / name).read_text())["description"]
    return description


def test_sectioned_description_uses_requirement_sections_only() -> None:
    reqs = extract_requirements(_example("senior-backend-engineer.json"))
    texts = [r.text for r in reqs]
    assert "Mentor engineers" not in texts  # responsibilities are not requirements
    assert not any("Ignore all previous instructions" in t for t in texts)
    assert [r.importance for r in reqs].count(Imp.REQUIRED) == 7
    assert [r.importance for r in reqs].count(Imp.PREFERRED) == 3

    by_text = {r.text: r for r in reqs}
    years = by_text["5+ years of professional software development experience"]
    assert years.category is Cat.EXPERIENCE_YEARS
    assert years.details == {"min_years": 5}
    assert by_text["Strong Python and FastAPI experience"].terms == ["python", "fastapi"]
    edu = by_text["Bachelor's degree in Computer Science or a related field"]
    assert edu.category is Cat.EDUCATION and edu.details["education_level"] == "bachelor"
    auth = by_text["Must be legally authorized to work in Canada"]
    assert auth.category is Cat.WORK_AUTHORIZATION and auth.details["countries"] == ["CA"]
    assert by_text["Excellent written and verbal communication"].category is Cat.OTHER
    assert by_text["AWS certification"].category is Cat.CERTIFICATION
    go = by_text["Go experience is a plus"]
    # "go" is too ambiguous to detect in free text, so this stays uninterpreted.
    assert go.category is Cat.OTHER and go.importance is Imp.PREFERRED


def test_sponsorship_and_us_detection() -> None:
    req = classify_requirement("Must be authorized to work in the US; we are unable to sponsor")
    assert req.category is Cat.WORK_AUTHORIZATION
    assert req.details == {"countries": ["US"], "sponsorship_available": False}
    assert req.importance is Imp.REQUIRED
    assert classify_requirement("Come work with us, authorized to work").details["countries"] == []


def test_inline_preferred_cue_overrides_section() -> None:
    req = classify_requirement("- Experience with dbt preferred", Imp.REQUIRED)
    assert req.importance is Imp.PREFERRED
    assert req.terms == ["dbt"]


def test_experience_years_with_skill_terms() -> None:
    req = classify_requirement("3-5 years building data pipelines with Spark and Airflow")
    assert req.category is Cat.EXPERIENCE_YEARS
    assert req.details["min_years"] == 3
    assert req.terms == ["spark", "airflow"]


def test_unstructured_description_falls_back_to_sentences() -> None:
    reqs = extract_requirements(
        "We build tools for farmers. You should know Python and Django. We like dogs."
    )
    assert [r.text for r in reqs] == ["You should know Python and Django."]
    assert reqs[0].importance is Imp.UNSPECIFIED


def test_description_without_requirements_yields_none() -> None:
    assert extract_requirements("A friendly team. Great snacks. Apply today!") == []


def test_unbulleted_lines_inside_sections_are_kept_and_deduped() -> None:
    reqs = extract_requirements("Requirements:\nPython\nPython\nTeamwork\n\nBenefits\nDental")
    assert [(r.text, r.category) for r in reqs] == [("Python", Cat.SKILL), ("Teamwork", Cat.OTHER)]


def test_posting_attribute_inference() -> None:
    assert infer_work_arrangement("Toronto (Hybrid)") is WorkArrangement.HYBRID
    assert infer_work_arrangement(None, "Fully remote role") is WorkArrangement.REMOTE
    assert infer_work_arrangement("Austin, TX", "Great team") is WorkArrangement.UNKNOWN
    assert infer_employment_type("Data Engineer", "a full-time role") is EmploymentType.FULL_TIME
    assert infer_employment_type("Permanent resident status needed") is EmploymentType.UNKNOWN
