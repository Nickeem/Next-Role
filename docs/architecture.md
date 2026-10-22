# Architecture

This page describes the implemented system: Phase 1 plus the Phase 2 work merged so far. The
direction for later phases is in [roadmap.md](roadmap.md), and the decisions behind this
design are in [decisions/](decisions/).

## Overview

Next-Role is a **modular monolith**: one Python package (`next_role`) served by FastAPI and
backed by PostgreSQL, deployed with Docker Compose (ADR 0001). A separate `worker` process from the same image
publishes notifications (ADR 0004). Every request-path operation is synchronous and fast: rule-based extraction and
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
| `matching/` | Pure matching engine, assessment persistence, and retrieval | candidates, jobs, review |
| `review/` | Candidate decisions: pure state machine (`workflow.py`), reviews, append-only events | matching, notifications |
| `documents/` | Phase 3 (in progress): display model shared by every renderer (`display.py`), DOCX renderer and file QA (`docx_renderer.py`, `qa.py`), artifact queue, completion and notifications (`artifacts.py`), approval of exact files (`service.approve`); runs queued on approval and claimed with a lease (`runs.py`), input snapshots, fact selection and drafting with an optional LLM (`drafting.py`), pure claim checks (`verification.py`), bundle edits, questions and confirmations (`service.py`), the worker executor (`generation.py`), template registry, artifact storage | candidates, jobs, matching, review, integrations (via protocol) |
| `notifications/` | Transactional outbox, preferences, `NotificationPublisher` protocol, dispatcher with retries | db |
| `integrations/ntfy.py` | ntfy HTTP publisher (httpx) | config, notifications |
| `renderer.py` | `python -m next_role.renderer` (Compose `renderer` service, Playwright image): claims pending files with a lease, renders PDF (`documents/pdf_renderer.py`, Chromium) and DOCX, checks them, stores them atomically, sweeps orphans | documents |
| `worker.py` | `python -m next_role.worker`: postponed-review reminders and outbox dispatch | review, notifications, integrations |
| `integrations/llm/` | `LLMProvider` protocol, Ollama and OpenAI-compatible HTTP adapters | config, httpx |
| `auth/` | Operator browser sessions: login, lookup, logout, login throttle | db |
| `api/` | FastAPI routes, dependencies (database session, API-key or cookie auth with CSRF) | services |
| `api/spa.py` | Serves the built web workspace with an `index.html` fallback and a strict CSP | — |
| `main.py` | App factory: wiring, middleware, error handlers | everything |
| `frontend/` | Vite + React SPA: queue, review detail and decisions, activity, settings. `src/templates/` holds the resume and letter templates, shared with `print.html`, the renderer's print page | API |

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

opportunity_reviews (candidate × opportunity) ─< review_events
       └─> latest_assessment_id                     └─> assessment_id
notification_outbox ─> opportunity_reviews (optional)
notification_preferences (one row per operator)
```

- **`candidate_facts`** is a single table for all fact types, discriminated by `fact_type`.
  Type-specific fields live in JSONB `attributes`, validated per type (ADR 0002).
  Provenance is recorded as `verification_status` (`self_reported` or `user_verified`, with
  `verified_at`) plus `fact_evidence` links. Facts are retired (soft-deleted), not removed.
- **`job_opportunities`** holds normalized fields. Unknown values stay null or `unknown`.
  The table has a lifecycle `status` (`active`/`archived`), dedup keys (`canonical_url`, which
  is unique, and `identity_key`), and records how requirements were extracted
  (`extraction_method`, `extraction_notes`).
- **`job_postings`** stores each distinct submission's payload with a unique `content_hash` (normalized
  description, canonical URL, company, title, and location).
- **`job_requirements`** stores the original `text`, `category`, `importance`, canonical skill
  `terms`, and category-specific `details` (`min_years`, `education_level`, `countries`,
  `sponsorship_available`).
- **`suitability_assessments`** are immutable. Each stores the score, coverage, tier, status
  counts, preference checks, `method_version`, `as_of`, and an `input_fingerprint` (unique per
  candidate × job), which makes runs idempotent. **`requirement_assessments`** store each
  requirement's status, weight, credit, explanation, and a JSONB snapshot of the cited
  evidence.
- **`opportunity_reviews`** holds the candidate's current decision for each candidate ×
  opportunity: `status`, `postpone_until`, `latest_assessment_id`, and a `version` used to
  reject stale changes. **`review_events`** is its append-only history (a trigger rejects
  updates): event type, previous and new status, the assessment the decision was based on,
  reason, actor, and request ID. States and transitions are in
  [features/review-workflow.md](features/review-workflow.md) (ADR 0004).
- **`notification_outbox`** holds queued notifications with a unique `event_key`, a payload
  free of personal data, delivery status, attempts, and the last redacted error.

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
4. In the same transaction, `review.service.record_assessment` opens the candidate's review
   in `pending_review` and queues a `review.ready` notification, or, if a review exists,
   points it at the new assessment without changing the candidate's decision.

## Decisions

`review.service.apply_decision` locks the review row, compares the client's
`expected_version`, validates the action with the pure `review.workflow.decide`, updates the
state, and appends a `review_events` row, all in one transaction. Approving for drafting also
queues a `generation_runs` row in that transaction. Reopening an approval calls
`documents.runs.revoke`, which refuses while a run holds a live lease, and otherwise cancels
queued runs and withdraws the approval's bundle. Invalid transitions raise
`ConflictError` (409); missing confirmation, reason, or postpone date raise
`InvalidInputError` (422). Decisions never queue notifications.

## Worker

`python -m next_role.worker` runs one loop, every `NEXT_ROLE_WORKER_POLL_SECONDS`:

0. `documents.generation.process_next` executes up to five due generation runs: drafting
   after an approval, section regeneration, and re-checking edited documents. It runs whether
   or not ntfy is enabled. A run is claimed with a lease, drafted outside any transaction, and
   written in one transaction that re-checks the lease and the approval. With no LLM, or
   without `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA`, documents are assembled verbatim from facts.
1. `review.service.queue_due_reminders` queues a `review.postponed_due` notification for each
   postponed review whose date has passed. Its `event_key` includes the date, so it is queued
   once per date.
2. `notifications.dispatcher.dispatch_pending` claims due outbox rows one at a time
   (`FOR UPDATE SKIP LOCKED`, one transaction each), cancels expired or no-longer-relevant
   ones, and publishes the rest through the `NotificationPublisher` (ntfy). Retryable failures
   are rescheduled with exponential backoff; the rest are marked `failed`.

With ntfy disabled the worker idles. Each loop iteration, and each notification dispatched
within a batch, touches a heartbeat file; the Compose healthcheck
(`python -m next_role.worker --check`) fails if it is older than
max(60 s, 3 × poll, 2 × (poll + ntfy timeout)). Operations:
[operations/ntfy.md](operations/ntfy.md).

The review routes (`api/routes/reviews.py`) are thin. Read models for the queue, summary,
details, and activity live in `review/queries.py`, and join reviews to their job and latest
assessment in SQL so the queue can filter and sort server-side.

## Security and safety

- **Authentication** (`api/deps.py`, ADR 0005). Every `/api/v1` route except
  `/auth/*` requires one of:
  - an API key in the `X-API-Key` header, compared in constant time (scripts). A key that is
    present but wrong is rejected without falling back to the cookie;
  - a browser session cookie (`next_role_session`: `HttpOnly`, `Secure`, `SameSite=Strict`)
    from `POST /auth/login` with `NEXT_ROLE_UI_PASSWORD`. Only a SHA-256 of the token is
    stored (`operator_sessions`), sessions expire after `NEXT_ROLE_SESSION_TTL_HOURS`, and
    logout deletes the row. State-changing requests made with the cookie must send the
    session's `X-CSRF-Token`. Failed logins are throttled per client (5 per 5 minutes, in
    memory).

  The app refuses to start without an API key unless `NEXT_ROLE_AUTH_DISABLED=true` is set
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
  - Candidate facts reach an LLM only for document drafting and checks, and only with
    `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA=true` (ADR 0006); contact details never do. External
    providers also need `NEXT_ROLE_LLM_ALLOW_EXTERNAL=true`.
- **Web workspace.** Served from the API's origin (`api/spa.py`) with a strict
  Content-Security-Policy (`script-src 'self'`, `frame-ancestors 'none'`), `X-Frame-Options:
  DENY`, and `Referrer-Policy: no-referrer` on every response. Job descriptions are rendered
  as text, never HTML, and source links are shown only for `http(s)` URLs.
- **Input limits.** Description length is capped (`NEXT_ROLE_MAX_JOB_DESCRIPTION_CHARS`),
  and unknown fields are rejected (`extra="forbid"`).

## Not yet implemented

Document generation is being built (Phase 3; see
[features/document-generation.md](features/document-generation.md)). There is no job
discovery or application tracking, and there are no
multi-user accounts. The data model is designed so that each can be added as new tables that
reference opportunities, candidates, reviews, and assessments, without restructuring existing
ones.
