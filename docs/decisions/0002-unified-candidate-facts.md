# ADR 0002: Unified candidate fact table with explicit provenance

- Status: accepted (Phase 1)
- Date: 2026-10-18

## Context

Career claims come in many shapes: experience, skills, education, certifications, projects,
and achievements. Matching must treat all of them uniformly as potential evidence, and every
substantive claim needs provenance. That means distinguishing what the candidate confirmed
from what was merely entered, and linking claims to supporting documents.

## Decision

- Store every claim in **one `candidate_facts` table**. The table has common columns (title,
  organization, description, dates, canonical `skills`) and a JSONB `attributes` column
  validated by a per-type Pydantic model (`ATTRIBUTE_MODELS`). Input is validated by a
  discriminated union of per-type models (`ExperienceIn`, `SkillIn`, and so on).
- Record provenance as two independent signals:
  - `verification_status` is `self_reported` (the default) or `user_verified`, with
    `verified_at`.
  - `fact_evidence` links facts to `evidence_sources` (resume, certificate, transcript,
    portfolio, and so on). Each source is stored as a reference, not as document content.
- Derive **support** from those signals: `verified`, `evidenced`, or `unsupported`. Only
  verified and evidenced facts can satisfy a requirement.
- Editing a verified fact's content resets it to `self_reported`. Retiring a fact sets
  `retired_at` instead of deleting the row, so history and past assessment evidence stay
  intact.

## Alternatives considered

- **One table per fact type.** Stronger column typing, but matching and provenance would need
  polymorphic joins, and every new fact type would need a new table and its own evidence
  linkage.
- **A single JSON document per candidate.** Simple to import, but individual facts could not
  be referenced from assessments, evidence links, or future generated documents.

## Consequences

- Type-specific fields are validated in application code, not by database constraints.
  `test_models_match_migrated_schema` and API tests guard the contract.
- Assessments and future documents can cite individual fact IDs.
- Uploading and storing evidence documents is out of scope. `reference` is a pointer.
