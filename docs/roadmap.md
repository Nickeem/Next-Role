# Roadmap

The roadmap is planning context, not authorization to build future phases (see `AGENTS.md`).
Each phase is proposed and reviewed separately.

## Phase 1: Foundation and matching (implemented)

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

## Phase 2: Review and approval workspace (in progress)

Goal: the candidate reviews persisted assessments in a protected web workspace and records
explicit decisions (shortlist, reject, postpone, approve for drafting), with an append-only
history and ntfy notifications from a background worker. Approval only records intent: no
documents are generated and nothing is submitted. The specification is
[features/review-workflow.md](features/review-workflow.md); progress is tracked in
[implementation/phase-2-progress.md](implementation/phase-2-progress.md).

Pull requests, in order:

- [x] Docs alignment: this roadmap, ADR 0004 (review workflow and outbox), ADR 0005 (web
      workspace and session authentication).
- [x] Review workflow core: review state machine, decision events, notification outbox,
      backfill of existing assessments.
- [x] Review API: queue summary and list, details, decisions, activity, notification
      preferences.
- [ ] ntfy worker: publisher adapter, outbox dispatcher with retries, postponed-review
      reminders, test notification, Compose `worker` service.
- [ ] Session authentication for the browser (password login, cookie session, CSRF).
- [ ] Web workspace: review queue, assessment detail, decisions, activity, settings.
- [ ] Hardening: security and accessibility passes, runbook, release checklist.

## Later phases (high level, not yet planned in detail)

- **Phase 3: Document generation.** Fact selection from the knowledge base, LLM-drafted
  resumes and cover letters, verification of every substantive claim against facts,
  deterministic rendering, and versioned artifacts. Drafting starts only for opportunities
  whose latest review state is `approved_for_drafting`, and checks which assessment the
  approval was based on.
- **Phase 4: Job discovery and background processing.** Job provider adapters (respecting
  terms and rate limits), scheduled searches, asynchronous extraction and re-assessment on
  the PostgreSQL-backed worker introduced in Phase 2.
- **Phase 5: Application assistance.** Opportunity lifecycle and application tracking.
  Submission assistance happens only with separate, explicit, action-specific candidate
  approval.

Candidates for improving matching, in any phase:

- LLM-assisted mapping of `unknown` requirements to evidence, with deterministic
  verification of the cited facts.
- A larger skill vocabulary.
- Evaluating field of study and seniority.
