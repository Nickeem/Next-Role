# Phase 1 implementation progress

Working log for Phase 1 (Foundation and Matching). It exists so that another session can resume
the work without repeating it. Branch: `feat/phase-1-foundation-matching`.

## Milestones

| # | Milestone | Status |
| - | --------- | ------ |
| 1 | Foundation: packaging, config, logging, DB, error handling, health, Alembic, Docker, tooling | done |
| 2 | Candidate knowledge base with provenance (facts, evidence sources, preferences) | done |
| 3 | Manual job ingestion, rule-based requirement extraction, deduplication | done |
| 4 | Deterministic matching, scoring, persisted assessments | pending |
| 5 | LLM provider abstraction (optional, schema-validated, with deterministic fallback) | pending |
| 6 | Documentation, end-to-end demo, PR | pending |

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

## Testing log

- M1: 6 tests pass (config validation, health, readiness, request-id propagation).
- M2: 22 tests pass (candidate API, provenance, verification reset, cross-candidate evidence
  rejection, migration round trip and model/migration drift check, skill vocabulary).
- M3: 38 tests pass (extraction unit tests, including a prompt-injection line in the example
  posting; ingestion, unknown fields, manual requirements, three dedup paths, validation,
  status filter).

## Known limitations / open issues

- (none yet)
