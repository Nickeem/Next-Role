# Roadmap

The roadmap is planning context, not authorization to build future phases (see `AGENTS.md`).
Each phase is proposed and reviewed separately.

## Phase 1: Foundation and matching (implemented; under review)

Goal: a user can store verified career information, submit a job description, and receive an
explainable, evidence-backed suitability assessment.

Delivered:

- [x] Application foundation: FastAPI, PostgreSQL, SQLAlchemy and Alembic, typed configuration,
      JSON logging, error handling, health checks, Docker Compose, ruff, mypy, and pytest.
- [x] Candidate knowledge base with provenance (verification status, evidence sources) and
      matching preferences.
- [x] Manual job ingestion with source retention, normalization, unknown-preserving fields,
      and deduplication.
- [x] Rule-based requirement extraction, with optional LLM-assisted segmentation (validated,
      grounded, with fallback).
- [x] Deterministic matching and scoring (`rules-v1`), and persisted, idempotent assessments.

Known limitations are listed in [matching.md](matching.md#7-known-limitations-of-the-rule-based-path)
and [implementation/phase-1-progress.md](implementation/phase-1-progress.md).

## Later phases (high level, not yet planned in detail)

- **Phase 2: Background processing and job discovery.** A worker process with a simple
  PostgreSQL-backed task queue, workflow events, job provider adapters (respecting terms and
  rate limits), scheduled searches, and asynchronous re-assessment.
- **Phase 3: Notifications and candidate review.** Candidate-facing alerts for strong
  matches, review and decision records (interested / not interested), and delivery tracking.
- **Phase 4: Document generation.** Fact selection from the knowledge base, LLM-drafted
  resumes and cover letters, verification of every substantive claim against facts,
  deterministic rendering, and versioned artifacts.
- **Phase 5: Application assistance.** Opportunity lifecycle and application tracking.
  Submission assistance happens only with separate, explicit, action-specific candidate
  approval.

Candidates for improving matching, in any phase:

- LLM-assisted mapping of `unknown` requirements to evidence, with deterministic
  verification of the cited facts.
- A larger skill vocabulary.
- Evaluating field of study and seniority.
