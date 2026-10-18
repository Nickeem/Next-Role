# Phase 1 implementation progress

Working log for Phase 1 (Foundation and Matching). It exists so that another session can resume
the work without repeating it. Branch: `feat/phase-1-foundation-matching`.

## Milestones

| # | Milestone | Status |
| - | --------- | ------ |
| 1 | Foundation: packaging, config, logging, DB, error handling, health, Alembic, Docker, tooling | done |
| 2 | Candidate knowledge base with provenance (facts, evidence sources, preferences) | done |
| 3 | Manual job ingestion, rule-based requirement extraction, deduplication | pending |
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

## Testing log

- M1: 6 tests pass (config validation, health, readiness, request-id propagation).
- M2: 22 tests pass (candidate API, provenance, verification reset, cross-candidate evidence
  rejection, migration round trip and model/migration drift check, skill vocabulary).

## Known limitations / open issues

- (none yet)
