import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

import { mockFetch } from "../test-utils";
import type { ReviewListItem } from "../types";
import { ReviewQueue } from "./ReviewQueue";

const ITEM: ReviewListItem = {
  id: "rev-1",
  candidate_id: "cand-1",
  job_id: "job-1",
  status: "postponed",
  postpone_until: "2026-10-18T09:00:00Z",
  follow_up_due: true,
  version: 2,
  created_at: "2026-10-10T10:00:00Z",
  updated_at: "2026-10-11T10:00:00Z",
  job: {
    title: "Data Engineer",
    company: "Contoso (fictional)",
    location: "Remote",
    work_arrangement: "remote",
    status: "active",
  },
  assessment: {
    id: "asm-1",
    candidate_id: "cand-1",
    job_id: "job-1",
    score: 81,
    tier: "strong",
    has_blocking_gaps: false,
    method_version: "rules-v1",
    as_of: "2026-10-01",
    created_at: "2026-10-10T10:00:00Z",
  },
};

const SUMMARY = {
  counts: { pending_review: 3, shortlisted: 1, postponed: 1, rejected: 0, approved_for_drafting: 2 },
  follow_ups_due: 1,
  total: 7,
};

afterEach(() => vi.unstubAllGlobals());

test("lists reviews with stored scores, decisions, and due follow-ups", async () => {
  mockFetch({
    "GET /api/v1/reviews/summary": () => ({ body: SUMMARY }),
    "GET /api/v1/reviews": () => ({ body: { items: [ITEM], total: 1, limit: 20, offset: 0 } }),
    "GET /api/v1/activity": () => ({ body: { items: [], total: 0, limit: 3, offset: 0 } }),
  });
  render(
    <MemoryRouter>
      <ReviewQueue />
    </MemoryRouter>,
  );

  const table = await screen.findByRole("table", { name: "Roles awaiting a decision" });
  expect(table).toHaveTextContent("Data Engineer");
  expect(table).toHaveTextContent("81");
  expect(table).toHaveTextContent("Strong match");
  expect(table).toHaveTextContent("Postponed");
  expect(table).toHaveTextContent("Due since");
  expect(screen.getByRole("link", { name: "Open Data Engineer" })).toHaveAttribute("href", "/reviews/rev-1");
  expect(screen.getByRole("list", { name: "Review counts" })).toHaveTextContent("3 to review");
});

test("filters are sent to the API and an empty result explains itself", async () => {
  const user = userEvent.setup();
  const urls: URL[] = [];
  mockFetch({
    "GET /api/v1/reviews/summary": () => ({ body: SUMMARY }),
    "GET /api/v1/reviews": (url) => {
      urls.push(url);
      return { body: { items: [], total: 0, limit: 20, offset: 0 } };
    },
    "GET /api/v1/activity": () => ({ body: { items: [], total: 0, limit: 3, offset: 0 } }),
  });
  render(
    <MemoryRouter>
      <ReviewQueue />
    </MemoryRouter>,
  );

  expect(await screen.findByText("No roles to review")).toBeVisible();
  await user.selectOptions(screen.getByLabelText("Suitability"), "weak");
  await user.type(screen.getByLabelText("Search"), "contoso");
  await user.click(screen.getByRole("button", { name: "Apply" }));

  await waitFor(() => {
    const last = urls.filter((u) => u.searchParams.get("limit") === "20").at(-1);
    expect(last?.searchParams.getAll("tier")).toEqual(["weak"]);
    expect(last?.searchParams.get("q")).toBe("contoso");
    expect(last?.searchParams.getAll("status")).toEqual(["pending_review", "shortlisted", "postponed"]);
  });
});
