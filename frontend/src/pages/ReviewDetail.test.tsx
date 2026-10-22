import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { CandidateProvider } from "../candidates";
import { eventsFixture, detailFixture } from "../fixtures";
import { mockFetch } from "../test-utils";
import type { CandidateSummary } from "../types";
import { ReviewDetail } from "./ReviewDetail";

function renderDetail(candidates: CandidateSummary[] = []) {
  return render(
    <CandidateProvider candidates={candidates}>
      <MemoryRouter initialEntries={["/reviews/rev-1"]}>
        <Routes>
          <Route path="/reviews/:id" element={<ReviewDetail />} />
        </Routes>
      </MemoryRouter>
    </CandidateProvider>,
  );
}

afterEach(() => vi.unstubAllGlobals());

test("shows the stored assessment, evidence, and separate AI and candidate labels", async () => {
  mockFetch({
    "GET /api/v1/reviews/rev-1": () => ({ body: detailFixture() }),
    "GET /api/v1/reviews/rev-1/events": () => ({ body: eventsFixture }),
  });
  renderDetail();

  expect(await screen.findByRole("heading", { name: "Senior Backend Engineer" })).toBeVisible();
  expect(screen.getByText("42")).toBeVisible();
  const decision = screen.getByRole("region", { name: "Your decision" });
  expect(within(decision).getByText("AI:")).toBeVisible();
  expect(within(decision).getByText("Weak match")).toBeVisible();
  expect(within(decision).getByText("Needs review")).toBeVisible();

  // Evidence is listed for a match, and missing evidence is labelled, never invented.
  expect(screen.getByRole("list", { name: "Evidence for: Strong Python experience" })).toHaveTextContent(
    "Backend Engineer",
  );
  expect(screen.getAllByText("Evidence unavailable")).toHaveLength(2);
  expect(screen.getByText(/not a probability of being hired/)).toBeVisible();

  // Untrusted posting content is shown as text and unsafe links are not rendered.
  expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /manual/ })).toBeNull();
});

test("approving a weak match requires confirmation and a reason", async () => {
  const user = userEvent.setup();
  const calls = mockFetch({
    "GET /api/v1/reviews/rev-1": () => ({ body: detailFixture() }),
    "GET /api/v1/reviews/rev-1/events": () => ({ body: eventsFixture }),
    "POST /api/v1/reviews/rev-1/decisions": () => ({ body: detailFixture().review }),
  });
  renderDetail();

  await user.click(await screen.findByRole("button", { name: "Approve for drafting" }));
  const dialog = screen.getByRole("dialog", { name: "Approve for drafting?" });
  expect(dialog).toHaveTextContent("Nothing is sent to the employer");
  expect(dialog).toHaveTextContent("goes against the assessment");

  await user.click(within(dialog).getByRole("button", { name: "Approve for drafting" }));
  expect(within(dialog).getByRole("alert")).toHaveTextContent("Please give a reason");
  expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);

  await user.type(within(dialog).getByLabelText(/Reason/), "Recruiter confirmed sponsorship");
  await user.click(within(dialog).getByRole("button", { name: "Approve for drafting" }));

  await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
  const post = calls.find((c) => c.method === "POST");
  expect(JSON.parse(String(post?.init.body))).toEqual({
    action: "approve_for_drafting",
    expected_version: 1,
    confirm: true,
    reason: "Recruiter confirmed sponsorship",
  });
});

test("a stale version reloads the review and explains why", async () => {
  const user = userEvent.setup();
  let version = 1;
  const calls = mockFetch({
    "GET /api/v1/reviews/rev-1": () => ({ body: detailFixture({ version }) }),
    "GET /api/v1/reviews/rev-1/events": () => ({ body: eventsFixture }),
    "POST /api/v1/reviews/rev-1/decisions": () => {
      version = 2;
      return { status: 409, body: { error: "conflict", detail: "Review has changed" } };
    },
  });
  renderDetail();

  await user.click(await screen.findByRole("button", { name: "Postpone" }));
  await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Postpone" }));

  expect(await screen.findByText(/changed since you opened it/)).toBeVisible();
  await waitFor(() =>
    expect(calls.filter((c) => c.path === "/api/v1/reviews/rev-1").length).toBeGreaterThan(1),
  );
});

test("escape closes the dialog without deciding", async () => {
  const user = userEvent.setup();
  const calls = mockFetch({
    "GET /api/v1/reviews/rev-1": () => ({ body: detailFixture() }),
    "GET /api/v1/reviews/rev-1/events": () => ({ body: eventsFixture }),
  });
  renderDetail();
  await user.click(await screen.findByRole("button", { name: "Reject" }));
  expect(screen.getByRole("dialog")).toBeVisible();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
});

test("keyboard focus stays inside the dialog", async () => {
  const user = userEvent.setup();
  mockFetch({
    "GET /api/v1/reviews/rev-1": () => ({ body: detailFixture() }),
    "GET /api/v1/reviews/rev-1/events": () => ({ body: eventsFixture }),
  });
  renderDetail();
  await user.click(await screen.findByRole("button", { name: "Reject" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByLabelText(/Reason/)).toHaveFocus();
  for (let i = 0; i < 5; i += 1) {
    await user.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  }
  await user.tab({ shift: true });
  expect(dialog).toContainElement(document.activeElement as HTMLElement);
});

test("a history load failure is shown with a retry, not hidden", async () => {
  const user = userEvent.setup();
  let fail = true;
  mockFetch({
    "GET /api/v1/reviews/rev-1": () => ({ body: detailFixture() }),
    "GET /api/v1/reviews/rev-1/events": () =>
      fail ? { status: 503, body: { detail: "Database unavailable" } } : { body: eventsFixture },
  });
  renderDetail();

  const history = await screen.findByRole("region", { name: "History" });
  expect(within(history).getByRole("alert")).toHaveTextContent("Database unavailable");
  fail = false;
  await user.click(within(history).getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(screen.queryByText("Database unavailable")).toBeNull());
  const loaded = screen.getByRole("region", { name: "History" });
  expect(within(loaded).getAllByRole("listitem").length).toBeGreaterThan(0);
});

test("with several candidates the review names whose it is", async () => {
  mockFetch({
    "GET /api/v1/reviews/rev-1": () => ({ body: detailFixture() }),
    "GET /api/v1/reviews/rev-1/events": () => ({ body: eventsFixture }),
  });
  renderDetail([
    { id: "cand-2", full_name: "Sam Sample", headline: null },
    { id: "cand-1", full_name: "Alex Example", headline: null },
  ]);
  expect(await screen.findByText("Alex Example")).toBeVisible();
});

test("an approved review links to its documents", async () => {
  const detail = detailFixture();
  detail.review.status = "approved_for_drafting";
  mockFetch({
    "GET /api/v1/reviews/rev-1": () => ({ body: detail }),
    "GET /api/v1/reviews/rev-1/events": () => ({ body: eventsFixture }),
    "GET /api/v1/reviews/rev-1/documents": () => ({
      body: {
        runs: [{ id: "run-1", intent: "initial", status: "completed", last_error: null }],
        bundles: [
          { id: "b-1", review_id: "rev-1", status: "needs_input", drafting_mode: "deterministic", version: 2, created_at: "2026-10-22T10:00:00Z" },
        ],
      },
    }),
  });
  renderDetail();
  const card = await screen.findByRole("region", { name: "Documents" });
  expect(within(card).getByRole("link", { name: "Open resume and cover letter" })).toHaveAttribute(
    "href",
    "/documents/b-1",
  );
  expect(within(card).getByText("Needs your input")).toBeVisible();
});
