import { useState } from "react";

import { api, ApiError } from "../api";
import { ACTION_LABEL, formatDateTime, requiresConfirmation, requiresReason } from "../labels";
import type { DecisionInput, Review, ReviewAction, Tier } from "../types";
import { DecisionDialog } from "./DecisionDialog";
import { StatusPill, TierBadge } from "./Pills";

const ORDER: ReviewAction[] = [
  "approve_for_drafting",
  "shortlist",
  "postpone",
  "return_to_review",
  "reopen",
  "reject",
];

interface Props {
  review: Review;
  tier: Tier;
  hasBlockingGaps: boolean;
  onChanged: () => void;
}

export function DecisionPanel({ review, tier, hasBlockingGaps, onChanged }: Props) {
  const [action, setAction] = useState<ReviewAction | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const actions = ORDER.filter((a) => review.allowed_actions.includes(a));

  async function submit(decision: DecisionInput) {
    try {
      await api.decide(review.id, decision);
      setAction(null);
      setMessage(`${ACTION_LABEL[decision.action]}: saved.`);
      onChanged();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setAction(null);
        setMessage("This review changed since you opened it. It has been reloaded; check it again.");
        onChanged();
        return;
      }
      throw err;
    }
  }

  function start(next: ReviewAction) {
    setMessage(null);
    const needsDialog =
      requiresConfirmation(next) || requiresReason(next, tier, hasBlockingGaps) || next === "postpone";
    if (needsDialog) {
      setAction(next);
    } else {
      void submit({ action: next, expected_version: review.version }).catch((err: unknown) =>
        setMessage(err instanceof Error ? err.message : String(err)),
      );
    }
  }

  return (
    <section id="decision" className="card decision" aria-labelledby="decision-title">
      <h2 id="decision-title" className="card__title">
        Your decision
      </h2>
      <dl className="decision__labels">
        <div>
          <dt>Assessment</dt>
          <dd>
            <TierBadge tier={tier} />
          </dd>
        </div>
        <div>
          <dt>Your decision</dt>
          <dd>
            <StatusPill status={review.status} />
          </dd>
        </div>
        {review.status === "postponed" && review.postpone_until && (
          <div>
            <dt>Until</dt>
            <dd>{formatDateTime(review.postpone_until)}</dd>
          </div>
        )}
      </dl>
      <div className="decision__actions">
        {actions.map((a) => (
          <button
            key={a}
            type="button"
            className={`btn ${
              a === "approve_for_drafting" ? "btn--primary" : a === "reject" ? "btn--danger-outline" : "btn--secondary"
            }`}
            onClick={() => start(a)}
          >
            {ACTION_LABEL[a]}
          </button>
        ))}
      </div>
      <p className={message ? "notice" : "sr-only"} role="status">
        {message}
      </p>
      {action && (
        <DecisionDialog
          action={action}
          version={review.version}
          tier={tier}
          hasBlockingGaps={hasBlockingGaps}
          onSubmit={submit}
          onClose={() => setAction(null)}
        />
      )}
    </section>
  );
}
