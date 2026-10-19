// Mirrors the API contracts in src/next_role/*/schemas.py. Keep in sync with OpenAPI.

export type ReviewStatus =
  | "pending_review"
  | "shortlisted"
  | "postponed"
  | "rejected"
  | "approved_for_drafting";

export type ReviewAction =
  | "shortlist"
  | "postpone"
  | "reject"
  | "approve_for_drafting"
  | "return_to_review"
  | "reopen";

export type ReviewEventType = "created" | "assessment_updated" | ReviewAction;

export type Tier = "strong" | "moderate" | "weak" | "insufficient_data";

export type MatchStatus =
  | "met"
  | "partially_met"
  | "not_met"
  | "unverified"
  | "no_evidence"
  | "unknown";

export type ReviewSort = "updated" | "score" | "assessed" | "created";

export interface AssessmentSummary {
  id: string;
  candidate_id: string;
  job_id: string;
  score: number | null;
  tier: Tier;
  has_blocking_gaps: boolean;
  method_version: string;
  as_of: string;
  created_at: string;
}

export interface ReviewListItem {
  id: string;
  candidate_id: string;
  job_id: string;
  status: ReviewStatus;
  postpone_until: string | null;
  follow_up_due: boolean;
  version: number;
  created_at: string;
  updated_at: string;
  job: {
    title: string | null;
    company: string | null;
    location: string | null;
    work_arrangement: string;
    status: string;
  };
  assessment: AssessmentSummary;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface ReviewSummary {
  counts: Record<ReviewStatus, number>;
  follow_ups_due: number;
  total: number;
}

export interface Review {
  id: string;
  candidate_id: string;
  job_id: string;
  status: ReviewStatus;
  postpone_until: string | null;
  latest_assessment_id: string;
  version: number;
  allowed_actions: ReviewAction[];
  created_at: string;
  updated_at: string;
}

export interface Evidence {
  fact_id: string | null;
  fact_type: string;
  title: string;
  support: string;
  reason: string;
}

export interface RequirementResult {
  requirement_id: string;
  text: string;
  category: string;
  importance: string;
  status: MatchStatus;
  weight: number;
  credit: number;
  explanation: string;
  evidence: Evidence[];
}

export interface Assessment {
  id: string;
  candidate_id: string;
  job_id: string;
  method: string;
  method_version: string;
  as_of: string;
  score: number | null;
  score_excluding_unknowns: number | null;
  coverage: number;
  tier: Tier;
  has_blocking_gaps: boolean;
  status_counts: Record<string, number>;
  preference_checks: { name: string; status: string; explanation: string }[];
  summary: {
    matched: string[];
    partially_matched: string[];
    gaps: string[];
    unverified: string[];
    unknowns: string[];
  };
  requirements: RequirementResult[];
  disclaimer: string;
  created_at: string;
}

export interface Job {
  id: string;
  title: string | null;
  company: string | null;
  location: string | null;
  work_arrangement: string;
  employment_type: string;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string | null;
  description: string;
  source: string;
  source_url: string | null;
  status: string;
  created_at: string;
}

export interface ReviewDetail {
  review: Review;
  job: Job;
  assessment: Assessment;
  decision_assessment_id: string | null;
  newer_assessment_available: boolean;
}

export interface ReviewEvent {
  id: string;
  review_id: string;
  event_type: ReviewEventType;
  previous_status: ReviewStatus | null;
  new_status: ReviewStatus;
  assessment_id: string;
  reason: string | null;
  postpone_until: string | null;
  actor: string;
  created_at: string;
}

export interface ActivityItem extends ReviewEvent {
  candidate_id: string;
  job_id: string;
  job_title: string | null;
  company: string | null;
}

export interface DecisionInput {
  action: ReviewAction;
  expected_version: number;
  reason?: string;
  confirm?: boolean;
  postpone_until?: string;
}

export interface NotificationPreferences {
  review_ready: boolean;
  review_postponed_due: boolean;
  updated_at: string | null;
}

export interface NtfyStatus {
  enabled: boolean;
  server: string | null;
  click_links: boolean;
  counts: Record<"pending" | "sent" | "failed" | "cancelled", number>;
  last_accepted_at: string | null;
  last_error: string | null;
  last_error_at: string | null;
}

export interface SessionInfo {
  authenticated: boolean;
  method: string | null;
  csrf_token: string | null;
  expires_at: string | null;
}
