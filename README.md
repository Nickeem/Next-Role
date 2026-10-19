# Next-Role

A self-hosted system for evaluating job opportunities against a candidate's **verified** career
history. Later phases will generate tailored resumes and assist with applications, always under
explicit human approval (see [AGENTS.md](AGENTS.md) and [docs/roadmap.md](docs/roadmap.md)).

## What works today (Phase 1)

- **Candidate knowledge base.** Covers experience, skills, education, certifications,
  projects, achievements, and matching preferences. Every fact carries provenance: a
  verification status plus links to evidence sources. Facts can be imported as one JSON
  profile or maintained one at a time over the API.
- **Manual job ingestion.** The raw submission is preserved verbatim, and a normalized
  opportunity record is created. Details the posting does not state are stored as unknown,
  never guessed. Deduplication detects exact resubmissions and links repeated postings of the
  same job (same URL, or same company, title, and location).
- **Requirement extraction.** Requirements are extracted and classified into skill,
  experience years, education, certification, language, work authorization, or other. The
  extractor is rule-based by default. Optionally, an LLM (self-hosted Ollama or an
  OpenAI-compatible API) segments the description, with schema validation, grounding checks,
  and a fallback to the rules.
- **Explainable suitability assessments.** Each requirement gets a status (met, partially
  met, not met, unverified, no evidence, or unknown), the supporting evidence, and an
  explanation. The assessment adds a transparent weighted score, coverage, a tier,
  blocking-gap detection, and preference checks. Unsupported claims never count as matches.
  The methodology is in [docs/matching.md](docs/matching.md).
- **Persistence.** Assessments are stored as immutable, idempotent snapshots and can be
  retrieved later.

Out of scope for Phase 1: automated job discovery, notifications, document generation,
applications, and a frontend.

## Prerequisites

- Docker with Docker Compose v2. This is all you need to run the system.
- For development: Python 3.12 and [uv](https://docs.astral.sh/uv/).

## Run with Docker Compose

```bash
cp .env.example .env
# Edit .env: set POSTGRES_PASSWORD, NEXT_ROLE_API_KEY, and the matching password in
# NEXT_ROLE_DATABASE_URL (only used when running outside Docker).
docker compose up -d --build --wait     # starts db, runs migrations, starts the API and worker
curl http://localhost:8000/health/ready
```

The API listens on `http://localhost:8000`, with interactive OpenAPI docs at `/docs`. Every
`/api/v1` route requires an `X-API-Key` header matching `NEXT_ROLE_API_KEY`.

Run the end-to-end demo against the stack, using synthetic data:

```bash
./scripts/demo.sh
```

The demo creates the example candidate, submits a job, shows that a resubmission is
deduplicated, runs an assessment, prints each requirement with its status and explanation,
and retrieves the stored assessment. See [docs/api-examples.md](docs/api-examples.md) for the
individual requests.

## Configuration

All settings are environment variables prefixed with `NEXT_ROLE_`. They are documented in
[.env.example](.env.example). The defaults keep all processing local and deterministic
(`NEXT_ROLE_LLM_PROVIDER=none`).

## Documentation

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | Modules, boundaries, data model, request flow |
| [docs/matching.md](docs/matching.md) | Matching rules, statuses, scoring, limitations |
| [docs/api-examples.md](docs/api-examples.md) | Request/response examples for the workflow |
| [docs/development.md](docs/development.md) | Local development, tests, quality checks, migrations |
| [docs/roadmap.md](docs/roadmap.md) | Phases and current progress |
| [docs/decisions/](docs/decisions/) | Architecture decision records |
| [docs/implementation/phase-1-progress.md](docs/implementation/phase-1-progress.md) | Phase 1 working log |
| [docs/operations/ntfy.md](docs/operations/ntfy.md) | ntfy notifications: setup, delivery, troubleshooting |
| [docs/features/review-workflow.md](docs/features/review-workflow.md) | Phase 2 specification: review workflow, notifications, workspace |
| [docs/implementation/phase-2-progress.md](docs/implementation/phase-2-progress.md) | Phase 2 working log |

## Privacy

- Use synthetic data for examples and tests. Never commit real resumes, `.env` files, or
  database dumps.
- Candidate facts are never sent to an LLM provider. If LLM extraction is enabled, only job
  description text is sent. A third-party provider requires the explicit opt-in
  `NEXT_ROLE_LLM_ALLOW_EXTERNAL=true`.
- Logs record identifiers and counts, not candidate facts or job text.
