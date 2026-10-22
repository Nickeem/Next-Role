# ntfy notifications

Next-Role publishes a few notifications to **your existing self-hosted ntfy server**. It does
not run ntfy itself. The `worker` service publishes them; the API and the browser never
contact ntfy and never see the token.

| Event | When | Message |
| --- | --- | --- |
| `review.ready` | A job is assessed for the first time | "A role is ready for your review." |
| `review.postponed_due` | A postponed review reaches its date and is still postponed | "A postponed role is ready for another look." |
| `integration.test` | You press "Send test notification" (or call the API) | "Resume review notifications are connected." |

Messages never include the employer, job title, description, score, or any candidate data.
When `NEXT_ROLE_PUBLIC_BASE_URL` is set, each message links to the review in the app, which
requires login.

## 1. Prepare ntfy

Use access control so that the topic is private. Server configuration (`server.yml`), as in the
[ntfy access-control docs](https://docs.ntfy.sh/config/#access-control):

```yaml
auth-file: /var/lib/ntfy/auth.db
auth-default-access: deny-all
```

Create one user that may only **publish**, one that may only **read**, and a token for the
publisher:

```bash
ntfy user add resume-bot                              # publisher (prompts for a password)
ntfy user add phone                                   # your subscriber devices
ntfy access resume-bot resume-review write-only
ntfy access phone resume-review read-only
ntfy token add resume-bot                             # prints tk_...
```

Do not use the public `ntfy.sh` server, and do not rely on an obscure topic name as the only
protection.

## 2. Configure Next-Role

In `.env` (never commit it):

```dotenv
NEXT_ROLE_NTFY_ENABLED=true
NEXT_ROLE_NTFY_BASE_URL=https://ntfy.example.internal
NEXT_ROLE_NTFY_TOPIC=resume-review
NEXT_ROLE_NTFY_TOKEN=tk_...
NEXT_ROLE_PUBLIC_BASE_URL=https://resume.example.internal
```

- The base URL must be `https`. If ntfy is reachable only over a private network (for example
  another container on the same Docker network), set `NEXT_ROLE_NTFY_ALLOW_HTTP=true` to accept
  `http://`.
- The topic may contain only letters, digits, `-`, and `_`.
- The worker must be able to reach the ntfy URL. From a container, `localhost` is the
  container itself; use the ntfy host's name or `host.docker.internal`.

Restart the stack (`docker compose up -d`). Then check:

```bash
docker compose logs worker      # "worker started", not "ntfy is disabled"
curl -sS $API/api/v1/integrations/ntfy/status -H "X-API-Key: $KEY"
curl -sS -X POST $API/api/v1/integrations/ntfy/test -H "X-API-Key: $KEY"   # 202
```

## 3. Subscribe

In the ntfy app (Android, iOS) or the web app, add your server, log in as the read-only user
(`phone`), and subscribe to the topic. iOS instant delivery for self-hosted servers needs
`upstream-base-url: "https://ntfy.sh"` in the server config; see the
[ntfy iOS notes](https://docs.ntfy.sh/config/#ios-instant-notifications). Only a poll request
ID is forwarded upstream, not the message.

## Delivery behaviour

- Notifications are written to the `notification_outbox` table in the same transaction as the
  event that causes them. If ntfy is down, reviewing and deciding still work.
- The worker polls every `NEXT_ROLE_WORKER_POLL_SECONDS` (10). Each row is claimed with
  `FOR UPDATE SKIP LOCKED`, so running more than one worker never sends a row twice
  concurrently.
- **Retries:** timeouts, connection errors, `429`, and `5xx` are retried with exponential
  backoff (30 s doubling, capped at 1 h, with jitter) up to
  `NEXT_ROLE_NOTIFICATION_MAX_ATTEMPTS` (8). Other `4xx` responses, such as `401` or `403`
  for a wrong token or missing permission, fail immediately.
- **Expiry:** a notification still unsent after `NEXT_ROLE_NOTIFICATION_MAX_AGE_HOURS` (24) is
  cancelled rather than sent late. This also prevents a burst of old messages when you enable
  ntfy after a while.
- **Reminders:** each postponed date produces at most one reminder. If you reschedule, decide,
  or return the review before the reminder is sent, it is cancelled as "No longer relevant".
- **At least once:** if the worker stops after ntfy accepted a message but before recording it,
  the message is sent again. A `2xx` from ntfy means the server accepted it, not that a device
  displayed it.
- **Documents:** `documents.ready`, `documents.needs_input`, and `documents.failed` are
  queued once per bundle when all its current files have rendered (or a drafting run failed
  for good). They link to the documents page, carry no personal data, and are cancelled if
  the approval to draft is revoked before sending. One `documents` preference switches all
  three off.
- Turning an event type off (`PATCH /api/v1/notification-preferences`) affects events from then
  on; already-queued notifications are still sent.

## Troubleshooting

`GET /api/v1/integrations/ntfy/status` returns counts by status (`pending`, `sent`, `failed`,
`cancelled`), when ntfy last accepted a message, and the last error. Errors are redacted to an
HTTP status or an error type, for example:

| `last_error` | Likely cause |
| --- | --- |
| `ntfy returned HTTP 401` | Token wrong, revoked, or expired |
| `ntfy returned HTTP 403` | The publisher user lacks write access to the topic |
| `ntfy returned HTTP 429` | ntfy rate limit (`visitor-request-limit-*`); retried |
| `ntfy request failed: ConnectError` | Worker cannot reach the base URL (DNS, network, TLS); retried |
| `ntfy request timed out` | Server slow or unreachable; retried |

Inspect rows directly if needed:

```sql
SELECT event_type, status, attempts, last_error, next_attempt_at, sent_at
FROM notification_outbox ORDER BY created_at DESC LIMIT 20;
```

To retry failed rows after fixing the configuration:

```sql
UPDATE notification_outbox
SET status = 'pending', attempts = 0, next_attempt_at = now()
WHERE status = 'failed' AND created_at > now() - interval '24 hours';
```
