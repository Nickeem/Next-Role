import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import type { Bundle } from "../documents/types";
import { mockFetch } from "../test-utils";
import type { DesignTokens, DisplayDocument } from "../templates/types";
import { Documents } from "./Documents";

const tokens: DesignTokens = {
  template_id: "clean",
  template_version: "1",
  font_css: "Arial",
  body_pt: 10.5,
  small_pt: 10,
  name_pt: 20,
  heading_pt: 11.5,
  line_height: 1.35,
  section_gap_pt: 12,
  item_gap_pt: 7,
  margin_in: 0.8,
  page: { size: "letter", width_in: 8.5, height_in: 11 },
  accent: "#334155",
  text: "#111827",
  muted: "#4b5563",
  heading_style: "rule",
  accent_name: false,
  target_resume_pages: 2,
  target_letter_pages: 1,
};

const display: DisplayDocument = {
  kind: "resume",
  header: { name: "Alex Example", contact: ["alex@example.invalid"] },
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
          bullets: [{ claim_id: "exp-1-b1", text: "Built REST APIs in Python" }],
        },
      ],
    },
  ],
  salutation: null,
  paragraphs: [],
  closing: null,
};

const FACT = "00000000-0000-4000-8000-000000000001";

function bundle(overrides: Partial<Bundle> = {}): Bundle {
  const claimCheck = (status: "supported" | "needs_review", issues = [] as { code: string; message: string; blocking: boolean }[]) => ({
    claim_id: "exp-1-b1",
    path: "/sections/0/items/0/bullets/0",
    status,
    issues,
    evidence: { [FACT]: "h" },
    verdict: null,
  });
  return {
    id: "b-1",
    review_id: "rev-1",
    candidate_id: "cand-1",
    status: "needs_input",
    drafting_mode: "deterministic",
    version: 4,
    created_at: "2026-10-22T10:00:00Z",
    job: { id: "job-1", title: "Senior Backend Engineer", company: "Fabrikam (fictional)" },
    drafting_notes: ["No language model is configured"],
    resume: {
      id: "rev-r1",
      bundle_id: "b-1",
      kind: "resume",
      seq: 1,
      created_by: "system",
      note: "Drafted",
      created_at: "2026-10-22T10:00:00Z",
      verification_status: "blocked",
      content_hash: "x",
      content: {
        kind: "resume",
        sections: [
          {
            id: "experience",
            type: "experience",
            title: "Experience",
            items: [
              {
                id: "exp-1",
                source_fact_id: FACT,
                organization: "Northwind (fictional)",
                position: "Backend Engineer",
                bullets: [{ claim_id: "exp-1-b1", text: "Built REST APIs in Python", evidence_ids: [FACT], connective: false }],
              },
            ],
          },
        ],
      },
      verification: {
        id: "v1",
        status: "blocked",
        claims: [
          claimCheck("needs_review", [
            { code: "unconfirmed_fact", message: "Rests on facts you have not confirmed yet", blocking: true },
          ]),
        ],
        issues: [],
        semantic_checked: false,
        created_at: "2026-10-22T10:00:00Z",
      },
    },
    cover_letter: {
      id: "rev-l1",
      bundle_id: "b-1",
      kind: "cover_letter",
      seq: 1,
      created_by: "system",
      note: "Drafted",
      created_at: "2026-10-22T10:00:00Z",
      verification_status: "passed",
      content_hash: "y",
      content: { kind: "cover_letter", paragraphs: [] },
      verification: { id: "v2", status: "passed", claims: [], issues: [], semantic_checked: false, created_at: "2026-10-22T10:00:00Z" },
    },
    clarifications: [
      {
        id: "q-1",
        kind: "resume",
        key: "requirement:r1",
        question: "The job asks for: “Kubernetes”. Describe it if you have it.",
        material: true,
        status: "open",
        answer: null,
        answer_kind: "career_fact",
        proposed_fact_id: null,
        dismiss_reason: null,
      },
    ],
    style: {
      id: "style-1",
      seq: 1,
      template_id: "clean",
      template_version: "1",
      settings: {
        template_id: "clean",
        page_size: "letter",
        density: "normal",
        accent: "slate",
        font: "sans",
        section_order: [],
        hidden_sections: [],
      },
      tokens,
    },
    runs: [],
    artifacts: ["rev-r1", "rev-l1"].flatMap((rev, i) =>
      (["pdf", "docx"] as const).map((format, j) => ({
        id: `art-${i}${j}`,
        revision_id: rev,
        style_id: "style-1",
        format,
        status: "ready" as const,
        sha256: `hash-${i}${j}`,
        bytes: 40000,
        page_count: format === "pdf" ? 1 : null,
        qa: { passed: true, warnings: [] },
        last_error: null,
      })),
    ),
    approval: null,
    approval_current: false,
    preview: { resume: display, cover_letter: { ...display, kind: "cover_letter", sections: [] } },
    ...overrides,
  };
}

const templates = {
  templates: [
    { id: "clean", version: "1", name: "Clean", description: "Understated" },
    { id: "modern-deedy", version: "1", name: "Modern Deedy", description: "Dense" },
    { id: "modern", version: "1", name: "Modern", description: "Accent" },
  ],
  controls: { page_size: ["a4", "letter"], density: ["normal", "compact"], accent: ["slate", "teal"], font: ["sans", "serif"] },
  defaults: bundle().style!.settings,
};

function routes(current: () => Bundle, extra: Parameters<typeof mockFetch>[0] = {}) {
  return mockFetch({
    "GET /api/v1/bundles/b-1": () => ({ body: current() }),
    "GET /api/v1/bundles/b-1/revisions": () => ({ body: [current().resume] }),
    "GET /api/v1/templates": () => ({ body: templates }),
    "GET /api/v1/candidates/cand-1": () => ({
      body: { facts: [{ id: FACT, title: "Backend Engineer", fact_type: "experience", verification_status: "self_reported", retired_at: null }] },
    }),
    "POST /api/v1/bundles/b-1/preview": () => ({ body: { display, tokens } }),
    ...extra,
  });
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/documents/b-1"]}>
      <Routes>
        <Route path="/documents/:id" element={<Documents />} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test("shows the preview, what blocks approval, and how to resolve it", async () => {
  routes(() => bundle());
  renderPage();

  expect(await screen.findByRole("heading", { name: /Documents: Senior Backend Engineer/ })).toBeVisible();
  expect(screen.getByText("Needs your input")).toBeVisible();
  const preview = screen.getByLabelText("Resume preview");
  expect(within(preview).getByText("Built REST APIs in Python")).toBeVisible();

  const blockers = screen.getByRole("list", { name: "Before you can approve" });
  expect(blockers).toHaveTextContent("1 item in the resume needs attention");
  expect(blockers).toHaveTextContent("1 required question");
  expect(screen.getByRole("button", { name: "Approve documents" })).toBeDisabled();

  const checks = screen.getByRole("list", { name: "Statements to resolve in the resume" });
  expect(checks).toHaveTextContent("Rests on facts you have not confirmed yet");
  expect(await within(checks).findByRole("button", { name: /Confirm “Backend Engineer” is accurate/ })).toBeVisible();

  // Downloads are labelled as drafts until approved.
  expect(screen.getAllByRole("link", { name: /Download.*draft/ })).toHaveLength(4);
});

test("confirming a fact re-checks the documents", async () => {
  const user = userEvent.setup();
  const calls = routes(() => bundle(), {
    "POST /api/v1/candidates/cand-1/facts/00000000-0000-4000-8000-000000000001/verify": () => ({ body: {} }),
    "POST /api/v1/bundles/b-1/verifications": () => ({ status: 202, body: { id: "run-1" } }),
  });
  renderPage();
  await user.click(await screen.findByRole("button", { name: /Confirm “Backend Engineer” is accurate/ }));
  await waitFor(() =>
    expect(calls.filter((c) => c.method === "POST").map((c) => c.path)).toEqual([
      `/api/v1/candidates/cand-1/facts/${FACT}/verify`,
      "/api/v1/bundles/b-1/verifications",
    ]),
  );
  expect(await screen.findByText(/Fact confirmed/)).toBeVisible();
});

test("required questions can only be dismissed with a reason", async () => {
  const user = userEvent.setup();
  const calls = routes(() => bundle(), {
    "POST /api/v1/clarifications/q-1/dismiss": () => ({ body: { ...bundle().clarifications[0], status: "dismissed" } }),
  });
  renderPage();
  await user.click(await screen.findByRole("button", { name: "I don't have this" }));
  await user.click(screen.getByRole("button", { name: "Dismiss question" }));
  expect(screen.getByRole("alert")).toHaveTextContent("A reason is required");
  await user.type(screen.getByLabelText(/Why skip it/), "Never used it");
  await user.click(screen.getByRole("button", { name: "Dismiss question" }));
  await waitFor(() => expect(calls.some((c) => c.path.endsWith("/dismiss"))).toBe(true));
  const post = calls.find((c) => c.path.endsWith("/dismiss"));
  expect(JSON.parse(String(post?.init.body))).toEqual({ reason: "Never used it", expected_version: 4 });
});

test("edits are saved with the bundle version, and a stale version reloads", async () => {
  const user = userEvent.setup();
  let saves = 0;
  const calls = routes(() => bundle(), {
    "POST /api/v1/bundles/b-1/revisions": () => {
      saves += 1;
      return saves === 1
        ? { status: 409, body: { error: "conflict", detail: "Document bundle has changed." } }
        : { status: 201, body: bundle().resume };
    },
  });
  renderPage();
  await user.click(await screen.findByRole("button", { name: "Edit resume" }));
  const field = screen.getByLabelText(/Bullet for Backend Engineer, Northwind \(fictional\) 1/);
  await user.clear(field);
  await user.type(field, "Built and ran REST APIs in Python");
  expect(screen.getByText("Edited: checked on save")).toBeVisible();

  await user.click(screen.getByRole("button", { name: "Save edits" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("has changed. The page has been refreshed.");
  await user.click(screen.getByRole("button", { name: "Save edits" }));
  await waitFor(() => expect(saves).toBe(2));
  const body = JSON.parse(String(calls.filter((c) => c.path.endsWith("/revisions") && c.method === "POST")[1]?.init.body));
  expect(body.kind).toBe("resume");
  expect(body.expected_version).toBe(4);
  expect(body.content.sections[0].items[0].bullets[0].text).toBe("Built and ran REST APIs in Python");
});

test("approval sends the exact revisions, style, and file hashes", async () => {
  const user = userEvent.setup();
  const ready = () =>
    bundle({
      status: "ready_for_review",
      clarifications: [],
      resume: { ...bundle().resume!, verification: { ...bundle().resume!.verification!, status: "passed", claims: [] } },
    });
  const calls = routes(ready, {
    "POST /api/v1/bundles/b-1/approval": () => ({ status: 201, body: { id: "appr-1" } }),
  });
  renderPage();
  await user.click(await screen.findByRole("button", { name: "Approve documents" }));
  const dialog = screen.getByRole("dialog", { name: "Approve these documents?" });
  expect(dialog).toHaveTextContent("does not apply to the job");
  await user.click(within(dialog).getByRole("button", { name: "Approve documents" }));
  await waitFor(() => expect(calls.some((c) => c.path.endsWith("/approval"))).toBe(true));
  const body = JSON.parse(String(calls.find((c) => c.path.endsWith("/approval"))?.init.body));
  expect(body).toEqual({
    expected_version: 4,
    resume_revision_id: "rev-r1",
    cover_letter_revision_id: "rev-l1",
    style_id: "style-1",
    artifacts: { "art-00": "hash-00", "art-01": "hash-01", "art-10": "hash-10", "art-11": "hash-11" },
  });
});

test("a withdrawn bundle is read-only", async () => {
  routes(() => bundle({ status: "withdrawn" }));
  renderPage();
  expect(await screen.findByText(/approval to draft for this role was revoked/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Edit resume" })).toBeDisabled();
  expect(screen.getByRole("list", { name: "Before you can approve" })).toHaveTextContent("revoked");
});

test("design changes preview before they are applied", async () => {
  const user = userEvent.setup();
  const calls = routes(() => bundle(), {
    "POST /api/v1/bundles/b-1/style": () => ({ status: 201, body: bundle().style }),
  });
  renderPage();
  await user.click(await screen.findByRole("radio", { name: /Modern Deedy/ }));
  await waitFor(() => expect(calls.some((c) => c.path.endsWith("/preview"))).toBe(true), { timeout: 2000 });
  const previewBody = JSON.parse(String(calls.find((c) => c.path.endsWith("/preview"))?.init.body));
  expect(previewBody.settings.template_id).toBe("modern-deedy");
  await user.click(screen.getByRole("button", { name: "Apply design" }));
  await waitFor(() => expect(calls.some((c) => c.path.endsWith("/style"))).toBe(true));
  const styleBody = JSON.parse(String(calls.find((c) => c.path.endsWith("/style"))?.init.body));
  expect(styleBody).toMatchObject({ settings: { template_id: "modern-deedy" }, expected_version: 4 });
});

test("the approval dialog keeps focus inside and closes on Escape", async () => {
  const user = userEvent.setup();
  const ready = () =>
    bundle({
      status: "ready_for_review",
      clarifications: [],
      resume: { ...bundle().resume!, verification: { ...bundle().resume!.verification!, status: "passed", claims: [] } },
    });
  routes(ready);
  renderPage();
  const open = await screen.findByRole("button", { name: "Approve documents" });
  await user.click(open);
  const dialog = screen.getByRole("dialog", { name: "Approve these documents?" });
  const confirm = within(dialog).getByRole("button", { name: "Approve documents" });
  expect(confirm).toHaveFocus();
  await user.tab();
  expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
  await user.tab();
  expect(confirm).toHaveFocus();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
});
