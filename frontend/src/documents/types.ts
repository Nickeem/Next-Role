// Mirrors src/next_role/documents/api_schemas.py and schemas.py. Keep in sync with OpenAPI.

import type { DesignTokens, DisplayDocument } from "../templates/types";

export type DocumentKind = "resume" | "cover_letter";
export type BundleStatus = "needs_input" | "ready_for_review" | "approved" | "withdrawn";
export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
export type ClaimStatus = "supported" | "needs_review" | "unsupported";
export type ArtifactStatus = "pending" | "rendering" | "ready" | "failed" | "superseded";
export type SectionType =
  | "summary"
  | "experience"
  | "projects"
  | "education"
  | "certifications"
  | "skills";

// --- Canonical content (what the editor changes) ---------------------------------------

export interface Claim {
  claim_id: string;
  text: string;
  evidence_ids: string[];
  connective: boolean;
}

export interface Item {
  id: string;
  source_fact_id: string;
  bullets?: Claim[];
  [field: string]: unknown;
}

export interface Section {
  id: string;
  type: SectionType;
  title: string;
  items?: Item[];
  claims?: Claim[];
  skills?: Claim[];
}

export interface ResumeContent {
  kind: "resume";
  sections: Section[];
  [field: string]: unknown;
}

export interface LetterContent {
  kind: "cover_letter";
  paragraphs: { id: string; claims: Claim[] }[];
  [field: string]: unknown;
}

export type Content = ResumeContent | LetterContent;

// --- API --------------------------------------------------------------------------------

export interface Issue {
  code: string;
  message: string;
  blocking: boolean;
}

export interface ClaimCheck {
  claim_id: string;
  path: string;
  status: ClaimStatus;
  issues: Issue[];
  // Cited fact id (or "stmt:<id>", or "about:me") -> content hash it was checked against.
  evidence: Record<string, string>;
  verdict: string | null;
}

export interface Verification {
  id: string;
  status: "passed" | "blocked";
  claims: ClaimCheck[];
  issues: Issue[];
  semantic_checked: boolean;
  created_at: string;
}

export interface RevisionSummary {
  id: string;
  kind: DocumentKind;
  seq: number;
  created_by: "system" | "llm" | "operator";
  note: string | null;
  created_at: string;
  verification_status: "passed" | "blocked" | null;
}

export interface Revision extends RevisionSummary {
  bundle_id: string;
  content: Content;
  content_hash: string;
  verification: Verification | null;
}

export interface Clarification {
  id: string;
  kind: DocumentKind;
  key: string;
  question: string;
  material: boolean;
  status: "open" | "answered" | "dismissed";
  answer: string | null;
  answer_kind: "career_fact" | "letter_statement" | null;
  proposed_fact_id: string | null;
  dismiss_reason: string | null;
}

export interface StyleSettings {
  template_id: DesignTokens["template_id"];
  page_size: "a4" | "letter";
  density: "normal" | "compact";
  accent: "slate" | "navy" | "teal" | "burgundy" | "forest";
  font: "sans" | "serif";
  section_order: SectionType[];
  hidden_sections: SectionType[];
}

export interface Style {
  id: string;
  seq: number;
  template_id: string;
  template_version: string;
  settings: StyleSettings;
  tokens: DesignTokens;
}

export interface Run {
  id: string;
  intent: "initial" | "regenerate_section" | "verify";
  parameters: Record<string, unknown>;
  status: RunStatus;
  stage: string | null;
  attempts: number;
  last_error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Artifact {
  id: string;
  revision_id: string;
  style_id: string;
  format: "pdf" | "docx";
  status: ArtifactStatus;
  sha256: string | null;
  bytes: number | null;
  page_count: number | null;
  qa: { passed: boolean; warnings: string[] } | null;
  last_error: string | null;
}

export interface Approval {
  id: string;
  resume_revision_id: string;
  cover_letter_revision_id: string;
  style_id: string;
  created_at: string;
}

export interface BundleSummary {
  id: string;
  review_id: string;
  status: BundleStatus;
  drafting_mode: "llm" | "deterministic";
  version: number;
  created_at: string;
}

export interface Bundle extends BundleSummary {
  candidate_id: string;
  job: { id: string; title: string | null; company: string | null };
  drafting_notes: string[];
  resume: Revision | null;
  cover_letter: Revision | null;
  clarifications: Clarification[];
  style: Style | null;
  runs: Run[];
  artifacts: Artifact[];
  approval: Approval | null;
  approval_current: boolean;
  preview: Partial<Record<DocumentKind, DisplayDocument>>;
}

export interface ReviewDocuments {
  runs: Run[];
  bundles: BundleSummary[];
}

export interface Preview {
  display: DisplayDocument;
  tokens: DesignTokens;
}

export interface TemplateInfo {
  id: StyleSettings["template_id"];
  version: string;
  name: string;
  description: string;
}

export interface Templates {
  templates: TemplateInfo[];
  controls: Record<"page_size" | "density" | "accent" | "font", string[]>;
  defaults: StyleSettings;
}

export interface FactRef {
  id: string;
  title: string;
  fact_type: string;
  verification_status: "self_reported" | "user_verified";
  retired_at: string | null;
}
