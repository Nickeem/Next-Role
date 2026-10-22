# ADR 0006: Grounded document generation, and sending candidate data to an LLM

- Status: accepted (Phase 3)
- Date: 2026-10-22

## Context

Phase 3 drafts a tailored resume and cover letter for each job the candidate approves for
drafting (Phase 2). `AGENTS.md` forbids fabricated skills, credentials, employment,
achievements, dates, and metrics, and requires substantive generated claims to be verified
before documents are candidate-ready. ADR 0003 limited LLMs to segmenting job text and
stated that candidate data is never sent to a model. Rewriting resume bullets is impossible
under that rule. The default configuration has no LLM at all (`NEXT_ROLE_LLM_PROVIDER=none`).

## Decision

- **A new `documents` module** owns fact selection, drafting, verification, revisions,
  styles, artifacts, and document approvals. `review` keeps owning the decision to draft.
- **Trigger in the decision transaction.** `review.service.apply_decision` inserts a
  `generation_runs` row when the action is `approve_for_drafting`, keyed by the approval
  event (`INSERT … ON CONFLICT DO NOTHING`). The worker re-checks that the review is still
  approved, by the same event, for the same assessment, before it starts.
- **Typed canonical content.** The model returns JSON that validates against the
  `resume/v1` or `cover-letter/v1` schema, through `LLMProvider.complete_json()`. It never
  produces markup or files. Organization, title, dates, institution, and credential are
  copied from the cited fact by code.
- **Deterministic verification first.** Protected values, numbers with units, evidence IDs,
  and canonical skills are checked by code against the run's immutable input snapshot. An
  optional LLM entailment check can only downgrade a claim (to `needs_review` or
  `unsupported`), never upgrade one. Only `supported` claims can be approved. A claim that
  checks cannot establish (including operator edits without an LLM) is resolved by editing
  it or by confirming its text as a new candidate fact, never by a bare acknowledgement.
  Cited facts must still be active, unchanged, and confirmed at approval.
- **Fact trust.** Drafts may use `self_reported` and `user_verified` facts. A claim that
  rests only on `self_reported` facts blocks document approval until those facts are
  confirmed. At approval, cited facts must still have the content hash recorded in the
  snapshot.
- **No-LLM mode.** With no provider, a deterministic assembler selects, orders, and copies
  fact text verbatim, and the cover letter is a partial draft with questions. The same
  verification and rendering apply.
- **Candidate data and providers.** Drafting sends selected candidate facts, the job
  description, and requirements to the configured provider. Every provider needs
  `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA=true`, including Ollama: the provider type does not
  prove that its `NEXT_ROLE_LLM_BASE_URL` is self-hosted. External providers still also need
  `NEXT_ROLE_LLM_ALLOW_EXTERNAL=true`. Without the flags, generation uses the no-LLM mode and
  records why. Contact details are never sent: the header is filled in by
  code.
- **Missing evidence** becomes a clarification request. The draft omits the claim. Answers
  that are career facts become proposed facts, which need explicit confirmation.

ADR 0003 still governs matching. Its statement that candidate data never reaches an LLM is
replaced by the provider rules above.

## Alternatives considered

- **LLM-only verification.** Not reproducible, and a model judging its own output is weak
  evidence. Kept as an extra, downgrade-only layer.
- **Templates with slots and no LLM at all.** Truthful, but the bullets cannot be tailored.
  Kept as the no-LLM mode instead of the only mode.
- **Only `user_verified` facts.** Strictest, but drafts would be thin for most profiles.
  Blocking approval on self-reported claims gives the same guarantee at approval time.
- **An agent framework or multi-step tool-using agent.** Not needed: the steps are fixed and
  each one has a schema.

## Consequences

- Candidate data can now reach an LLM, but only with an explicit flag (two for external providers). The README and
  `.env.example` must say so.
- Every revision stores the prompt and policy versions it was produced with. Outputs are
  reproducible in inputs, not bit for bit.
- Verification rules are versioned (`check_version`). Changing them requires a version bump,
  like `matching.engine.METHOD_VERSION`.
- The deterministic checks only see what the facts record. A rewritten claim that stays
  within the facts' values but strengthens their meaning is caught only by the LLM check or
  the operator's review. This limitation is shown in the UI.
