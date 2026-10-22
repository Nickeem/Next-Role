"""Text helpers shared by fact selection, drafting and verification.

"Verbatim" support means a claim equals a whole sentence or a whole segment (title,
description, responsibility, metric) of a cited fact after normalization. A substring is not
enough: dropping words can change meaning ("did not lead" → "lead").
"""

import re
import unicodedata
from datetime import date

from next_role.documents.snapshot import FactSnapshot
from next_role.skills import detect_skills

# Curly quotes and en/em dashes compare equal to their ASCII forms.
_QUOTES = str.maketrans(
    {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-"}
)
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
# Numbers with an optional currency sign, thousands separators, decimals and a unit suffix.
_NUMBER = re.compile(
    r"(?<![\w.])([$€£])?\s?(\d[\d,]*(?:\.\d+)?)\s?(%|percent\b|k\b|m\b|million\b|billion\b|x\b|\+)?",
    re.IGNORECASE,
)
_UNITS = {"percent": "%", "million": "m", "billion": "b"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTES).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip(" .;:!,")


def sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text.strip()) if s.strip()]


def fact_segments(fact: FactSnapshot) -> list[str]:
    """Statements a fact makes, each of which a claim may reproduce verbatim."""
    parts = [fact.title]
    if fact.description:
        parts.append(fact.description)
    for key in ("responsibilities", "metrics"):
        value = fact.attributes.get(key)
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
    return parts


def verbatim_units(fact: FactSnapshot) -> set[str]:
    units: set[str] = set()
    for part in fact_segments(fact):
        units.add(normalize(part))
        units.update(normalize(s) for s in sentences(part))
    units.discard("")
    return units


def is_verbatim(text: str, sources: list[str]) -> bool:
    """Whether ``text`` equals a whole statement or sentence of one of ``sources``."""
    wanted = normalize(text)
    for source in sources:
        if wanted == normalize(source) or wanted in {normalize(s) for s in sentences(source)}:
            return True
    return False


def fact_text(fact: FactSnapshot) -> str:
    """Everything a fact says, for number and skill checks."""
    values = [*fact_segments(fact), fact.organization or ""]
    for key, value in fact.attributes.items():
        if key not in {"responsibilities", "metrics"} and isinstance(value, str | int | float):
            values.append(str(value))
    for day in (fact.start_date, fact.end_date):
        if day:
            values.append(str(day.year))
    return " \n ".join(v for v in values if v)


def numbers(text: str) -> set[str]:
    """Normalized numeric values with their units, e.g. {"40%", "$2m", "12"}."""
    found = set()
    for currency, value, unit in _NUMBER.findall(unicodedata.normalize("NFKC", text)):
        unit = unit.lower()
        unit = _UNITS.get(unit, unit)
        found.add(f"{currency}{value.replace(',', '')}{unit}")
    return found


def skills_in(text: str) -> set[str]:
    return set(detect_skills(text))


def year_month(day: date | None) -> str | None:
    return f"{day.year:04d}-{day.month:02d}" if day else None
