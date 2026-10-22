# Phase 3 release checklist

Sign off each item before calling Phase 3 complete. The acceptance criteria are in
[features/document-generation.md](../features/document-generation.md#15-acceptance-criteria).

## Automated checks

- [ ] `uv run ruff format --check . && uv run ruff check . && uv run mypy && uv run pytest`
- [ ] `cd frontend && npm ci && npm run typecheck && npm test && npm run build`
- [ ] PDF tests in the renderer image ([development.md](../development.md#pdf-rendering-tests)):
      `pytest -m renderer` passes
- [ ] `docker compose up -d --build --wait`: `db`, `api`, `worker`, and `renderer` healthy,
      `migrate` exited 0, `/health/ready` reports `0009_document_notifications`

## Candidate journey (real stack, synthetic data)

- [ ] Approving a review for drafting shows "Drafting documents…" on the review, then a link
      to the documents
- [ ] Without an LLM, the drafting note says documents were assembled from your facts; with an
      LLM but without `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA=true`, it says so too
- [ ] Every bullet matches a profile statement (no LLM), or is checked (LLM); an invented
      number or skill entered in the editor is marked "Not supported" after saving
- [ ] Unconfirmed facts are listed with "Confirm … is accurate"; confirming re-checks
- [ ] "This is true as written" adds a confirmed fact to the profile and the claim becomes
      supported
- [ ] A required question cannot be dismissed without a reason; a motivation answer is
      quoted verbatim after redrafting the letter
- [ ] All three templates, both page sizes, both densities: the preview matches the PDF;
      the DOCX opens and edits in Word or LibreOffice
- [ ] Approve stays disabled with reasons listed until everything is resolved and all four
      files are ready; approving names files without `-DRAFT`
- [ ] Any edit or design change after approval returns the bundle to "Ready for approval"
- [ ] Editing in two tabs: the second gets the "changed" message and reloads
- [ ] Reopening the review withdraws the documents; they stay readable but cannot change
- [ ] Nothing is ever submitted or sent to an employer

## Operations

- [ ] `docker compose stop renderer`, change the design, `docker compose start renderer`: the
      new files render; nothing stays "Rendering"
- [ ] `docker compose restart worker` while drafting: the run is taken over after its lease
      and completes once
- [ ] From inside the renderer, `python -c "import urllib.request as u; u.urlopen('http://example.com', timeout=5)"`
      fails
- [ ] With ntfy configured, `documents.needs_input` or `documents.ready` arrives once, with
      no personal data, linking to the documents page
- [ ] Backup and restore of `pgdata` and `artifacts` ([runbook.md](runbook.md#backup-and-restore))
      round-trips, and downloads still pass their hash check
- [ ] The erasure procedure in the runbook removes a test candidate and its documents
