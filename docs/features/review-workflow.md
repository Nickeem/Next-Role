# Review and approval workspace (Phase 2)

This is the Phase 2 specification, adapted from the proposed plan of 18 October 2026 to the
Phase 1 code. The decisions behind it are in
[ADR 0004](../decisions/0004-review-workflow-and-notification-outbox.md) and
[ADR 0005](../decisions/0005-web-workspace-and-session-auth.md).

**Phase boundary.** Approval records that the candidate authorizes *future* drafting. Phase 2
does not generate resumes or cover letters, discover jobs, or submit applications.

## 1. Outcome

The candidate opens a protected web workspace, sees which assessed jobs need attention,
inspects the score, evidence, gaps, and unknowns, and records a decision. Every decision is
persisted with its reason and the assessment it was based on, and can be audited later.
Selected events are published to the candidate's existing ntfy server by a background
worker. If ntfy is down, decisions still succeed.

## 2. Differences from the proposed plan

| Proposed plan | Implemented as | Why |
| --- | --- | --- |
| One review per opportunity | One review per `(candidate_id, opportunity_id)` | Assessments are per candidate × opportunity, and `candidates` holds many rows |
| Assessment statuses `not assessed`/`running`/`failed` | Only `completed` exists | Phase 1 assessment runs synchronously within the request |
| Idempotent bridge from assessment-completed events | Review row and outbox row written in the assessment's transaction | There is no separate completion event to bridge |
| `GET /api/v1/opportunities…` | Review routes under `/api/v1/reviews…`; job and assessment details reuse `/jobs` and `/assessments` | Phase 1 already exposes `/jobs` |
| `NTFY_*` variables | `NEXT_ROLE_NTFY_*` | Settings share the `NEXT_ROLE_` prefix |
| `backend/`, `frontend/`, `docs/adr/` | `src/next_role/<module>/`, `frontend/`, `docs/decisions/` | Existing layout |
| Next.js | Vite + React SPA served by FastAPI | One image and one auth boundary (ADR 0005) |
| Optimistic version **and** idempotency key | Optimistic version only | A retried request gets `409` and the client reloads; enough for one operator |
| Behaviour on re-assessment unspecified | See §3.3 | Assessments are immutable; a changed knowledge base creates a new one |

## 3. Review workflow

### 3.1 States

| State | Meaning |
| --- | --- |
| `pending_review` | Assessed and awaiting a decision |
| `shortlisted` | Worth pursuing; drafting is **not** authorized |
| `postponed` | Deferred until `postpone_until` (UTC, in the future when set) |
| `rejected` | Dismissed; kept for history and hidden from the queue by default |
| `approved_for_drafting` | The candidate explicitly authorized a later phase to draft documents |

Review state is separate from the assessment (owned by `matching`) and from the opportunity's
`active`/`archived` status (owned by `jobs`). Rejecting does not archive the job, and no
review state is derived from a score.

### 3.2 Actions

| Action | From | To | Requires |
| --- | --- | --- | --- |
| `shortlist` | pending_review, postponed | shortlisted | reason if the AI tier disagrees |
| `postpone` | pending_review, shortlisted, postponed | postponed | `postpone_until` in the future |
| `reject` | pending_review, shortlisted, postponed | rejected | `confirm: true`; reason if the tier is `strong` |
| `approve_for_drafting` | pending_review, shortlisted, postponed | approved_for_drafting | `confirm: true`; reason if the AI tier disagrees |
| `return_to_review` | shortlisted, postponed | pending_review | — |
| `reopen` | rejected, approved_for_drafting | pending_review | reason |

"The AI tier disagrees" with a positive decision (shortlist, approve) when the assessment
tier is `weak` or `insufficient_data`, or the assessment has blocking gaps. It disagrees with
a rejection when the tier is `strong` and there are no blocking gaps. A low score never
blocks a decision; it only requires a reason.

Reopening an approval revokes it. In Phase 2 nothing consumes approvals, so revocation is
always allowed. Phase 3 checks the current review state and its assessment before drafting.
It refuses to revoke only while a generation run is `running`; a queued run is cancelled, and
reopening after drafting withdraws the bundle and its document approval (see [document-generation.md §2](document-generation.md#2-differences-from-the-proposed-plan)).

### 3.3 Assessments and reviews

- The first completed assessment for a candidate × opportunity creates the review in
  `pending_review` and queues a `review.ready` notification.
- A later assessment (new inputs, new fingerprint) updates `latest_assessment_id` and records
  an `assessment_updated` event. It does not change the state. Every decision event records
  the assessment it was based on, so the UI can show "newer assessment available" when they
  differ.
- Re-running an assessment with identical inputs returns the stored one and changes nothing.
- The migration creates reviews for assessments that existed before Phase 2, without queuing
  notifications.

### 3.4 Integrity

- Each change runs in one transaction that locks the review row, checks `expected_version`
  (stale → `409`), updates the state and version, and appends an event.
- Events are append-only. A database trigger rejects updates and deletes on `review_events`,
  and its foreign key is `ON DELETE RESTRICT`, so cascades cannot remove history either.
- Invalid transitions → `409`. Missing reason, confirmation, or a past date → `422`.
- Actor is `operator` for decisions and `system` for assessment-driven events, until
  per-user accounts exist.

## 4. Notifications

| Event | Sent when | Message (default, no personal data) |
| --- | --- | --- |
| `review.ready` | A review is created by a new assessment | "A role is ready for review." |
| `review.postponed_due` | A postponed review reaches `postpone_until` and is still postponed | "A postponed role is ready for another look." |
| `integration.test` | The candidate sends a test from Settings | "Resume review notifications are connected." |

- Messages contain no employer, title, description, score, or candidate data. They link to
  the review in the app (`NEXT_ROLE_PUBLIC_BASE_URL`), which requires login.
- Routine decisions made by the operator are not notified.
- Rows go to `notification_outbox` with a deterministic, unique `event_key`, in the same
  transaction as the event that caused them. The worker publishes them over HTTPS with a
  server-held bearer token, retries timeouts, 429 and 5xx with exponential backoff and
  jitter, and marks rows `failed` after the attempt budget, or immediately on other 4xx.
- Delivery is at least once. A 2xx means ntfy accepted the message, not that a device showed
  it.
- Postponed reminders are computed from PostgreSQL by the worker. Rescheduling changes the
  `event_key`, so the old reminder never fires for the new date.

## 5. API (summary)

All routes are under `/api/v1` and require authentication, except `POST /auth/login` (which
creates a session) and `GET /auth/session` (which reports whether one exists). Exact schemas
are in OpenAPI.

| Route | Purpose |
| --- | --- |
| `GET /reviews/summary` | Counts by state, follow-ups due |
| `GET /reviews` | Paginated queue: filters (state, tier, company, search), server-side sort |
| `GET /reviews/{review_id}` | Review, job, latest assessment, and the assessment the last decision used |
| `POST /reviews/{review_id}/decisions` | Apply an action with `expected_version` |
| `GET /reviews/{review_id}/events` | Timeline for one review |
| `GET /activity` | Cross-review timeline |
| `GET`/`PATCH /notification-preferences` | Enable or disable each event type |
| `GET /integrations/ntfy/status` | Configured, last accepted publish, pending/failed counts, last redacted error |
| `POST /integrations/ntfy/test` | Queue a test notification (rate-limited) |
| `POST /auth/login` | Exchange the operator password for a session cookie and CSRF token (no auth; rate-limited) |
| `GET /auth/session` | Whether the caller is signed in, and the CSRF token (no auth) |
| `POST /auth/logout` | End the current session |

## 6. Web workspace

Visual direction from the reference design: light-grey canvas, rounded near-white app frame,
narrow icon rail, pill tabs (Review Queue, Decisions, Activity, Settings), white cards, and
coloured status pills that always include text. Accent tokens: blue `#397CF4` (primary),
violet `#8D65F7` (pending), green `#20A979` (approved/shortlisted), amber `#D59A21`
(postponed), muted red `#C75B67` (rejected), each checked for contrast.

- The queue is a table on wide screens and stacked cards on narrow ones. There is no
  drag-and-drop for decisions.
- The workspace is scoped to one candidate at a time. With more than one candidate profile,
  a "Candidate" selector in the header scopes the queue, counts, decisions, and activity, and
  the detail page names the candidate, so reviews of the same job are never mixed up.
- The queue and each Decisions tab are paginated.
- Signing out leaves the workspace only after the server has revoked the session; a failure
  is shown and the user stays signed in.
- Details show the stored Phase 1 score, tier, coverage, assessment date, matched
  requirements with evidence, gaps, unverified and unknown items, and the disclaimer. Missing
  evidence reads "Evidence unavailable".
- The AI tier and the candidate's decision are labelled separately.
- Approve and reject open a confirmation dialog. Overrides ask for a reason; postpone asks
  for a date.
- Loading, empty, and error states; keyboard navigation; visible focus; reduced motion.

## 7. Acceptance criteria

- [ ] An assessed opportunity appears in the protected queue with its stored score, date,
      strengths, gaps, and evidence references.
- [ ] The candidate can shortlist, reject, postpone to a date, and approve for drafting; no
      documents or applications are produced.
- [ ] Every change is persisted in an append-only history; invalid and stale changes are
      rejected.
- [ ] The first assessment of a candidate and opportunity creates the review and one
      `review.ready` outbox row; later assessments only record `assessment_updated`. A
      postponed review produces one reminder for its current date.
- [ ] Notifications reach the configured ntfy server with server-held credentials and link
      back to the app.
- [ ] ntfy downtime does not block review; retries and failures are visible.
- [ ] The UI follows the design direction, is responsive and keyboard accessible.
- [ ] Tests, migrations, OpenAPI, environment examples, ntfy guide, and runbook are included.
