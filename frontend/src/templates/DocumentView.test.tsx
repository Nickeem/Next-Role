import { render, screen } from "@testing-library/react";

import { DocumentView } from "./DocumentView";
import type { DesignTokens, DisplayDocument } from "./types";

const tokens: DesignTokens = {
  template_id: "modern",
  template_version: "1",
  font_css: "Arial, sans-serif",
  body_pt: 10.5,
  small_pt: 10,
  name_pt: 22,
  heading_pt: 12,
  line_height: 1.35,
  section_gap_pt: 13,
  item_gap_pt: 7,
  margin_in: 0.75,
  page: { size: "letter", width_in: 8.5, height_in: 11 },
  accent: "#0f766e",
  text: "#111827",
  muted: "#4b5563",
  heading_style: "bar-accent",
  accent_name: true,
  target_resume_pages: 2,
  target_letter_pages: 1,
};

const resume: DisplayDocument = {
  kind: "resume",
  header: { name: "Alex Example", contact: ["Toronto", "alex@example.invalid"] },
  regarding: null,
  sections: [
    {
      id: "experience",
      type: "experience",
      title: "Experience",
      inline: [],
      entries: [
        {
          heading: "Backend Engineer",
          subheading: "Northwind (fictional)",
          dates: "Mar 2019 – Present",
          location: null,
          bullets: [{ claim_id: "c1", text: '<img src="x" onerror="alert(1)">Built APIs' }],
        },
      ],
    },
    {
      id: "skills",
      type: "skills",
      title: "Skills",
      entries: [],
      inline: [
        { claim_id: "s1", text: "Python" },
        { claim_id: "s2", text: "PostgreSQL" },
      ],
    },
  ],
  salutation: null,
  paragraphs: [],
  closing: null,
};

test("renders the display model as text with template classes and tokens", () => {
  const { container } = render(<DocumentView document={resume} tokens={tokens} />);
  const article = container.querySelector("article");
  expect(article).toHaveClass("nr-modern", "nr-h-bar-accent", "nr-accent-name", "nr-preview");
  expect(article?.style.getPropertyValue("--nr-accent")).toBe("#0f766e");
  expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Alex Example");
  expect(screen.getByText("Toronto")).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Experience" })).toBeInTheDocument();
  expect(screen.getByText("Mar 2019 – Present")).toBeInTheDocument();
  // Text that looks like markup stays text.
  expect(container.querySelector("img")).toBeNull();
  expect(screen.getByText(/<img src="x"/)).toBeInTheDocument();
  expect(container.querySelector('[data-claim="s2"]')).toHaveTextContent("· PostgreSQL");
});

test("renders a cover letter with its closing and signature", () => {
  const letter: DisplayDocument = {
    ...resume,
    kind: "cover_letter",
    sections: [],
    regarding: "Application for Engineer, Acme",
    salutation: "Dear Hiring Team,",
    paragraphs: [[{ claim_id: "p1", text: "I am applying." }, { claim_id: "p2", text: "Thanks." }]],
    closing: "Sincerely,",
  };
  render(<DocumentView document={letter} tokens={tokens} mode="print" />);
  expect(screen.getByText("Application for Engineer, Acme")).toBeInTheDocument();
  expect(screen.getByText("Sincerely,")).toBeInTheDocument();
  expect(screen.getAllByText("Alex Example")).toHaveLength(2);
});

test.each(["clean", "modern-deedy", "modern"] as const)(
  "the %s template renders the headline and entry details",
  (template_id) => {
    const withDetails: DisplayDocument = {
      ...resume,
      header: { ...resume.header, headline: "Backend engineer" },
      sections: [
        {
          id: "certifications",
          type: "certifications",
          title: "Certifications",
          inline: [],
          entries: [
            {
              heading: "AWS Certified Solutions Architect",
              subheading: null,
              dates: "Jan 2024",
              location: null,
              details: ["Credential ID ABC-123", "https://example.invalid/verify"],
              bullets: [],
            },
          ],
        },
      ],
    };
    const { container } = render(
      <DocumentView document={withDetails} tokens={{ ...tokens, template_id }} />,
    );
    expect(container.querySelector(".nr-headline")).toHaveTextContent("Backend engineer");
    expect(container.querySelector(".nr-details")).toHaveTextContent(
      "Credential ID ABC-123 · https://example.invalid/verify",
    );
  },
);
