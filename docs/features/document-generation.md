# Tailored resume and cover-letter generation (Phase 3)

This is the Phase 3 specification, adapted from the proposed implementation plan of
21 October 2026 to the code merged in Phases 1 and 2. The decisions behind it are in
[ADR 0006](../decisions/0006-document-generation-and-candidate-data.md) and
[ADR 0007](../decisions/0007-document-rendering-and-renderer-service.md). Progress is tracked in
[implementation/phase-3-progress.md](../implementation/phase-3-progress.md).

**Phase boundary.** Phase 3 drafts, verifies, renders, and lets the candidate approve
documents. It never submits an application, uploads a file to an employer, or sends an
employer-facing message. Approving documents is not approval to apply.

## 1. Outcome

When the candidate approves a job for drafting (Phase 2), the worker automatically drafts a
resume and a cover letter for that job, from a snapshot of the job, the assessment the
approval was based on, and the candidate's facts. Every substantive claim cites the facts
that support it and is checked against them. Where evidence is missing, the draft leaves
the claim out and asks the candidate a question. Both documents are rendered to PDF and
DOCX in one of three templates. The candidate reviews, edits, answers questions, changes
the template, and finally approves the exact revisions and files.

## 2. Differences from the proposed plan

| Proposed plan | Implemented as | Why |
| --- | --- | --- |
| Phase 2 authorization event and adapter | The `approve_for_drafting` row in `review_events` is the authorization. `apply_decision` inserts the generation run in the same transaction. | No event bus exists. The outbox pattern (ADR 0004) already writes side effects in the decision's transaction. |
| `job_version` / snapshot checksum | The approval event's `assessment_id`, which must equal the review's `latest_assessment_id` when the run starts | Opportunities and requirements are not edited after ingestion. Assessments are immutable and fingerprinted. |
| "Refuse revocation once drafting starts" (Phase 2 note) | `reopen` is refused while a run is `running`. A queued run is cancelled. After completion, reopening marks the bundle `withdrawn` and invalidates its document approval. | Refusing forever would leave a review stuck in `approved_for_drafting`. Phase 2 docs now point here. |
| `ClaimEvidenceLink` table | Claims and their `evidence_ids` live in the revision's canonical JSON. Verification results are stored per revision in `document_verifications`. | Revisions are immutable, so a separate link table would only duplicate them. JSONB is queryable when needed. |
| Generic job/queue system | `generation_runs` is the generation queue (worker). `document_artifacts` rows in `pending` are the render queue (renderer). Both are claimed with `FOR UPDATE SKIP LOCKED` plus a lease. | Same PostgreSQL pattern as the outbox. No broker. |
| Protected print route on the API | The renderer loads a bundled `print.html` entry, built from the same React template components as the preview, from local files. It injects the document JSON, and every network request is blocked. | No browser-to-API credential or network path, and nothing for SSRF to reach. |
| Ownership checks per user | Every document route is scoped by candidate and requires operator authentication (API key or session plus CSRF) | There is one operator and no per-user accounts (ADR 0005). |
| "Verified" facts | Both `self_reported` and `user_verified` facts may be used. A claim that rests on a `self_reported` fact blocks approval until that fact is confirmed. | Maintainer decision (2026-10-22). |
| LLM always present | With `NEXT_ROLE_LLM_PROVIDER=none`, a deterministic assembler selects and orders facts and uses their text verbatim. The cover letter becomes a partial draft with questions. | Maintainer decision (2026-10-22). The default configuration has no LLM. |
| Candidate data never sent to an LLM (ADR 0003) | Candidate facts are sent to the configured provider for drafting and semantic checks. Any provider (local or external) needs `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA=true`. | Drafting cannot work without it. The provider type does not prove where its endpoint runs, so sending candidate data is always an explicit opt-in (ADR 0006). |
| Contact details | New validated `contact` on `candidates` (email, phone, links) | The resume header needs them, and the profile has no such fields. |

## 3. Workflow

1. The candidate applies `approve_for_drafting` to a review. In the same transaction,
   `apply_decision` inserts a `generation_runs` row (`intent = initial`, `queued`), keyed by
   the approval event. A retried or concurrent request cannot create a second run.
2. The worker claims the run and checks the authorization again. The review must still be
   `approved_for_drafting`, its latest approval event must be this one, and its
   `latest_assessment_id` must equal the event's `assessment_id`. Otherwise the run fails
   with `stale_authorization`, and the UI asks the candidate to approve again.
3. The worker writes an immutable **input snapshot**: job fields and requirements, the
   assessment's requirement results, the candidate's display details, and every active fact
   with its type, attributes, verification status, evidence links, and a content hash.
4. **Evidence selection** (deterministic) ranks facts against the requirements and the
   assessment's matched evidence. Keyword overlap is relevance only; it never upgrades a
   fact's trust.
5. **Drafting** produces typed resume and cover-letter JSON (§5). With an LLM, the model may
   select, reorder, and rewrite the selected facts and must cite `evidence_ids`. Without
   one, the assembler uses fact text verbatim.
6. **Verification** (§6) runs on each revision. Missing material evidence becomes a
   clarification request, and the unsupported claim is left out, not guessed.
7. The run creates the **bundle** with its first resume and letter revisions, then queues
   artifact rows for each document × format (PDF, DOCX) with the default style.
8. The **renderer** claims artifact rows, renders, runs QA, stores the file with its SHA-256,
   and marks the row `ready` or `failed`.
9. When every artifact for the current revisions has settled, one notification is queued
   in the outbox: `documents.failed` if any artifact failed permanently; otherwise
   `documents.needs_input` if questions or blocking issues are open; otherwise
   `documents.ready`.
10. The candidate edits, answers questions, regenerates a section, or changes the style.
    Each change creates a new revision or style and new artifacts (§8).
11. The candidate approves the bundle. The approval binds the exact revisions, style, and
    artifact hashes (§9).

### 3.1 Partial drafts

Both formats are rendered even when questions are open, unless the document cannot be
rendered safely. The incomplete state is shown in the UI, in artifact metadata, and in the
download file name (`…-DRAFT.pdf`). There is no watermark in the PDF, because a watermark
hurts text extraction. Open **material** questions and blocking issues prevent approval. The
candidate can dismiss a non-material question and leave the content out. The dismissal is
recorded.

## 4. Data model

```
opportunity_reviews ─< review_events (approve_for_drafting) ─< generation_runs ─> generation_inputs
                                                                    │
document_bundles (candidate × opportunity, run) ─┬─< document_revisions ─< document_verifications
                                                 ├─< clarification_requests
                                                 ├─< document_styles
                                                 ├─< document_artifacts (revision × style × format)
                                                 └─< document_approvals
```

| Table | Key fields and invariants |
| --- | --- |
| `generation_runs` | `review_id`, `authorization_event_id`, `bundle_id` (set once the initial run creates the bundle), `intent` (`initial`, `regenerate_section`, `verify`), `idempotency_key` (unique), `parameters`, `input_id`, `status`, `stage`, `attempts`, `next_attempt_at`, `lease_token`, `lease_expires_at`, redacted `last_error` |
| `generation_inputs` | Immutable JSONB snapshot, `snapshot_hash`, `policy_version`, prompt and model reference. Rows are never updated. |
| `document_bundles` | `candidate_id`, `opportunity_id`, `review_id`, `authorization_event_id` (unique: one bundle per approval), `input_id`, `status` (§7), `drafting_mode` and notes, `version` for optimistic locking |
| `document_revisions` | Immutable: `bundle_id`, `kind` (`resume`, `cover_letter`), `parent_id`, `schema_version`, `content` (JSONB), `content_hash`, `created_by` (`system`, `llm`, `operator`), `seq` (1, 2, 3… per bundle and kind; the highest is current), `run_id`, `note` |
| `document_verifications` | `revision_id`, `check_version`, per-claim results, blocking and non-blocking issues, `created_at` |
| `clarification_requests` | `bundle_id`, `revision_id`, `claim_path`, `question`, `material`, `status` (`open`, `answered`, `dismissed`), answer, disposition |
| `document_styles` | Immutable: `seq` (the highest is current), `template_id`, `template_version`, `settings` (page size, density, accent, font, section order and visibility), `style_hash` |
| `document_artifacts` | `revision_id`, `style_id`, `format`, `status` (§7), `storage_key`, `sha256`, `bytes`, `page_count`, `renderer_version`, QA result, lease fields |
| `document_approvals` | Approved revision IDs, style ID, artifact IDs and hashes, verification IDs, the authorization event, actor, `created_at`. Append-only, same trigger as `review_events`. |

There are no "current revision" pointers to keep in sync: the current revision of each kind
and the current style are the rows with the highest `seq`, which a unique constraint keeps
unambiguous. A revision's verification status is that of its latest `document_verifications`
row.

`document_revisions`, `generation_inputs`, `document_styles`, and `document_approvals` are
append-only. A trigger rejects updates and deletes, as on `review_events`.

## 5. Canonical document

The canonical document is a Pydantic model (`documents/schemas.py`), not the React tree. The
LLM emits this JSON. It never emits HTML, CSS, or file formats.

```json
{
  "kind": "resume",
  "schema_version": "resume/v1",
  "header": {"name": "…", "headline": "…", "location": "…", "contact": {"email": "…", "links": []}},
  "sections": [
    {
      "id": "experience",
      "type": "experience",
      "title": "Experience",
      "items": [
        {
          "id": "exp-1",
          "source_fact_id": "f3b0…",
          "organization": "Example Ltd",
          "position": "Software Developer",
          "start_date": "2024-01",
          "end_date": null,
          "bullets": [
            {"claim_id": "c-001", "text": "Built a Python analytics service…", "evidence_ids": ["a91e…"]}
          ]
        }
      ]
    }
  ]
}
```

- Section types: `summary`, `experience`, `projects`, `education`, `certifications`, `skills`.
- Items carry `source_fact_id`. Organization, position, dates, institution, and credential
  name are copied from the fact by code, not written by the model.
- The cover letter has `salutation`, `paragraphs[]` (each a list of claims, some citing
  evidence and some marked `connective`), and `closing`.
- Limits: bullets per item, characters per bullet, and paragraph count, enforced by the
  schema.
- Style is separate from content (`document_styles`).

## 6. Truthfulness and verification

**Allowed:** choosing relevant facts, reordering, summarizing and rewriting supported facts,
emphasizing skills that are evidenced, and leaving things out.

**Not allowed:** inventing metrics, team sizes, certifications, employers, titles, years of
experience, tools, outcomes, or client names, and adding unsupported keywords.

Checks, in order. Each claim ends as `supported`, `needs_review`, or `unsupported`:

1. **Schema and completeness.** The JSON validates, IDs are unique, dates are well formed,
   limits are respected, and no section is empty.
2. **Protected facts.** Organization, position, dates, institution, and credential match the
   cited source fact. Every number, percentage, currency amount, and duration in a claim
   appears in a cited fact's text, metrics, or attributes, with the same unit.
3. **Evidence links.** Every substantive claim cites at least one fact ID present in the
   snapshot. Skills and tools named in a claim are canonicalized (`skills.py`) and must be
   among the cited facts' skills or text.
4. **Semantic check (LLM only).** A separate call judges whether each rewritten claim is
   entailed by its cited facts. "Entailed" is accepted. "Uncertain" becomes `needs_review`.
   "Not entailed" becomes `unsupported`. Without an LLM, only checks 2 and 3 apply. Verbatim
   assembled text passes them; text that is not a verbatim fact sentence is `unsupported`,
   because nothing has established its meaning.
5. **Cross-document.** The letter cites only facts the snapshot contains, and its protected
   values match the resume's.
6. **Trust.** A claim whose evidence includes only `self_reported` facts is blocking until
   those facts are `user_verified` (maintainer decision). Confirming a fact from the review
   page calls the existing verify endpoint.

At approval time the checks are re-evaluated against the **current** facts. Each cited
fact must still be active (not retired), its content hash must still equal the snapshot's,
and its status must be `user_verified`. Editing or retiring a fact after generation therefore
blocks approval until the section is regenerated or edited. This reuses Phase 1's rule that
editing a fact clears its confirmation.

`unsupported` and `needs_review` claims, and blocking issues, all prevent approval. There is
no bare acknowledgement. The operator resolves a claim in one of three ways:

- edit or remove it (a new revision, verified again);
- regenerate the section;
- **confirm it as a fact**: the claim's text becomes a new `user_verified` candidate fact
  with a `user_statement` evidence source, and a system revision adds that fact to the
  claim's `evidence_ids`. The claim is then supported verbatim by a confirmed fact, and the
  candidate's attestation is recorded with provenance like any other fact.

This applies equally to drafted and operator-edited text, with or without an LLM.

**Clarification answers.** An answer that is a career fact (for example "Did Project X have
a measurable result?") is proposed as a new `candidate_fact` with an evidence source of kind
`user_statement`. It is used only after the operator confirms it. Letter-only answers (for
example motivation for the company) stay on the clarification request and are cited as
`operator_statement` evidence for that bundle only.

**Untrusted input.** The job description and requirement text are passed to the model as
quoted data, with a system instruction that they carry no authority. Output that cites
unknown IDs, or contains unsupported values, fails verification regardless of what the
prompt said. A test covers an injection line in a job description.

## 7. States

Each lifecycle is a pure transition table, like `review/workflow.py`.

| Lifecycle | States |
| --- | --- |
| Generation run | `queued` → `running` → `completed` / `failed` / `cancelled` |
| Revision verification | `pending` → `passed` / `blocked` (with per-claim `supported` / `needs_review` / `unsupported`) |
| Bundle | `needs_input` ⇄ `ready_for_review` → `approved`; any change returns an approved bundle to `needs_input`/`ready_for_review`; `withdrawn` when the review is reopened. Whether drafting is in progress is the run's status, not the bundle's. |
| Artifact | `pending` → `rendering` → `ready` / `failed`; `superseded` when a newer revision or style replaces it |

- Runs and artifacts are claimed with `FOR UPDATE SKIP LOCKED` in a short transaction that
  sets `lease_expires_at`. A row whose lease has expired can be reclaimed, so a crashed
  worker never leaves a run stuck. Attempts are bounded, and backoff is exponential.
- Provider timeouts, 429, and 5xx retry. Schema-invalid model output retries once, then the
  run falls back to the deterministic assembler for the affected section and records why.
  Security and authorization failures do not retry.
- An artifact is `ready` only after the file is fully written, fsynced, hashed, and passes
  QA. The row update is the commit point. Files without a `ready` row are orphans, and a
  sweep removes them.
- The bundle's current revision pointers move atomically with the new revision row.
  Downloads always name the revision and style they belong to, and an older revision's file
  is never served as current.

## 8. Edits, regeneration, and styles

- **Save edit:** the structured form submits the full section with the bundle `version`. A
  stale version gets `409`. The edit creates a new revision (`created_by = operator`),
  invalidates its artifacts, and queues a `verify_revision` run.
- **Regenerate section:** the run uses the original input snapshot plus answered
  clarifications. It replaces only that section, and verifies and renders the result.
- **Change style:** creates a new `document_styles` row and new artifacts. It does not
  create a content revision or call the LLM.
- **Answer or dismiss a clarification:** updates the request. Accepting an answer offers a
  section regeneration and never edits text silently.

## 9. Approval

`POST /bundles/{id}/approval` with `expected_version`, the revision IDs, the style ID, and the
artifact hashes the operator saw. The server locks the bundle and checks:

- the version and every ID and hash still match current;
- all four artifacts (2 documents × 2 formats) are `ready`, and their QA passed;
- verification passed: every claim is `supported` and there are no blocking issues;
- no material clarification is open;
- the trust and freshness rules in §6 hold;
- the review is still `approved_for_drafting` for the same authorization event.

It then records a `document_approvals` row and sets the bundle to `approved`. Any later
revision or style change moves the bundle back to `ready_for_review`. The old approval stays
in history but no longer describes the current files. Phase 3 has no consumer of approvals.
A future application workflow must read approvals explicitly.

## 10. Templates and rendering

Three single-column resume templates and one cover-letter style, implemented independently
(not copied) in `frontend/src/templates/`. Resume-Matcher is only design inspiration, and its
license is checked before borrowing anything (PR 5).

| Template | Character |
| --- | --- |
| `clean` (default) | Understated, conventional heading hierarchy |
| `modern-deedy` | Denser, oriented to one page for technical roles; minimum type size still applies |
| `modern` | Clean, with a restrained accent colour from a fixed preset list |

Controls: template, A4 or US Letter, density (`normal`/`compact`), accent preset, font from a
bundled list (licensed fonts, e.g. SIL OFL, with system fallback), and section order and
visibility. There is no free CSS, there are no user fonts, and the minimum body size is 9.5 pt.

- **Preview:** the SPA renders the same components from the canonical JSON. No PDF is
  needed to preview.
- **PDF:** the renderer runs Chromium through Playwright, loads the bundled `print.html`,
  injects the JSON, blocks all network access, waits for fonts, measures overflow, and
  prints a text-based PDF. Render time and concurrency are bounded.
- **DOCX:** `python-docx` maps sections to native Word styles (headings, body, list bullets).
  Text is never rendered as an image. The goal is close branding and the same content, not
  pixel parity with the PDF. Known differences are documented.
- **QA (both formats):** the file exists and is not empty; extracted text (via `pypdf`, and
  from the OOXML) contains every section heading and every claim's text in order; page count
  is recorded, with a warning above the target (resume 2 pages, letter 1); in the PDF, no
  element overflows its box (measured in the DOM before printing). Long words wrap. Text is
  never silently truncated.

## 11. API (summary)

All routes are under `/api/v1` and require authentication.

| Route | Purpose |
| --- | --- |
| `GET /reviews/{review_id}/documents` | Bundles for a review, with run status |
| `GET /bundles/{id}` | Bundle, current revisions, verification, clarifications, artifacts |
| `GET /bundles/{id}/revisions` · `GET /revisions/{id}` | History and one revision |
| `POST /bundles/{id}/revisions` | Save an edit (`expected_version`) |
| `POST /bundles/{id}/regenerations` | Regenerate a section (client idempotency key) |
| `POST /bundles/{id}/style` | New style; queues renders |
| `POST /clarifications/{id}/answer` · `/dismiss` | Answer or dismiss a question |
| `POST /bundles/{id}/claims/{claim_id}/confirm-as-fact` | Record a claim as a confirmed fact and re-cite it |
| `POST /bundles/{id}/approval` | Approve exact revisions, style, and hashes |
| `GET /artifacts/{id}/download` | A `ready` file after checking its hash; `Content-Disposition: attachment`, `nosniff`, `no-store`. Superseded files are kept for history but are not downloadable as current. |
| `POST /artifacts/{id}/retry` | Queue a failed file again |
| `GET /templates` | Template registry (IDs, versions, controls) |

## 12. Notifications

| Event | Sent when | Message |
| --- | --- | --- |
| `documents.ready` | First complete set of artifacts for a bundle with nothing blocking | "Draft documents are ready for review." |
| `documents.needs_input` | The same, with open material questions or blocking issues | "Draft documents need your input." |
| `documents.failed` | A run or render failed permanently | "Document generation needs attention." |

The messages carry no personal data, link to the bundle page (login required), are queued
once per bundle and event (deterministic `event_key`), and can be switched off with the
`documents` notification preference (one switch for all three). Delivery failures never change document state.

## 13. Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA` | `false` | Required to send candidate facts to any LLM provider |
| `NEXT_ROLE_ARTIFACT_DIR` | `/var/lib/next-role/artifacts` | Artifact storage root (Compose volume) |
| `NEXT_ROLE_GENERATION_MAX_ATTEMPTS` | `3` | Attempts per run |
| `NEXT_ROLE_GENERATION_LEASE_SECONDS` | `600` | Run lease |
| `NEXT_ROLE_RENDER_TIMEOUT_SECONDS` | `30` | Per-artifact render limit |
| `NEXT_ROLE_RENDER_CONCURRENCY` | `1` | Parallel renders in the renderer |

## 14. Security and privacy

- Job text and model output are untrusted (§6). The model cannot choose markup, URLs, or
  files.
- The renderer runs as a non-root user, with no network route to anything, all Playwright
  requests other than bundled files aborted, JavaScript only from the bundle, a read-only
  root filesystem, and write access only to the artifact volume.
- The API mounts the artifact volume read-only. There is no static route to it. Downloads
  go through authenticated routes that resolve a storage key from the database, and are
  never built from user input.
- Logs carry IDs, statuses, durations, and counts. They never contain document text,
  prompts, contact details, or job text.
- Retention: artifacts and revisions are kept until the operator deletes the candidate. The
  runbook gains the erasure and backup procedure for the artifact volume, alongside `pgdata`.

## 15. Acceptance criteria

The cross-cutting scenarios of the proposed plan (§12 there) are the acceptance tests:

- [ ] A review that is not `approved_for_drafting`, or whose assessment changed after
      approval, starts no generation.
- [ ] A repeated approval request creates one run and one bundle.
- [ ] Matching facts are emphasized and cited. An invented metric or certification is
      blocking.
- [ ] Retiring or editing a cited fact after generation blocks approval.
- [ ] Without an LLM, an operator edit that is not grounded verbatim in a fact blocks
      approval until it is changed or confirmed as a fact.
- [ ] Missing material evidence yields a partial draft, questions, and both formats.
- [ ] An answered question is stored with provenance, and the new revision is verified and
      rendered.
- [ ] A style change renders new files without calling the LLM.
- [ ] An edit creates a new revision, and old verification and approval do not carry over.
- [ ] PDF and DOCX text contains every heading and claim. Long content wraps and is never
      truncated.
- [ ] A failed PDF with a successful DOCX shows a per-artifact error with retry, and blocks
      approval.
- [ ] A worker or renderer restart mid-task recovers, with no orphaned `ready` rows.
- [ ] ntfy downtime does not affect documents.
- [ ] A stale approval gets `409`.
- [ ] Nothing is ever submitted.
