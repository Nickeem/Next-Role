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

## Phase 2: Review and approval workspace (implemented; awaiting review and sign-off)

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
- [x] ntfy worker: publisher adapter, outbox dispatcher with retries, postponed-review
      reminders, test notification, Compose `worker` service.
- [x] Session authentication for the browser (password login, cookie session, CSRF).
- [x] Web workspace: review queue, assessment detail, decisions, activity, settings.
- [x] Hardening: security and accessibility passes, runbook, release checklist.

## Phase 3: Tailored resume and cover-letter generation (implemented; awaiting review and sign-off)

Goal: for each job the candidate approves for drafting, generate a tailored resume and cover
letter whose substantive claims cite and are checked against the candidate's facts, render
them to PDF and DOCX in one of three templates, and let the candidate edit, answer
questions, and approve exact revisions. Nothing is submitted. The specification is
[features/document-generation.md](features/document-generation.md), the decisions are ADR 0006
and ADR 0007, and progress is tracked in
[implementation/phase-3-progress.md](implementation/phase-3-progress.md).

Pull requests, in order:

- [x] Docs alignment: repository-fit audit, specification, ADR 0006, ADR 0007.
- [x] Document foundation: schemas, tables, run creation on approval, snapshots, storage.
- [x] Drafting and verification: evidence selection, LLM and deterministic drafting, claim
      checks, clarifications.
- [x] DOCX rendering and the artifact lifecycle.
- [x] Templates, PDF rendering, and the renderer service.
- [x] Document workspace: preview, editing, styles, approval.
- [x] Hardening: notifications, recovery, security, runbook, release checklist.

## LLM hardening (done)

Goal: make every LLM call observable, and let each task use the model and reasoning effort
it needs. This is groundwork for a future ADR that revisits ADR 0003 and gives the LLM more
responsibility. Nothing here changes what the LLM decides: matching, scoring, and
classification stay deterministic.

- [x] LLM provider setup guide (PR #18).
- [x] Retry OpenAI-compatible calls without a rejected temperature (PR #19).
- [x] Stop asking the same document question twice (PR #20).
- [x] One gateway for every LLM call, call events per task, and status on the Settings page
      (PR #21).
- [x] A model and reasoning effort per task
      ([ADR 0008](decisions/0008-per-task-llm-model-and-effort.md)), as a stack of pull
      requests:
  - [x] Docs alignment: ADR 0008, this section (PR #22).
  - [x] Model and effort per call through the provider and gateway, with no behaviour change
        (PR #23).
  - [x] Model catalog and reasoning-effort rules (PR #24).
  - [x] Stored per-task choices, resolution, and fallback to the default model (PR #25).
  - [x] Model discovery and the task-model API (PR #26).
  - [x] Settings UI and operations docs (PR #27).

## Later phases (high level, not yet planned in detail)

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
