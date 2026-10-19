import { useEffect, useId, useRef, useState, type FormEvent } from "react";

import { ApiError } from "../api";
import { ACTION_LABEL, overridesAssessment, requiresConfirmation, requiresReason } from "../labels";
import type { DecisionInput, ReviewAction, Tier } from "../types";

const EXPLANATION: Partial<Record<ReviewAction, string>> = {
  approve_for_drafting:
    "This records your approval for a later phase to draft a resume and cover letter. " +
    "No documents are generated and nothing is sent to the employer.",
  reject: "Rejected roles are hidden from the queue but kept in your history. You can reopen them.",
  reopen: "The review returns to your queue. If it was approved, the approval is revoked.",
  postpone: "You will be reminded when the date arrives, if reminders are enabled.",
};

function localDateTimeValue(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

interface Props {
  action: ReviewAction;
  version: number;
  tier: Tier;
  hasBlockingGaps: boolean;
  onSubmit: (decision: DecisionInput) => Promise<void>;
  onClose: () => void;
}

export function DecisionDialog({ action, version, tier, hasBlockingGaps, onSubmit, onClose }: Props) {
  const titleId = useId();
  const reasonId = useId();
  const dateId = useId();
  const firstField = useRef<HTMLTextAreaElement & HTMLInputElement>(null);
  const [reason, setReason] = useState("");
  const [until, setUntil] = useState(() => {
    const next = new Date(Date.now() + 7 * 24 * 3600 * 1000);
    next.setHours(9, 0, 0, 0);
    return localDateTimeValue(next);
  });
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const needsReason = requiresReason(action, tier, hasBlockingGaps);
  const override = overridesAssessment(action, tier, hasBlockingGaps);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    firstField.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (needsReason && !reason.trim()) {
      setError("Please give a reason.");
      return;
    }
    const decision: DecisionInput = { action, expected_version: version };
    if (reason.trim()) decision.reason = reason.trim();
    if (requiresConfirmation(action)) decision.confirm = true;
    if (action === "postpone") {
      const date = new Date(until);
      if (Number.isNaN(date.getTime()) || date.getTime() <= Date.now()) {
        setError("Choose a date and time in the future.");
        return;
      }
      decision.postpone_until = date.toISOString();
    }
    setSubmitting(true);
    try {
      await onSubmit(decision);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <form onSubmit={submit}>
          <h2 id={titleId} className="dialog__title">
            {ACTION_LABEL[action]}?
          </h2>
          {EXPLANATION[action] && <p className="muted">{EXPLANATION[action]}</p>}
          {override && (
            <p className="notice notice--warn">
              This goes against the assessment. Your reason is kept with the decision.
            </p>
          )}
          {action === "postpone" && (
            <div className="field">
              <label htmlFor={dateId}>Remind me on</label>
              <input
                id={dateId}
                ref={firstField}
                type="datetime-local"
                value={until}
                min={localDateTimeValue(new Date())}
                onChange={(e) => setUntil(e.target.value)}
                required
              />
            </div>
          )}
          <div className="field">
            <label htmlFor={reasonId}>
              Reason {needsReason ? <span className="required">(required)</span> : "(optional)"}
            </label>
            <textarea
              id={reasonId}
              ref={action === "postpone" ? undefined : firstField}
              value={reason}
              maxLength={2000}
              rows={3}
              onChange={(e) => setReason(e.target.value)}
              aria-required={needsReason}
            />
          </div>
          {error && (
            <p className="notice notice--error" role="alert">
              {error}
            </p>
          )}
          <div className="dialog__actions">
            <button type="button" className="btn btn--secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className={`btn ${action === "reject" ? "btn--danger" : "btn--primary"}`}
              disabled={submitting}
            >
              {submitting ? "Saving…" : ACTION_LABEL[action]}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
