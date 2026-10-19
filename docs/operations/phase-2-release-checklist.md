# Phase 2 release checklist

Sign off each item before calling Phase 2 complete. The acceptance criteria are in
[features/review-workflow.md](../features/review-workflow.md#7-acceptance-criteria).

## Automated checks

- [ ] `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest`
- [ ] `cd frontend && npm ci && npm run typecheck && npm test && npm run build`
- [ ] `cd frontend && npm audit --omit=dev` reports no vulnerabilities
- [ ] `docker compose up -d --build --wait`: `db`, `api`, and `worker` healthy, `migrate`
      exited 0, `/health/ready` reports the newest migration

## Candidate journey (real stack, synthetic data)

- [ ] `./scripts/demo.sh` completes; the review is opened and shortlisted
- [ ] Sign in with `NEXT_ROLE_UI_PASSWORD`; a wrong password shows an error
- [ ] The queue lists the assessed job with its stored score, tier, and assessment date
- [ ] The detail page shows matched requirements with evidence, gaps, unverified and unknown
      items, the disclaimer, and "Evidence unavailable" where nothing is cited
- [ ] Shortlist, postpone to a date, reject (with confirmation), approve for drafting (with
      confirmation), and reopen (with a reason) all work; history shows each one
- [ ] Approving a weak match asks for a reason
- [ ] Deciding in two tabs: the second gets the "changed" message and reloads
- [ ] With two candidate profiles, the header "Candidate" selector scopes the queue, counts,
      decisions, and activity, and the detail page names the candidate
- [ ] Sign-out with the API stopped shows an error and stays signed in
- [ ] No document is generated and nothing is sent anywhere by approval
- [ ] Restarting `docker compose restart api worker db` keeps every review and decision

## Notifications (your ntfy server)

- [ ] Configure as in [ntfy.md](ntfy.md); Settings shows "Connected to <host>"
- [ ] "Send test notification" arrives on a subscribed device
- [ ] Assessing a new job delivers "A role is ready for your review", linking to the review
      (sign-in required)
- [ ] Postponing a review a few minutes ahead delivers one reminder; rescheduling before it
      fires delivers only the new one
- [ ] With ntfy stopped, decisions still work; Settings shows the error and pending count;
      after restarting ntfy the queued notifications go out
- [ ] Messages contain no employer, title, score, or personal data

## Accessibility

- [ ] Keyboard only: skip link, navigation, filters, open a review, decide through the dialog
      (focus stays in the dialog; Escape cancels), back to the queue
- [ ] Screen reader (VoiceOver or NVDA): status pills, tier, and decision are announced as
      text; decision results are announced
- [ ] 390 px wide: no horizontal scrolling; bottom navigation; "Your decision" button
- [ ] Reduced motion: no spinner animation

## Security

- [ ] Served over HTTPS through a reverse proxy; the API port is not exposed beyond it
- [ ] `NEXT_ROLE_AUTH_DISABLED` is false; API key, UI password, and ntfy token are long and
      unique, and not committed
- [ ] The ntfy token belongs to a write-only user; anonymous reads of the topic are denied
- [ ] Browser devtools: the session cookie is HttpOnly/Secure/SameSite=Strict; no token or
      API key in the page source, bundle, or local storage
- [ ] A backup was taken and a restore tested ([runbook.md](runbook.md#backup-and-restore))
