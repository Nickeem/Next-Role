# ADR 0009: Candidate model revisions: support means confirmation, and an about-me

- Status: accepted (Candidate profile)
- Date: 2026-11-13
- Amends: [ADR 0002](0002-unified-candidate-facts.md) (provenance and support),
  [ADR 0006](0006-document-generation-and-candidate-data.md) (what drafting sends and cites)

## Context

The next feature is a profile editor in the web UI, followed later by resume import. Before
building UI on the candidate model, the model needs cleaning up:

- Several fields are stored but read by nothing: the profile `headline` and `summary`,
  `preferences.desired_titles`, skill `proficiency` and `category`, experience
  `employment_type`, certification `credential_id` and `credential_url`, and project `url`.
- Matching and documents disagree on when a fact counts. Matching (ADR 0002) counts a fact
  that is `user_verified` **or** linked to any evidence source. Documents ignore evidence: a
  claim can be approved only when the facts it cites are confirmed.
- Skill-specific years come from a stated `years_experience` attribute, while total
  experience is computed from dates. Nothing reconciles the two.
- The only way to keep a fact off documents is retiring it, which also removes it from
  matching.
- Facts do not record how they were created, though they increasingly come from places other
  than the candidate's own typing: document answers, claims promoted in the document editor,
  and later resume import.

This is a single-user system: the candidate both enters and confirms every fact. The system
cannot establish that a fact is true. It can establish that the candidate reviewed a fact's
exact text. That review, confirmation, is the checkpoint that matters. An evidence source is
a title and an optional pointer that nothing fetches or checks, so linking one proves no more
than typing the fact did.

The database is in early use and is cleared when this lands, so no data is migrated in
place beyond what keeps the migration correct on any database, and no deprecated inputs are
kept.

## Decision

1. **About-me.** `Candidate.summary` becomes `about_me` (column and API), limited to 2,000
   characters. The migration stops with a clear message if a stored value is longer; it never
   truncates. The about-me is the candidate's self-description (voice, motivation, interests,
   working style). It is self-reported, never confirmed, and never used by extraction or
   matching. It joins the generation snapshot with a hash, and approval treats a changed
   about-me like an edited fact.
2. **Personal statements.** Personal statements may cite the about-me; factual claims must
   cite facts. A claim cites the about-me with the reserved evidence id `about:me` and is then
   a personal statement. Personal statements are allowed in any cover-letter paragraph and,
   at most one, in the resume summary. Each must cite the about-me, be supported by it (the
   existing semantic check, with the about-me as source text), and introduce no checkable
   detail (numbers, durations, dates, job titles, names of employers, institutions or
   credentials, skill terms) unless a cited confirmed fact contains it. A personal statement
   that fails at drafting time is removed; if the detail came from the about-me itself, the
   drafter asks a question instead. A personal statement needs no confirmed facts unless it
   cites facts. The deterministic drafting path does not use the about-me.
3. **Headline.** Shown in the resume and cover-letter headers, rendered by code like the
   name. Never sent to an LLM.
4. **Unused fields.** `desired_titles` stays as the input for Phase 4 job searches. Skill
   `category` stays for grouping skills in the profile editor. `credential_id`,
   `credential_url` and project `url` are rendered in documents as code-rendered details
   (not claims, not sent to an LLM). Skill `proficiency` and experience `employment_type` are
   removed and stripped from stored attributes.
5. **Support means confirmation.** A fact supports matching only when it is `user_verified`.
   Linked evidence stays visible as documentation but no longer changes support. Support is
   `confirmed` or `unconfirmed`. The `user_statement` evidence kind is removed (confirmation
   covers it); existing sources become `other`. Facts created from documents get no evidence
   source. Creating or updating a fact may confirm it in the same request. Evidence ids leave
   the matching fingerprint; verification status stays.
6. **Matching `rules-v2`.** The support rule above. Acceptable locations are
   `preferences.locations` when set, otherwise the home location (`Candidate.location`), which
   joins the fingerprint. Years for a skill come from confirmed, dated experience and project
   facts that list or mention the skill, merged with the same overlap handling as total
   experience; the stated `years_experience` is used only when no such fact exists, and the
   evidence says it is stated. The fingerprint uses each fact's content hash and verification
   status instead of `updated_at`, so fields that do not affect matching (origin, inclusion
   in documents) do not create new assessments.
7. **Fact origin.** `origin` is `manual` (web UI), `api`, `import` (profile import) or
   `document_question` (answers to document questions and claims promoted in the document
   editor), set on every creation path and exposed in `FactOut`. It is not part of the content
   hash or the matching fingerprint. Existing rows become `api`.
8. **Keeping a fact off documents.** `include_in_documents` (default true). Excluded facts
   still count for matching and stay in the profile. The generation snapshot leaves them, and
   facts under them, out; the drafter asks no question about a requirement only they support.
   A document citing a fact that is now excluded is flagged at approval like an edited fact.
9. **Typed fact update.** `PUT /candidates/{id}/facts/{fact_id}` takes the same typed body as
   creation, for the fact's existing type, with the same validation and the same
   confirmation-reset rule as `PATCH`. `PATCH` stays, deprecated for content edits.

## Alternatives considered

- **Drop the summary.** Simplest, but documents then have no source for the candidate's own
  voice, and the LLM is left to invent motivation or tone.
- **Use the summary as unverified claims.** Documents could cite it like a fact. That lets
  anything typed into one free-text box reach a resume as a claim, bypassing confirmation and
  the per-fact checks.
- **Count independent evidence kinds as support** (certificate, transcript, portfolio,
  profile, reference). Nothing fetches or checks a source, so in a single-user system a linked
  source proves no more than the fact's own text. It would also keep two rules for "counts".
- **Keep the current rule.** Matching would keep counting facts that documents refuse to
  cite, so an assessment could say "met" on evidence that can never appear in a document.

## Consequences

- Facts that counted only because of linked evidence stop supporting matching until they are
  confirmed. Old assessments are immutable and keep their recorded support (`verified`,
  `evidenced`, `unsupported`); new ones record `confirmed` or `unconfirmed`.
- The about-me reaches the LLM only with drafting and the semantic check, both of which run
  only under `NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA` (ADR 0006). Contact details and the headline
  stay out of every LLM call.
- `resume/v1` and `cover-letter/v1` keep their versions: `about:me` and the new display fields
  only widen what they accept, so stored documents stay readable.
- The checkable-detail rule is deliberately strict: a harmless capitalized word (a city, say)
  in a personal statement removes it unless a cited confirmed fact contains it.
- Adding fact origins or evidence kinds needs a migration: both are check constraints.
