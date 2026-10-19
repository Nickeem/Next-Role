import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { eventsFixture, detailFixture } from "../fixtures";
import { mockFetch } from "../test-utils";
import { ReviewDetail } from "./ReviewDetail";

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={["/reviews/rev-1"]}>
      <Routes>
        <Route path="/reviews/:id" element={<ReviewDetail />} />
      </Routes>
    </MemoryRouter>,
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
  expect(dialog).toHaveTextContent("No documents are generated");
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
