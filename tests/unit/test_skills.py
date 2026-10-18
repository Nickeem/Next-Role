from next_role.skills import detect_skills, normalize_skill, normalize_skills, skill_family


def test_aliases_normalize_to_canonical() -> None:
    assert normalize_skill(" Postgres ") == "postgresql"
    assert normalize_skill("K8s") == "kubernetes"
    assert normalize_skill("Golang") == "go"
    assert normalize_skill("Go") == "go"


def test_unknown_skills_are_cleaned_not_dropped() -> None:
    assert normalize_skill("  Event   Sourcing ") == "event sourcing"


def test_normalize_skills_dedupes_preserving_order() -> None:
    assert normalize_skills(["Python", "python3", "", "AWS"]) == ["python", "aws"]


def test_detect_skills_in_text() -> None:
    text = "Experience with Python, Postgres and Apache Kafka; Spring Boot a plus. C# or C++."
    assert detect_skills(text) == ["python", "postgresql", "kafka", "spring", "c#", "c++"]


def test_ambiguous_aliases_not_detected_in_free_text() -> None:
    assert detect_skills("You will go far; the rest of the team uses R and C.") == []


def test_family_lookup() -> None:
    assert skill_family("mysql") == skill_family("postgresql") == "relational_database"
    assert skill_family("not-a-skill") is None
