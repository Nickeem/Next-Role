import { render, screen, within } from "@testing-library/react";

import { mockFetch } from "../test-utils";
import type { LLMStatus, LLMTask, LLMTaskStatus } from "../types";
import { Settings } from "./Settings";

const ZERO = { ok: 0, provider_error: 0, schema_invalid: 0, temperature_dropped: 0, deterministic_fallback: 0 };

function task(name: LLMTask, extra: Partial<LLMTaskStatus> = {}): LLMTaskStatus {
  return {
    task: name,
    model: "gpt-x",
    available: true,
    counts_24h: ZERO,
    counts: ZERO,
    last_error: null,
    last_success: null,
    ...extra,
  };
}

const CONFIGURED: LLMStatus = {
  provider: "openai_compatible",
  enabled: true,
  host: "llm.example.invalid",
  allow_external: true,
  allow_candidate_data: false,
  retention_days: 30,
  tasks: [
    task("extraction", {
      counts_24h: { ...ZERO, ok: 3, schema_invalid: 1, temperature_dropped: 1, deterministic_fallback: 1 },
      last_error: { message: "ValidationError", at: "2026-11-02T10:00:00Z", model: "gpt-x", params: null },
      last_success: { at: "2026-11-02T11:00:00Z", model: "gpt-x", latency_ms: 420 },
    }),
    task("resume_drafting", { available: false }),
    task("letter_drafting", { available: false }),
    task("semantic_check", { available: false }),
  ],
};

function routes(llm: LLMStatus) {
  return {
    "GET /api/v1/integrations/llm/status": () => ({ body: llm }),
    "GET /api/v1/integrations/ntfy/status": () => ({
      body: {
        enabled: false,
        server: null,
        click_links: false,
        counts: { pending: 0, sent: 0, failed: 0, cancelled: 0 },
        last_accepted_at: null,
        last_error: null,
        last_error_at: null,
      },
    }),
    "GET /api/v1/notification-preferences": () => ({
      body: { review_ready: true, review_postponed_due: true, documents: true, updated_at: null },
    }),
  };
}

afterEach(() => vi.unstubAllGlobals());

test("the language model card shows per-task outcomes and the last problem", async () => {
  mockFetch(routes(CONFIGURED));
  render(<Settings onLogout={async () => {}} />);

  const card = (await screen.findByRole("heading", { name: "Language model" })).closest("section")!;
  const extraction = await within(card).findByText("Requirement extraction");
  const row = extraction.closest("div")!;
  expect(row).toHaveTextContent("gpt-x");
  expect(row).toHaveTextContent("24 h: 3 ok, 1 failed, 1 fell back, 1 temperature dropped");
  expect(row).toHaveTextContent("Last problem: ValidationError");
  expect(row).toHaveTextContent("in 420 ms");
  expect(within(card).getByText(/llm\.example\.invalid/)).toBeVisible();
  expect(within(card).getByText("Job text only")).toBeVisible();
  expect(
    within(card).getAllByText(/set NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA/),
  ).toHaveLength(3);
});

test("the language model card explains when no provider is configured", async () => {
  mockFetch(
    routes({
      ...CONFIGURED,
      provider: "none",
      enabled: false,
      host: null,
      allow_external: false,
      tasks: CONFIGURED.tasks.map((t) => ({ ...t, available: false, model: null })),
    }),
  );
  render(<Settings onLogout={async () => {}} />);

  const card = (await screen.findByRole("heading", { name: "Language model" })).closest("section")!;
  expect(await within(card).findByText(/No language model is configured/)).toBeVisible();
  expect(within(card).queryByText("Requirement extraction")).toBeNull();
});
