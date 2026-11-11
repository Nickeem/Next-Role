# ADR 0008: A model and reasoning effort per LLM task

- Status: accepted (LLM hardening)
- Date: 2026-11-11

## Context

Four tasks call the language model: requirement extraction, resume drafting, cover-letter
drafting, and the claim (semantic) check. All of them use one model, `NEXT_ROLE_LLM_MODEL`.
The tasks need different things. Extraction is high volume and mechanical, so a fast, cheap
model is enough. Drafting is writing. The claim check decides whether a drafted claim is
supported by the candidate's facts, so it benefits from a stronger model.

Current OpenAI models are reasoning models. When the reasoning effort is anything other than
`none`, they reject `temperature`. Next-Role sends `temperature: 0` and no effort, so the
model's default effort (`medium`) applies and the temperature is rejected. The provider then
retries without it (`temperature_dropped`). That works, but it costs a failed request, and
every task pays for the same reasoning effort whatever it needs.

Since PR #21, every call goes through `LLMGateway` and is recorded per task in
`llm_call_events`. That gives one place to choose a model and effort, and one place to see
what each call sent.

## Decision

- **A model and a reasoning effort per task, stored in the database.** The new
  `llm_task_models` table has one row per task, with nullable `model` and `reasoning_effort`.
  A missing row or a null column means "use the default": `NEXT_ROLE_LLM_MODEL` for the
  model, and a per-task default for the effort (extraction `none`, resume and letter drafting
  `medium`, claim check `low`). The setting applies to the whole deployment, not to one
  operator, because the worker uses it whoever is signed in.
- **Chosen in Settings, resolved per call.** The operator picks a model and effort for each
  task on the Settings page. Gateways are still built once per process. They ask a resolver
  for the model and effort on every call, and the resolver caches the table for a few seconds,
  so the `api` and `worker` processes pick up a change without a restart.
- **A curated catalog.** A table in code lists the OpenAI model families Next-Role supports.
  Each entry records its reasoning efforts, whether Chat Completions supports strict
  `json_schema` output for it, and a tier (`fast` or `strong`) shown as a hint. Settings
  offers only models that are both in the catalog and listed by the configured key
  (`GET /v1/models`). Dated snapshots match their family.
- **Only supported efforts are sent.** If a task's effort is not one its model supports, the
  lowest effort the model supports is used, and the request params record it. `temperature:
  0` is sent only when the effort is `none`. A model outside the catalog gets today's request
  shape (`temperature: 0`, no `reasoning_effort`) and keeps the retry without temperature.
- **OpenAI only.** Selection is available only when `NEXT_ROLE_LLM_PROVIDER` is
  `openai_compatible` and the base URL host is `api.openai.com`, because the catalog
  describes OpenAI's models. Otherwise every task uses `NEXT_ROLE_LLM_MODEL` with today's
  request shape. Stored choices are then ignored but not deleted.
- **Unavailable models fall back to the default.** If OpenAI says the chosen model does not
  exist or the key cannot use it, the attempt is repeated once with `NEXT_ROLE_LLM_MODEL` and
  recorded as `model_fallback`.
- **Settings choose models, not destinations.** Base URL, API key, `allow_external` and
  `allow_candidate_data` stay in environment configuration. Nothing here changes where data
  is sent, and ADR 0006's candidate-data gate is unchanged.
- **The LLM decides nothing new.** Matching, scoring, and classification stay deterministic
  (ADR 0003). This decision chooses which model performs the existing tasks.

## Consequences

- A run resolves the model per call. If a setting changes mid-run, the resume and the cover
  letter can be drafted by different models. Each document's note names the model and effort
  that drafted it.
- Higher efforts are slower, and reasoning tokens are billed as output. The worker's health
  threshold derives from `NEXT_ROLE_LLM_TIMEOUT_SECONDS`, which may need raising.
- The catalog is maintained by hand. A new OpenAI model is not offered until it is added.
- Adding an `LLMTask` value needs a migration for the check constraints on both
  `llm_call_events` and `llm_task_models`.

## Alternatives considered

- **An environment variable per task** (`NEXT_ROLE_LLM_MODEL_EXTRACTION`, …). Simple and
  needs no table, but every change needs a restart of both processes and editing the
  deployment. It is also easy to name a model the key cannot use, with nothing to check it.
- **An unfiltered model list** (everything `GET /v1/models` returns). The listing includes
  embedding, audio, image, and deprecated models. Their efforts and structured-output support
  are unknown, so many choices would fail at the first call.
- **One reasoning effort for every task.** It fixes the temperature problem, but extraction
  would pay for reasoning it does not need, or the claim check would get too little.
