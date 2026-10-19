# Phase 2 implementation progress

Working log for Phase 2 (Review and Approval Workspace). It exists so that another session
can resume the work without repeating it. The specification is
[features/review-workflow.md](../features/review-workflow.md).

## Pull requests

| # | Branch | Scope | Status |
| - | ------ | ----- | ------ |
| 1 | `docs/phase-2-alignment` | Roadmap, ADR 0004, ADR 0005, specification, this log | PR open |
| 2 | `feat/review-workflow-core` | Review states, decision events, outbox, backfill | PR open |
| 3 | `feat/review-api` | Queue, details, decisions, activity, preferences routes | not started |
| 4 | `feat/ntfy-worker` | Publisher, dispatcher, reminders, test notification, Compose worker | not started |
| 5 | `feat/auth-sessions` | Password login, cookie sessions, CSRF | not started |
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

## Testing log

- PR 2: 149 tests pass (54 workflow unit tests over the full transition matrix,
  confirmations, reasons, and postpone dates; 12 integration tests for review creation,
  re-assessment, stale versions, a concurrent decision blocked on the row lock, per-candidate
  reviews, disabled preferences, the append-only trigger, and the backfill).

## Known limitations / open issues

- None yet.
