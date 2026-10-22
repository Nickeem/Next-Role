# Operations runbook

For a single-operator, self-hosted deployment with Docker Compose. ntfy specifics are in
[ntfy.md](ntfy.md).

## Services

| Service | Role | Health |
| --- | --- | --- |
| `db` | PostgreSQL 16, the only durable store (`pgdata` volume) | `pg_isready` |
| `migrate` | Runs `alembic upgrade head`, then exits | must exit 0 before `api`/`worker` start |
| `api` | FastAPI: web workspace at `/`, API at `/api/v1` | `GET /health/ready` (database reachable, schema revision) |
| `worker` | Document drafting and checks, postponed-review reminders, ntfy publishing | `python -m next_role.worker --check` (heartbeat, touched after each loop and each dispatched notification, is newer than max(60 s, 3 × poll, 2 × (poll + ntfy timeout)), and poll + 4 × LLM timeout when an LLM is configured) |
| `renderer` | Renders PDF (Chromium) and DOCX files, checks them, writes them to the `artifacts` volume, removes orphaned files (ADR 0007). Internal network only, read-only root filesystem | `python -m next_role.renderer --check` (heartbeat newer than max(60 s, 3 × poll, 2 × render timeout + 30 s)) |

`docker compose ps` shows all of them; `docker compose logs -f api worker renderer` shows JSON logs
with identifiers and counts only (no candidate facts, job text, or tokens).

## First deployment

1. `cp .env.example .env`, then set at least `POSTGRES_PASSWORD` (and the same password in
   `NEXT_ROLE_DATABASE_URL`), `NEXT_ROLE_API_KEY`, and `NEXT_ROLE_UI_PASSWORD` (12+
   characters). Generate secrets with
   `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Optional: configure ntfy ([ntfy.md](ntfy.md)) and `NEXT_ROLE_PUBLIC_BASE_URL`.
3. `docker compose up -d --build --wait`, then open the app and sign in.

## Exposing the app

- Put it behind a TLS-terminating reverse proxy (Caddy, Traefik, nginx) and publish only the
  proxy. The session cookie is `Secure`, so browsers send it only over HTTPS (or to
  `localhost`).
- The API port is published on all interfaces by default (`API_HOST_PORT`). If the proxy runs
  on the same host, bind it to loopback by changing the mapping to
  `"127.0.0.1:${API_HOST_PORT:-8000}:8000"`.
- Failed logins are throttled per client address. Behind a proxy every request comes from
  the proxy's address unless uvicorn trusts forwarded headers. Add
  `--proxy-headers --forwarded-allow-ips=<proxy address>` to the `api` command so the
  throttle sees real client addresses; otherwise five bad attempts from anywhere block
  sign-in for everyone for five minutes (the API key keeps working).
- Do not set `NEXT_ROLE_AUTH_DISABLED=true` on anything reachable from another machine.

## Upgrades

```bash
git pull
docker compose up -d --build --wait      # migrate runs first; api and worker restart
curl -sS http://localhost:8000/health/ready   # schema_revision is the newest migration
```

Back up first (below). Migrations are tested to round-trip, but a downgrade discards data
in tables added by later migrations.

## Backup and restore

Everything durable is in PostgreSQL (candidate facts, jobs, assessments, reviews, decision
history, documents and their versions, the notification outbox, and sessions), except the
rendered PDF and DOCX files, which are in the `artifacts` volume. Back up both together:
each ready file's SHA-256 is in the database, and a download is refused if they disagree.

```bash
# Backup (compressed, custom format)
docker compose exec -T db pg_dump -U next_role -d next_role -Fc > next-role-$(date +%F).dump

# Restore into an empty database
docker compose stop api worker
docker compose exec -T db dropdb -U next_role next_role
docker compose exec -T db createdb -U next_role next_role
docker compose exec -T db pg_restore -U next_role -d next_role --no-owner < next-role-YYYY-MM-DD.dump
docker compose up -d --wait
```

```bash
# Rendered files (the volume is named <project>_artifacts, e.g. next-role_artifacts)
docker run --rm -v next-role_artifacts:/a:ro -v "$PWD":/b alpine \
  tar czf /b/next-role-artifacts-$(date +%F).tgz -C /a .
# Restore (with the renderer stopped)
docker compose stop renderer
docker run --rm -v next-role_artifacts:/a -v "$PWD":/b alpine \
  sh -c "tar xzf /b/next-role-artifacts-YYYY-MM-DD.tgz -C /a && chown -R 10001 /a"
docker compose up -d --wait
```

A file lost after restoring only the database can be rendered again: its download reports
it unavailable, and `POST /api/v1/artifacts/{id}/retry` re-renders a ready file whose stored
copy is missing or does not match its hash (failed files can also be retried from the
documents page).

Backups contain personal data: store them encrypted and outside the repository. Test a
restore before you rely on the backups.

## Rotating secrets

| Secret | Steps | Effect |
| --- | --- | --- |
| `NEXT_ROLE_API_KEY` | Change in `.env`; `docker compose up -d api` | Scripts need the new key |
| `NEXT_ROLE_UI_PASSWORD` | Change in `.env`; `docker compose up -d api`; then sign out all sessions (below) | Existing sessions otherwise last until they expire |
| `NEXT_ROLE_NTFY_TOKEN` | `ntfy token add resume-bot`, update `.env`, `docker compose up -d worker`, then `ntfy token remove resume-bot <old>` | Failed rows (`HTTP 401`) can be re-queued ([ntfy.md](ntfy.md)) |
| `POSTGRES_PASSWORD` | `ALTER USER next_role PASSWORD '…'` in `psql`, update `.env`, `docker compose up -d` | — |

Sign out every browser session:

```bash
docker compose exec db psql -U next_role -d next_role -c "DELETE FROM operator_sessions"
```

## Common problems

| Symptom | Check |
| --- | --- |
| Sign-in says "Browser login is not configured" | `NEXT_ROLE_UI_PASSWORD` is not set for the `api` service |
| Sign-in works but every page returns to the login screen | Served over plain HTTP on a host other than `localhost`: use HTTPS, or (private networks only) `NEXT_ROLE_SESSION_COOKIE_SECURE=false` |
| "Too many failed logins" | Wait five minutes; behind a proxy, see "Exposing the app" |
| A decision returns "has changed … reload and retry" | Another tab or a new assessment updated the review; reload and decide again |
| `worker` is `unhealthy` | `docker compose logs worker`; usually the database is unreachable; the worker retries each poll |
| Notifications stop | `GET /api/v1/integrations/ntfy/status` or Settings; see [ntfy.md](ntfy.md#troubleshooting) |
| Documents never appear after approving for drafting | `docker compose logs worker`; the run's status and error are on the review page and in `GET /api/v1/reviews/{id}/documents`. `stale_authorization` means the role was re-assessed or the approval revoked: approve again |
| Documents are "assembled from your facts" although an LLM is configured | `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA` is not `true` (ADR 0006); the bundle's drafting notes say so |
| Files stay "Waiting to render" | `docker compose ps renderer` and `docker compose logs renderer`; the renderer must reach `db` on the internal `backend` network |
| A file "failed its checks" | A rendering bug: the file lost or reordered text. Logs carry counts only; retry from the documents page, and report it with the template and page size |
| A download says the file is unavailable | The file is missing or does not match its hash (restored database without the volume?): re-render it with `POST /api/v1/artifacts/{id}/retry` (see Backup and restore) |
| `/health/ready` returns 503 | Database down or migrations not applied: `docker compose logs db migrate` |

## Data handling

- Decision history (`review_events`) cannot be updated or deleted, by design (a database
  trigger), and its foreign key is `ON DELETE RESTRICT`: deleting a review, job, or candidate
  that has history fails instead of cascading. There is no API to delete candidates or jobs.
- Generated documents follow the same rule: `generation_inputs`, `document_revisions`,
  `document_styles`, and `document_approvals` are append-only (triggers), and document tables
  reference candidates, jobs and reviews with `ON DELETE RESTRICT`.
- To erase a candidate's personal data (for example on request), take a backup, then run the
  following in one transaction (tested against the schema at `0009`). Record why outside
  the database. The candidate's rendered files lose their database rows and the renderer's
  orphan sweep deletes them within about an hour and a half. Backups taken earlier still
  contain them.

  ```sql
  BEGIN;
  ALTER TABLE review_events DISABLE TRIGGER review_events_append_only;
  ALTER TABLE generation_inputs DISABLE TRIGGER generation_inputs_append_only;
  ALTER TABLE document_revisions DISABLE TRIGGER document_revisions_append_only;
  ALTER TABLE document_styles DISABLE TRIGGER document_styles_append_only;
  ALTER TABLE document_approvals DISABLE TRIGGER document_approvals_append_only;
  CREATE TEMP TABLE erase_bundles ON COMMIT DROP AS
    SELECT id FROM document_bundles WHERE candidate_id = '<candidate id>';
  DELETE FROM document_approvals WHERE bundle_id IN (SELECT id FROM erase_bundles);
  DELETE FROM document_artifacts WHERE bundle_id IN (SELECT id FROM erase_bundles);
  DELETE FROM document_verifications WHERE revision_id IN
    (SELECT id FROM document_revisions WHERE bundle_id IN (SELECT id FROM erase_bundles));
  DELETE FROM clarification_requests WHERE bundle_id IN (SELECT id FROM erase_bundles);
  DELETE FROM document_revisions WHERE bundle_id IN (SELECT id FROM erase_bundles);
  DELETE FROM document_styles WHERE bundle_id IN (SELECT id FROM erase_bundles);
  DELETE FROM generation_runs WHERE review_id IN
    (SELECT id FROM opportunity_reviews WHERE candidate_id = '<candidate id>');
  DELETE FROM document_bundles WHERE id IN (SELECT id FROM erase_bundles);
  DELETE FROM generation_inputs WHERE candidate_id = '<candidate id>';
  DELETE FROM review_events WHERE review_id IN
    (SELECT id FROM opportunity_reviews WHERE candidate_id = '<candidate id>');
  DELETE FROM candidates WHERE id = '<candidate id>';
  ALTER TABLE review_events ENABLE TRIGGER review_events_append_only;
  ALTER TABLE generation_inputs ENABLE TRIGGER generation_inputs_append_only;
  ALTER TABLE document_revisions ENABLE TRIGGER document_revisions_append_only;
  ALTER TABLE document_styles ENABLE TRIGGER document_styles_append_only;
  ALTER TABLE document_approvals ENABLE TRIGGER document_approvals_append_only;
  COMMIT;
  ```
- Rejected reviews are kept, and hidden from the queue by default.
- Outbox rows are kept for diagnostics. To prune old delivered ones:
  `DELETE FROM notification_outbox WHERE status IN ('sent', 'cancelled') AND created_at < now() - interval '90 days';`
