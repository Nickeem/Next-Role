import { useId, useState, type FormEvent } from "react";

import type { Clarification } from "./types";

function Question({
  q,
  busy,
  onAnswer,
  onDismiss,
}: {
  q: Clarification;
  busy: boolean;
  onAnswer: (answer: string) => Promise<boolean>;
  onDismiss: (reason: string) => Promise<boolean>;
}) {
  const answerId = useId();
  const reasonId = useId();
  const [answer, setAnswer] = useState("");
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    if (dismissing) {
      if (q.material && !reason.trim()) {
        setError("A reason is required to dismiss a required question.");
        return;
      }
      await onDismiss(reason.trim());
    } else {
      if (!answer.trim()) {
        setError("Write an answer, or dismiss the question.");
        return;
      }
      await onAnswer(answer.trim());
    }
  }

  return (
    <li className="question">
      <p>
        {q.material && <span className="required-tag">Required</span>} {q.question}
      </p>
      <form onSubmit={submit}>
        {dismissing ? (
          <div className="field">
            <label htmlFor={reasonId}>
              Why skip it? {q.material ? <span className="required">(required)</span> : "(optional)"}
            </label>
            <input id={reasonId} value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} />
          </div>
        ) : (
          <div className="field">
            <label htmlFor={answerId}>Your answer</label>
            <textarea
              id={answerId}
              rows={2}
              maxLength={2000}
              value={answer}
              onChange={(e) => setAnswer(e.target.value)}
            />
            <span className="muted small">
              {q.answer_kind === "letter_statement"
                ? "Your words may be quoted in the cover letter."
                : "Saved to your profile as an unconfirmed fact; confirm it before it can be used."}
            </span>
          </div>
        )}
        {error && (
          <p className="notice notice--error" role="alert">
            {error}
          </p>
        )}
        <div className="question__actions">
          <button type="submit" className="btn btn--primary btn--small" disabled={busy}>
            {dismissing ? "Dismiss question" : "Save answer"}
          </button>
          <button
            type="button"
            className="btn btn--secondary btn--small"
            onClick={() => {
              setDismissing(!dismissing);
              setError(null);
            }}
          >
            {dismissing ? "Answer instead" : "I don't have this"}
          </button>
        </div>
      </form>
    </li>
  );
}

export function Questions({
  questions,
  busy,
  onAnswer,
  onDismiss,
}: {
  questions: Clarification[];
  busy: boolean;
  onAnswer: (q: Clarification, answer: string) => Promise<boolean>;
  onDismiss: (q: Clarification, reason: string) => Promise<boolean>;
}) {
  const open = questions.filter((q) => q.status === "open");
  const resolved = questions.filter((q) => q.status !== "open");
  if (questions.length === 0) return null;
  return (
    <section className="card" aria-labelledby="questions-title">
      <h2 id="questions-title" className="card__title">
        Questions {open.length > 0 && <span className="muted small">({open.length} open)</span>}
      </h2>
      <p className="muted small">
        Where your profile has no evidence, the draft leaves things out and asks instead. Only
        answer with what you can stand behind.
      </p>
      {open.length > 0 && (
        <ul className="questions">
          {open.map((q) => (
            <Question
              key={q.id}
              q={q}
              busy={busy}
              onAnswer={(a) => onAnswer(q, a)}
              onDismiss={(r) => onDismiss(q, r)}
            />
          ))}
        </ul>
      )}
      {resolved.length > 0 && (
        <details className="resolved">
          <summary className="small">Resolved ({resolved.length})</summary>
          <ul className="mini__list">
            {resolved.map((q) => (
              <li key={q.id} className="small">
                <span className="muted">{q.question}</span>
                <br />
                {q.status === "answered" ? (
                  <>
                    Answered: <q>{q.answer}</q>
                    {q.answer_kind === "career_fact" && " · added to your profile, unconfirmed; redraft the section after confirming it"}
                  </>
                ) : (
                  <>Dismissed{q.dismiss_reason ? `: ${q.dismiss_reason}` : ""}</>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
