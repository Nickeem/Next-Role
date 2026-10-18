# Architecture

This page describes the implemented system as of Phase 1. The direction for later phases is
in [roadmap.md](roadmap.md), and the decisions behind this design are in
[decisions/](decisions/).

## Overview

Next-Role is a **modular monolith**: one Python package (`next_role`) served by FastAPI and
backed by PostgreSQL, deployed with Docker Compose (ADR 0001). There are no background
workers yet. Every Phase 1 operation is synchronous and fast: rule-based extraction and
matching take milliseconds. The only potentially slow call is optional LLM extraction, which
is bounded by a timeout and one retry.

```
HTTP client ──► FastAPI routes (api/) ──► domain services ──► SQLAlchemy models ──► PostgreSQL
                                             │
                                             ├── pure logic: skills.py, jobs/extraction.py,
                                             │               matching/engine.py
                                             └── adapters:   integrations/llm/ (optional)
```

## Modules

| Module | Responsibility | Depends on |
| --- | --- | --- |
| `config.py` | Typed settings from env vars (pydantic-settings), validated at startup | — |
| `db.py`, `models.py` | Engine, sessions, declarative base; `models.py` aggregates all ORM models for Alembic | config |
| `logging_config.py` | JSON logging with a per-request `request_id` | — |
| `errors.py` | Domain exceptions (`NotFoundError`, `InvalidInputError`, `ConflictError`) | — |
| `skills.py` | Shared skill vocabulary: canonical names, aliases, families, free-text detection | — |
| `candidates/` | Knowledge base: candidates, facts, evidence sources, preferences, provenance | db, skills |
| `jobs/` | Manual ingestion, normalization, deduplication, requirement extraction | db, skills, integrations (via protocol) |
| `matching/` | Pure matching engine, assessment persistence, and retrieval | candidates, jobs |
| `integrations/llm/` | `LLMProvider` protocol, Ollama and OpenAI-compatible HTTP adapters | config, httpx |
| `api/` | FastAPI routes, dependencies (session, API-key auth) | services |
| `main.py` | App factory: wiring, middleware, error handlers | everything |

Boundaries:

- Domain services take a SQLAlchemy `Session` and Pydantic contracts. They never import
  FastAPI.
- `matching/engine.py` and `jobs/extraction.py` are pure functions over plain data, and are
  unit-tested without a database.
- Requirement extraction is injected through the `RequirementExtractor` protocol
  (`jobs/service.py`). The app chooses rule-based or LLM-assisted extraction at startup.
  Domain code never imports a provider SDK.
- Route handlers stay thin: validate input, call a service, serialize the result.
  Transactions are committed by the `get_session` dependency.

## Data model

```
candidates ─┬─< evidence_sources
            └─< candidate_facts ─< fact_evidence >─ evidence_sources
                    └─ parent_fact_id (e.g. achievement → experience)

job_opportunities ─┬─< job_postings        (raw submissions, verbatim)
                   └─< job_requirements    (classified statements)

suitability_assessments (candidate × opportunity) ─< requirement_assessments ─> job_requirements
```

- **`candidate_facts`** is a single table for all fact types, discriminated by `fact_type`.
  Type-specific fields live in JSONB `attributes`, validated per type (ADR 0002).
  Provenance is recorded as `verification_status` (`self_reported` or `user_verified`, with
  `verified_at`) plus `fact_evidence` links. Facts are retired (soft-deleted), not removed.
- **`job_opportunities`** holds normalized fields. Unknown values stay null or `unknown`.
  The table has a lifecycle `status` (`active`/`archived`), dedup keys (`canonical_url`, which
  is unique, and `identity_key`), and records how requirements were extracted
  (`extraction_method`, `extraction_notes`).
- **`job_postings`** stores each distinct submission's payload with a unique `content_hash`.
- **`job_requirements`** stores the original `text`, `category`, `importance`, canonical skill
  `terms`, and category-specific `details` (`min_years`, `education_level`, `countries`,
  `sponsorship_available`).
- **`suitability_assessments`** are immutable. Each stores the score, coverage, tier, status
  counts, preference checks, `method_version`, `as_of`, and an `input_fingerprint` (unique per
  candidate × job), which makes runs idempotent. **`requirement_assessments`** store each
  requirement's status, weight, credit, explanation, and a JSONB snapshot of the cited
  evidence.

All tables use UUID primary keys and timezone-aware timestamps. Enums are stored as
constrained strings. Schema changes go through Alembic migrations (`migrations/versions/`). A
test checks that the ORM models and the migrated schema have not drifted apart.

## Request flow: assess a job

1. `POST /api/v1/jobs/{job_id}/assessments` loads the opportunity with its requirements, and
   the candidate with active facts and evidence links.
2. `matching.service` computes the input fingerprint. If an assessment with that fingerprint
   exists, it is returned (HTTP 200).
3. Otherwise, the ORM records are converted into immutable views, `engine.assess()` evaluates
   each requirement and the preferences, and the result is persisted (HTTP 201).

## Security and safety

- **Authentication.** An API key in the `X-API-Key` header, compared in constant time. The
  app refuses to start without a key unless `NEXT_ROLE_AUTH_DISABLED=true` is set
  explicitly. Health endpoints are unauthenticated.
- **Untrusted job text.**
  - Rule-based extraction only parses the text.
  - LLM output is schema-validated and must quote the description verbatim. The rules, not
    the model, classify each statement and do all matching.
  - Extracted text that cannot be interpreted becomes an `unknown` requirement and earns no
    score. A test covers a prompt-injection line.
- **Privacy.**
  - Validation errors never echo submitted input.
  - Logs carry IDs and counts only.
  - Candidate data never goes to an LLM, and external providers need an explicit opt-in.
- **Input limits.** Description length is capped (`NEXT_ROLE_MAX_JOB_DESCRIPTION_CHARS`),
  and unknown fields are rejected (`extra="forbid"`).

## Not yet implemented

There are no background workers or task queue, no workflow events table, no job discovery,
notifications, documents, applications, or approvals, no multi-user accounts, and no
frontend. The data model is designed so that each can be added as new tables that reference
opportunities, candidates, and assessments, without restructuring existing ones.
