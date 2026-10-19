import type { MatchStatus, ReviewAction, ReviewEventType, ReviewStatus, Tier } from "./types";

export const STATUS_LABEL: Record<ReviewStatus, string> = {
  pending_review: "Needs review",
  shortlisted: "Shortlisted",
  postponed: "Postponed",
  rejected: "Rejected",
  approved_for_drafting: "Approved for drafting",
};

export const TIER_LABEL: Record<Tier, string> = {
  strong: "Strong match",
  moderate: "Moderate match",
  weak: "Weak match",
  insufficient_data: "Insufficient data",
};

export const ACTION_LABEL: Record<ReviewAction, string> = {
  shortlist: "Shortlist",
  postpone: "Postpone",
  reject: "Reject",
  approve_for_drafting: "Approve for drafting",
  return_to_review: "Return to review",
  reopen: "Reopen",
};

export const EVENT_LABEL: Record<ReviewEventType, string> = {
  created: "Ready for review",
  assessment_updated: "New assessment",
  ...ACTION_LABEL,
};

export const MATCH_LABEL: Record<MatchStatus, string> = {
  met: "Met",
  partially_met: "Partially met",
  not_met: "Not met",
  unverified: "Unverified claim",
  no_evidence: "No evidence",
  unknown: "Unknown",
};

/** Mirrors review.workflow.overrides_assessment so the UI can ask for a reason up front. */
export function overridesAssessment(
  action: ReviewAction,
  tier: Tier,
  hasBlockingGaps: boolean,
): boolean {
  if (action === "shortlist" || action === "approve_for_drafting") {
    return hasBlockingGaps || tier === "weak" || tier === "insufficient_data";
  }
  if (action === "reject") return tier === "strong" && !hasBlockingGaps;
  return false;
}

export function requiresReason(action: ReviewAction, tier: Tier, gaps: boolean): boolean {
  return action === "reopen" || overridesAssessment(action, tier, gaps);
}

export function requiresConfirmation(action: ReviewAction): boolean {
  return action === "reject" || action === "approve_for_drafting";
}

const dateTime = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

export function formatDateTime(iso: string): string {
  return dateTime.format(new Date(iso));
}

/** Dates without a time (e.g. assessment as_of) are calendar dates, not instants. */
export function formatDate(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return dateOnly.format(new Date(y ?? 1970, (m ?? 1) - 1, d ?? 1));
}

export function jobLabel(title: string | null, company: string | null): string {
  return [title ?? "Untitled role", company].filter(Boolean).join(" · ");
}
