// Same-origin API client. The session cookie is HttpOnly; the CSRF token is kept in memory
// and sent on every state-changing request.

import type {
  ActivityItem,
  CandidateSummary,
  DecisionInput,
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
  ntfyTest: () => request<{ outbox_id: string; status: string }>("POST", "/integrations/ntfy/test"),
};
