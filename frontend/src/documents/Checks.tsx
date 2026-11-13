import { CLAIM_LABEL, KIND_LABEL, claimTexts, problems } from "./labels";
import type { DocumentKind, FactRef, Revision } from "./types";

export type Act = (run: () => Promise<unknown>, done?: string) => Promise<boolean>;

// Evidence id of the candidate's about-me: a claim citing it is a personal statement (ADR 0009).
const ABOUT_ME = "about:me";
const CONFIRMABLE = new Set(["not_verbatim", "uncertain_wording", "no_evidence", "not_entailed"]);

/** Claims that are not yet supported, why, and how to resolve each one. */
export function Checks({
  kind,
  revision,
  facts,
  busy,
  onConfirmClaim,
  onConfirmFact,
}: {
  kind: DocumentKind;
  revision: Revision;
  facts: Map<string, FactRef>;
  busy: boolean;
  onConfirmClaim: (claimId: string) => void;
  onConfirmFact: (factId: string) => void;
}) {
  const verification = revision.verification;
  const texts = claimTexts(revision.content);
  if (!verification) {
    return (
      <section className="card" aria-labelledby="checks-title">
        <h2 id="checks-title" className="card__title">
          Checks
        </h2>
        <p className="muted" role="status">
          This version is waiting to be checked against your profile…
        </p>
      </section>
    );
  }
  const open = problems(verification.claims);
  const supported = verification.claims.length - open.length;
  return (
    <section className="card" aria-labelledby="checks-title">
      <div className="card__head">
        <h2 id="checks-title" className="card__title">
          Checks
        </h2>
        <span className={`check-badge check-badge--${verification.status}`}>
          {verification.status === "passed" ? "All supported" : `${open.length} to resolve`}
        </span>
      </div>
      <p className="muted small">
        {supported} of {verification.claims.length} statements in the {KIND_LABEL[kind].toLowerCase()}{" "}
        are backed by confirmed facts in your profile.
        {verification.semantic_checked ? " Reworded text was checked by the language model." : ""}
      </p>
      {open.length > 0 && (
        <ul className="checks" aria-label={`Statements to resolve in the ${KIND_LABEL[kind].toLowerCase()}`}>
          {open.map((c) => {
            const isItem = c.claim_id.startsWith("item-");
            const unconfirmed = Object.keys(c.evidence).filter(
              (id) =>
                !id.startsWith("stmt:") &&
                id !== ABOUT_ME &&
                facts.get(id)?.verification_status === "self_reported",
            );
            const personal = ABOUT_ME in c.evidence;
            const canConfirmClaim = !isItem && c.issues.some((i) => CONFIRMABLE.has(i.code));
            return (
              <li key={c.claim_id} className={`check check--${c.status}`}>
                <div className="check__head">
                  <q className="check__text">{texts.get(c.claim_id) ?? c.claim_id}</q>
                  {personal && (
                    <span className="tag" title="Rests on your about-me, not on facts">
                      Personal statement
                    </span>
                  )}
                  <span className={`claim claim--${c.status}`}>{CLAIM_LABEL[c.status]}</span>
                </div>
                <ul className="check__issues">
                  {c.issues.map((issue, i) => (
                    <li key={i} className="small">
                      {issue.message}
                    </li>
                  ))}
                </ul>
                <div className="check__actions">
                  {unconfirmed.map((id) => (
                    <button
                      key={id}
                      type="button"
                      className="btn btn--secondary btn--small"
                      disabled={busy}
                      onClick={() => onConfirmFact(id)}
                    >
                      Confirm “{facts.get(id)?.title ?? "fact"}” is accurate
                    </button>
                  ))}
                  {canConfirmClaim && (
                    <button
                      type="button"
                      className="btn btn--secondary btn--small"
                      disabled={busy}
                      onClick={() => onConfirmClaim(c.claim_id)}
                      title="Adds this exact statement to your profile as a confirmed fact"
                    >
                      This is true as written
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
