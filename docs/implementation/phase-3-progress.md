# Phase 3 implementation progress

Working log for Phase 3 (Tailored Resume and Cover-Letter Generation). It exists so that
another session can resume the work without repeating it. The specification is
[features/document-generation.md](../features/document-generation.md).

## Pull requests

| # | Branch | Plan milestone | Scope | Status |
| - | ------ | -------------- | ----- | ------ |
| 1 | `docs/phase-3-alignment` | M0 | Repository-fit audit, specification, ADR 0006, ADR 0007, roadmap, this log | PR open |
| 2 | `feat/document-foundation` | M1 | `documents` module: canonical schemas, tables and migration, run creation in `apply_decision`, reopen guard, authorization re-check, input snapshot, leased claiming, `ArtifactStore`, template registry metadata, candidate `contact` | PR open |
| 3 | `feat/document-drafting` | M2 | Evidence selection, deterministic assembler, LLM drafter, verification checks 1–6, clarifications, section regeneration, `verify` runs in the worker | PR open |
| 4 | `feat/document-docx` | M3 | Renderer process and artifact lifecycle, style revisions, DOCX renderer, DOCX QA, download route, approval, `documents.*` notifications | PR open |
| 5 | `feat/document-pdf` | M3 | Templates (`clean`, `modern-deedy`, `modern`), `print.html`, Playwright PDF renderer, PDF QA, `renderer` Dockerfile target and Compose service, license review | PR open |
| 6 | `feat/document-workspace` | M4 | Bundle page: preview, structured editor, style controls, evidence and issues, clarifications, regenerate, history, downloads, approval | PR open |
| 7 | `chore/phase-3-hardening` | M5 | Restart and failure tests, security and accessibility passes, resource limits, runbook, release checklist | not started |

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

- PR 3: 299 Python tests pass (+34 unit: verbatim support, dropped negation, invented numbers
  and skills, unknown and misattributed evidence, statements only in letters, rewording with
  and without an LLM, the LLM cannot override deterministic failures, unconfirmed facts,
  protected entry fields, skill claims, connective templates and smuggled claims, stale
  evidence; deterministic draft passes its own checks, unconfirmed facts block, questions,
  verbatim answers, prompt injection, LLM bullets with code-owned entries, prompts escape job
  text and omit contact details, retry then per-document fallback, judge batching, section
  replacement, strict output schemas; +11 integration: drafting end to end, edit then check,
  confirm as fact, confirming profile facts and re-checking, answering and dismissing
  questions, letter regeneration quoting an answer, career answers as unconfirmed facts,
  regeneration validation and idempotency, a newer assessment stops drafting, a cancelled
  run writes nothing, withdrawn bundles are read-only, LLM drafting with cached verdicts,
  candidate-data opt-in).
- PR 3 implementation notes: every run builds its own input snapshot (regeneration uses the
  current facts, including ones confirmed since); verification always checks against the
  current active facts and records the content hash of each cited fact; entries (jobs,
  degrees…) are recorded as `item-<id>` results so their source facts are tracked too. LLM
  verdicts are cached per bundle by a key over the claim text and its sources. JSON in
  prompts escapes `<` and `>` so job text cannot close its tag. Regeneration refuses to
  overwrite an edit made while it ran (it retries from the new revision).

- PR 4: 329 Python tests pass (+19 unit: dates, punctuation, section order and visibility,
  letter display, DOCX for 3 templates × 2 page sizes × 2 densities passing QA, native bullets
  and template font, QA catches missing and reordered text and records counts only, empty and
  non-PDF files, long unbroken text kept whole; +11 integration: four files queued per
  bundle, a DOCX-only renderer leaves PDFs, one notification when every file settles,
  hash-checked draft-named downloads behind auth, style changes supersede files without
  touching content or drafting, a file superseded mid-render is discarded, failures retry
  then fail and notify, QA failures are never ready, the orphan sweep, approval binds exact
  revisions and hashes and is lost on change, approval refused after a fact is retired or
  the review reopened, failed initial runs notify).
- PR 4 implementation notes: the display model (`display.py`) is the single place that
  formats dates, orders and hides sections and punctuates sentences; DOCX, the coming PDF
  print page, the web preview and QA all consume it. QA stores counts, never text.
  Approval and notifications moved into this PR because they depend on files settling.
  A test that left a session open blocked the fixture's `TRUNCATE`; tests close sessions.

- PR 5: 330 Python tests pass and 8 PDF tests are skipped outside the renderer image; in the
  image (`docker run … -m renderer`) all 8 pass: every template in A4 and Letter yields a
  one-page text PDF that passes QA at the right page width, the browser only loads bundled
  files (markup and URLs in content stay text), and long content breaks across pages with a
  length warning and no overflow. 27 frontend tests pass (+2: template rendering escapes
  markup and applies tokens; letter layout). +1 integration: preview of current and unsaved
  content and style.
- PR 5 manual checks: screenshots of the three templates and the letter reviewed (clean
  rule headings; Modern Deedy uppercase accent headings and tighter spacing; Modern accent
  name and bar headings). The print page's CSP blocked an injected style tag. On the full
  Compose stack (`db`, `migrate`, `api`, `worker`, `renderer`, all healthy): approving a
  review drafted both documents, the renderer produced four files that passed QA, and the
  PDF downloaded as `alex-example-resume-fabrikam-logistics-r1-DRAFT.pdf`. From inside the
  renderer, HTTP and raw TCP to the internet failed, the root filesystem was read-only, and
  the API's artifact mount was read-only.
- PR 5 license review: no code, styles, fonts or assets were taken from Resume-Matcher; the
  three templates were written for this project from general layout conventions. Fonts are
  the Liberation families shipped in the Playwright image (SIL Open Font License 1.1),
  mapped to Arial and Times New Roman in DOCX; no font files are bundled. Playwright and
  Chromium are Apache-2.0/BSD; `python-docx` is MIT; `pypdf` is BSD-3-Clause.
- PR 5 notes: Playwright cannot install Chromium on the development host's OS (Ubuntu
  26.04), so Chromium runs only in the pinned image. QA compares text with whitespace
  removed, because Chromium may split a very long word across lines.

- PR 6: 35 frontend tests pass (+8: documents page shows preview, blockers and how to
  resolve them; confirming a fact re-checks; required questions need a reason to dismiss;
  edits carry the bundle version and a 409 reloads; approval sends exact revision, style and
  file hashes; withdrawn bundles are read-only; design changes preview before applying; an
  approved review links to its documents). Python suite unchanged (330 passed).
- PR 6 manual check on the Compose stack with the rebuilt API image: signed in, opened the
  review, followed the Documents link, viewed checks, questions, files and design, opened the
  editor and the cover letter, at 1440 px and 390 px. No console errors and no horizontal
  overflow. Fixed from screenshots: a lowercased "Add bullet" label, a repeated drafting
  notice, and a file row wrapping its download button.
- PR 6 notes: the preview of unsaved edits, unapplied designs and older versions comes from
  `POST /bundles/{id}/preview`, so it always matches the files. The page polls every 3 s
  only while drafting, checks or rendering are in progress. Approving for drafting now
  explains that drafting starts; reopening explains that drafts are withdrawn.

## Known limitations / open issues

- The renderer image is large (about 3.8 GB, mostly Chromium and its dependencies).
- The workspace has no drag-and-drop; bullets are reordered with up and down buttons.
- Achievements without a parent job or project are not placed on the resume yet.
- The deterministic checks cannot detect a claim that strengthens meaning without changing
  values. Only the optional LLM check and the operator's review catch that.

## Review follow-up

- PR 1 (#10): the candidate-data flag is required for every provider, including Ollama;
  operator edits that checks cannot establish block approval and are resolved by editing or
  confirming the text as a fact (no bare acknowledgement); cited facts must be active at
  approval; a permanently failed artifact queues `documents.failed`, not `ready`; the Phase 2
  docs and ADR 0004 now point to the narrowed revocation rule.
