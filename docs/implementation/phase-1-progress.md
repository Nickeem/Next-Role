# Phase 1 implementation progress

Working log for Phase 1 (Foundation and Matching). It exists so that another session can resume
the work without repeating it. Branch: `feat/phase-1-foundation-matching`.

## Milestones

| # | Milestone | Status |
| - | --------- | ------ |
| 1 | Foundation: packaging, config, logging, DB, error handling, health, Alembic, Docker, tooling | done |
| 2 | Candidate knowledge base with provenance (facts, evidence sources, preferences) | done |
| 3 | Manual job ingestion, rule-based requirement extraction, deduplication | done |
| 4 | Deterministic matching, scoring, persisted assessments | done |
| 5 | LLM provider abstraction (optional, schema-validated, with deterministic fallback) | done |
| 6 | Documentation, end-to-end demo, PR | done (PR open for review) |

## Decisions

- Python 3.12, managed with `uv` (`uv.lock` committed). Tooling: ruff (lint and format),
  mypy `--strict` on `src/`, pytest.
- Synchronous SQLAlchemy 2.0 with psycopg 3. FastAPI runs sync endpoints in its thread pool.
  That is enough for Phase 1, and the code stays simpler than with async.
- Integration tests run against real PostgreSQL (`docker-compose.test.yml`, port 5433). There
  is no SQLite substitute, because the schema uses PostgreSQL types (JSONB, ARRAY).
- Every `/api/v1` route requires an `X-API-Key` header. The app refuses to start without a key
  unless `NEXT_ROLE_AUTH_DISABLED=true`.
- All candidate facts share one `candidate_facts` table, discriminated by `fact_type`, with
  validated JSONB `attributes` (ADR 0002). Provenance has two parts: `verification_status`
  (`self_reported` or `user_verified`) and evidence-source links. Support is derived from
  them: `verified`, `evidenced`, or `unsupported`.
- Editing a verified fact's content resets it to `self_reported`. Deleting a fact retires it
  (soft delete).
- `next_role.skills` is a shared, explicit skill vocabulary with aliases and families. Aliases
  too ambiguous for free text (`go`, `r`, `rest`, ...) only normalize when given explicitly.
- SQLAlchemy 2.1 no longer cascades objects into the session through backrefs, so new objects
  are added with `session.add` explicitly.
- Jobs have three tables. `job_postings` holds each distinct raw submission verbatim.
  `job_opportunities` is the normalized, de-duplicated record. `job_requirements` are the
  classified requirement statements, each with `terms` and category-specific `details`.
- Deduplication runs in order: exact normalized description hash (nothing stored), then
  canonical URL, then the company + title + location identity key. The last two link a new
  posting to the existing opportunity.
- Missing title, company, location, and salary stay null. Work arrangement and employment
  type fall back to `unknown` when the text gives no explicit cue.
- Requirement extraction is pluggable (`RequirementExtractor` protocol in `jobs/service.py`).
  The default is rule-based. Requirements supplied explicitly in the submission are
  classified but not extracted (`extraction_method = manual`).
- Matching is a pure engine (`matching/engine.py`) over plain data views. The service layer
  maps ORM records into those views and persists the results. The methodology, statuses,
  weights, and tiers are documented in `docs/matching.md` (version `rules-v1`).
- Assessments are immutable and idempotent, keyed by an input fingerprint (facts,
  preferences, job, requirements, method version, `as_of`). `as_of` can be set in the
  request, which keeps results deterministic.
- Fixed the findings from the `/code-review` of the uncommitted M4 draft: inclusive day-based
  durations; whole-word location matching; "or equivalent" no longer makes skills
  alternatives; certification stopwords; work authorization is `unknown` (not `not_met`)
  when sponsorship is unstated; undated experience and `degree_level=other` are `unknown`;
  skills detected once per fact; migration added.
- Also fixed: a years requirement in an unrecognized area ("3+ years of Go") is `unknown`.
  Previously it fell back to total experience, an over-claim.
- The LLM is optional and only *segments* descriptions. `LLMProvider` is a protocol with
  Ollama and OpenAI-compatible HTTP adapters (httpx, no vendor SDKs). Outputs are validated
  with Pydantic. Statements not found in the description are dropped. Classification stays
  rule-based. Failures retry once, then fall back to the rules, and the reason goes in
  `extraction_notes`. Only job text is sent, never candidate data. External providers need
  `NEXT_ROLE_LLM_ALLOW_EXTERNAL=true`. Matching never uses the LLM (ADR 0003).

## Testing log

- M1: 6 tests pass (config validation, health, readiness, request-id propagation).
- M2: 22 tests pass (candidate API, provenance, verification reset, cross-candidate evidence
  rejection, migration round trip and model/migration drift check, skill vocabulary).
- M3: 38 tests pass (extraction unit tests, including a prompt-injection line in the example
  posting; ingestion, unknown fields, manual requirements, three dedup paths, validation,
  status filter).
- M4: 69 tests pass (25 matching engine unit tests; assessment API with an end-to-end scenario
  on the synthetic candidate, a mismatched role, idempotency and re-assessment after
  verification, retired facts, empty requirements, 404s).
- M5: 80 tests pass (fake-provider extraction, grounding, retry and fallback, prompt
  delimiting, httpx MockTransport adapter tests, factory, API wiring).
- M6: the end-to-end workflow ran against the real Docker Compose stack (`scripts/demo.sh`):
  readiness reported revision `0004_assessments`; candidate created; job ingested; the
  resubmission was deduplicated; assessment score 69, tier moderate; the stored assessment
  was retrieved. API logs carry only identifiers and counts.

## Known limitations / open issues

- The limitations of rule-based extraction and matching are listed in `docs/matching.md` §7.
  They include paraphrases and soft skills (`unknown`), a limited skill vocabulary, ambiguous
  skill names that cannot be detected in free text, and field of study and seniority not being
  evaluated.
- Work authorization is the candidate's own declaration. It is treated as met and the
  explanation flags it for confirmation.
- Deduplication on company + title + location can link two genuinely different openings
  with identical metadata. The raw postings are kept and the outcome says
  `linked_to_existing`.
- LLM extraction runs inside `POST /jobs` (bounded by a timeout and one retry). Moving it to a
  background worker is Phase 2.
- Single shared API key: no per-user accounts or authorization yet.
- Tests print a `StarletteDeprecationWarning` about httpx in the TestClient. It is harmless.
- The LLM adapters are tested with mocked HTTP only. They have not been exercised against a
  live Ollama or OpenAI-compatible server in this environment.

## Commits

- `0e65a3b` foundation · `f68f471` candidate knowledge base · `9d7b8b3` job ingestion ·
  `208bcba` matching and assessments · `5fb738b` LLM extraction · docs, demo, and handoff
  (this commit).
