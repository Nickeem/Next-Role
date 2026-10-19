# ADR 0004: Review workflow, decision history, and a PostgreSQL notification outbox

- Status: accepted (Phase 2)
- Date: 2026-10-19

## Context

Phase 1 produces immutable suitability assessments per candidate × opportunity. Phase 2 lets
the candidate act on them: shortlist, reject, postpone, or approve for future drafting, and
be notified through their existing self-hosted ntfy server. `AGENTS.md` requires explicit
lifecycle states, decisions stored separately from processing status, an auditable history,
idempotent side effects, and no new infrastructure without a reason. ADR 0001 deferred
background workers until they were needed. Publishing notifications and firing postponed
reminders are the first work that must happen outside a request.

## Decision

- **A new `review` module** owns human decisions. `matching` keeps owning assessments, and
  scores are never changed to express a decision.
- **One review per `(candidate_id, opportunity_id)`** in `opportunity_reviews`: current
  state, `postpone_until`, `latest_assessment_id`, and an integer `version`. The states and
  allowed actions are a pure transition table, documented in
  [features/review-workflow.md](../features/review-workflow.md).
- **Append-only `review_events`**: one row per decision or system event, with the previous
  and new state, the assessment the decision was based on, the reason, actor, and request ID.
  A trigger rejects `UPDATE` on the table.
- **Concurrency:** each change locks the review row (`SELECT … FOR UPDATE`) and compares the
  client's `expected_version`. A mismatch returns `409`.
- **Reviews are created inside `run_assessment`**, in the same transaction as the first new
  assessment for the pair. Later assessments update `latest_assessment_id` without changing
  the state.
- **Transactional outbox:** notification-worthy events insert a `notification_outbox` row
  with a unique, deterministic `event_key` in the same transaction. A worker process
  (`python -m next_role.worker`, same image, separate Compose service) claims rows with
  `FOR UPDATE SKIP LOCKED`, publishes through a `NotificationPublisher` protocol, and records
  the outcome. Retries use exponential backoff with jitter and a bounded attempt budget.
- **ntfy adapter** in `integrations/`, httpx only, with a bearer token from configuration
  (`NEXT_ROLE_NTFY_*`). The token never reaches the browser, logs, or the database.
- **Postponed reminders** are found by the worker polling `opportunity_reviews`. The
  reminder's `event_key` includes `postpone_until`, so rescheduling cannot fire a stale one.

## Alternatives considered

- **Review keyed by opportunity only.** Simpler, but wrong as soon as two candidates are
  assessed against one job.
- **Storing the decision on the assessment.** Breaks assessment immutability, and a
  re-assessment would lose or duplicate the decision.
- **Publishing to ntfy from the request handler.** Couples decisions to ntfy availability
  and gives no retry or audit trail.
- **Redis, Celery, or another broker.** Not justified for one operator and a handful of
  messages a day. PostgreSQL already provides locking and durability.
- **ntfy delayed delivery for reminders.** Cannot be cancelled reliably when the candidate
  reschedules or decides early, and keeps the schedule outside our database.
- **Event sourcing the review state.** The state row plus an append-only log gives the same
  audit trail with simpler queries.

## Consequences

- The system now has two processes: `api` and `worker`. Both use the same image and
  configuration.
- Notifications are at least once. A crash between a successful publish and the commit can
  send a duplicate.
- `matching.service` depends on `review.service`. Any future caller of `run_assessment`
  (such as a discovery worker) gets review creation for free.
- Phase 3 must check the current review state and its assessment before drafting, and must
  block revocation once drafting starts.
