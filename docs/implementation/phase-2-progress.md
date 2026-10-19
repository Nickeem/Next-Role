# Phase 2 implementation progress

Working log for Phase 2 (Review and Approval Workspace). It exists so that another session
can resume the work without repeating it. The specification is
[features/review-workflow.md](../features/review-workflow.md).

## Pull requests

| # | Branch | Scope | Status |
| - | ------ | ----- | ------ |
| 1 | `docs/phase-2-alignment` | Roadmap, ADR 0004, ADR 0005, specification, this log | PR open |
| 2 | `feat/review-workflow-core` | Review states, decision events, outbox, backfill | PR open |
| 3 | `feat/review-api` | Queue, details, decisions, activity, preferences routes | PR open |
| 4 | `feat/ntfy-worker` | Publisher, dispatcher, reminders, test notification, Compose worker | PR open |
| 5 | `feat/auth-sessions` | Password login, cookie sessions, CSRF | PR open |
| 6 | `feat/review-workspace` | React SPA | not started |
| 7 | `chore/phase-2-hardening` | Security, accessibility, runbook, release checklist | not started |

Each PR is based on the previous one until it merges.

## Decisions

- Maintainer decisions (2026-10-19): Vite + React SPA served by FastAPI with cookie sessions
  (ADR 0005); reviews keyed by `(candidate_id, opportunity_id)`; this work is Phase 2, and
  job discovery moves to Phase 4.
- Baseline before Phase 2: 83 tests pass on `main` (`02e5adf`).
- PR 2: the transition rules are a pure table in `review/workflow.py`; the service only
  loads, locks, and persists. A positive decision (shortlist, approve) overrides the
  assessment when the tier is `weak`/`insufficient_data` or there are blocking gaps; a
  rejection overrides it when the tier is `strong` without blocking gaps. Overrides and
  reopening need a reason.
- `review_events.created_at` defaults to `clock_timestamp()` so several events in one
  transaction keep their order.
- Outbox and review inserts use `INSERT … ON CONFLICT DO NOTHING`, so concurrent first
  assessments and repeated events cannot create duplicates.
- Migration `0005_review_workflow` backfills one `pending_review` review (and a `created`
  event) per pair assessed before Phase 2, and queues no notifications.
- PR 3: routes are `/reviews`, `/reviews/summary`, `/reviews/{id}`,
  `/reviews/{id}/decisions`, `/reviews/{id}/events`, `/activity`, and
  `/notification-preferences`. Job and assessment details are embedded in the review detail
  rather than duplicated as `/opportunities` routes. The queue returns `{items, total, limit,
  offset}`; search escapes `%` and `_`. ntfy status and test routes move to PR 4 with the
  adapter.
- PR 4: the worker claims one outbox row per transaction and holds the row lock during the
  bounded HTTP call (`NEXT_ROLE_NTFY_TIMEOUT_SECONDS`), instead of a lease column. A crash
  releases the lock and leaves the row pending.
- Outbox rows older than `NEXT_ROLE_NOTIFICATION_MAX_AGE_HOURS` are cancelled instead of sent
  late. Postponed reminders are re-checked at send time and cancelled if the review is no
  longer postponed to the same date. Migration `0006_outbox_cancelled` adds the status.
- 401/403 and other 4xx fail immediately; timeouts, connection errors, 429, and 5xx retry.
  Stored errors are an HTTP status or exception class only.
- The test notification is limited to one per minute (429) and needs ntfy enabled (409).
- The worker lowers the `httpx` logger to WARNING, because request logs include the topic URL.
- PR 5: `require_auth` replaces `require_api_key`. The CSRF token is random per session and
  stored as-is (it is not a credential on its own); the session token is stored as a SHA-256.
  `GET /auth/session` returns the CSRF token so the SPA can recover it after a reload.
  Unauthenticated requests now say "Not authenticated" instead of "Missing or invalid API
  key" (still 401). The login throttle is in memory per API process.

## Testing log

- PR 2: 149 tests pass (54 workflow unit tests over the full transition matrix,
  confirmations, reasons, and postpone dates; 12 integration tests for review creation,
  re-assessment, stale versions, a concurrent decision blocked on the row lock, per-candidate
  reviews, disabled preferences, the append-only trigger, and the backfill).

## Known limitations / open issues

- None yet.
- PR 3: 160 tests pass (11 API tests: queue contents and sort, filters and search,
  pagination, rejected hidden by default, detail, decision flow and history, 409/422/404
  errors, weak-tier override, summary and due follow-ups, activity filters, preferences,
  auth).
- PR 4: 190 tests pass (17 unit tests for the ntfy adapter, configuration, and backoff; 13
  integration tests for dispatch, retries, attempt budget, permanent failure, expiry,
  `SKIP LOCKED`, reminders once per date, rescheduling, leaving postponed, disabled
  reminders, status and test routes, rate limit).
- PR 4 manual check against a real ntfy v2.11.0 container with `deny-all` access, a
  write-only publisher token, and a read-only subscriber: `python -m next_role.worker --once`
  published `review.ready`; the subscriber received it with the click link and no personal
  data; anonymous and publisher-token reads returned 403. A wrong token produced `failed`
  ("HTTP 401", one attempt); with ntfy stopped, the row stayed `pending` with a backoff
  ("ConnectError"). `docker compose up --build --wait` started db, migrate, api, and worker;
  readiness reported `0006_outbox_cancelled`.
- PR 5: 200 tests pass (10 integration tests: cookie flags and hashed storage, wrong
  password and throttle, reads with the cookie, CSRF required on writes, CSRF recovery after
  reload, logout revokes a replayed cookie, expiry, API key without CSRF, a wrong API key does
  not fall back to the cookie, login unavailable without a password, short password refused).
