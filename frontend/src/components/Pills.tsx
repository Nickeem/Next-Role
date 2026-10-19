import { STATUS_LABEL, TIER_LABEL } from "../labels";
import type { ReviewStatus, Tier } from "../types";

export function StatusPill({ status }: { status: ReviewStatus }) {
  return (
    <span className={`pill pill--${status}`}>
      <span className="pill__dot" aria-hidden="true" />
      {STATUS_LABEL[status]}
    </span>
  );
}

/** The assessment's tier, labelled as the system's view, distinct from the decision. */
export function TierBadge({ tier, prefix = true }: { tier: Tier; prefix?: boolean }) {
  return (
    <span className={`tier tier--${tier}`}>
      {prefix && <span className="tier__prefix">AI: </span>}
      {TIER_LABEL[tier]}
    </span>
  );
}
