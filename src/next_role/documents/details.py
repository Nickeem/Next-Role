"""Checkable details in personal statements (ADR 0009).

A personal statement rests on the candidate's about-me, which is self-reported and never
confirmed. It may describe voice, motivation, interests and working style, but it must not
introduce anything a reader could check (numbers, durations, dates, job titles, names of
employers, institutions or credentials, skills) unless a cited confirmed fact states it.

Detection is deliberately strict and purely lexical:

- numbers with units (``numbers``) and number words ("ten", "a decade");
- month names;
- skill terms from the skill vocabulary;
- role words ("engineer", "lead", "CTO", …) and any fact title in the profile;
- names: any capitalized word that does not start a sentence, and any acronym. This catches
  employers, institutions and credentials, and also harmless words such as a city.

The target job title and company may be named, as in connective sentences.
"""

import re
from collections.abc import Iterable

from next_role.documents.text import normalize, numbers, sentences, skills_in

NUMBER_WORDS = frozenset(
    {
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "thirty",
        "forty",
        "fifty",
        "sixty",
        "seventy",
        "eighty",
        "ninety",
        "hundred",
        "hundreds",
        "thousand",
        "thousands",
        "million",
        "millions",
        "billion",
        "dozen",
        "dozens",
        "decade",
        "decades",
        "half",
        "twice",
    }
)
MONTHS = frozenset(
    {
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    }
)
ROLE_WORDS = frozenset(
    {
        "engineer",
        "engineers",
        "developer",
        "developers",
        "programmer",
        "manager",
        "managers",
        "director",
        "directors",
        "lead",
        "leads",
        "head",
        "principal",
        "senior",
        "staff",
        "junior",
        "architect",
        "architects",
        "analyst",
        "analysts",
        "scientist",
        "scientists",
        "consultant",
        "consultants",
        "founder",
        "cofounder",
        "co-founder",
        "intern",
        "internship",
        "administrator",
        "designer",
        "researcher",
        "specialist",
        "coordinator",
        "officer",
        "executive",
        "president",
        "vp",
        "cto",
        "ceo",
        "cfo",
        "coo",
        "cio",
    }
)
# Capitalized words that are not names.
_NOT_NAMES = frozenset({"I", "I'm", "I've", "I'd", "I'll"})
_WORD = re.compile(r"[A-Za-z][\w&+.'-]*")
_ACRONYM = re.compile(r"^[A-Z][A-Z0-9&+.-]+$")


def _strip(text: str, phrases: Iterable[str]) -> str:
    for phrase in phrases:
        if phrase:
            text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
    return text


def _words(text: str) -> list[tuple[str, bool]]:
    """Words with whether each starts a sentence."""
    out: list[tuple[str, bool]] = []
    for sentence in sentences(text) or [text]:
        for i, match in enumerate(_WORD.finditer(sentence)):
            out.append((match.group().rstrip(".'-"), i == 0))
    return out


def checkable_details(
    text: str, allowed_phrases: Iterable[str] = (), known_titles: Iterable[str] = ()
) -> set[str]:
    """The checkable details ``text`` states, each as a short label (e.g. "10", "Acme")."""
    text = _strip(text, allowed_phrases)
    skills = skills_in(text)
    found = set(numbers(text)) | skills
    lowered = normalize(text)
    for title in known_titles:
        if (
            title
            and normalize(title) not in skills
            and re.search(rf"(?<!\w){re.escape(normalize(title))}(?!\w)", lowered)
        ):
            found.add(title)
    for word, starts_sentence in _words(text):
        low = word.lower()
        if low in skills:  # already reported as a skill
            continue
        if low in NUMBER_WORDS or low in ROLE_WORDS:
            found.add(low)
            continue
        if word in _NOT_NAMES:
            continue
        is_name = word[:1].isupper() and not starts_sentence
        if word in MONTHS or _ACRONYM.match(word) or is_name:
            found.add(word)
    return found


def unsupported_details(
    text: str,
    support: str,
    allowed_phrases: Iterable[str] = (),
    known_titles: Iterable[str] = (),
) -> list[str]:
    """Details in ``text`` that ``support`` (the cited confirmed facts' text) does not state."""
    have_numbers = numbers(support)
    have_skills = skills_in(support)
    lowered = normalize(support)
    missing = []
    for detail in checkable_details(text, allowed_phrases, known_titles):
        if detail in have_numbers or detail in have_skills:
            continue
        if re.search(rf"(?<!\w){re.escape(normalize(detail))}(?!\w)", lowered):
            continue
        missing.append(detail)
    return sorted(missing, key=str.lower)


def sentence_with(text: str, detail: str) -> str | None:
    """The first sentence of ``text`` that states ``detail``, to quote in a question."""
    for sentence in sentences(text):
        if detail in checkable_details(sentence):
            return sentence.strip()
    return None
