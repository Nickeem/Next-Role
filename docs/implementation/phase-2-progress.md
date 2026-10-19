# Phase 2 implementation progress

Working log for Phase 2 (Review and Approval Workspace). It exists so that another session
can resume the work without repeating it. The specification is
[features/review-workflow.md](../features/review-workflow.md).

## Pull requests

| # | Branch | Scope | Status |
| - | ------ | ----- | ------ |
| 1 | `docs/phase-2-alignment` | Roadmap, ADR 0004, ADR 0005, specification, this log | PR open |
| 2 | `feat/review-workflow-core` | Review states, decision events, outbox, backfill | not started |
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

## Known limitations / open issues

- None yet.
