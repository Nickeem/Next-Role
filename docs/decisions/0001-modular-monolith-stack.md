# ADR 0001: Modular monolith on FastAPI, PostgreSQL, and Docker Compose

- Status: accepted (Phase 1)
- Date: 2026-10-18

## Context

The system is self-hosted by a single candidate or a small group. It needs durable,
auditable records (candidates, jobs, assessments), validated contracts, and room for
background work in later phases. `AGENTS.md` prefers a modular monolith with PostgreSQL as
the source of truth.

## Decision

- **One Python 3.12 package** (`next_role`) with logical modules: `candidates`, `jobs`,
  `matching`, `integrations`, and `api`. Domain services do not depend on FastAPI.
  Integrations sit behind protocols.
- **FastAPI** with synchronous endpoints, **Pydantic v2** contracts, and **pydantic-settings**
  for configuration.
- **PostgreSQL 16** with **SQLAlchemy 2** (synchronous, psycopg 3) and **Alembic**
  migrations. JSONB holds type-specific attributes and evidence snapshots. ARRAY holds skill
  terms.
- **Docker Compose** runs three services: `db`, a one-shot `migrate`, and `api`. There is a
  separate compose file for a disposable test database.
- **uv** for dependency management. **ruff**, **mypy --strict**, and **pytest** for quality.
- **No background workers in Phase 1.** All Phase 1 operations finish within one request.
  Services are written so they can later be called from a worker process.

## Alternatives considered

- **Async SQLAlchemy and FastAPI.** Rejected for now. Synchronous code is simpler and the
  workload is light, and FastAPI runs synchronous endpoints in a thread pool.
- **SQLite for tests.** Rejected. The schema uses PostgreSQL types, and tests should exercise
  the real database.
- **A task queue (Celery, RQ, or a broker).** Deferred. Phase 1 has no long-running work.
  When workers are needed, a PostgreSQL-backed queue is preferred over a new broker.
- **An LLM agent framework.** Rejected. A narrow `LLMProvider` protocol over HTTP is enough
  (see ADR 0003).

## Consequences

- One deployable image, used for both migrations and the API.
- LLM extraction, when enabled, adds latency to `POST /jobs`, bounded by the timeout and one
  retry. Moving extraction to a worker is planned for Phase 4 (see the roadmap).
- PostgreSQL is required for development tests. A compose file provides it.
