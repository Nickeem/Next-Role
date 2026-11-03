// Same-origin API client. The session cookie is HttpOnly; the CSRF token is kept in memory
// and sent on every state-changing request.

import type {
  Approval,
  Artifact,
  Bundle,
  Clarification,
  Content,
  DocumentKind,
  FactRef,
  Preview,
  ReviewDocuments,
  Revision,
  RevisionSummary,
  Run,
  Style,
  StyleSettings,
  Templates,
} from "./documents/types";
import type {
  ActivityItem,
  CandidateSummary,
  DecisionInput,
  LLMStatus,
  NotificationPreferences,
  NtfyStatus,
  Page,
  Review,
  ReviewDetail,
  ReviewEvent,
  ReviewListItem,
  ReviewSummary,
  SessionInfo,
} from "./types";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

let csrfToken: string | null = null;
let onUnauthorized: () => void = () => {};

export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler;
}

type Query = Record<string, string | number | boolean | string[] | undefined | null>;

function buildUrl(path: string, query?: Query): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    for (const v of Array.isArray(value) ? value : [value]) params.append(key, String(v));
  }
  const qs = params.toString();
  return `/api/v1${path}${qs ? `?${qs}` : ""}`;
}

function messageFrom(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String(d.msg) : String(d)))
        .join("; ");
    }
  }
  return `Request failed (HTTP ${status})`;
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(buildUrl(path, query), {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/")) onUnauthorized();
    const code =
      data && typeof data === "object" && "error" in data ? String(data.error) : "error";
    throw new ApiError(response.status, code, messageFrom(data, response.status));
  }
  return data as T;
}

export interface ReviewQuery {
  candidate_id?: string;
  status?: string[];
  tier?: string[];
  q?: string;
  due?: boolean;
  sort?: string;
  limit?: number;
  offset?: number;
}

export const api = {
  async session(): Promise<SessionInfo> {
    const info = await request<SessionInfo>("GET", "/auth/session");
    csrfToken = info.csrf_token;
    return info;
  },
  async login(password: string): Promise<SessionInfo> {
    const info = await request<SessionInfo>("POST", "/auth/login", { password });
    csrfToken = info.csrf_token;
    return info;
  },
  async logout(): Promise<void> {
    await request<void>("POST", "/auth/logout");
    csrfToken = null;
  },
  candidates: () => request<CandidateSummary[]>("GET", "/candidates"),
  summary: (candidateId?: string) =>
    request<ReviewSummary>("GET", "/reviews/summary", undefined, { candidate_id: candidateId }),
  reviews: (query: ReviewQuery) =>
    request<Page<ReviewListItem>>("GET", "/reviews", undefined, { ...query }),
  review: (id: string) => request<ReviewDetail>("GET", `/reviews/${encodeURIComponent(id)}`),
  events: (id: string) =>
    request<ReviewEvent[]>("GET", `/reviews/${encodeURIComponent(id)}/events`),
  decide: (id: string, decision: DecisionInput) =>
    request<Review>("POST", `/reviews/${encodeURIComponent(id)}/decisions`, decision),
  activity: (query: {
    candidate_id?: string;
    limit?: number;
    offset?: number;
    actor?: string;
    event_type?: string[];
  }) =>
    request<Page<ActivityItem>>("GET", "/activity", undefined, { ...query }),
  preferences: () => request<NotificationPreferences>("GET", "/notification-preferences"),
  updatePreferences: (update: Partial<Omit<NotificationPreferences, "updated_at">>) =>
    request<NotificationPreferences>("PATCH", "/notification-preferences", update),
  ntfyStatus: () => request<NtfyStatus>("GET", "/integrations/ntfy/status"),
  llmStatus: () => request<LLMStatus>("GET", "/integrations/llm/status"),
  ntfyTest: () => request<{ outbox_id: string; status: string }>("POST", "/integrations/ntfy/test"),

  // Documents (Phase 3)
  reviewDocuments: (reviewId: string) =>
    request<ReviewDocuments>("GET", `/reviews/${encodeURIComponent(reviewId)}/documents`),
  bundle: (id: string) => request<Bundle>("GET", `/bundles/${encodeURIComponent(id)}`),
  revisions: (id: string) =>
    request<RevisionSummary[]>("GET", `/bundles/${encodeURIComponent(id)}/revisions`),
  revision: (id: string) => request<Revision>("GET", `/revisions/${encodeURIComponent(id)}`),
  templates: () => request<Templates>("GET", "/templates"),
  candidateFacts: (candidateId: string) =>
    request<{ facts: FactRef[] }>("GET", `/candidates/${encodeURIComponent(candidateId)}`),
  confirmFact: (candidateId: string, factId: string) =>
    request<FactRef>(
      "POST",
      `/candidates/${encodeURIComponent(candidateId)}/facts/${encodeURIComponent(factId)}/verify`,
    ),
  preview: (id: string, body: { kind: DocumentKind; content?: Content; settings?: StyleSettings }) =>
    request<Preview>("POST", `/bundles/${encodeURIComponent(id)}/preview`, body),
  saveEdit: (id: string, body: { kind: DocumentKind; content: Content; expected_version: number }) =>
    request<Revision>("POST", `/bundles/${encodeURIComponent(id)}/revisions`, body),
  regenerate: (
    id: string,
    body: { kind: DocumentKind; section: string; expected_version: number; idempotency_key: string },
  ) => request<Run>("POST", `/bundles/${encodeURIComponent(id)}/regenerations`, body),
  recheck: (id: string) => request<Run>("POST", `/bundles/${encodeURIComponent(id)}/verifications`),
  confirmClaim: (id: string, claimId: string, body: { kind: DocumentKind; expected_version: number }) =>
    request<Revision>(
      "POST",
      `/bundles/${encodeURIComponent(id)}/claims/${encodeURIComponent(claimId)}/confirm-as-fact`,
      body,
    ),
  answer: (id: string, body: { answer: string; expected_version: number }) =>
    request<Clarification>("POST", `/clarifications/${encodeURIComponent(id)}/answer`, body),
  dismiss: (id: string, body: { reason?: string; expected_version: number }) =>
    request<Clarification>("POST", `/clarifications/${encodeURIComponent(id)}/dismiss`, body),
  setStyle: (id: string, body: { settings: StyleSettings; expected_version: number }) =>
    request<Style>("POST", `/bundles/${encodeURIComponent(id)}/style`, body),
  retryArtifact: (id: string) =>
    request<Artifact>("POST", `/artifacts/${encodeURIComponent(id)}/retry`),
  approveDocuments: (
    id: string,
    body: {
      expected_version: number;
      resume_revision_id: string;
      cover_letter_revision_id: string;
      style_id: string;
      artifacts: Record<string, string>;
    },
  ) => request<Approval>("POST", `/bundles/${encodeURIComponent(id)}/approval`, body),
};

export function downloadUrl(artifactId: string): string {
  return buildUrl(`/artifacts/${encodeURIComponent(artifactId)}/download`);
}
