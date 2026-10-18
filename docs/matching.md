# Matching and scoring methodology (`rules-v1`)

This document describes how the deterministic matcher in
`src/next_role/matching/engine.py` evaluates a candidate against a job. Rules and weights are
versioned. Every assessment stores `method_version`, and any change to the rules below must
bump `METHOD_VERSION`.

> The suitability score is a **ranking heuristic** over the job's stated requirements and the
> candidate's supported evidence. It is **not** a probability of being hired, and it says
> nothing about requirements the posting does not state.

## 1. Evidence: what counts

Each candidate fact has a derived **support** level:

| Support | Condition | Can produce a match? |
| --- | --- | --- |
| `verified` | `verification_status = user_verified` | yes |
| `evidenced` | not verified, but linked to at least one evidence source | yes |
| `unsupported` | self-reported with no evidence | no, only `unverified` |

Retired facts are ignored. Work authorization comes from candidate *preferences*. It is treated
as a candidate declaration (`support = candidate_declared`), and its explanation says it should
be confirmed before applying.

## 2. Requirement statuses

| Status | Meaning | Score credit |
| --- | --- | --- |
| `met` | Supported evidence satisfies the requirement | 1.0 |
| `partially_met` | Supported evidence partly satisfies it (see rules) | 0.5 |
| `not_met` | Supported candidate information **contradicts** it (a confirmed gap) | 0 |
| `unverified` | Only unsupported claims address it | 0 |
| `no_evidence` | The knowledge base contains nothing relevant | 0 |
| `unknown` | The rules cannot interpret or evaluate it | 0 |

Missing evidence (`no_evidence`) and uninterpretable requirements (`unknown`) are never
treated as satisfied or as failed.

## 3. Rules by requirement category

Requirements are categorized during extraction (`jobs/extraction.py`). Skill terms are
canonical names from `next_role/skills.py`.

**Skill / language** (any requirement with recognized terms). Each term is evaluated on its
own:

- A supported fact that lists the skill, or mentions it in its title or text, gives `met`.
- Otherwise, a supported fact with a *related* skill (same family, e.g. MySQL for PostgreSQL)
  gives `partially_met`. Broad families never substitute: programming languages, human
  languages, query languages, scripting, and process.
- Otherwise, an unsupported fact mentioning the skill gives `unverified`. Anything else gives
  `no_evidence`.

Term results are then combined. If all terms are met, the requirement is `met`. If any term is
met or partially met, it is `partially_met`. Otherwise it is `unverified` if any term is
unverified, and `no_evidence` if none is. When the requirement offers alternatives ("Java *or*
Kotlin"), the best term wins. Trailing qualifiers such as "…, or equivalent experience" do not
make the listed skills alternatives.

**Experience years** (`min_years` parsed from "5+ years …").

- If the requirement names skills, only experience facts using those skills count. Otherwise
  all experience counts. If it names an area the vocabulary cannot recognize ("3+ years of
  Go"), the result is `unknown` instead of falling back to total experience.
- Years are the total length of supported, dated experience: ranges are inclusive and
  overlapping or adjacent ranges are merged. Current roles run until `as_of`. A supported
  skill fact's stated `years_experience` can raise the total for that skill.
- years ≥ N gives `met`. years ≥ 0.7·N gives `partially_met`. Below that gives `not_met`.
- If the supported experience has incomplete dates, the result is `unknown`. If only
  unsupported experience is relevant, it is `unverified`. With none at all, it is
  `no_evidence`.

**Education** (lowest level mentioned: high school < associate < bachelor < master <
doctorate; a bare "degree" means associate or higher).

- The highest supported, completed degree at or above the required level gives `met`. Field of
  study is not evaluated.
- A supported degree below the required level gives `not_met`.
- Only `degree_level = other` gives `unknown`. Only unsupported or in-progress education gives
  `unverified`. No education at all gives `no_evidence`.

**Certification.** A certification fact is relevant if it shares a skill term with the
requirement, or if at least half of the distinctive words overlap. Filler words such as
"must", "hold", "current", and "certification" are ignored. Results:

- A supported, unexpired certification gives `met`. A supported but expired one gives
  `partially_met`.
- An unsupported one gives `unverified`. None gives `no_evidence`.

**Work authorization** (countries and sponsorship parsed from the requirement):

- If no country is named, the result is `unknown`. If the candidate stated no authorization,
  it is `no_evidence`.
- If the candidate is authorized in every named country, it is `met`.
- If not, it is `partially_met` when sponsorship is available, `not_met` when the posting says
  there is no sponsorship, and `unknown` when sponsorship is not mentioned.

**Other** requirements, such as "excellent communication", always give `unknown`.

## 4. Score, coverage, and tier

Each requirement has an importance weight: required = 3, unspecified = 2, preferred = 1.

```
score                    = round(100 * Σ(weight * credit) / Σ weight)            # all requirements
score_excluding_unknowns = round(100 * Σ(weight * credit) / Σ weight[determined])
coverage                 = Σ weight[determined] / Σ weight
determined               = status in {met, partially_met, not_met}
```

`score` is conservative: unknown, unverified, and missing evidence earn nothing.
`score_excluding_unknowns` shows the score if every undetermined requirement were set aside.
Read the two together with `coverage`.

`has_blocking_gaps` is true when any **required** requirement is `not_met`.

| Tier | Condition |
| --- | --- |
| `insufficient_data` | no requirements, or coverage < 0.4 |
| `strong` | score ≥ 75 and no blocking gaps |
| `moderate` | score ≥ 50 |
| `weak` | otherwise |

## 5. Preference checks (not scored)

Work arrangement, location, employment type, and salary are compared with the candidate's
preferences. Each check reports `compatible`, `conflict`, or `unknown`, and none of them
affects the score.

- Location uses whole-word matching, so "CA" does not match "Chicago".
- A remote job is compatible on location, but regional restrictions are not evaluated.
- Salaries are only compared when both currencies match.

## 6. Idempotency and history

Assessments are immutable. Each one stores a fingerprint of the active facts (ids, update
times, verification, evidence links), the preferences, the job attributes and requirements,
`method_version`, and `as_of`. Re-running with identical inputs returns the existing
assessment. Any change to those inputs creates a new assessment and keeps the old one.

## 7. Known limitations of the rule-based path

- Requirements are extracted with headings and keyword rules. Unusual formatting can merge,
  split, or miss requirements, and unrecognized statements become `unknown`.
- Skills outside the explicit vocabulary are only matched by exact name: a requirement the
  rules cannot classify is matched against skills the candidate listed explicitly when it
  names one verbatim (for example "COBOL required" and a `cobol` skill fact). Ambiguous names
  ("Go", "R", "C") are not detected in free text.
- There is no semantic understanding. Paraphrases ("ship backend services" vs "API
  development") do not match, and soft skills are only evaluated through that exact-name
  match.
- Field of study, seniority, and the quality or recency of experience are not evaluated.
- Work authorization is the candidate's own declaration and is not verified.
