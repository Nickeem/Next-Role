# ADR 0003: Deterministic matching; LLM limited to grounded requirement segmentation

- Status: accepted (Phase 1)
- Date: 2026-10-18

## Context

Suitability assessments must be explainable and reproducible, and must never present an
unsupported claim as a match. Job descriptions are untrusted input and may contain prompt
injection. The system has to work without any paid LLM, while still allowing local (Ollama)
or external models where they help.

## Decision

- Matching, scoring, and eligibility checks are **pure, deterministic rules**
  (`matching/engine.py`, `rules-v1`, documented in `docs/matching.md`). Each requirement
  gets one of six statuses. "No evidence" and "unknown" are kept separate from a confirmed
  "not met".
- The score is a transparent weighted sum, stored together with coverage and a tier.
  Assessments are immutable and idempotent, keyed by an input fingerprint.
- An LLM may optionally **segment** a job description into requirement statements and set
  each one's importance. The output must:
  - validate against a Pydantic schema,
  - quote the description verbatim; anything else is dropped as ungrounded.

  The deterministic classifier then assigns category, terms, and details.
- A provider failure or invalid output retries once, then falls back to the rules. The
  reason is recorded in `extraction_notes`.
- Providers sit behind `LLMProvider.complete_json()`. The Ollama and OpenAI-compatible
  adapters use plain httpx, not vendor SDKs.
- Only job text is ever sent. Candidate data is not. An external provider requires
  `NEXT_ROLE_LLM_ALLOW_EXTERNAL=true`.

## Alternatives considered

- **LLM-scored matching.** More semantic coverage, but not reproducible. It is also hard to
  guarantee that cited evidence exists, and it is exposed to injection from the job text.
  Deferred. A future version could let an LLM *propose* evidence for `unknown` requirements,
  with the cited facts and support levels verified deterministically.
- **Embedding or vector search.** Not justified for Phase 1 data volumes, and it would add
  infrastructure.

## Consequences

- Without an LLM, recall is limited by the keyword rules and the skill vocabulary.
  Paraphrased or soft-skill requirements come out as `unknown`. These limitations are
  documented and made visible to the user, not hidden.
- Changes to the rules require bumping `METHOD_VERSION`. Old assessments keep their version.
