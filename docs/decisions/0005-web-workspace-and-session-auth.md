# ADR 0005: React SPA served by FastAPI, with cookie sessions for the browser

- Status: accepted (Phase 2)
- Date: 2026-10-19

## Context

Phase 2 adds a web workspace. Phase 1 authenticates every `/api/v1` request with one shared
`X-API-Key`. A browser application cannot hold that key without exposing it in its bundle.
Network location (LAN only) is not treated as sufficient protection for career data.

## Decision

- **Frontend:** a Vite + React + TypeScript single-page app in `frontend/`. A Node stage in
  the Dockerfile builds it, and the static files are copied into the Python image. FastAPI
  serves them at `/`, with an `index.html` fallback for client-side routes. In development,
  the Vite dev server proxies `/api` to a local API.
- **Browser authentication:** a single operator password from configuration. `POST
  /api/v1/auth/login` compares it in constant time and creates a server-side session:
  a random token, stored hashed in PostgreSQL with an expiry, and sent as an `HttpOnly`,
  `Secure`, `SameSite=Strict` cookie. Logout deletes the session. Login attempts are
  rate-limited.
- **CSRF:** cookie-authenticated requests that change state must send an `X-CSRF-Token`
  header matching the token bound to the session, in addition to `SameSite=Strict`.
- **`X-API-Key` keeps working** for scripts and the demo. A request is authenticated by either
  a valid API key or a valid session.

## Alternatives considered

- **Next.js with a server-side proxy holding the API key.** Adds a Node runtime, a second
  container, and a second authentication layer to keep consistent.
- **A trusted reverse-proxy identity header.** Only safe if such a proxy is already
  deployed, and it is easy to misconfigure into trusting a spoofed header.
- **JWTs in local storage.** Exposed to XSS, and hard to revoke.

## Consequences

- One image and one process serve both the API and the UI. The worker uses the same image.
- The Docker build needs Node in a build stage only. Python tooling and CI are unchanged,
  and frontend checks (`npm run lint`, `npm run build`, tests) are added.
- There are still no per-user accounts. Decisions are attributed to `operator`.
