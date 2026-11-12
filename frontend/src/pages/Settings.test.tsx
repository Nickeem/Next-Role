import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { mockFetch } from "../test-utils";
import type { LLMModels, LLMStatus, LLMTask, LLMTaskModels, LLMTaskStatus } from "../types";
import { Settings } from "./Settings";

const ZERO = {
  ok: 0,
  provider_error: 0,
  schema_invalid: 0,
  temperature_dropped: 0,
  deterministic_fallback: 0,
  model_fallback: 0,
};

function task(name: LLMTask, extra: Partial<LLMTaskStatus> = {}): LLMTaskStatus {
  return {
    task: name,
    model: "gpt-x",
    reasoning_effort: null,
    model_source: "default",
    effort_source: null,
    available: true,
    counts_24h: ZERO,
    counts: ZERO,
    last_error: null,
    last_success: null,
    last_attempt: null,
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

const UNAVAILABLE_TASK_MODELS: LLMTaskModels = {
  available: false,
  reason: "Per-task models are available only with OpenAI.",
  default_model: "gpt-x",
  tasks: [],
};

function routes(
  llm: LLMStatus,
  taskModels: LLMTaskModels = UNAVAILABLE_TASK_MODELS,
  models: LLMModels | null = null,
) {
  return {
    "GET /api/v1/integrations/llm/status": () => ({ body: llm }),
    "GET /api/v1/integrations/llm/task-models": () => ({ body: taskModels }),
    "GET /api/v1/integrations/llm/models": () => ({ body: models }),
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

// --- Model per task ---------------------------------------------------------------------

const ALL_EFFORTS = ["none", "low", "medium", "high", "xhigh", "max"] as const;

const MODELS: LLMModels = {
  available: true,
  reason: null,
  stale: false,
  fetched_at: "2026-11-11T09:00:00Z",
  models: [
    { id: "test-fast", tier: "fast", reasoning_efforts: [...ALL_EFFORTS] },
    { id: "test-strong", tier: "strong", reasoning_efforts: ["low", "medium", "high", "xhigh", "max"] },
  ],
};

const DEFAULT_EFFORT = {
  extraction: "none",
  resume_drafting: "medium",
  letter_drafting: "medium",
  semantic_check: "low",
} as const;

const TASK_MODELS: LLMTaskModels = {
  available: true,
  reason: null,
  default_model: "test-fast",
  tasks: (Object.keys(DEFAULT_EFFORT) as LLMTask[]).map((name) => ({
    task: name,
    model: null,
    reasoning_effort: null,
    effective_model: "test-fast",
    effective_reasoning_effort: DEFAULT_EFFORT[name],
    model_source: "default",
    effort_source: "default",
    default_reasoning_effort: DEFAULT_EFFORT[name],
  })),
};

const OPENAI: LLMStatus = {
  ...CONFIGURED,
  host: "api.openai.com",
  allow_candidate_data: true,
  tasks: CONFIGURED.tasks.map((t) => ({
    ...t,
    available: true,
    model: "test-fast",
    reasoning_effort: DEFAULT_EFFORT[t.task],
    last_attempt:
      t.task === "extraction"
        ? { at: "2026-11-11T09:30:00Z", outcome: "ok", model: "test-fast", reasoning_effort: "none" }
        : null,
  })),
};

async function modelForm() {
  const form = await screen.findByRole("form", { name: "Model per task" });
  const group = (task: string) => within(form).getByRole("group", { name: task });
  return { form, group };
}

function options(select: HTMLElement): string[] {
  return within(select).getAllByRole("option").map((o) => o.textContent ?? "");
}

test("each task offers the discovered models and its model's efforts", async () => {
  mockFetch(routes(OPENAI, TASK_MODELS, MODELS));
  render(<Settings onLogout={async () => {}} />);
  const { group } = await modelForm();

  const extraction = group("Requirement extraction");
  const model = within(extraction).getByLabelText("Model");
  const effort = within(extraction).getByLabelText("Reasoning effort");
  expect(options(model)).toEqual(["Default (test-fast)", "test-fast (fast)", "test-strong (strong)"]);
  expect(options(effort)).toEqual(["Default (none)", ...ALL_EFFORTS]);
  expect(extraction).toHaveTextContent("Uses test-fast, effort none");
  expect(extraction).toHaveTextContent("last call sent test-fast, effort none");
  expect(options(within(group("Resume drafting")).getByLabelText("Reasoning effort"))[0]).toBe(
    "Default (medium)",
  );
  expect(group("Claim check")).toHaveTextContent(/lowers verification quality/);
  expect(group("Resume drafting")).not.toHaveTextContent(/verification quality/);
});

test("the effort list follows the chosen model and drops an unsupported effort", async () => {
  const user = userEvent.setup();
  mockFetch(routes(OPENAI, TASK_MODELS, MODELS));
  render(<Settings onLogout={async () => {}} />);
  const { group } = await modelForm();
  const extraction = group("Requirement extraction");
  const model = within(extraction).getByLabelText("Model");
  const effort = within(extraction).getByLabelText("Reasoning effort");

  await user.selectOptions(effort, "none");
  await user.selectOptions(model, "test-strong");
  expect(options(effort)).toEqual(["Default (none)", "low", "medium", "high", "xhigh", "max"]);
  // "none" is not supported by test-strong, so the choice falls back to the default.
  expect(effort).toHaveValue("");

  await user.selectOptions(effort, "high");
  await user.selectOptions(model, "test-fast");
  expect(effort).toHaveValue("high"); // still supported
});

test("saving sends every task's choice and reloads", async () => {
  const user = userEvent.setup();
  const calls = mockFetch({
    ...routes(OPENAI, TASK_MODELS, MODELS),
    "PUT /api/v1/integrations/llm/task-models": () => ({ body: TASK_MODELS }),
  });
  render(<Settings onLogout={async () => {}} />);
  const { form, group } = await modelForm();
  await user.selectOptions(within(group("Resume drafting")).getByLabelText("Model"), "test-strong");
  await user.selectOptions(
    within(group("Resume drafting")).getByLabelText("Reasoning effort"),
    "high",
  );
  await user.click(within(form).getByRole("button", { name: "Save models" }));

  expect(await within(form).findByText(/Saved\./)).toBeVisible();
  const put = calls.find((c) => c.method === "PUT")!;
  expect(JSON.parse(String(put.init.body))).toEqual({
    tasks: {
      extraction: { model: null, reasoning_effort: null },
      resume_drafting: { model: "test-strong", reasoning_effort: "high" },
      letter_drafting: { model: null, reasoning_effort: null },
      semantic_check: { model: null, reasoning_effort: null },
    },
  });
  await waitFor(() =>
    expect(calls.filter((c) => c.path.endsWith("/task-models") && c.method === "GET")).toHaveLength(2),
  );
});

test("a rejected save shows the reason", async () => {
  const user = userEvent.setup();
  mockFetch({
    ...routes(OPENAI, TASK_MODELS, MODELS),
    "PUT /api/v1/integrations/llm/task-models": () => ({
      status: 422,
      body: { error: "invalid_input", detail: "test-strong is not a supported model" },
    }),
  });
  render(<Settings onLogout={async () => {}} />);
  const { form } = await modelForm();
  await user.click(within(form).getByRole("button", { name: "Save models" }));
  expect(await within(form).findByRole("alert")).toHaveTextContent("not a supported model");
});

test("selection explains why it is unavailable and keeps the status rows", async () => {
  mockFetch(routes(CONFIGURED));
  render(<Settings onLogout={async () => {}} />);
  const card = (await screen.findByRole("heading", { name: "Language model" })).closest("section")!;
  expect(await within(card).findByText(/available only with OpenAI/)).toBeVisible();
  expect(within(card).queryByRole("form")).toBeNull();
  expect(within(card).getByText("Requirement extraction")).toBeVisible();
});

test("a failed model listing explains itself and keeps the status rows", async () => {
  mockFetch(
    routes(OPENAI, TASK_MODELS, {
      ...MODELS,
      available: false,
      reason: "Could not list the models this API key can use (openai_compatible returned HTTP 401).",
      models: [],
      fetched_at: null,
    }),
  );
  render(<Settings onLogout={async () => {}} />);
  const card = (await screen.findByRole("heading", { name: "Language model" })).closest("section")!;
  expect(await within(card).findByText(/HTTP 401/)).toBeVisible();
  expect(within(card).queryByRole("form")).toBeNull();
  expect(within(card).getByText("Claim check")).toBeVisible();
});

test("a stale model listing is flagged", async () => {
  mockFetch(routes(OPENAI, TASK_MODELS, { ...MODELS, stale: true }));
  render(<Settings onLogout={async () => {}} />);
  const { form } = await modelForm();
  expect(within(form).getByText(/Could not refresh the model list/)).toBeVisible();
});
