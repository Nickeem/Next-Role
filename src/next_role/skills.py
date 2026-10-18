"""Shared skill vocabulary used to normalize candidate skills and job requirements.

Canonical names are lowercase identifiers. Each canonical skill has aliases (matched as whole
words) and an optional *family* used to recognize related-but-different skills (for example,
MySQL is related to PostgreSQL). Matching on a family yields a partial match, never a full one.

The vocabulary is intentionally small and explicit. Unknown skills are still normalized
(lowercased, whitespace-collapsed) so they match exactly, but they cannot be detected in free
text and have no family.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class SkillDef:
    canonical: str
    aliases: tuple[str, ...] = ()
    family: str | None = None
    # Aliases that are too ambiguous to detect in free text (e.g. "go", "r"). They still
    # normalize when given explicitly as a skill name.
    explicit_only: tuple[str, ...] = field(default=())


_SKILLS: tuple[SkillDef, ...] = (
    # Programming languages
    SkillDef("python", ("python3",), "programming_language"),
    SkillDef("java", (), "programming_language"),
    SkillDef("javascript", ("js", "ecmascript"), "programming_language"),
    SkillDef("typescript", ("ts",), "programming_language", explicit_only=("ts",)),
    SkillDef("go", ("golang",), "programming_language", explicit_only=("go",)),
    SkillDef("rust", (), "programming_language"),
    SkillDef("c++", ("cpp",), "programming_language"),
    SkillDef("c#", ("csharp", "c sharp"), "programming_language"),
    SkillDef("c", (), "programming_language", explicit_only=("c",)),
    SkillDef("ruby", (), "programming_language"),
    SkillDef("php", (), "programming_language"),
    SkillDef("kotlin", (), "programming_language"),
    SkillDef("swift", (), "programming_language"),
    SkillDef("scala", (), "programming_language"),
    SkillDef("r", (), "programming_language", explicit_only=("r",)),
    SkillDef("sql", (), "query_language"),
    SkillDef("bash", ("shell scripting", "shell"), "scripting", explicit_only=("shell",)),
    # Web frameworks
    SkillDef("fastapi", (), "python_web_framework"),
    SkillDef("django", (), "python_web_framework"),
    SkillDef("flask", (), "python_web_framework"),
    SkillDef("react", ("react.js", "reactjs"), "frontend_framework"),
    SkillDef("vue", ("vue.js", "vuejs"), "frontend_framework"),
    SkillDef("angular", (), "frontend_framework"),
    SkillDef("node.js", ("nodejs", "node"), "javascript_runtime", explicit_only=("node",)),
    SkillDef("spring", ("spring boot",), "java_framework", explicit_only=("spring",)),
    SkillDef("ruby on rails", ("rails",), "ruby_framework"),
    # Data stores
    SkillDef("postgresql", ("postgres", "psql"), "relational_database"),
    SkillDef("mysql", ("mariadb",), "relational_database"),
    SkillDef("sql server", ("mssql", "microsoft sql server"), "relational_database"),
    SkillDef("oracle database", ("oracle db",), "relational_database"),
    SkillDef("sqlite", (), "relational_database"),
    SkillDef("mongodb", ("mongo",), "document_database"),
    SkillDef("dynamodb", (), "document_database"),
    SkillDef("redis", (), "key_value_store"),
    SkillDef("elasticsearch", ("elastic search", "opensearch"), "search_engine"),
    SkillDef("snowflake", (), "data_warehouse"),
    SkillDef("bigquery", ("big query",), "data_warehouse"),
    SkillDef("redshift", (), "data_warehouse"),
    # Data / messaging / ML
    SkillDef("kafka", ("apache kafka",), "message_broker"),
    SkillDef("rabbitmq", (), "message_broker"),
    SkillDef("spark", ("apache spark", "pyspark"), "data_processing"),
    SkillDef("airflow", ("apache airflow",), "workflow_orchestration"),
    SkillDef("dbt", (), "data_transformation"),
    SkillDef("pandas", (), "data_analysis"),
    SkillDef("machine learning", ("ml",), "machine_learning", explicit_only=("ml",)),
    SkillDef("pytorch", (), "ml_framework"),
    SkillDef("tensorflow", (), "ml_framework"),
    SkillDef("scikit-learn", ("sklearn", "scikit learn"), "ml_framework"),
    # Cloud / infrastructure
    SkillDef("aws", ("amazon web services",), "cloud_platform"),
    SkillDef("gcp", ("google cloud", "google cloud platform"), "cloud_platform"),
    SkillDef("azure", ("microsoft azure",), "cloud_platform"),
    SkillDef("docker", (), "containers"),
    SkillDef("kubernetes", ("k8s",), "container_orchestration"),
    SkillDef("terraform", (), "infrastructure_as_code"),
    SkillDef("ansible", (), "configuration_management"),
    SkillDef("linux", (), "operating_system"),
    SkillDef("git", (), "version_control"),
    SkillDef("ci/cd", ("ci cd", "continuous integration", "continuous delivery"), "delivery"),
    SkillDef("github actions", (), "delivery"),
    SkillDef("jenkins", (), "delivery"),
    SkillDef("prometheus", (), "observability"),
    SkillDef("grafana", (), "observability"),
    # Practices / APIs
    SkillDef(
        "rest apis",
        ("rest", "restful", "rest api", "restful apis"),
        "api_design",
        explicit_only=("rest",),
    ),
    SkillDef("graphql", (), "api_design"),
    SkillDef("grpc", (), "api_design"),
    SkillDef("microservices", ("microservice",), "architecture"),
    SkillDef("distributed systems", (), "architecture"),
    SkillDef("agile", ("scrum",), "process"),
    SkillDef("tdd", ("test-driven development", "test driven development"), "testing"),
    # Human languages
    SkillDef("english", (), "human_language"),
    SkillDef("spanish", (), "human_language"),
    SkillDef("french", (), "human_language"),
    SkillDef("german", (), "human_language"),
    SkillDef("portuguese", (), "human_language"),
    SkillDef("mandarin", ("chinese",), "human_language"),
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


@lru_cache
def _alias_index() -> dict[str, SkillDef]:
    index: dict[str, SkillDef] = {}
    for skill in _SKILLS:
        for name in (skill.canonical, *skill.aliases):
            index[_clean(name)] = skill
    return index


@lru_cache
def _detection_pattern() -> re.Pattern[str]:
    names: list[str] = []
    for skill in _SKILLS:
        for name in (skill.canonical, *skill.aliases):
            if name not in skill.explicit_only:
                names.append(re.escape(_clean(name)))
    # Longest first so "spring boot" wins over "spring", "apache kafka" over "kafka".
    names.sort(key=len, reverse=True)
    return re.compile(r"(?<![\w+#.])(?:" + "|".join(names) + r")(?![\w+#])")


def normalize_skill(name: str) -> str:
    """Map a skill name to its canonical form; unknown names are cleaned but kept."""
    cleaned = _clean(name)
    skill = _alias_index().get(cleaned)
    return skill.canonical if skill else cleaned


def normalize_skills(names: list[str]) -> list[str]:
    """Normalize and de-duplicate, preserving first-seen order."""
    seen: dict[str, None] = {}
    for name in names:
        if name.strip():
            seen.setdefault(normalize_skill(name), None)
    return list(seen)


def skill_family(canonical: str) -> str | None:
    skill = _alias_index().get(canonical)
    return skill.family if skill else None


def is_known_skill(canonical: str) -> bool:
    return canonical in _alias_index()


def detect_skills(text: str) -> list[str]:
    """Find known skills mentioned in free text, as canonical names in first-seen order."""
    found: dict[str, None] = {}
    for match in _detection_pattern().finditer(_clean(text)):
        found.setdefault(_alias_index()[match.group(0)].canonical, None)
    return list(found)
