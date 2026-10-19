import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { App } from "./App";
import { CandidateProvider } from "./candidates";
import { Decisions } from "./pages/Decisions";
import { listItemFixture } from "./fixtures";
import { mockFetch } from "./test-utils";
import type { CandidateSummary } from "./types";

const CANDIDATES: CandidateSummary[] = [
  { id: "cand-1", full_name: "Alex Example", headline: null },
  { id: "cand-2", full_name: "Sam Sample", headline: null },
];
const EMPTY_PAGE = { items: [], total: 0, limit: 20, offset: 0 };
const SUMMARY = {
  counts: { pending_review: 0, shortlisted: 0, postponed: 0, rejected: 0, approved_for_drafting: 0 },
  follow_ups_due: 0,
  total: 0,
};

function workspaceRoutes(logoutStatus: number) {
  return {
    "GET /api/v1/auth/session": () => ({
      body: { authenticated: true, method: "session", csrf_token: "csrf", expires_at: null },
    }),
    "GET /api/v1/candidates": () => ({ body: CANDIDATES }),
    "GET /api/v1/reviews/summary": () => ({ body: SUMMARY }),
    "GET /api/v1/reviews": () => ({ body: EMPTY_PAGE }),
    "GET /api/v1/activity": () => ({ body: EMPTY_PAGE }),
    "POST /api/v1/auth/logout": () =>
      logoutStatus === 204 ? { status: 204 } : { status: logoutStatus, body: { detail: "Server error" } },
  };
}

beforeEach(() => {
  localStorage.clear();
  window.history.pushState({}, "", "/");
});
afterEach(() => vi.unstubAllGlobals());

test("every list is scoped to the selected candidate", async () => {
  const user = userEvent.setup();
  const calls = mockFetch(workspaceRoutes(204));
  render(<App />);

  const picker = await screen.findByRole("combobox", { name: "Candidate" });
  expect(picker).toHaveValue("cand-1");
  await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/reviews")).toBe(true));
  const scoped = (id: string) =>
    calls
      .filter((c) => ["/api/v1/reviews", "/api/v1/reviews/summary", "/api/v1/activity"].includes(c.path))
      .every((c) => new URL(c.url).searchParams.get("candidate_id") === id);
  expect(scoped("cand-1")).toBe(true);

  calls.length = 0;
  await user.selectOptions(picker, "cand-2");
  await waitFor(() => expect(calls.some((c) => c.path === "/api/v1/reviews")).toBe(true));
  expect(scoped("cand-2")).toBe(true);
  expect(localStorage.getItem("next-role.candidate")).toBe("cand-2");
});

test("a failed sign-out keeps the user signed in and says so", async () => {
  const user = userEvent.setup();
  mockFetch(workspaceRoutes(500));
  render(<App />);

  const [settingsLink] = await screen.findAllByRole("link", { name: "Settings" });
  await user.click(settingsLink!);
  await user.click(await screen.findByRole("button", { name: "Sign out" }));
  expect(await screen.findByText(/still signed in/)).toHaveAttribute("role", "alert");
  expect(screen.getByRole("heading", { name: "Settings" })).toBeVisible();
  expect(screen.queryByLabelText("Password")).toBeNull();
});

test("a successful sign-out returns to the login screen", async () => {
  const user = userEvent.setup();
  mockFetch(workspaceRoutes(204));
  render(<App />);

  const [settingsLink] = await screen.findAllByRole("link", { name: "Settings" });
  await user.click(settingsLink!);
  await user.click(await screen.findByRole("button", { name: "Sign out" }));
  expect(await screen.findByLabelText("Password")).toBeVisible();
});

test("decisions are paginated beyond the first page", async () => {
  const user = userEvent.setup();
  const calls = mockFetch({
    "GET /api/v1/reviews": (url) => {
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const count = Math.min(25, 60 - offset);
      return {
        body: {
          items: Array.from({ length: count }, (_, i) => listItemFixture(`rev-${offset + i}`)),
          total: 60,
          limit: 25,
          offset,
        },
      };
    },
  });
  render(
    <CandidateProvider candidates={CANDIDATES}>
      <MemoryRouter>
        <Decisions />
      </MemoryRouter>
    </CandidateProvider>,
  );

  expect(await screen.findByText("1–25 of 60")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "Next" }));
  await user.click(await screen.findByRole("button", { name: "Next" }));
  expect(await screen.findByText("51–60 of 60")).toBeVisible();
  expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
  const last = new URL(calls[calls.length - 1]!.url).searchParams;
  expect(last.get("offset")).toBe("50");
  expect(last.get("candidate_id")).toBe("cand-1");
});
