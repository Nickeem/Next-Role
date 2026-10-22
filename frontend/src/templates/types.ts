// Mirrors next_role.documents.display (the display model) and templates.resolve_tokens.
// The server builds both; templates only lay them out.

export interface DisplayText {
  claim_id: string | null;
  text: string;
}

export interface DisplayEntry {
  heading: string;
  subheading: string | null;
  dates: string | null;
  location: string | null;
  bullets: DisplayText[];
}

export interface DisplaySection {
  id: string;
  type: "summary" | "experience" | "projects" | "education" | "certifications" | "skills";
  title: string;
  entries: DisplayEntry[];
  inline: DisplayText[];
}

export interface DisplayDocument {
  kind: "resume" | "cover_letter";
  header: { name: string; contact: string[] };
  regarding: string | null;
  sections: DisplaySection[];
  salutation: string | null;
  paragraphs: DisplayText[][];
  closing: string | null;
}

export interface DesignTokens {
  template_id: "clean" | "modern-deedy" | "modern";
  template_version: string;
  font_css: string;
  body_pt: number;
  small_pt: number;
  name_pt: number;
  heading_pt: number;
  line_height: number;
  section_gap_pt: number;
  item_gap_pt: number;
  margin_in: number;
  page: { size: "a4" | "letter"; width_in: number; height_in: number };
  accent: string;
  text: string;
  muted: string;
  heading_style: "rule" | "caps-accent" | "bar-accent";
  accent_name: boolean;
  target_resume_pages: number;
  target_letter_pages: number;
}

export interface PrintPayload {
  document: DisplayDocument;
  tokens: DesignTokens;
}
