# Phase 3 implementation progress

Working log for Phase 3 (Tailored Resume and Cover-Letter Generation). It exists so that
another session can resume the work without repeating it. The specification is
[features/document-generation.md](../features/document-generation.md).

## Pull requests

| # | Branch | Plan milestone | Scope | Status |
| - | ------ | -------------- | ----- | ------ |
| 1 | `docs/phase-3-alignment` | M0 | Repository-fit audit, specification, ADR 0006, ADR 0007, roadmap, this log | PR open |
| 2 | `feat/document-foundation` | M1 | `documents` module: canonical schemas, tables and migration, run creation in `apply_decision`, reopen guard, authorization re-check, input snapshot, leased claiming, `ArtifactStore`, template registry metadata, candidate `contact` | PR open |
| 3 | `feat/document-drafting` | M2 | Evidence selection, deterministic assembler, LLM drafter, verification checks 1–6, clarifications, section regeneration, `verify_revision` runs in the worker | not started |
| 4 | `feat/document-docx` | M3 | Renderer process and artifact lifecycle, style revisions, DOCX renderer, DOCX QA, download route | not started |
| 5 | `feat/document-pdf` | M3 | Templates (`clean`, `modern-deedy`, `modern`), `print.html`, Playwright PDF renderer, PDF QA, `renderer` Dockerfile target and Compose service, license review | not started |
| 6 | `feat/document-workspace` | M4 | Bundle page: preview, structured editor, style controls, evidence and issues, clarifications, regenerate, history, downloads, approval | not started |
| 7 | `chore/phase-3-hardening` | M5 | `documents.*` notifications, restart and failure tests, security and accessibility passes, resource limits, runbook, release checklist | not started |

Each PR is based on the previous one until it merges. DOCX comes before PDF so that the
artifact lifecycle can be built and tested without a browser.

## Repository-fit audit (M0, 2026-10-22)

What exists and is reused:

| Plan component | Existing code | Gap |
| --- | --- | --- |
| Phase 2 authorization adapter | `review_events` `approve_for_drafting` row (with `assessment_id`), `opportunity_reviews.version` | Run creation and the reopen guard in `review.service` |
| Context provider | `candidates.service`, `jobs.models`, `matching.models` (`requirement_assessments.evidence` snapshot) | Facts are editable, so runs need their own snapshot with per-fact content hashes |
| Evidence model | `candidate_facts` with `verification_status`, `fact_evidence`, `parent_fact_id`, `AchievementAttributes.metrics` | No contact details on `candidates` |
| LLM | `LLMProvider.complete_json()`, Ollama and OpenAI-compatible adapters, `external` flag | Candidate-data opt-in (ADR 0006) |
| Orchestrator / queue | Worker loop and outbox claiming (`FOR UPDATE SKIP LOCKED`) | No task table. The worker only runs when ntfy is enabled; it must run generation regardless |
| Notifications | Outbox, preferences, ntfy publisher, dispatcher | Three new event types and preference columns |
| Auth | `require_auth` (API key, or cookie plus CSRF) | None. One operator, scoped by candidate |
| Storage | — | `ArtifactStore`, `artifacts` volume, backup notes |
| UI | Vite + React SPA, design tokens, strict CSP, review detail page | Templates, print entry, bundle page |
| Rendering | — | Renderer service (ADR 0007), `python-docx`, `pypdf`, `playwright` |

Facts confirmed in code:

- Opportunities and requirements are not modified after ingestion (later postings only
  link), so the approval's `assessment_id` pins the job version.
- Editing a fact's content resets `user_verified` to `self_reported`
  (`candidates.service.update_fact`), which the approval freshness rule relies on.
- ADR 0003 states that candidate data is never sent to an LLM. ADR 0006 supersedes that
  statement for drafting only.

## Decisions

- Maintainer decisions (2026-10-22):
  - The renderer is a separate Compose service built from the same codebase (ADR 0007).
  - Drafts may use self-reported facts, but claims resting on them block approval until
    the facts are confirmed.
  - With no LLM configured, a deterministic assembler produces verbatim drafts.
- The Phase 2 note "refuse revocation once drafting starts" is narrowed: refuse only while a
  run is `running`, cancel a queued run, and withdraw the bundle after completion
  (specification §2).

## Testing log

- Baseline before Phase 3: 219 Python and 25 frontend tests pass on `main` (`1c34741`).
- PR 2: 253 Python tests pass (+19 unit: canonical schemas, style settings, template tokens,
  storage atomicity and hashes, retry policy, bundle status; +15 integration: one run per
  approval, other decisions queue nothing, reopen cancels a queued run, reopen refused under a
  live lease and allowed after it expires, reopen withdraws a bundle and re-approval starts a
  new run, leases prevent double claims and recover crashes, attempts exhausted, retry with
  backoff, authorization goes stale on a newer assessment, snapshot pins the assessment and
  tracks retired and edited facts, append-only trigger, review documents route, contact
  validation). The migration drift test covers `0008_documents`.

- PR 2 implementation notes: bundles have no "current revision" pointers; the current
  revision per kind and the current style are the highest `seq` (unique per bundle), which
  avoids circular foreign keys. Verification status lives on `document_verifications`, not
  on the immutable revision. Bundle states are `needs_input`, `ready_for_review`, `approved`,
  `withdrawn`; drafting progress is the run's status. Document tables reference candidates,
  jobs and reviews with `ON DELETE RESTRICT`, like `review_events`. The worker does not
  process runs yet (PR 3). Notifications move from PR 7 to PR 4, where artifacts settle.

## Known limitations / open issues

- Resume-Matcher's license and any font licenses are reviewed in PR 5, before any design
  element is adapted.
- Exact Playwright image version, bundled fonts, and renderer resource limits are chosen in
  PR 5.
- The deterministic checks cannot detect a claim that strengthens meaning without changing
  values. Only the optional LLM check and the operator's review catch that.

## Review follow-up

- PR 1 (#10): the candidate-data flag is required for every provider, including Ollama;
  operator edits that checks cannot establish block approval and are resolved by editing or
  confirming the text as a fact (no bare acknowledgement); cited facts must be active at
  approval; a permanently failed artifact queues `documents.failed`, not `ready`; the Phase 2
  docs and ADR 0004 now point to the narrowed revocation rule.
