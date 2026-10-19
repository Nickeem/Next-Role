import { api } from "./api";
import { mockFetch } from "./test-utils";

afterEach(() => vi.unstubAllGlobals());

test("sends the CSRF token on writes, not on reads", async () => {
  const calls = mockFetch({
    "POST /api/v1/auth/login": () => ({
      body: { authenticated: true, method: "session", csrf_token: "csrf-123", expires_at: null },
    }),
    "GET /api/v1/reviews": () => ({ body: { items: [], total: 0, limit: 50, offset: 0 } }),
    "PATCH /api/v1/notification-preferences": () => ({
      body: { review_ready: false, review_postponed_due: true, updated_at: null },
    }),
  });
  await api.login("correct horse battery staple");
  await api.reviews({ status: ["pending_review", "shortlisted"], q: "data" });
  await api.updatePreferences({ review_ready: false });

  const [, read, write] = calls;
  expect(new Headers(read?.init.headers).get("X-CSRF-Token")).toBeNull();
  expect(new Headers(write?.init.headers).get("X-CSRF-Token")).toBe("csrf-123");
  expect(read?.init.credentials).toBe("same-origin");
});

test("repeats array query parameters and surfaces API errors", async () => {
  const calls = mockFetch({
    "GET /api/v1/reviews": () => ({ status: 422, body: { error: "validation_error", detail: [{ msg: "bad tier" }] } }),
  });
  await expect(api.reviews({ tier: ["weak", "strong"] })).rejects.toThrow("bad tier");
  expect(calls[0]?.path).toBe("/api/v1/reviews");
});
