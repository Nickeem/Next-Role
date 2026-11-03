# LLM provider

Next-Role runs without a language model by default (`NEXT_ROLE_LLM_PROVIDER=none`), and
everything works that way. Configuring a provider adds two optional uses:

| Use | Service | What is sent | Extra opt-in |
| --- | --- | --- | --- |
| Requirement extraction | `api` | Job description text only | `NEXT_ROLE_LLM_ALLOW_EXTERNAL=true` for `openai_compatible` |
| Document drafting and semantic checks | `worker` | The job text and selected candidate facts (never contact details) | `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA=true` for **every** provider |

The model never decides matching results or scores (ADR 0003). For extraction, it only splits
the description into requirement statements. Statements that do not appear in the text are
dropped, and on any failure extraction falls back to the rules. For drafting, every rewritten
claim is checked against your facts (see
[document-generation.md](../features/document-generation.md)).

## 1. Choose a provider

| `NEXT_ROLE_LLM_PROVIDER` | Endpoint called | Notes |
| --- | --- | --- |
| `none` (default) | — | Rule-based extraction, deterministic document assembly |
| `ollama` | `POST {base}/api/chat` | Self-hosted [Ollama](https://ollama.com). Uses a JSON-schema `format` |
| `openai_compatible` | `POST {base}/v1/chat/completions` | Any API that supports `response_format: {type: "json_schema", strict: true}`. Treated as external |

Both request `temperature: 0`. If an OpenAI-compatible model rejects that, the request is
retried once without it and the model's default is used from then on. The model must support
structured (JSON-schema) output. Small models that ignore the schema cause retries and then
fallbacks.

## 2a. Ollama (self-hosted)

On the Ollama host:

```bash
ollama pull llama3.1:8b
# Listen beyond localhost if Next-Role runs in Docker or on another machine:
OLLAMA_HOST=0.0.0.0:11434 ollama serve
```

In `.env` (never commit it):

```dotenv
NEXT_ROLE_LLM_PROVIDER=ollama
NEXT_ROLE_LLM_BASE_URL=http://host.docker.internal:11434
NEXT_ROLE_LLM_MODEL=llama3.1:8b
NEXT_ROLE_LLM_TIMEOUT_SECONDS=60
# Only if you want drafting to use the model (sends candidate facts to it):
NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA=true
```

- Inside a container, `localhost` means the container itself. Use the Ollama host's name, its
  LAN IP, or `host.docker.internal`. On Linux, `host.docker.internal` resolves only if the
  `api` and `worker` services have `extra_hosts: ["host.docker.internal:host-gateway"]` (add it
  in `docker-compose.override.yml`).
- Ollama has no authentication. Do not expose port 11434 beyond your private network.
- `NEXT_ROLE_LLM_API_KEY` is optional. If set, it is sent as `Authorization: Bearer …`, which
  helps when Ollama sits behind an authenticating reverse proxy.

## 2b. OpenAI-compatible API

```dotenv
NEXT_ROLE_LLM_PROVIDER=openai_compatible
NEXT_ROLE_LLM_BASE_URL=https://api.example.com      # without the trailing /v1
NEXT_ROLE_LLM_MODEL=<model name>
NEXT_ROLE_LLM_API_KEY=<key>
NEXT_ROLE_LLM_ALLOW_EXTERNAL=true                   # required: job text leaves the server
# Only if you also want drafting (sends candidate facts to the third party):
NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA=true
```

- The client adds `/v1/chat/completions` itself. A base URL that already ends in `/v1`
  produces `/v1/v1/...` and returns `404`.
- Self-hosted servers that expose this API (vLLM, llama.cpp server, LM Studio, LiteLLM)
  work too. They still need `NEXT_ROLE_LLM_ALLOW_EXTERNAL=true`, because Next-Role cannot tell
  where the endpoint runs.
- Check the provider's data retention terms before you enable candidate data.

## 3. Restart and check

```bash
docker compose up -d
docker compose logs api | grep "llm requirement extraction enabled"   # shows provider, external
docker compose logs worker | grep "worker started"                    # "drafting": "llm"
```

If the stack does not start, look for a settings error in the logs:

| Error | Fix |
| --- | --- |
| `NEXT_ROLE_LLM_BASE_URL and NEXT_ROLE_LLM_MODEL are required` | Set both when the provider is not `none` |
| `The openai_compatible provider may send job text to a third party; ...` | Set `NEXT_ROLE_LLM_ALLOW_EXTERNAL=true` |

Then submit a job ([api-examples.md](../api-examples.md)) and open **Settings → Language
model** in the web workspace. It shows, for each task (requirement extraction, resume
drafting, cover letter drafting, claim check), the model, how many calls succeeded, failed,
fell back to deterministic code or had their temperature dropped in the last 24 hours, and
the last problem with its time. The same data is available from the API:

```bash
curl -sS $API/api/v1/integrations/llm/status -H "X-API-Key: $KEY"
```

The response names the provider and its host only (never the base URL path or the API key),
`allow_external`, `allow_candidate_data`, and per task: `available` (false for drafting and
checks without `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA`), `counts_24h` and `counts` (since the
retention window, `NEXT_ROLE_LLM_EVENT_RETENTION_DAYS`, 30 days) by outcome, `last_error`
(message, time, model, and the request params sent: model, whether and which temperature,
response format type, timeout) and `last_success` (time, model, latency). Prompts, job text,
facts and model output are never stored or returned.

The job in the response also shows what happened:

- `"extraction_method": "llm"` with `extraction_notes` such as
  `ollama/llama3.1:8b (requirements-v1)` means the model was used.
- `"extraction_method": "rules"` with notes such as
  `LLM extraction failed (LLMError); used rules` means it fell back. See Troubleshooting.

For documents, a bundle's drafting notes say whether it was drafted by the model (for
example `ollama/llama3.1:8b (drafting-v2)`) or assembled from your facts, and why.

## Behaviour and limits

- **Extraction** tries twice when output is invalid or the request fails, then uses the rules.
  Submitting a job is never blocked by the model.
- **Drafting** retries timeouts, `429`, and `5xx` with backoff, up to
  `NEXT_ROLE_GENERATION_MAX_ATTEMPTS` (3). Output that fails the schema is retried once, then
  the affected section falls back to the deterministic assembler, and the reason is recorded.
- `NEXT_ROLE_LLM_TIMEOUT_SECONDS` (60) applies to each request. The worker health check allows
  poll + 4 × timeout when an LLM is configured, so a very large timeout also delays detection
  of a stuck worker.
- Logs record the provider name and error type, never prompts, job text, or facts.
- To turn the model off, set `NEXT_ROLE_LLM_PROVIDER=none` and restart. Existing jobs and
  documents are unchanged.

## Troubleshooting

Provider errors are reported as an HTTP status or an error type:

| Symptom | Likely cause |
| --- | --- |
| `ollama request failed: ConnectError` / `openai_compatible request failed: ConnectError` | Container cannot reach the base URL: `localhost` used from a container, Ollama bound to 127.0.0.1, missing `host-gateway` mapping, or firewall |
| `... request failed: ReadTimeout` | Model too slow (often loading it on first use, or CPU-only). Raise the timeout or use a smaller model |
| `... returned HTTP 401` / `403` | Missing or wrong `NEXT_ROLE_LLM_API_KEY` |
| `... returned HTTP 404` | Wrong base URL (for example a trailing `/v1`), or the model is not pulled (`ollama list`) |
| `... returned HTTP 400` | The endpoint or model does not support JSON-schema structured output |
| `temperature_dropped` in the status counts, or log `model rejected temperature; using its default` | Informational. The model (for example an OpenAI reasoning model) accepts only its default temperature; Next-Role stops sending `temperature: 0` until restart and output is less repeatable. Later calls show `temperature_sent: false` in their params |
| `ValidationError` in extraction notes, or sections falling back | The model returned JSON that does not match the schema. Use a more capable model |
| `LLM returned no grounded requirements` | The model paraphrased instead of copying text. Use a more capable model; rules are used meanwhile |
| Documents "assembled from your facts" although a provider is set | `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA` is not `true` (ADR 0006) |

To test connectivity from inside the stack:

```bash
# Ollama: answers 200 ("Ollama is running") on its base URL.
docker compose exec worker python -c "import os, httpx; print(httpx.get(os.environ['NEXT_ROLE_LLM_BASE_URL']).status_code)"

# OpenAI-compatible: the bare base URL may return 421 or 404 even when everything works.
# Ask for the configured model instead: 200 means the key and model name are accepted.
docker compose exec worker python -c "import os, httpx; r = httpx.get(os.environ['NEXT_ROLE_LLM_BASE_URL'] + '/v1/models/' + os.environ['NEXT_ROLE_LLM_MODEL'], headers={'Authorization': 'Bearer ' + os.environ['NEXT_ROLE_LLM_API_KEY']}); print(r.status_code, r.text[:200])"
```
